"""Seletor de instrumento do Tonetune."""
from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Pango", "1.0")

from gi.repository import Adw, GLib, Gtk, Pango  # noqa: E402

from ac7 import PART_NAMES  # noqa: E402
from tones import (  # noqa: E402
    CATEGORY_LABELS, category_label, drum_tones, melodic_tones, tone_for,
)


class InstrumentDialog(Adw.Window):
    def __init__(self, parent, part: int, patch: int, bank: int,
                 current_override, on_apply, on_reset) -> None:
        super().__init__(transient_for=parent, modal=True)
        self.set_destroy_with_parent(True)
        self.set_default_size(620, 700)
        self.part = part
        self.on_apply = on_apply
        self.on_reset = on_reset
        self.current_override = current_override
        self.current_tone = (current_override
                             or tone_for(patch, bank, part))
        self.selected_tone = self.current_tone
        self._category: int | None = None
        self._suppress = True

        self.tones = drum_tones() if part in (1, 2) else melodic_tones()
        title = Adw.WindowTitle(
            title=f"Instrumento · {PART_NAMES.get(part, f'Parte {part}')}",
            subtitle="Troca só na prévia; o arquivo AC7 não é alterado")
        header = Adw.HeaderBar()
        header.set_title_widget(title)

        cancel = Gtk.Button(label="Cancelar")
        cancel.connect("clicked", lambda _button: self.close())
        header.pack_start(cancel)

        if current_override is not None:
            reset = Gtk.Button(label="Restaurar do AC7")
            reset.add_css_class("flat")
            reset.connect("clicked", lambda _button: self._reset())
            header.pack_start(reset)

        self.apply_button = Gtk.Button(label="Usar tom")
        self.apply_button.add_css_class("suggested-action")
        self.apply_button.connect("clicked", lambda _button: self._apply())
        header.pack_end(self.apply_button)

        toolbar = Adw.ToolbarView()
        toolbar.add_top_bar(header)

        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        content.set_margin_top(12)
        content.set_margin_bottom(12)
        content.set_margin_start(12)
        content.set_margin_end(12)

        hint = Gtk.Label(
            label=("Escolha um dos kits de bateria para esta parte."
                   if part in (1, 2)
                   else "Escolha qualquer tom do CT-X800 para ouvir esta "
                        "parte com outro instrumento."),
            xalign=0)
        hint.add_css_class("caption")
        hint.add_css_class("dim-label")
        hint.set_wrap(True)
        content.append(hint)

        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.search = Gtk.SearchEntry()
        self.search.set_placeholder_text("Nome ou número (ex.: 184)")
        self.search.set_hexpand(True)
        self.search.connect("search-changed", lambda _entry: self._filter())
        row.append(self.search)

        if part not in (1, 2):
            categories = sorted(number for number in CATEGORY_LABELS
                                if number != 14)
            self._categories = categories
            self.category_dropdown = Gtk.DropDown.new_from_strings(
                ["Todas"] + [category_label(number)
                             for number in categories])
            self.category_dropdown.set_selected(0)
            self.category_dropdown.connect(
                "notify::selected", self._category_changed)
            row.append(self.category_dropdown)
        else:
            self._categories = []
            self.category_dropdown = None
        content.append(row)

        self.listbox = Gtk.ListBox()
        self.listbox.set_selection_mode(Gtk.SelectionMode.SINGLE)
        self.listbox.set_activate_on_single_click(False)
        self.listbox.set_filter_func(self._filter_row)
        self.listbox.connect("row-selected", self._row_selected)
        self.listbox.connect("row-activated", lambda *_args: self._apply())
        self.rows: list[Gtk.ListBoxRow] = []
        selected_row = None
        for tone in self.tones:
            item = Gtk.ListBoxRow()
            item.tone = tone  # type: ignore[attr-defined]
            box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
            box.set_margin_top(4)
            box.set_margin_bottom(4)
            box.set_margin_start(8)
            box.set_margin_end(8)
            number = Gtk.Label(label=f"{tone.number:03d}")
            number.set_width_chars(4)
            number.add_css_class("monospace")
            number.add_css_class("dim-label")
            box.append(number)
            name = Gtk.Label(label=tone.name, xalign=0)
            name.set_hexpand(True)
            name.set_ellipsize(Pango.EllipsizeMode.END)
            box.append(name)
            category = Gtk.Label(label=category_label(tone.category), xalign=1)
            category.add_css_class("caption")
            category.add_css_class("dim-label")
            box.append(category)
            item.set_child(box)
            self.listbox.append(item)
            self.rows.append(item)
            if tone == self.current_tone:
                selected_row = item

        scroller = Gtk.ScrolledWindow()
        scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroller.set_vexpand(True)
        scroller.set_child(self.listbox)
        scroller.add_css_class("card")
        content.append(scroller)
        self.scroller = scroller

        self.status = Gtk.Label(
            label="Clique em um tom e depois em Usar tom.",
            xalign=0)
        self.status.add_css_class("caption")
        self.status.add_css_class("dim-label")
        self.status.set_ellipsize(Pango.EllipsizeMode.END)
        content.append(self.status)

        toolbar.set_content(content)
        self.set_content(toolbar)
        self.connect("close-request", self._on_close_request)

        if selected_row is not None:
            self.listbox.select_row(selected_row)
            GLib.idle_add(self._scroll_to_selected)
        else:
            self._suppress = False
        self._update_apply()

    # ------------------------------------------------------------- filtros
    def _category_changed(self, dropdown, _param) -> None:
        index = dropdown.get_selected()
        self._category = (None if index <= 0
                          else self._categories[index - 1])
        self._filter()

    def _filter(self) -> None:
        self.listbox.invalidate_filter()

    def _filter_row(self, row: Gtk.ListBoxRow) -> bool:
        tone = getattr(row, "tone", None)
        if tone is None:
            return False
        if self._category is not None and tone.category != self._category:
            return False
        query = self.search.get_text().strip().casefold()
        if query:
            text = f"{tone.number:03d} {tone.number} {tone.name}".casefold()
            if query not in text:
                return False
        return True

    def _row_selected(self, _listbox, row) -> None:
        if row is None:
            self.selected_tone = None
        else:
            self.selected_tone = getattr(row, "tone", None)
        if self.selected_tone is not None:
            self.status.set_label(
                f"{self.selected_tone.number:03d} · "
                f"{self.selected_tone.name}")
        self._update_apply()

    def _update_apply(self) -> None:
        self.apply_button.set_sensitive(self.selected_tone is not None)

    def _scroll_to_selected(self) -> bool:
        row = self.listbox.get_selected_row()
        self._suppress = False
        if row is not None:
            allocation = row.get_allocation()
            adjustment = self.scroller.get_vadjustment()
            adjustment.set_value(max(0, allocation.y - 80))
        return GLib.SOURCE_REMOVE

    # ------------------------------------------------------------- ações
    def _apply(self) -> None:
        if self.selected_tone is None:
            return
        self.on_apply(self.selected_tone)
        self.close()

    def _reset(self) -> None:
        self.on_reset()
        self.close()

    def _on_close_request(self, _window) -> bool:
        self._suppress = True
        return False
