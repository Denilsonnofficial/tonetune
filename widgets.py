"""Widgets desenhados do Tonetune."""
from __future__ import annotations

import math

import cairo
import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Pango", "1.0")
gi.require_version("PangoCairo", "1.0")

from gi.repository import Adw, Gtk, Pango, PangoCairo  # noqa: E402

from ac7 import SECTION_NAMES, Section  # noqa: E402
from tones import detail  # noqa: E402

PART_COLORS = (
    "#f6bd60",  # percussão
    "#ef8354",  # bateria
    "#4ecdc4",  # baixo
    "#8ecae6",  # piano
    "#a78bfa",  # strings
    "#f472b6",  # e. piano
    "#84cc16",  # violão
    "#38bdf8",  # pad/coro
)

ACCENT = "#2ec4b6"


def _rounded(cr: cairo.Context, x: float, y: float, width: float,
             height: float, radius: float) -> None:
    radius = min(radius, width / 2, height / 2)
    cr.new_sub_path()
    cr.arc(x + width - radius, y + radius, radius, -math.pi / 2, 0)
    cr.arc(x + width - radius, y + height - radius, radius, 0, math.pi / 2)
    cr.arc(x + radius, y + height - radius, radius, math.pi / 2, math.pi)
    cr.arc(x + radius, y + radius, radius, math.pi, 3 * math.pi / 2)
    cr.close_path()


class ColorDot(Gtk.DrawingArea):
    def __init__(self, color: str, size: int = 10) -> None:
        super().__init__()
        self.color = color
        self.set_size_request(size, size)
        self.set_draw_func(self._draw)

    def _draw(self, _area, cr, width, height) -> None:
        cr.set_source_rgb(*_hex(self.color))
        cr.arc(width / 2, height / 2, min(width, height) / 2 - 0.5, 0,
               2 * math.pi)
        cr.fill()


def _hex(value: str) -> tuple[float, float, float]:
    value = value.lstrip("#")
    return tuple(int(value[index:index + 2], 16) / 255.0
                 for index in (0, 2, 4))


class MiniBars(Gtk.DrawingArea):
    """Barrinhas de atividade por compasso."""

    def __init__(self) -> None:
        super().__init__()
        self.set_size_request(68, 18)
        self.set_valign(Gtk.Align.CENTER)
        self.counts: list[int] = []
        self.set_draw_func(self._draw)

    def set_counts(self, counts: list[int]) -> None:
        self.counts = counts
        self.queue_draw()

    def _draw(self, _area, cr, width, height) -> None:
        if not self.counts:
            return
        dark = Adw.StyleManager.get_default().get_dark()
        base = (1, 1, 1, 0.14) if dark else (0, 0, 0, 0.12)
        peak = max(self.counts) or 1
        step = width / len(self.counts)
        bar_width = max(1.5, step - 1.5)
        for index, count in enumerate(self.counts):
            x = index * step
            fraction = count / peak
            bar_height = max(2.0, fraction * (height - 2))
            y = height - bar_height
            if count:
                cr.set_source_rgba(*_hex(ACCENT), 0.35 + 0.55 * fraction)
            else:
                cr.set_source_rgba(*base)
            _rounded(cr, x, y, bar_width, bar_height, 1.5)
            cr.fill()


class SectionRow(Gtk.ListBoxRow):
    def __init__(self, section: Section, on_play) -> None:
        super().__init__()
        self.section = section
        self.add_css_class("section-row")
        if section.is_empty:
            self.add_css_class("empty-section")

        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=9)
        row.set_margin_top(6)
        row.set_margin_bottom(6)
        row.set_margin_start(8)
        row.set_margin_end(8)

        number = Gtk.Label(label=str(section.index))
        number.set_width_chars(2)
        number.add_css_class("section-number")
        row.append(number)

        text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=1)
        text.set_hexpand(True)
        name = Gtk.Label(label=section.name, xalign=0)
        name.add_css_class("section-name")
        text.append(name)
        if section.is_empty:
            subtitle_text = "seção vazia"
        else:
            bars = "compasso" if section.measures == 1 else "compassos"
            subtitle_text = (f"{section.measures} {bars} · "
                             f"{section.note_count} notas")
        subtitle = Gtk.Label(label=subtitle_text, xalign=0)
        subtitle.add_css_class("caption")
        subtitle.add_css_class("dim-label")
        text.append(subtitle)
        row.append(text)

        counts = _bar_counts(section)
        bars = MiniBars()
        bars.set_counts(counts)
        row.append(bars)

        play = Gtk.Button.new_from_icon_name("media-playback-start-symbolic")
        play.set_valign(Gtk.Align.CENTER)
        play.add_css_class("flat")
        play.add_css_class("section-play")
        play.set_tooltip_text(f"Tocar {section.name}")
        play.set_sensitive(not section.is_empty)
        play.connect("clicked", lambda _button: on_play(section.index))
        row.append(play)

        self.set_child(row)
        if section.is_empty:
            self.set_sensitive(False)
        if 1 <= section.index <= 9:
            self.set_tooltip_text(f"Tecla {section.index}")


def _bar_counts(section: Section) -> list[int]:
    if section.measures <= 0:
        return [section.note_count]
    bar_ticks = max(1, section.total_ticks // section.measures)
    counts = [0] * section.measures
    for track in section.tracks:
        for note in track.notes:
            index = min(section.measures - 1, max(0, note.tick // bar_ticks))
            counts[index] += 1
    return counts


class PartRow(Gtk.Box):
    def __init__(self, part: int, on_change) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        self.part = part
        self.on_change = on_change
        self._updating = False
        self.add_css_class("part-row")
        self.color = PART_COLORS[part - 1]

        top = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        top.append(ColorDot(self.color, 11))

        text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        text.set_hexpand(True)
        self.name = Gtk.Label(xalign=0)
        self.name.set_markup(
            f"<b>{part}. {_part_name(part)}</b>")
        self.name.add_css_class("part-name")
        text.append(self.name)
        self.tone = Gtk.Label(xalign=0)
        self.tone.add_css_class("caption")
        self.tone.add_css_class("dim-label")
        self.tone.set_ellipsize(Pango.EllipsizeMode.END)
        text.append(self.tone)
        top.append(text)

        self.mute = Gtk.ToggleButton(label="M")
        self.mute.add_css_class("flat")
        self.mute.add_css_class("part-mute")
        self.mute.set_tooltip_text("Mutar esta parte")
        self.mute.set_valign(Gtk.Align.CENTER)
        self.mute.connect("toggled", self._changed)
        top.append(self.mute)

        self.solo = Gtk.ToggleButton(label="S")
        self.solo.add_css_class("flat")
        self.solo.add_css_class("part-solo")
        self.solo.set_tooltip_text("Ouvir somente as partes em solo")
        self.solo.set_valign(Gtk.Align.CENTER)
        self.solo.connect("toggled", self._changed)
        top.append(self.solo)

        self.append(top)

        self.level = Gtk.ProgressBar()
        self.level.set_show_text(False)
        self.level.set_size_request(-1, 3)
        self.level.add_css_class("part-volume")
        self.append(self.level)

        self.notes = Gtk.Label(xalign=0)
        self.notes.add_css_class("caption")
        self.notes.add_css_class("dim-label")
        self.append(self.notes)

    def _changed(self, _button) -> None:
        if self._updating:
            return
        self.on_change(self.part)

    def set_mixer(self, mixer) -> None:
        self._updating = True
        text = detail(mixer.patch, mixer.bank, self.part)
        if mixer.volume == 0:
            text = f"mudo no AC7 · {text}"
        self.tone.set_label(text)
        self.tone.set_tooltip_text(text)
        self.level.set_fraction(max(0, min(127, mixer.volume)) / 127)
        self._updating = False

    def set_section(self, section: Section) -> None:
        count = section.note_count_for_part(self.part)
        if count:
            self.notes.set_label(f"{count} notas nesta seção")
            self.remove_css_class("part-inactive")
        else:
            self.notes.set_label("sem notas nesta seção")
            self.add_css_class("part-inactive")

    def set_state(self, muted: bool, soloed: bool) -> None:
        self._updating = True
        self.mute.set_active(muted)
        self.solo.set_active(soloed)
        self._updating = False


def _part_name(part: int) -> str:
    return {
        1: "Percussão", 2: "Bateria", 3: "Baixo", 4: "Piano",
        5: "Strings", 6: "E. Piano", 7: "Violão", 8: "Pad/Coro",
    }[part]


class PatternView(Gtk.DrawingArea):
    """Linha do tempo com as oito partes e as notas da seção."""

    def __init__(self) -> None:
        super().__init__()
        self.rhythm = None
        self.section: Section | None = None
        self.position = 0.0
        self.set_size_request(-1, 330)
        self.set_vexpand(True)
        self.set_draw_func(self._draw)
        Adw.StyleManager.get_default().connect(
            "notify::dark", lambda *_args: self.queue_draw())

    def set_data(self, rhythm, section: Section | None) -> None:
        self.rhythm = rhythm
        self.section = section
        self.position = 0.0
        self.queue_draw()

    def set_position(self, position: float) -> None:
        position = max(0.0, min(1.0, position))
        if abs(position - self.position) > 0.0004:
            self.position = position
            self.queue_draw()

    def _draw(self, _area, cr, width, height) -> None:
        dark = Adw.StyleManager.get_default().get_dark()
        if dark:
            bg = (1, 1, 1, 0.035)
            grid = (1, 1, 1, 0.10)
            grid_strong = (1, 1, 1, 0.17)
            text = (1, 1, 1, 0.55)
            lane = (1, 1, 1, 0.018)
        else:
            bg = (0, 0, 0, 0.025)
            grid = (0, 0, 0, 0.09)
            grid_strong = (0, 0, 0, 0.16)
            text = (0, 0, 0, 0.55)
            lane = (0, 0, 0, 0.016)

        _rounded(cr, 0.5, 0.5, width - 1, height - 1, 12)
        cr.set_source_rgba(*bg)
        cr.fill()

        section = self.section
        if section is None:
            _center_text(cr, width, height, "Nenhuma seção selecionada",
                         text, 12, bold=True)
            return

        left, right = 16.0, 14.0
        top, bottom = 26.0, 12.0
        plot_width = max(1.0, width - left - right)
        plot_height = max(1.0, height - top - bottom)
        lane_height = plot_height / 8.0
        bar_count = max(1, section.measures)
        bar_width = plot_width / bar_count

        for bar in range(bar_count):
            x = left + bar * bar_width
            cr.set_source_rgba(*grid_strong)
            cr.set_line_width(1.0)
            cr.move_to(round(x) + 0.5, top - 4)
            cr.line_to(round(x) + 0.5, top + plot_height)
            cr.stroke()

        beats = max(1, section.beats)
        for beat in range(bar_count * beats):
            x = left + (beat / (bar_count * beats)) * plot_width
            cr.set_source_rgba(*grid)
            cr.set_line_width(1.0)
            cr.move_to(round(x) + 0.5, top)
            cr.line_to(round(x) + 0.5, top + plot_height)
            cr.stroke()

        for part in range(1, 9):
            y = top + (part - 1) * lane_height
            if part % 2 == 0:
                cr.set_source_rgba(*lane)
                cr.rectangle(left, y, plot_width, lane_height)
                cr.fill()
            cr.set_source_rgba(*grid)
            cr.set_line_width(1.0)
            cr.move_to(left, round(y) + 0.5)
            cr.line_to(left + plot_width, round(y) + 0.5)
            cr.stroke()
            cr.set_source_rgba(*_hex(PART_COLORS[part - 1]), 0.8)
            cr.rectangle(left - 8, y + 4, 3, max(2, lane_height - 8))
            cr.fill()

        # Números dos compassos, sem poluir quando há muitos compassos.
        label_every = 1
        while bar_width * label_every < 30:
            label_every *= 2
        layout = PangoCairo.create_layout(cr)
        layout.set_font_description(Pango.FontDescription("Sans 8.5"))
        for bar in range(0, bar_count, label_every):
            layout.set_text(str(bar + 1), -1)
            text_width, text_height = layout.get_pixel_size()
            x = left + bar * bar_width + 2
            cr.set_source_rgba(*text)
            cr.move_to(x, 4 + (16 - text_height) / 2)
            PangoCairo.show_layout(cr, layout)

        note_height = max(2.2, min(6.0, lane_height * 0.18))
        total_ticks = max(1, section.total_ticks)
        for part in range(1, 9):
            notes = section.notes_for_part(part)
            if not notes:
                continue
            pitches = [note.pitch for note in notes]
            low, high = min(pitches), max(pitches)
            span = max(12, high - low)
            low -= (span - (high - low)) / 2
            high = low + span
            y0 = top + (part - 1) * lane_height
            inner_top = y0 + lane_height * 0.18
            inner_height = max(1.0, lane_height * 0.64)
            color = _hex(PART_COLORS[part - 1])
            for note in notes:
                start = max(0, min(total_ticks, note.tick))
                end = max(start + 1, min(total_ticks,
                                        note.tick + max(1, note.length)))
                x = left + (start / total_ticks) * plot_width
                note_width = max(2.2, ((end - start) / total_ticks)
                                 * plot_width - 0.4)
                fraction = 1.0 - (note.pitch - low) / max(1.0, high - low)
                y = inner_top + fraction * (inner_height - note_height)
                alpha = 0.35 + 0.65 * (note.velocity / 127.0)
                cr.set_source_rgba(*color, alpha)
                _rounded(cr, x, y, note_width, note_height, 1.4)
                cr.fill()

        if section.is_empty:
            _center_text(cr, width, height, "Seção vazia", text, 12,
                         bold=True)

        x = left + self.position * plot_width
        cr.set_source_rgba(*_hex(ACCENT), 0.20)
        cr.set_line_width(6)
        cr.move_to(x, top - 4)
        cr.line_to(x, top + plot_height)
        cr.stroke()
        cr.set_source_rgba(*_hex(ACCENT), 0.95)
        cr.set_line_width(1.8)
        cr.move_to(x, top - 4)
        cr.line_to(x, top + plot_height)
        cr.stroke()


def _center_text(cr: cairo.Context, width: float, height: float, text: str,
                 color, size: float, bold: bool = False) -> None:
    layout = PangoCairo.create_layout(cr)
    description = Pango.FontDescription(f"Sans {size}")
    if bold:
        description.set_weight(Pango.Weight.BOLD)
    layout.set_font_description(description)
    layout.set_text(text, -1)
    text_width, text_height = layout.get_pixel_size()
    cr.set_source_rgba(*color)
    cr.move_to((width - text_width) / 2, (height - text_height) / 2)
    PangoCairo.show_layout(cr, layout)


def section_label(index: int) -> str:
    return SECTION_NAMES.get(index, f"Elemento {index}")
