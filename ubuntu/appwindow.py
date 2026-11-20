"""ASTRA Code Arena: the app's own native window (GTK 3 + WebKitGTK, both part of Ubuntu). No Chrome, no browser.
One window only: starting it again brings the existing window to the front. Sign-in is remembered between launches."""
import os
import sys
from pathlib import Path

os.environ.setdefault("WEBKIT_DISABLE_DMABUF_RENDERER", "1")  # avoids a blank window on some graphics drivers

import gi  # noqa: E402

gi.require_version("Gtk", "3.0")
try:
    gi.require_version("WebKit2", "4.1")
except ValueError:
    gi.require_version("WebKit2", "4.0")
from gi.repository import Gio, GLib, Gtk, WebKit2  # noqa: E402

URL = next((a for a in sys.argv[1:] if a.startswith("http")), "http://127.0.0.1:8765/")
ICONS = ("/usr/share/icons/hicolor/scalable/apps/astra-arena.svg", str(Path(__file__).with_name("astra-arena.svg")))


def build(app):
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

    view = WebKit2.WebView.new_with_context(ctx)

    def on_decide(_view, decision, kind):   # student / organiser links open in the normal browser; the app stays in this window
        if kind == WebKit2.PolicyDecisionType.NEW_WINDOW_ACTION:
            Gtk.show_uri_on_window(None, decision.get_navigation_action().get_request().get_uri(), 0)
            decision.ignore()
            return True
        return False
    view.connect("decide-policy", on_decide)

    def on_fail(v, _event, uri, _err):   # the service is still starting, or was stopped: say so and retry by itself
        v.load_html("<body style='font:16px system-ui;display:grid;place-items:center;height:90vh;color:#444;background:#f6f6fa'><div style='text-align:center'>"
                    "<h2>ASTRA Code Arena is starting…</h2><p>This window reconnects by itself in a few seconds.</p>"
                    "<p style='color:#888'>If it does not, open a terminal and run: <b>astra-arena restart</b></p></div>"
                    f"<script>setTimeout(()=>location.replace('{URL}'),4000)</script></body>", URL)
        return True
    view.connect("load-failed", on_fail)

    win = Gtk.ApplicationWindow(application=app, title="ASTRA Code Arena")
    win.set_default_size(1200, 880)
    for icon in ICONS:
        if os.path.exists(icon):
            try:
                win.set_icon_from_file(icon)
            except GLib.Error:
                pass
            break
    win.add(view)
    view.load_uri(URL)
    win.show_all()
    return win


def main():
    GLib.set_prgname("astra-arena")            # matches StartupWMClass in the .desktop file: one icon in the dock
    GLib.set_application_name("ASTRA Code Arena")
    app = Gtk.Application(application_id="space.astraclub.arena", flags=Gio.ApplicationFlags.FLAGS_NONE)
    app.connect("activate", lambda a: a.get_active_window().present() if a.get_active_window() else build(a))
    return app.run([sys.argv[0]])


if __name__ == "__main__":
    sys.exit(main())
