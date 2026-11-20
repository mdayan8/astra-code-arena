"""A small native window for ASTRA Code Arena (GTK + WebKit), used when no Chrome-family browser is installed.
It is a real desktop window with the app's icon and title: no tabs, no address bar."""
import os
import sys
from pathlib import Path

import gi

gi.require_version("Gtk", "3.0")
try:
    gi.require_version("WebKit2", "4.1")
except ValueError:
    gi.require_version("WebKit2", "4.0")
from gi.repository import GLib, Gtk, WebKit2  # noqa: E402

URL = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8765/"
GLib.set_prgname("astra-arena")          # matches StartupWMClass in the .desktop file: one icon in the dock
GLib.set_application_name("ASTRA Code Arena")

ctx = WebKit2.WebContext.get_default()
data = Path(GLib.get_user_data_dir()) / "astra-arena-app"   # keeps the sign-in cookie between launches
cookies = ctx.get_cookie_manager()
data.mkdir(parents=True, exist_ok=True)
cookies.set_persistent_storage(str(data / "cookies.sqlite"), WebKit2.CookiePersistentStorage.SQLITE)


def on_download(_ctx, download):
    """Move packages and backups go to the Downloads folder."""
    def decide(d, name):
        folder = GLib.get_user_special_dir(GLib.UserDirectory.DIRECTORY_DOWNLOAD) or os.path.expanduser("~")
        d.set_destination("file://" + os.path.join(folder, name))
        return True
    download.connect("decide-destination", decide)


ctx.connect("download-started", on_download)
view = WebKit2.WebView.new_with_context(ctx)
view.get_settings().set_enable_developer_extras(False)


def on_decide(_view, decision, kind):
    """Links to the student or organiser site open in the normal browser; the control panel stays in this window."""
    if kind == WebKit2.PolicyDecisionType.NEW_WINDOW_ACTION:
        Gtk.show_uri_on_window(None, decision.get_navigation_action().get_request().get_uri(), 0)
        decision.ignore()
        return True
    return False


view.connect("decide-policy", on_decide)
win = Gtk.Window(title="ASTRA Code Arena")
win.set_default_size(1200, 880)
win.set_wmclass("astra-arena", "astra-arena")
for icon in ("/usr/share/icons/hicolor/scalable/apps/astra-arena.svg", str(Path(__file__).with_name("astra-arena.svg"))):
    if os.path.exists(icon):
        win.set_icon_from_file(icon)
        break
win.add(view)
win.connect("destroy", Gtk.main_quit)
view.load_uri(URL)
win.show_all()
Gtk.main()
