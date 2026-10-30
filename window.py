"""Janela principal do Tonetune."""
from __future__ import annotations

import json
import threading
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Gdk", "4.0")

from gi.repository import Adw, Gdk, Gio, GLib, Gtk, Pango  # noqa: E402

from ac7 import AC7Error, Rhythm, Section, load_ac7  # noqa: E402
from audio import AudioError, PlaybackEngine, Renderer  # noqa: E402
from constants import APP_ID, APP_NAME  # noqa: E402
from instruments import InstrumentDialog  # noqa: E402
from tones import Tone  # noqa: E402
from widgets import PartRow, PatternView, SectionRow  # noqa: E402

CONFIG_DIR = Path(GLib.get_user_config_dir()) / "tonetune"
CONFIG_FILE = CONFIG_DIR / "config.json"
DEFAULT_CONFIG = {
    "recent": [],
    "last_folder": str(Path.home() / "Downloads"),
    "volume": 85,
    "loop": True,
    "width": 1280,
    "height": 830,
}
DEMO_PATH = Path(__file__).with_name("examples") / "DEMO.AC7"


def load_config() -> dict:
    data = dict(DEFAULT_CONFIG)
    try:
        raw = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        if isinstance(raw, dict):
            data.update(raw)
    except (OSError, ValueError):
        pass
    return data


def _fmt_duration(seconds: float) -> str:
    seconds = max(0, int(round(seconds)))
    return f"{seconds // 60}:{seconds % 60:02d}"


class TonetuneWindow(Adw.ApplicationWindow):
    def __init__(self, application, path: str | Path | None = None) -> None:
        super().__init__(application=application)
        self.config = load_config()
        self.set_default_size(int(self.config.get("width", 1280)),
                              int(self.config.get("height", 830)))
        self.set_size_request(1100, 700)
        self.set_title(APP_NAME)
        try:
            self.set_icon_name(APP_ID)
        except Exception:
            pass

        self.rhythm: Rhythm | None = None
        self.current_section: Section | None = None
        self.current_index = 0
        self.muted: set[int] = set()
        self.solo: set[int] = set()
        self.instruments: dict[int, Tone] = {}
        self.volumes: dict[int, int] = {}
        self._dialog = None
        self._busy = False
        self._request = 0
        self._timer_id: int | None = None
        self._reload_source: int | None = None
        self._restart_source: int | None = None
        self._monitors: list[Gio.FileMonitor] = []
        self._file_mtime = 0
        self._file_size = -1
        self._suppress_sections = False
        self._loading = False

        self.renderer = Renderer()
        self.engine = PlaybackEngine(volume=int(self.config.get("volume", 85)) / 100)
        self.engine.on_finished = self._on_playback_finished
        self.engine.on_error = self._on_playback_error

        self._build_actions()
        self._build_ui()
        self._add_drop_target()
        self._add_key_controller()

        self.connect("close-request", self._on_close_request)
        self._refresh_recent()
        if path:
            GLib.idle_add(self._load_initial, str(path))
        else:
            self.stack.set_visible_child_name("welcome")

    # ================================================================ ações
    def _build_actions(self) -> None:
        group = Gio.SimpleActionGroup()
        actions = (
            ("open", self._open_dialog),
            ("reload", self._reload_clicked),
            ("reveal", self._reveal_file),
            ("shortcuts", self._show_shortcuts),
            ("toggle-play", self._toggle_play),
            ("stop", self._stop),
            ("toggle-loop", self._toggle_loop),
            ("reset-mix", self._reset_mix),
        )
        for name, callback in actions:
            action = Gio.SimpleAction.new(name, None)
            action.connect("activate", lambda _action, _param, cb=callback: cb())
            group.add_action(action)
        self.insert_action_group("win", group)

    # ================================================================ interface
    def _build_ui(self) -> None:
        self.toolbar = Adw.ToolbarView()
        self.header = Adw.HeaderBar()
        self.window_title = Adw.WindowTitle(
            title=APP_NAME, subtitle="Reprodutor de ritmos CT-X800")
        self.header.set_title_widget(self.window_title)

        open_button = Gtk.Button.new_from_icon_name("folder-open-symbolic")
        open_button.set_tooltip_text("Abrir ritmo .AC7 (Ctrl+O)")
        open_button.connect("clicked", lambda _button: self._open_dialog())
        self.header.pack_start(open_button)

        self.reload_button = Gtk.Button.new_from_icon_name(
            "view-refresh-symbolic")
        self.reload_button.set_tooltip_text("Recarregar arquivo (F5)")
        self.reload_button.set_sensitive(False)
        self.reload_button.connect("clicked", lambda _button: self._reload_clicked())
        self.header.pack_start(self.reload_button)

        menu = Gio.Menu()
        menu.append("Abrir ritmo…", "win.open")
        menu.append("Recarregar", "win.reload")
        menu.append("Restaurar mixagem do AC7", "win.reset-mix")
        menu.append("Mostrar na pasta", "win.reveal")
        menu.append("Atalhos", "win.shortcuts")
        menu.append("Sobre o Tonetune", "app.about")
        menu_button = Gtk.MenuButton()
        menu_button.set_icon_name("open-menu-symbolic")
        menu_button.set_menu_model(menu)
        menu_button.set_tooltip_text("Menu")
        self.header.pack_end(menu_button)

        self.toolbar.add_top_bar(self.header)
        self.toast_overlay = Adw.ToastOverlay()
        self.stack = Gtk.Stack()
        self.stack.set_transition_type(Gtk.StackTransitionType.CROSSFADE)
        self.stack.add_named(self._build_welcome(), "welcome")
        self.stack.add_named(self._build_main(), "main")
        self.toast_overlay.set_child(self.stack)
        self.toolbar.set_content(self.toast_overlay)
        self.set_content(self.toolbar)

    def _build_welcome(self) -> Gtk.Widget:
        scroller = Gtk.ScrolledWindow()
        scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL,
                        valign=Gtk.Align.CENTER,
                        halign=Gtk.Align.CENTER,
                        spacing=12)
        outer.set_margin_top(36)
        outer.set_margin_bottom(36)
        outer.set_margin_start(36)
        outer.set_margin_end(36)

        self.welcome_logo = Gtk.Image.new_from_icon_name(APP_ID)
        self.welcome_logo.set_pixel_size(148)
        outer.append(self.welcome_logo)

        title = Gtk.Label(label=APP_NAME)
        title.add_css_class("welcome-title")
        outer.append(title)

        subtitle = Gtk.Label(
            label="Ouça e teste os ritmos do Casio CT-X800 sem ir ao teclado.")
        subtitle.add_css_class("welcome-subtitle")
        subtitle.set_justify(Gtk.Justification.CENTER)
        subtitle.set_wrap(True)
        outer.append(subtitle)

        open_button = Gtk.Button(label="Abrir ritmo .AC7")
        open_button.add_css_class("suggested-action")
        open_button.add_css_class("welcome-button")
        open_button.set_halign(Gtk.Align.CENTER)
        open_button.set_margin_top(10)
        open_button.connect("clicked", lambda _button: self._open_dialog())
        outer.append(open_button)

        self.demo_button = Gtk.Button(label="Ouvir ritmo de exemplo")
        self.demo_button.add_css_class("welcome-button")
        self.demo_button.set_halign(Gtk.Align.CENTER)
        self.demo_button.set_visible(DEMO_PATH.is_file())
        self.demo_button.connect("clicked", lambda _button: self.load_path(DEMO_PATH))
        outer.append(self.demo_button)

        hint = Gtk.Label(label="ou arraste um arquivo .AC7 para esta janela")
        hint.add_css_class("caption")
        hint.add_css_class("dim-label")
        outer.append(hint)

        self.recent_title = Gtk.Label(label="Recentes", xalign=0)
        self.recent_title.add_css_class("recent-title")
        self.recent_title.set_margin_top(18)
        self.recent_title.set_visible(False)
        outer.append(self.recent_title)

        self.recent_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL,
                                  spacing=4)
        self.recent_box.set_halign(Gtk.Align.FILL)
        self.recent_box.set_size_request(420, -1)
        outer.append(self.recent_box)

        scroller.set_child(outer)
        return scroller

    def _build_main(self) -> Gtk.Widget:
        main = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        main.set_margin_top(10)
        main.set_margin_bottom(10)
        main.set_margin_start(10)
        main.set_margin_end(10)

        main.append(self._build_sections_panel())

        center = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        center.set_hexpand(True)
        center.set_margin_start(10)
        center.set_margin_end(10)
        center.append(self._build_info_panel())
        center.append(self._build_pattern_panel())
        main.append(center)

        main.append(self._build_parts_panel())
        return main

    def _build_sections_panel(self) -> Gtk.Widget:
        panel = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        panel.add_css_class("panel")
        panel.set_size_request(286, -1)
        panel.set_margin_top(0)
        panel.set_margin_bottom(0)

        header = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        header.set_margin_top(14)
        header.set_margin_start(14)
        header.set_margin_end(14)
        title = Gtk.Label(label="Seções", xalign=0)
        title.add_css_class("panel-title")
        header.append(title)
        hint = Gtk.Label(label="Clique para escolher; ▶ toca na hora.",
                         xalign=0)
        hint.add_css_class("caption")
        hint.add_css_class("dim-label")
        header.append(hint)
        panel.append(header)

        scroller = Gtk.ScrolledWindow()
        scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroller.set_vexpand(True)
        scroller.set_margin_top(4)
        scroller.set_margin_bottom(10)
        scroller.set_margin_start(6)
        scroller.set_margin_end(6)
        self.sections_list = Gtk.ListBox()
        self.sections_list.set_selection_mode(Gtk.SelectionMode.SINGLE)
        self.sections_list.set_activate_on_single_click(True)
        self.sections_list.add_css_class("sections-list")
        self.sections_list.connect("row-selected", self._on_section_selected)
        scroller.set_child(self.sections_list)
        panel.append(scroller)
        return panel

    def _build_info_panel(self) -> Gtk.Widget:
        panel = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        panel.add_css_class("panel")
        panel.set_margin_top(12)
        panel.set_margin_bottom(12)
        panel.set_margin_start(14)
        panel.set_margin_end(14)

        top = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        title_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        title_box.set_hexpand(True)
        self.name_label = Gtk.Label(label="—", xalign=0)
        self.name_label.add_css_class("rhythm-name")
        self.name_label.set_ellipsize(Pango.EllipsizeMode.END)
        title_box.append(self.name_label)
        self.file_label = Gtk.Label(label="", xalign=0)
        self.file_label.add_css_class("caption")
        self.file_label.add_css_class("dim-label")
        self.file_label.set_ellipsize(Pango.EllipsizeMode.MIDDLE)
        title_box.append(self.file_label)
        top.append(title_box)

        self.badge_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL,
                                 spacing=6)
        self.badge_box.set_valign(Gtk.Align.START)
        top.append(self.badge_box)
        panel.append(top)

        transport = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.play_button = Gtk.Button.new_from_icon_name(
            "media-playback-start-symbolic")
        self.play_button.add_css_class("transport-play")
        self.play_button.set_tooltip_text("Tocar / pausar (Espaço)")
        self.play_button.connect("clicked", lambda _button: self._toggle_play())
        transport.append(self.play_button)

        self.stop_button = Gtk.Button.new_from_icon_name(
            "media-playback-stop-symbolic")
        self.stop_button.add_css_class("flat")
        self.stop_button.add_css_class("transport-small")
        self.stop_button.set_tooltip_text("Parar (Esc)")
        self.stop_button.connect("clicked", lambda _button: self._stop())
        transport.append(self.stop_button)

        self.loop_button = Gtk.ToggleButton()
        self.loop_button.set_icon_name("media-playlist-repeat-symbolic")
        self.loop_button.set_active(bool(self.config.get("loop", True)))
        self.loop_button.add_css_class("flat")
        self.loop_button.set_tooltip_text("Repetir a seção (L)")
        self.loop_button.connect("toggled", self._on_loop_toggled)
        transport.append(self.loop_button)

        transport.append(Gtk.Separator(orientation=Gtk.Orientation.VERTICAL))

        tempo_label = Gtk.Label(label="BPM")
        tempo_label.add_css_class("caption")
        tempo_label.add_css_class("dim-label")
        transport.append(tempo_label)
        self.tempo_spin = Gtk.SpinButton.new_with_range(20, 300, 1)
        self.tempo_spin.set_digits(0)
        self.tempo_spin.set_numeric(True)
        self.tempo_spin.set_width_chars(4)
        self.tempo_spin.set_tooltip_text(
            "Andamento de teste (não altera o arquivo)")
        self._tempo_handler = self.tempo_spin.connect(
            "value-changed", self._on_tempo_changed)
        transport.append(self.tempo_spin)

        transport.append(Gtk.Separator(orientation=Gtk.Orientation.VERTICAL))

        volume_icon = Gtk.Image.new_from_icon_name("audio-volume-high-symbolic")
        volume_icon.add_css_class("dim-label")
        transport.append(volume_icon)
        self.volume_scale = Gtk.Scale.new_with_range(
            Gtk.Orientation.HORIZONTAL, 0, 100, 1)
        self.volume_scale.set_draw_value(False)
        self.volume_scale.set_value(int(self.config.get("volume", 85)))
        self.volume_scale.set_size_request(110, -1)
        self.volume_scale.set_tooltip_text("Volume geral")
        self.volume_scale.connect("value-changed", self._on_volume_changed)
        transport.append(self.volume_scale)
        panel.append(transport)

        status = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=7)
        self.status_spinner = Gtk.Spinner()
        self.status_spinner.set_visible(False)
        status.append(self.status_spinner)
        self.status_label = Gtk.Label(label="Pronto", xalign=0)
        self.status_label.add_css_class("caption")
        self.status_label.add_css_class("dim-label")
        self.status_label.set_ellipsize(Pango.EllipsizeMode.END)
        status.append(self.status_label)
        panel.append(status)
        return panel

    def _build_pattern_panel(self) -> Gtk.Widget:
        panel = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        panel.add_css_class("panel")
        panel.set_vexpand(True)
        panel.set_margin_top(12)
        panel.set_margin_bottom(14)
        panel.set_margin_start(14)
        panel.set_margin_end(14)

        head = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=1)
        text.set_hexpand(True)
        self.pattern_title = Gtk.Label(label="Padrão", xalign=0)
        self.pattern_title.add_css_class("panel-title")
        text.append(self.pattern_title)
        self.pattern_subtitle = Gtk.Label(label="", xalign=0)
        self.pattern_subtitle.add_css_class("caption")
        self.pattern_subtitle.add_css_class("dim-label")
        text.append(self.pattern_subtitle)
        head.append(text)
        self.duration_label = Gtk.Label(label="")
        self.duration_label.add_css_class("badge")
        self.duration_label.set_valign(Gtk.Align.START)
        head.append(self.duration_label)
        panel.append(head)

        self.pattern = PatternView()
        panel.append(self.pattern)
        return panel

    def _build_parts_panel(self) -> Gtk.Widget:
        panel = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        panel.add_css_class("panel")
        panel.set_size_request(306, -1)

        header = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        header.set_margin_top(14)
        header.set_margin_start(14)
        header.set_margin_end(14)
        title = Gtk.Label(label="Partes", xalign=0)
        title.add_css_class("panel-title")
        header.append(title)
        hint = Gtk.Label(
            label="Ligue/desligue · S isola · timbre troca · volume ajusta.",
            xalign=0)
        hint.add_css_class("caption")
        hint.add_css_class("dim-label")
        hint.set_wrap(True)
        header.append(hint)
        panel.append(header)

        scroller = Gtk.ScrolledWindow()
        scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroller.set_vexpand(True)
        scroller.set_margin_top(4)
        scroller.set_margin_bottom(10)
        scroller.set_margin_start(6)
        scroller.set_margin_end(6)
        self.parts_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL,
                                 spacing=2)
        self.part_rows: dict[int, PartRow] = {}
        for part in range(1, 9):
            row = PartRow(part, self._on_part_changed,
                          self._on_part_volume,
                          self._open_instrument_dialog)
            self.part_rows[part] = row
            self.parts_box.append(row)
        scroller.set_child(self.parts_box)
        panel.append(scroller)
        return panel

    # ================================================================ load
    def _load_initial(self, path: str) -> bool:
        self.load_path(path)
        return False

    def load_path(self, path: str | Path, *, reloading: bool = False) -> bool:
        path = Path(path).expanduser()
        if not path.is_file():
            self._toast("Arquivo não encontrado.")
            return False
        try:
            rhythm = load_ac7(path)
        except AC7Error as exc:
            if reloading:
                self._toast(str(exc))
                self._set_status("O arquivo mudou, mas ainda não é válido.")
            else:
                self._toast(str(exc))
                self.stack.set_visible_child_name("welcome")
            return False

        was_playing = self.engine.playing
        was_paused = self.engine.paused
        if not reloading:
            self.engine.stop()
            self._request += 1
            self._set_busy(False)
            self.muted.clear()
            self.solo.clear()
            self.instruments.clear()
            self.volumes.clear()
        self._apply_rhythm(rhythm, reloading=reloading,
                           autoplay=was_playing and reloading)
        if reloading and was_paused:
            self._set_status("Ritmo atualizado (parado).")
        return True

    def _apply_rhythm(self, rhythm: Rhythm, *, reloading: bool,
                      autoplay: bool) -> None:
        previous_index = self.current_index
        self.rhythm = rhythm
        self._file_mtime = rhythm.path.stat().st_mtime_ns
        self._file_size = rhythm.path.stat().st_size
        self.reload_button.set_sensitive(True)
        self._loading = True
        self.tempo_spin.set_value(rhythm.tempo)
        self._loading = False
        self._update_info()
        self._rebuild_sections()
        if not reloading:
            for row in self.part_rows.values():
                row.set_state(False, False)
            self._update_parts_states()

        section = None
        if reloading:
            section = rhythm.section(previous_index)
        else:
            preferred = rhythm.section(2)  # Variação 1 é o melhor ponto de teste
            if preferred is not None and not preferred.is_empty:
                section = preferred
        if section is None or section.is_empty:
            section = rhythm.first_playable()
        if section is None:
            self._set_status("Este ritmo não tem seções com notas.")
            return
        self.current_index = section.index
        self.current_section = section
        self._update_section_ui()
        self._select_row(section.index)
        self.stack.set_visible_child_name("main")
        self._add_recent(rhythm.path)
        self._set_monitors(rhythm.path)
        if not reloading:
            self._set_status("Pronto. Selecione uma seção e aperte play.")
        if autoplay:
            self._start_playback()

    def _rebuild_sections(self) -> None:
        self._suppress_sections = True
        while (child := self.sections_list.get_first_child()) is not None:
            self.sections_list.remove(child)
        self.section_rows: dict[int, SectionRow] = {}
        if self.rhythm is not None:
            for section in self.rhythm.sections:
                row = SectionRow(section, self._play_section)
                self.sections_list.append(row)
                self.section_rows[section.index] = row
        self._suppress_sections = False

    def _update_badges(self) -> None:
        while (child := self.badge_box.get_first_child()) is not None:
            self.badge_box.remove(child)
        if self.rhythm is None:
            return
        tempo = (int(self.tempo_spin.get_value())
                 if hasattr(self, "tempo_spin") else self.rhythm.tempo)
        for text in (
            f"{tempo} BPM",
            self.rhythm.signature,
            f"{len(self.rhythm.sections)} seções",
            f"{self.rhythm.file_size / 1024:.1f} KB",
        ):
            label = Gtk.Label(label=text)
            label.add_css_class("badge")
            self.badge_box.append(label)

    def _update_info(self) -> None:
        if self.rhythm is None:
            return
        self.name_label.set_label(self.rhythm.display_name)
        self.name_label.set_tooltip_text(self.rhythm.display_name)
        self.window_title.set_subtitle(self.rhythm.path.name)
        self.file_label.set_label(self.rhythm.path.name)
        self.file_label.set_tooltip_text(str(self.rhythm.path))
        self._update_badges()

    def _select_section(self, index: int, *, play: bool) -> None:
        if self.rhythm is None:
            return
        section = self.rhythm.section(index)
        if section is None or section.is_empty:
            return
        was_active = self.engine.active
        if index != self.current_index:
            if was_active:
                self.engine.stop()
            self._request += 1
            self._set_busy(False)
            self.current_index = index
            self.current_section = section
            self._stop_timer()
            self.pattern.set_position(0)
            self._update_section_ui()
            self._select_row(index)
            if was_active or play:
                self._start_playback()
        elif play:
            self._start_playback()
        else:
            self._select_row(index)

    def _select_row(self, index: int) -> None:
        row = getattr(self, "section_rows", {}).get(index)
        if row is None:
            return
        self._suppress_sections = True
        self.sections_list.select_row(row)
        self._suppress_sections = False

    def _update_section_ui(self) -> None:
        section = self.current_section
        if section is None or self.rhythm is None:
            return
        tempo = int(self.tempo_spin.get_value())
        self.pattern.set_data(self.rhythm, section)
        self.pattern_title.set_label(section.name)
        bars = "compasso" if section.measures == 1 else "compassos"
        self.pattern_subtitle.set_label(
            f"{section.measures} {bars} · {section.beats}/{section.denominator}"
            f" · {section.note_count} notas")
        self.duration_label.set_label(
            _fmt_duration(self.rhythm.duration(section, tempo)))
        for part, row in self.part_rows.items():
            mixer = section.mixer_for_part(part)
            row.set_mixer(
                mixer,
                override_tone=self.instruments.get(part),
                volume=self.volumes.get(part, mixer.volume),
                volume_overridden=part in self.volumes,
            )
            row.set_section(section)
        self._update_parts_states()
        if self.engine.state == "stopped" and not self._busy:
            self._set_status("Pronto.")

    def _update_parts_states(self) -> None:
        for part, row in getattr(self, "part_rows", {}).items():
            row.set_state(part in self.muted, part in self.solo)

    def _play_section(self, index: int) -> None:
        self._select_section(index, play=True)

    def _on_section_selected(self, _listbox, row) -> None:
        if self._suppress_sections or row is None:
            return
        index = getattr(row, "section", None)
        if index is None:
            return
        self._select_section(index.index, play=False)

    # ================================================================ áudio
    def _toggle_play(self) -> None:
        if self._busy:
            return
        if self.engine.playing:
            self.engine.pause()
            self._stop_timer()
            self._set_status("Pausado.")
            self._update_play_icon()
        elif self.engine.paused:
            self.engine.resume()
            self._set_status(self._playing_text())
            self._start_timer()
            self._update_play_icon()
        else:
            self._start_playback()

    def _start_playback(self) -> None:
        if self.rhythm is None or self.current_section is None:
            return
        section = self.current_section
        rhythm = self.rhythm
        tempo = int(self.tempo_spin.get_value())
        muted = set(self.muted)
        solo = set(self.solo)
        instruments = {part: (tone.program, tone.bank)
                       for part, tone in self.instruments.items()}
        volumes = dict(self.volumes)
        self._request += 1
        request = self._request
        self._set_busy(True, f"Preparando {section.name}…")
        self._update_play_icon()

        def work() -> None:
            try:
                wav = self.renderer.render(
                    rhythm, section, tempo, muted, solo,
                    instruments, volumes)
            except (AudioError, OSError) as exc:
                GLib.idle_add(self._render_done, request, None, str(exc))
            except Exception as exc:  # pragma: no cover - proteção extra
                GLib.idle_add(self._render_done, request, None, str(exc))
            else:
                GLib.idle_add(self._render_done, request, wav, None)

        threading.Thread(target=work, daemon=True).start()

    def _render_done(self, request: int, wav: Path | None,
                     error: str | None) -> bool:
        if request != self._request:
            return False
        self._set_busy(False)
        if error or wav is None:
            self._set_status("Não foi possível preparar o áudio.")
            self._toast(error or "Erro ao preparar o áudio.")
            self._update_play_icon()
            return False
        try:
            self.engine.play(wav, loop=self.loop_button.get_active())
        except AudioError as exc:
            self._set_status("Não foi possível tocar.")
            self._toast(str(exc))
            self._update_play_icon()
            return False
        self._set_status(self._playing_text())
        self._start_timer()
        self._update_play_icon()
        return False

    def _playing_text(self) -> str:
        if self.current_section is None:
            return "Reproduzindo."
        loop = " · repetindo" if self.loop_button.get_active() else ""
        return f"Reproduzindo {self.current_section.name}{loop}."

    def _stop(self) -> None:
        self._request += 1
        self.engine.stop()
        self._stop_timer()
        self._set_busy(False)
        self.pattern.set_position(0)
        self._set_status("Parado.")
        self._update_play_icon()

    def _start_timer(self) -> None:
        self._stop_timer()
        self._timer_id = GLib.timeout_add(33, self._tick)

    def _stop_timer(self) -> None:
        if self._timer_id is not None:
            GLib.source_remove(self._timer_id)
            self._timer_id = None

    def _tick(self) -> bool:
        if (not self.engine.active or self.rhythm is None
                or self.current_section is None):
            self._timer_id = None
            return False
        duration = self.rhythm.duration(
            self.current_section, int(self.tempo_spin.get_value()))
        if duration > 0:
            self.pattern.set_position(self.engine.position() / duration)
        return True

    def _on_playback_finished(self) -> None:
        self._stop_timer()
        self.pattern.set_position(0)
        self._set_status("Fim da seção.")
        self._update_play_icon()

    def _on_playback_error(self, message: str) -> None:
        self._stop_timer()
        self._set_status("Erro de áudio.")
        self._toast(message)
        self._update_play_icon()

    def _update_play_icon(self) -> None:
        if self.engine.playing:
            icon = "media-playback-pause-symbolic"
        else:
            icon = "media-playback-start-symbolic"
        self.play_button.set_icon_name(icon)
        self.play_button.set_sensitive(not self._busy)

    def _set_busy(self, busy: bool, text: str = "") -> None:
        self._busy = busy
        self.status_spinner.set_visible(busy)
        if busy:
            self.status_spinner.start()
            self.status_label.set_label(text)
        else:
            self.status_spinner.stop()
        self.play_button.set_sensitive(not busy)
        self._update_play_icon()

    def _set_status(self, text: str) -> None:
        self.status_label.set_label(text)
        if not self._busy:
            self.status_spinner.set_visible(False)
            self.status_spinner.stop()

    def _on_loop_toggled(self, button) -> None:
        self.engine.set_loop(button.get_active())
        self.config["loop"] = button.get_active()
        if self.engine.playing:
            self._set_status(self._playing_text())

    def _toggle_loop(self) -> None:
        self.loop_button.set_active(not self.loop_button.get_active())

    def _on_tempo_changed(self, _spin) -> None:
        if self._loading or self.rhythm is None or self.current_section is None:
            return
        self._update_badges()
        self.duration_label.set_label(_fmt_duration(self.rhythm.duration(
            self.current_section, int(self.tempo_spin.get_value()))))
        if self.engine.active or self._busy:
            self._schedule_restart()

    def _on_volume_changed(self, scale) -> None:
        value = int(scale.get_value())
        self.engine.set_volume(value / 100)
        self.config["volume"] = value

    def _on_part_changed(self, _part: int) -> None:
        self.muted = {part for part, row in self.part_rows.items()
                      if not row.enabled.get_active()}
        self.solo = {part for part, row in self.part_rows.items()
                     if row.solo.get_active()}
        if self.engine.active or self._busy:
            self._schedule_restart()

    def _on_part_volume(self, part: int, value: int) -> None:
        self.volumes[part] = max(0, min(127, int(value)))
        if self.engine.active or self._busy:
            self._schedule_restart()

    def _open_instrument_dialog(self, part: int) -> None:
        if self.current_section is None:
            return
        mixer = self.current_section.mixer_for_part(part)
        override = self.instruments.get(part)

        def apply(tone: Tone) -> None:
            self.instruments[part] = tone
            self._refresh_part_row(part)
            if self.engine.active or self._busy:
                self._schedule_restart()

        def reset() -> None:
            self.instruments.pop(part, None)
            self._refresh_part_row(part)
            if self.engine.active or self._busy:
                self._schedule_restart()

        dialog = InstrumentDialog(
            self, part, mixer.patch, mixer.bank, override, apply, reset)
        self._dialog = dialog
        dialog.connect("close-request", self._on_instrument_closed)
        dialog.present()

    def _on_instrument_closed(self, _dialog) -> bool:
        self._dialog = None
        return False

    def _refresh_part_row(self, part: int) -> None:
        if self.current_section is None:
            return
        row = self.part_rows.get(part)
        if row is None:
            return
        mixer = self.current_section.mixer_for_part(part)
        row.set_mixer(
            mixer,
            override_tone=self.instruments.get(part),
            volume=self.volumes.get(part, mixer.volume),
            volume_overridden=part in self.volumes,
        )
        row.set_section(self.current_section)

    def _reset_mix(self) -> None:
        if (not self.instruments and not self.volumes
                and not self.muted and not self.solo):
            return
        self.instruments.clear()
        self.volumes.clear()
        self.muted.clear()
        self.solo.clear()
        for part in range(1, 9):
            self.part_rows[part].set_state(False, False)
            self._refresh_part_row(part)
        if self.engine.active or self._busy:
            self._schedule_restart()
        self._toast("Mixagem original do AC7 restaurada.")

    def _schedule_restart(self) -> None:
        if self._restart_source is not None:
            GLib.source_remove(self._restart_source)
        self._restart_source = GLib.timeout_add(350, self._restart_now)

    def _restart_now(self) -> bool:
        self._restart_source = None
        if self.rhythm is None or self.current_section is None:
            return False
        if self.engine.active or self._busy:
            self.engine.stop()
            self._start_playback()
        return False

    # ================================================================ arquivo
    def _open_dialog(self) -> None:
        dialog = Gtk.FileDialog.new()
        dialog.set_title("Abrir ritmo AC7")
        dialog.set_modal(True)
        filters = Gio.ListStore.new(Gtk.FileFilter)
        ac7_filter = Gtk.FileFilter()
        ac7_filter.set_name("Ritmos do Casio (*.AC7)")
        ac7_filter.add_pattern("*.ac7")
        ac7_filter.add_pattern("*.AC7")
        filters.append(ac7_filter)
        all_filter = Gtk.FileFilter()
        all_filter.set_name("Todos os arquivos")
        all_filter.add_pattern("*")
        filters.append(all_filter)
        dialog.set_filters(filters)
        dialog.set_default_filter(ac7_filter)

        folder = Path(self.config.get("last_folder", ""))
        if not folder.is_dir():
            folder = Path.home() / "Downloads"
        dialog.set_initial_folder(Gio.File.new_for_path(str(folder)))
        dialog.open(self, None, self._open_dialog_done)

    def _open_dialog_done(self, dialog, result) -> None:
        try:
            file = dialog.open_finish(result)
        except GLib.Error:
            return
        if file is None or file.get_path() is None:
            return
        path = Path(file.get_path())
        self.config["last_folder"] = str(path.parent)
        self.load_path(path)

    def _reload_clicked(self) -> None:
        if self.rhythm is None:
            return
        self._reload_file(0, manual=True)

    def _reload_file(self, attempt: int, manual: bool = False) -> None:
        if self.rhythm is None:
            return
        path = self.rhythm.path
        try:
            stat = path.stat()
        except OSError:
            self._toast("O arquivo não está mais disponível.")
            return
        if not manual and (stat.st_mtime_ns == self._file_mtime
                           and stat.st_size == self._file_size):
            return
        try:
            rhythm = load_ac7(path)
        except AC7Error as exc:
            if attempt < 3:
                if not self.engine.playing:
                    self._set_status("Aguardando o arquivo terminar de salvar…")
                GLib.timeout_add(
                    650, lambda: (self._reload_file(attempt + 1), False)[1])
            else:
                self._set_status("Não foi possível recarregar o arquivo.")
                self._toast(str(exc))
            return

        was_playing = self.engine.playing
        was_active = self.engine.active
        self._request += 1
        self._set_busy(False)
        if was_active:
            self.engine.stop()
            self._stop_timer()
        self._apply_rhythm(rhythm, reloading=True, autoplay=was_playing)
        if not was_playing:
            self._set_status("Ritmo atualizado (parado).")
        if manual:
            self._toast("Ritmo recarregado.")

    def _reveal_file(self) -> None:
        if self.rhythm is None:
            return
        try:
            Gio.AppInfo.launch_default_for_uri(
                self.rhythm.path.parent.as_uri(), None)
        except GLib.Error:
            self._toast("Não foi possível abrir a pasta.")

    def _add_recent(self, path: Path) -> None:
        path_text = str(path.resolve())
        recent = [item for item in self.config.get("recent", [])
                  if item != path_text]
        recent.insert(0, path_text)
        self.config["recent"] = recent[:8]
        self.config["last_folder"] = str(path.parent)
        self._save_config()
        self._refresh_recent()

    def _refresh_recent(self) -> None:
        if not hasattr(self, "recent_box"):
            return
        while (child := self.recent_box.get_first_child()) is not None:
            self.recent_box.remove(child)
        shown = 0
        for item in self.config.get("recent", []):
            path = Path(item)
            if not path.is_file():
                continue
            button = Gtk.Button()
            button.add_css_class("flat")
            button.add_css_class("recent-button")
            box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
            icon = Gtk.Image.new_from_icon_name("audio-x-generic-symbolic")
            icon.add_css_class("dim-label")
            box.append(icon)
            text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
            text.set_hexpand(True)
            name = Gtk.Label(label=path.name, xalign=0)
            name.set_ellipsize(Pango.EllipsizeMode.MIDDLE)
            text.append(name)
            folder = Gtk.Label(label=str(path.parent), xalign=0)
            folder.add_css_class("caption")
            folder.add_css_class("dim-label")
            folder.set_ellipsize(Pango.EllipsizeMode.MIDDLE)
            text.append(folder)
            box.append(text)
            button.set_child(box)
            button.set_tooltip_text(str(path))
            button.connect("clicked", lambda _b, p=path: self.load_path(p))
            self.recent_box.append(button)
            shown += 1
            if shown >= 5:
                break
        self.recent_title.set_visible(shown > 0)

    def _save_config(self) -> None:
        try:
            CONFIG_DIR.mkdir(parents=True, exist_ok=True)
            tmp = CONFIG_FILE.with_suffix(".tmp")
            tmp.write_text(json.dumps(self.config, indent=2,
                                      ensure_ascii=False),
                           encoding="utf-8")
            tmp.replace(CONFIG_FILE)
        except OSError:
            pass

    # ================================================================ monitores
    def _set_monitors(self, path: Path) -> None:
        for monitor in self._monitors:
            monitor.cancel()
        self._monitors.clear()
        try:
            parent_monitor = Gio.File.new_for_path(
                str(path.parent)).monitor_directory(
                    Gio.FileMonitorFlags.WATCH_MOVES, None)
            parent_monitor.connect("changed", self._on_monitor_changed)
            self._monitors.append(parent_monitor)
            file_monitor = Gio.File.new_for_path(
                str(path)).monitor_file(
                    Gio.FileMonitorFlags.WATCH_MOVES, None)
            file_monitor.connect("changed", self._on_monitor_changed)
            self._monitors.append(file_monitor)
        except GLib.Error:
            pass

    def _on_monitor_changed(self, _monitor, file, other_file,
                            _event_type) -> None:
        if self.rhythm is None:
            return
        current = self.rhythm.path
        names = set()
        for item in (file, other_file):
            if item is not None and item.get_path():
                names.add(Path(item.get_path()).name)
        if names and current.name not in names:
            return
        if self._reload_source is not None:
            GLib.source_remove(self._reload_source)
        self._reload_source = GLib.timeout_add(500, self._check_reload)

    def _check_reload(self) -> bool:
        self._reload_source = None
        if self.rhythm is None:
            return False
        try:
            stat = self.rhythm.path.stat()
        except OSError:
            return False
        if (stat.st_mtime_ns != self._file_mtime
                or stat.st_size != self._file_size):
            self._reload_file(0)
        return False

    # ================================================================ extras
    def _add_drop_target(self) -> None:
        target = Gtk.DropTarget.new(Gio.File, Gdk.DragAction.COPY)
        target.connect("drop", self._on_drop)
        self.add_controller(target)

    def _on_drop(self, _target, value, _x, _y) -> bool:
        if isinstance(value, Gio.File) and value.get_path():
            self.load_path(value.get_path())
            return True
        return False

    def _add_key_controller(self) -> None:
        controller = Gtk.EventControllerKey()
        controller.connect("key-pressed", self._on_key_pressed)
        self.add_controller(controller)

    def _on_key_pressed(self, _controller, keyval, _keycode, state) -> bool:
        focus = self.get_focus()
        if isinstance(focus, (Gtk.Entry, Gtk.Text, Gtk.SpinButton)):
            return False
        if state & (Gdk.ModifierType.CONTROL_MASK
                    | Gdk.ModifierType.ALT_MASK
                    | Gdk.ModifierType.SUPER_MASK):
            return False
        if keyval == Gdk.KEY_space:
            self._toggle_play()
            return True
        if keyval == Gdk.KEY_Escape:
            self._stop()
            return True
        if keyval in (Gdk.KEY_l, Gdk.KEY_L):
            self._toggle_loop()
            return True
        if keyval == Gdk.KEY_F5:
            self._reload_clicked()
            return True
        digit = None
        if Gdk.KEY_1 <= keyval <= Gdk.KEY_9:
            digit = keyval - Gdk.KEY_1 + 1
        elif keyval == Gdk.KEY_0:
            digit = 10
        if digit is not None and self.rhythm is not None:
            section = self.rhythm.section(digit)
            if section is not None and not section.is_empty:
                self._select_section(digit, play=True)
                return True
        return False

    def _show_shortcuts(self) -> None:
        dialog = Adw.Window(transient_for=self, modal=True, title="Atalhos")
        dialog.set_default_size(460, 570)
        toolbar = Adw.ToolbarView()
        header = Adw.HeaderBar()
        toolbar.add_top_bar(header)
        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        content.set_margin_top(18)
        content.set_margin_bottom(18)
        content.set_margin_start(18)
        content.set_margin_end(18)
        rows = (
            ("Espaço", "Tocar ou pausar"),
            ("Esc", "Parar"),
            ("L", "Ligar/desligar repetição"),
            ("1 a 9 e 0", "Tocar a seção correspondente"),
            ("F5", "Recarregar o arquivo"),
            ("Ctrl+O", "Abrir outro ritmo"),
            ("S", "Isolar uma parte"),
            ("Interruptor", "Ligar/desligar a parte"),
            ("Timbre", "Trocar o instrumento da parte"),
            ("Volume", "Arrastar o volume da parte"),
        )
        listbox = Gtk.ListBox()
        listbox.add_css_class("boxed-list")
        listbox.set_selection_mode(Gtk.SelectionMode.NONE)
        for key, description in rows:
            row = Gtk.ListBoxRow()
            box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
            box.set_margin_top(8)
            box.set_margin_bottom(8)
            box.set_margin_start(10)
            box.set_margin_end(10)
            key_label = Gtk.Label(label=key, xalign=0)
            key_label.add_css_class("monospace")
            key_label.set_size_request(105, -1)
            box.append(key_label)
            text = Gtk.Label(label=description, xalign=0)
            text.set_hexpand(True)
            box.append(text)
            row.set_child(box)
            listbox.append(row)
        content.append(listbox)
        toolbar.set_content(content)
        dialog.set_content(toolbar)
        dialog.present()

    def _toast(self, message: str) -> None:
        toast = Adw.Toast.new(message)
        toast.set_timeout(4)
        self.toast_overlay.add_toast(toast)

    def _on_close_request(self, _window) -> bool:
        self.config["width"] = self.get_width()
        self.config["height"] = self.get_height()
        self._save_config()
        self._request += 1
        self._stop_timer()
        if self._reload_source is not None:
            GLib.source_remove(self._reload_source)
            self._reload_source = None
        if self._restart_source is not None:
            GLib.source_remove(self._restart_source)
            self._restart_source = None
        for monitor in self._monitors:
            monitor.cancel()
        self._monitors.clear()
        self.engine.on_finished = None
        self.engine.on_error = None
        self.engine.close()
        return False
