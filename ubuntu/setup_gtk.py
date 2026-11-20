"""ASTRA Code Arena setup: a native Ubuntu window (GTK 3, already part of every Ubuntu desktop). No browser involved.
One button installs, updates or repairs everything and shows each step on a progress bar.
The install logic is shared with setup_ui.py (the web fallback): this file is only the window."""
import os
import shutil
import sys
import threading
import zipfile
from pathlib import Path

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import Gdk, GdkPixbuf, Gio, GLib, Gtk, Pango  # noqa: E402

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import setup_ui as core  # noqa: E402  (machine(), run_install(), job, STEPS, open_window(), PANEL, UPLOAD)

CSS = b"""
window { background: #f6f6fa; }
.title { font-size: 21px; font-weight: 800; }
.muted { color: #6b6f7e; }
.tile { background: #ffffff; border: 1px solid #e6e6ef; border-radius: 12px; padding: 10px 16px; }
.tile .big { font-size: 18px; font-weight: 800; }
.cap { background: #efeaff; color: #3b2a8f; border-radius: 12px; padding: 12px 14px; }
.ok { background: #e3f7ec; color: #0b6b37; border-radius: 12px; padding: 14px; }
.bad { background: #fde8ea; color: #8f1d27; border-radius: 12px; padding: 14px; }
.step { font-size: 15px; font-weight: 700; }
.secret { font-family: monospace; font-size: 17px; font-weight: 700; }
button.go { padding: 10px 22px; font-weight: 700; }
progressbar trough, progressbar progress { min-height: 12px; border-radius: 8px; }
textview, textview text { font-family: monospace; font-size: 11px; }
"""


def esc(s):
    return GLib.markup_escape_text(str(s))


def label(text="", css=None, wrap=True, markup=False, selectable=False):
    l = Gtk.Label(xalign=0)
    l.set_markup(text) if markup else l.set_text(text)
    l.set_line_wrap(wrap)
    l.set_selectable(selectable)
    if css:
        l.get_style_context().add_class(css)
    return l


class Setup(Gtk.ApplicationWindow):
    def __init__(self, app):
        super().__init__(application=app, title="ASTRA Code Arena · Setup")
        self.set_default_size(680, 640)
        self.set_position(Gtk.WindowPosition.CENTER)
        self.m = core.machine()
        self.last_state = None
        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14)
        for side in ("top", "bottom", "start", "end"):
            getattr(root, f"set_margin_{side}")(26)
        self.add(root)

        head = Gtk.Box(spacing=14)
        logo = HERE.parent / "static" / "img" / "arena-logo.svg"
        try:
            head.pack_start(Gtk.Image.new_from_pixbuf(GdkPixbuf.Pixbuf.new_from_file_at_size(str(logo), 54, 54)), False, False, 0)
            self.set_icon_from_file(str(HERE / "astra-arena.svg"))
        except GLib.Error:
            pass
        names = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        names.pack_start(label("ASTRA Code Arena", "title"), False, False, 0)
        names.pack_start(label(f"Setup for this computer · {self.m['os']}", "muted"), False, False, 0)
        head.pack_start(names, True, True, 0)
        root.pack_start(head, False, False, 0)

        tiles = Gtk.Box(spacing=10, homogeneous=True)
        for big, small in ((self.m["cores"], "CPU cores"), (f"{self.m['ram_gb']} GB", "RAM"), (f"{self.m['free_gb']} GB", "free disk"), (self.m["students"], "students at once")):
            box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
            box.get_style_context().add_class("tile")
            box.pack_start(label(str(big), "big", wrap=False), False, False, 0)
            box.pack_start(label(small, "muted", wrap=False), False, False, 0)
            tiles.pack_start(box, True, True, 0)
        root.pack_start(tiles, False, False, 0)

        self.stack = Gtk.Stack(transition_type=Gtk.StackTransitionType.CROSSFADE)
        root.pack_start(self.stack, True, True, 0)
        self.stack.add_named(self.page_start(), "start")
        self.stack.add_named(self.page_progress(), "progress")
        self.stack.add_named(self.page_done(), "done")
        GLib.timeout_add(400, self.tick)
        self.show_all()

    # ---------------- pages ----------------
    def page_start(self):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        m = self.m
        box.pack_start(label(f"With {m['ram_gb']} GB RAM and {m['cores']} cores, this PC handles <b>about {m['students']} students at once</b>, "
                             f"or <b>about {m['students_ai']}</b> with the AI model on.", "cap", markup=True), False, False, 0)
        low_disk = m["free_gb"] < 20
        if low_disk:
            box.pack_start(label(f"Installation needs 20 GB of free disk; this PC has {m['free_gb']} GB. Free some space, then open this again.", "bad"), False, False, 0)
        text = ("Already installed. Updating keeps every student, question and result." if m["installed"] else
                "One click installs everything it needs: compilers (C, C++, Java, Python, JavaScript), the platform, the background service and the app. "
                "It needs internet this one time and takes about 5 to 15 minutes. Ubuntu will ask for your password.")
        box.pack_start(label(text, "muted"), False, False, 0)
        self.ai = Gtk.CheckButton(label="Also install the AI model (Prompt challenges, AI Studio): about 2 GB download, uses about 1.5 GB RAM")
        self.ai.set_active(m["ram_gb"] >= 7)
        box.pack_start(self.ai, False, False, 0)
        row = Gtk.Box(spacing=10)
        row.pack_start(label("Moving from another computer? Choose its move package:", wrap=False), False, False, 0)
        self.pkg = Gtk.FileChooserButton(title="Choose the move package (astra-move-….zip)", action=Gtk.FileChooserAction.OPEN)
        flt = Gtk.FileFilter()
        flt.set_name("Move packages (*.zip)")
        flt.add_pattern("*.zip")
        self.pkg.add_filter(flt)
        row.pack_start(self.pkg, True, True, 0)
        box.pack_start(row, False, False, 0)
        self.start_msg = label("", "bad")
        self.start_msg.set_no_show_all(True)
        box.pack_start(self.start_msg, False, False, 0)
        buttons = Gtk.Box(spacing=10)
        self.go = Gtk.Button(label="Install ASTRA Code Arena" if not m["installed"] else "Update to this version" if not m["up_to_date"] else "Repair / reinstall")
        self.go.get_style_context().add_class("suggested-action")
        self.go.get_style_context().add_class("go")
        self.go.set_sensitive(not low_disk)
        self.go.connect("clicked", self.on_install)
        buttons.pack_start(self.go, False, False, 0)
        if m["running"]:
            b = Gtk.Button(label="Open the app")
            b.connect("clicked", self.open_app)
            buttons.pack_start(b, False, False, 0)
        box.pack_end(buttons, False, False, 0)
        return box

    def page_progress(self):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        self.step = label("Starting...", "step")
        box.pack_start(self.step, False, False, 0)
        self.bar = Gtk.ProgressBar(show_text=True)
        box.pack_start(self.bar, False, False, 0)
        self.line = label("", "muted", wrap=False)
        self.line.set_ellipsize(Pango.EllipsizeMode.END)
        box.pack_start(self.line, False, False, 0)
        self.steps = label("", markup=True)
        box.pack_start(self.steps, False, False, 0)
        self.fail = label("", "bad")
        self.fail.set_no_show_all(True)
        box.pack_start(self.fail, False, False, 0)
        self.retry = Gtk.Button(label="Try again")
        self.retry.set_halign(Gtk.Align.START)
        self.retry.set_no_show_all(True)
        self.retry.connect("clicked", lambda *_: (self.fail.hide(), self.retry.hide(), self.stack.set_visible_child_name("start")))
        box.pack_start(self.retry, False, False, 0)
        exp = Gtk.Expander(label="Details")
        sw = Gtk.ScrolledWindow()
        sw.set_min_content_height(170)
        self.log = Gtk.TextView(editable=False, cursor_visible=False, wrap_mode=Gtk.WrapMode.WORD_CHAR)
        sw.add(self.log)
        exp.add(sw)
        box.pack_start(exp, True, True, 0)
        return box

    def page_done(self):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        self.done_msg = label("", "ok", markup=True, selectable=True)
        box.pack_start(self.done_msg, False, False, 0)
        self.notes = label("", "cap", markup=True)
        self.notes.set_no_show_all(True)
        box.pack_start(self.notes, False, False, 0)
        box.pack_start(label("The app asks you to create its own password the first time it opens. Next time, open ASTRA Code Arena from the app menu or the desktop icon.", "muted"),
                       False, False, 0)
        b = Gtk.Button(label="Open the app")
        b.get_style_context().add_class("suggested-action")
        b.get_style_context().add_class("go")
        b.set_halign(Gtk.Align.START)
        b.connect("clicked", self.open_app)
        box.pack_end(b, False, False, 0)
        return box

    # ---------------- actions ----------------
    def open_app(self, *_):
        core.open_window(core.PANEL)
        self.get_application().quit()

    def on_install(self, *_):
        self.start_msg.hide()
        imp, path = False, self.pkg.get_filename()
        if path:
            try:
                if not (zipfile.is_zipfile(path) and "manifest.json" in zipfile.ZipFile(path).namelist()):
                    raise ValueError
                fd = os.open(core.UPLOAD, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
                with open(fd, "wb") as out, open(path, "rb") as src:
                    shutil.copyfileobj(src, out)
                imp = True
            except (OSError, ValueError, zipfile.BadZipFile):
                self.start_msg.set_text("That file is not an ASTRA Arena move package (astra-move-….zip). Choose another, or clear it.")
                self.start_msg.show()
                return
        self.stack.set_visible_child_name("progress")
        threading.Thread(target=core.run_install, args=(self.ai.get_active(), imp), daemon=True).start()

    def tick(self):
        j = core.job
        if j["state"] == "idle":
            return True
        total = max(1, j["total"])
        frac = 1.0 if j["state"] == "done" else min(0.98, j["done_steps"] / total)
        self.bar.set_fraction(frac)
        self.bar.set_text(f"{int(frac * 100)}%")
        self.step.set_text(j["step"] or "Starting...")
        self.line.set_text(j["log"][-1] if j["log"] else "")
        names = [s for s in core.STEPS if (j["ai"] or "AI" not in s) and (j["imp"] or "old computer" not in s)]
        cur = next((i for i, s in enumerate(names) if j["step"].startswith(" ".join(s.split()[:2]))), -1)
        rows = []
        for i, s in enumerate(names):
            if j["state"] == "done" or i < cur:
                rows.append(f'<span foreground="#12a150">✓  {esc(s)}</span>')
            elif i == cur:
                rows.append(f"<b>▶  {esc(s)}</b>")
            else:
                rows.append(f'<span foreground="#9a9db0">○  {esc(s)}</span>')
        self.steps.set_markup("\n".join(rows))
        buf = self.log.get_buffer()
        text = "\n".join(j["log"])
        if buf.get_char_count() != len(text):
            buf.set_text(text)
            self.log.scroll_to_mark(buf.create_mark(None, buf.get_end_iter(), False), 0.0, False, 0, 0)
        if j["state"] != self.last_state:
            self.last_state = j["state"]
            if j["state"] == "failed":
                self.fail.set_text("Setup stopped. " + (j["error"] or "See Details below."))
                self.fail.show()
                self.retry.show()
            if j["state"] == "done":
                msg = "<b>Installed and running.</b> It starts by itself after every reboot."
                if j["imp"]:
                    msg += "\nAll data from the old computer is here. Sign in with the same organiser accounts as before."
                if j["password"]:
                    msg += f'\n\nOrganiser site login: user <b>admin</b>, password <span font_family="monospace" weight="bold" size="large">{esc(j["password"])}</span>\nWrite it down: you must change it at first sign-in.'
                if j["students"]:
                    msg += f"\nStudents open: <b>{esc(j['students'])}</b>"
                self.done_msg.set_markup(msg)
                if j.get("notes"):
                    self.notes.set_markup("<b>The platform works. These parts can be finished later by running setup again:</b>\n" + "\n".join("• " + esc(n) for n in j["notes"]))
                    self.notes.show()
                self.stack.set_visible_child_name("done")
        return True


def main():
    # Installed and answering: this is just "open the app". The setup window is only for a first install or a repair.
    if core.installed_version() and core.panel_up() and "--setup" not in sys.argv:
        core.open_window(core.PANEL)
        return 0
    GLib.set_prgname("astra-arena-setup")
    app = Gtk.Application(application_id="space.astraclub.arena.setup", flags=Gio.ApplicationFlags.FLAGS_NONE)

    def activate(a):
        if a.get_active_window():
            return a.get_active_window().present()
        try:  # styling is a nicety: an older GTK that rejects a rule must not stop the setup
            prov = Gtk.CssProvider()
            prov.load_from_data(CSS)
            Gtk.StyleContext.add_provider_for_screen(Gdk.Screen.get_default(), prov, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        except GLib.Error:
            pass
        Setup(a)
    app.connect("activate", activate)
    return app.run([sys.argv[0]])


if __name__ == "__main__":
    sys.exit(main())
