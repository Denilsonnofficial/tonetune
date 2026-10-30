#!/usr/bin/env python3
"""Tonetune — reprodutor de ritmos AC7 do Casio CT-X800."""
from __future__ import annotations

import sys
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, Gdk, Gio, GLib, Gtk  # noqa: E402

from constants import APP_ID, APP_NAME, DESCRIPTION, GITHUB_URL, VERSION  # noqa: E402
from window import TonetuneWindow  # noqa: E402


class TonetuneApplication(Adw.Application):
    def __init__(self) -> None:
        super().__init__(
            application_id=APP_ID,
            flags=Gio.ApplicationFlags.HANDLES_OPEN,
        )
        self.window: TonetuneWindow | None = None

    # ------------------------------------------------------------- startup
    def do_startup(self) -> None:
        Adw.Application.do_startup(self)
        GLib.set_prgname(APP_ID)
        GLib.set_application_name(APP_NAME)

        display = Gdk.Display.get_default()
        if display is not None:
            theme = Gtk.IconTheme.get_for_display(display)
            theme.add_search_path(str(Path(__file__).with_name("data")
                                      / "icons"))
            try:
                Gtk.Window.set_default_icon_name(APP_ID)
            except Exception:
                pass
            try:
                provider = Gtk.CssProvider()
                provider.load_from_path(
                    str(Path(__file__).with_name("styles.css")))
                Gtk.StyleContext.add_provider_for_display(
                    display, provider,
                    Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
            except GLib.Error:  # pragma: no cover - CSS opcional
                pass

        for action, accels in {
            "win.open": ["<Primary>o"],
            "win.reload": ["F5"],
            "app.about": [],
        }.items():
            self.set_accels_for_action(action, accels)

        about = Gio.SimpleAction.new("about", None)
        about.connect("activate", lambda _action, _param: self._show_about())
        self.add_action(about)

        quit_action = Gio.SimpleAction.new("quit", None)
        quit_action.connect(
            "activate", lambda _action, _param: self.quit())
        self.add_action(quit_action)

    def do_activate(self) -> None:
        if self.window is None:
            self.window = TonetuneWindow(self)
        self.window.present()

    def do_open(self, files, _n_files: int, _hint: str) -> None:
        if not files:
            return
        path = files[0].get_path()
        if path is None:
            return
        if self.window is None:
            self.window = TonetuneWindow(self)
        self.window.load_path(path)
        self.window.present()

    def do_shutdown(self) -> None:
        if self.window is not None:
            self.window.engine.on_finished = None
            self.window.engine.on_error = None
            self.window.engine.close()
            self.window = None
        Adw.Application.do_shutdown(self)

    def _show_about(self) -> None:
        dialog = Adw.AboutDialog()
        dialog.set_application_name(APP_NAME)
        dialog.set_application_icon(APP_ID)
        dialog.set_developer_name("Denilson")
        dialog.set_version(VERSION)
        dialog.set_comments(DESCRIPTION)
        dialog.set_website(GITHUB_URL)
        dialog.set_issue_url(f"{GITHUB_URL}/issues")
        dialog.set_license_type(Gtk.License.GPL_3_0)
        dialog.set_developers(["Denilson"])
        dialog.set_copyright("© 2026 Denilson")
        dialog.present(self.window)


def main(argv: list[str] | None = None) -> int:
    app = TonetuneApplication()
    return app.run(argv if argv is not None else sys.argv)


if __name__ == "__main__":
    raise SystemExit(main())
