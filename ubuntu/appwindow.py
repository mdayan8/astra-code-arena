"""ASTRA Code Arena: the app's own native window (GTK 3 + WebKitGTK, both part of Ubuntu). No Chrome, no browser.
Built to feel instant:
  * the window appears at once with a loading screen; the page engine loads behind it
  * closing the window only hides it, so opening it again is immediate (one instance, kept in memory)
  * `--hidden` (used at login) preloads it without showing anything"""
import os
import sys
from pathlib import Path

os.environ.setdefault("WEBKIT_DISABLE_DMABUF_RENDERER", "1")  # avoids a blank window on some graphics drivers

import gi  # noqa: E402

gi.require_version("Gtk", "3.0")
try:
    gi.require_version("WebKit2", "4.1")   # only registers the version (fast); raises if the component is missing
except ValueError:
    gi.require_version("WebKit2", "4.0")
from gi.repository import GdkPixbuf, Gio, GLib, Gtk  # noqa: E402  (WebKit2 itself is imported later: it is the slow part)

URL = next((a for a in sys.argv[1:] if a.startswith("http")), "http://127.0.0.1:8765/")
HIDDEN = "--hidden" in sys.argv
ICONS = ("/usr/share/icons/hicolor/scalable/apps/astra-arena.svg", str(Path(__file__).with_name("astra-arena.svg")))
ICON = next((i for i in ICONS if os.path.exists(i)), None)


class App:
    def __init__(self):
        self.win = self.stack = self.view = None

    def activate(self, app):
        if self.win:                       # already running (maybe hidden): just show it
            self.win.show_all()
            self.win.present()
            return
        app.hold()                         # stay in memory after the window is closed
        self.win = win = Gtk.ApplicationWindow(application=app, title="ASTRA Code Arena")
        win.set_default_size(1200, 880)
        if ICON:
            try:
                win.set_icon_from_file(ICON)
            except GLib.Error:
                pass
        def on_close(w, _event):           # close = hide, so the next open is instant
            w.hide()
            return True                    # True tells GTK not to destroy the window
        win.connect("delete-event", on_close)
        self.stack = Gtk.Stack(transition_type=Gtk.StackTransitionType.CROSSFADE, transition_duration=150)
        splash = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14, halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER)
        if ICON:
            try:
                splash.pack_start(Gtk.Image.new_from_pixbuf(GdkPixbuf.Pixbuf.new_from_file_at_size(ICON, 84, 84)), False, False, 0)
            except GLib.Error:
                pass
        spin = Gtk.Spinner()
        spin.start()
        splash.pack_start(spin, False, False, 0)
        splash.pack_start(Gtk.Label(label="Opening ASTRA Code Arena…"), False, False, 0)
        self.stack.add_named(splash, "splash")
        win.add(self.stack)
        if not HIDDEN:
            win.show_all()                 # visible immediately; the page arrives a moment later
        GLib.idle_add(self.load_web, app)

    def show_web(self):
        if self.view and self.stack.get_visible_child_name() != "web":
            self.stack.set_visible_child_name("web")
        return False

    def load_web(self, app):
        from gi.repository import WebKit2
        ctx = WebKit2.WebContext.get_default()
        data = Path(GLib.get_user_data_dir()) / "astra-arena-app"
        data.mkdir(parents=True, exist_ok=True)
        ctx.get_cookie_manager().set_persistent_storage(str(data / "cookies.sqlite"), WebKit2.CookiePersistentStorage.SQLITE)

        def on_download(_ctx, download):   # move packages and backups go to the Downloads folder
            def decide(d, name):
                folder = GLib.get_user_special_dir(GLib.UserDirectory.DIRECTORY_DOWNLOAD) or os.path.expanduser("~")
                d.set_destination(GLib.filename_to_uri(os.path.join(folder, name), None))
                return True

            def finished(d):
                note = Gio.Notification.new("ASTRA Code Arena")
                note.set_body("Saved to your Downloads folder: " + os.path.basename(d.get_destination() or ""))
                app.send_notification(None, note)
            download.connect("decide-destination", decide)
            download.connect("finished", finished)
        ctx.connect("download-started", on_download)

        self.view = view = WebKit2.WebView.new_with_context(ctx)

        def on_decide(_view, decision, kind):   # student / organiser links open in the normal browser; the app stays here
            if kind == WebKit2.PolicyDecisionType.NEW_WINDOW_ACTION:
                Gtk.show_uri_on_window(None, decision.get_navigation_action().get_request().get_uri(), 0)
                decision.ignore()
                return True
            return False
        view.connect("decide-policy", on_decide)

        def on_fail(v, _event, _uri, _err):   # the service is still starting, or was stopped: say so and retry by itself
            v.load_html("<body style='font:16px system-ui;display:grid;place-items:center;height:90vh;color:#444;background:#f6f6fa'><div style='text-align:center'>"
                        "<h2>ASTRA Code Arena is starting…</h2><p>This window reconnects by itself in a few seconds.</p>"
                        "<p style='color:#888'>If it does not, open a terminal and run: <b>astra-arena restart</b></p></div>"
                        f"<script>setTimeout(()=>location.replace('{URL}'),4000)</script></body>", URL)
            self.show_web()
            return True
        view.connect("load-failed", on_fail)
        view.connect("load-changed", lambda _v, event: event == WebKit2.LoadEvent.FINISHED and self.show_web())
        self.stack.add_named(view, "web")
        view.show()
        view.load_uri(URL)
        GLib.timeout_add_seconds(6, self.show_web)   # never sit on the loading screen
        return False


def main():
    GLib.set_prgname("astra-arena")            # matches StartupWMClass in the .desktop file: one icon in the dock
    GLib.set_application_name("ASTRA Code Arena")
    app = Gtk.Application(application_id="space.astraclub.arena", flags=Gio.ApplicationFlags.FLAGS_NONE)
    app.connect("activate", App().activate)
    return app.run([sys.argv[0]])


if __name__ == "__main__":
    sys.exit(main())
