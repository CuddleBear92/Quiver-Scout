"""The maintainer window: what's missing from Quiver, what's on other sites, what's waiting, and the blacklist."""

from __future__ import annotations

import os
import queue
import re
import subprocess
import sys
import threading
import tkinter as tk
import webbrowser
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from . import engine, theme, updater
from .decisions import BLACKLIST, BLACKLIST_REASONS, WAIT_REASONS, WAITING, reason_label
from .engine import Candidate, Comparison
from .sources import EXTERNAL, Item
from .linkcheck import TAKEN_DOWN as TAKEN_DOWN_STATES
from .store import Store

# Columns: id, heading, width, kind ("date" sorts with empty values last, whichever way).
COL = {
    "title": ("Project", 230, ""),
    "game": ("Game", 220, ""),
    "platform": ("Platform", 80, ""),
    "method": ("Method", 110, ""),
    "sources": ("Listed on", 180, ""),
    "host": ("Site", 170, ""),
    "released": ("Latest release", 125, "date"),
    "version": ("Version", 80, ""),
    "pushed": ("Last push", 105, "date"),
    "runs_on": ("Runs on", 150, ""),
    "added": ("Listed since", 115, "date"),
    "first_seen": ("First seen", 115, "date"),
    "same_app": ("App in Quiver", 120, ""),
    "same_game": ("Game in Quiver", 120, ""),
    "reason": ("Reason", 220, ""),
    "note": ("Note", 220, ""),
    "since": ("Since", 100, "date"),
    "by": ("By", 90, ""),
    "state": ("Now", 120, ""),
    "also_on": ("Also listed on", 200, ""),
    "slug": ("Slug", 180, ""),
    "link": ("Link", 120, ""),
    "replacement": ("Possible replacement", 280, ""),
    "checked": ("Link checked", 105, "date"),
    "stars": ("Stars", 70, "num"),
    "downloads": ("Downloads", 90, "num"),
    "players": ("Played (90 days)", 130, "num"),
    "score": ("Score", 60, "num"),
    "reports": ("Reports", 70, "num"),
    "url": ("URL", 300, ""),
}

TABS = {
    "missing": ("Missing", ["title", "game", "platform", "method", "sources", "stars", "downloads", "players", "score", "reports", "released", "version", "pushed", "added",
                            "first_seen", "runs_on", "same_app", "same_game", "link", "checked", "url"]),
    "other": ("Other sites", ["title", "game", "host", "sources", "stars", "downloads", "released", "added", "first_seen", "runs_on", "link",
                              "checked", "url"]),
    "taken": ("Taken down", ["title", "game", "link", "checked", "replacement", "sources", "stars", "downloads", "players", "released", "pushed", "added",
                             "same_app", "same_game", "url"]),
    "waiting": ("Waiting", ["title", "game", "reason", "note", "stars", "downloads", "players", "score", "reports", "released", "version", "sources", "since", "by", "state",
                            "runs_on", "link", "checked", "url"]),
    "blacklist": ("Blacklist", ["title", "game", "reason", "note", "since", "by", "state", "link", "checked", "url"]),
    "quiver": ("In Quiver", ["title", "game", "stars", "downloads", "players", "score", "reports", "released", "version", "added", "also_on", "link", "checked", "slug",
                             "url"]),
    "qdown": ("Down in Quiver", ["title", "game", "link", "checked", "replacement", "stars", "downloads", "players", "released", "version", "added", "slug",
                                 "url"]),
}
# The Runs on filter's entry for projects no site says anything about.
NOT_KNOWN = "Not known"

# Progress messages that only count up, shown in the status line but not kept in the log.
NOISY = re.compile(r"(pages|records) \d+/\d+|\(\d+\)$|^Downloading build")

# Tabs whose rows are Quiver's own apps.
QUIVER_TABS = ("quiver", "qdown")

# Hidden until shown from a heading's right-click menu.
DEFAULT_HIDDEN = {"missing": ["pushed", "first_seen", "checked", "same_game"], "other": ["checked"],
                  "taken": ["same_game"], "waiting": ["by", "runs_on", "checked"],
                  "blacklist": ["by", "checked"], "quiver": ["checked"]}


def open_url(url: str) -> None:
    if url:
        webbrowser.open_new_tab(url)


def open_folder(path: str) -> None:
    if sys.platform == "win32":
        os.startfile(path)  # type: ignore[attr-defined]
    elif sys.platform == "darwin":
        subprocess.Popen(["open", path])
    else:
        subprocess.Popen(["xdg-open", path])


def screen_box(root: tk.Misc) -> tuple[int, int, int, int]:
    """(left, top, right, bottom) of every monitor together. On Windows that's the virtual screen, which reaches left
    of or above the main monitor when another sits there."""
    if sys.platform == "win32":
        try:
            import ctypes

            m = ctypes.windll.user32.GetSystemMetrics
            left, top = m(76), m(77)
            return left, top, left + m(78), top + m(79)
        except Exception:
            pass
    return 0, 0, root.winfo_vrootwidth() or root.winfo_screenwidth(), root.winfo_vrootheight() or root.winfo_screenheight()


GEOMETRY = re.compile(r"^(?:(\d+)x(\d+))?([+-]-?\d+)([+-]-?\d+)$")


def on_screen(root: tk.Misc, geometry: str) -> bool:
    """Whether a saved "WxH+X+Y" (or "+X+Y") would put the window's top-left corner area on a monitor."""
    m = GEOMETRY.match(geometry or "")
    if not m:
        return False
    x, y = int(m.group(3)), int(m.group(4))
    left, top, right, bottom = screen_box(root)
    return left - 50 <= x <= right - 150 and top - 10 <= y <= bottom - 80


def same_app_text(c: Candidate) -> str:
    """App in Quiver: the Quiver app with the same name and creator at another link ("likely: owner/repo"), and how
    many more."""
    if not c.same_app:
        return ""
    q = c.same_app[0]
    return "likely: " + q.url.split("://", 1)[-1].split("/", 1)[-1] + (
        f" (+{len(c.same_app) - 1})" if len(c.same_app) > 1 else "")


ASSETS = Path(__file__).resolve().parent / "assets"


def set_icon(window: tk.Tk) -> None:
    """The arrowhead icon (quiver_scout/assets, drawn by tools/make_icon.py) on the window and its taskbar button."""
    try:
        if sys.platform == "win32" and (ASSETS / "icon.ico").exists():
            window.iconbitmap(default=str(ASSETS / "icon.ico"))  # sharp at Windows' small sizes; dialogs get it too
        icon = tk.PhotoImage(file=str(ASSETS / "icon.png"))
        window.iconphoto(True, icon)
        window._icon = icon  # keep it alive  # noqa: SLF001
    except (tk.TclError, OSError):
        pass


def stat_values(c: Candidate) -> dict:
    """The popularity columns, with thousands separated; blank when no site gives the number."""
    num = lambda n: f"{n:,}" if n else ""  # noqa: E731
    return {"stars": num(c.stars), "downloads": num(c.downloads), "players": num(c.players),
            "score": str(c.score) if c.reports and c.score is not None else "", "reports": num(c.reports)}


def link_values(link: dict | None) -> dict:
    """The Link and Link checked columns for a link check result."""
    from .linkcheck import LABELS
    if not link:
        return {"link": "", "checked": "", "replacement": ""}
    text = LABELS.get(link.get("status", ""), link.get("status", ""))
    if link.get("status") == "moved" and link.get("moved_to"):
        text = "moved → " + link["moved_to"].split("://", 1)[-1].split("/", 1)[-1]
    found = link.get("replacements") or []
    replacement = ""
    if found:
        replacement = found[0]["url"].split("://", 1)[-1] + (f"  (+{len(found) - 1})" if len(found) > 1 else "")
    elif link.get("replacements_checked"):
        replacement = "none found"
    return {"link": text, "checked": link.get("checked", ""), "replacement": replacement}


class Table(ttk.Frame):
    """A Treeview with scrollbars and headings that sort, drag to reorder, and can be hidden.

    Column order, hidden columns, widths and sorting are kept per tab in settings.json.
    """

    def __init__(self, master, app: "App", tab: str):
        super().__init__(master)
        self.app, self.tab = app, tab
        self.columns = TABS[tab][1]
        self.tree = ttk.Treeview(self, columns=self.columns, show="headings", selectmode="extended")
        widths = self._saved("column_widths", {})
        for cid in self.columns:
            label, width, _ = COL[cid]
            self.tree.heading(cid, text=label, command=lambda c=cid: self.sort_by(c))
            self.tree.column(cid, width=widths.get(cid, width), minwidth=50,
                             stretch=cid in ("title", "game", "url", "note"))
        ys = ttk.Scrollbar(self, orient="vertical", command=self.tree.yview)
        xs = ttk.Scrollbar(self, orient="horizontal", command=self.tree.xview)
        self.tree.configure(yscrollcommand=ys.set, xscrollcommand=xs.set)
        self.tree.grid(row=0, column=0, sticky="nsew")
        ys.grid(row=0, column=1, sticky="ns")
        xs.grid(row=1, column=0, sticky="ew")
        self.rowconfigure(0, weight=1)
        self.columnconfigure(0, weight=1)
        saved = self._saved("sort", None)
        self.sort_col, self.sort_desc = (saved[0], saved[1]) if saved and saved[0] in self.columns else (None, False)
        self.default_order: list[str] = []  # the rows as the list gives them, for going back to no sorting
        self._drag: dict | None = None
        self._dragged = False  # a drag just ended: the heading's click that Tk sends after it isn't a sort
        self.apply_columns()
        self._headings()
        self.tree.bind("<<TreeviewSelect>>", lambda e: app.show_details())
        self.tree.bind("<Double-1>", self._double)
        self.tree.bind("<Return>", lambda e: app.open_repo())
        self.tree.bind("<Button-3>", self._menu)
        self.tree.bind("<Button-2>", self._menu)
        self.tree.bind("<ButtonPress-1>", self._press, add="+")
        self.tree.bind("<B1-Motion>", self._motion, add="+")
        self.tree.bind("<ButtonRelease-1>", self._release, add="+")

    # ---- settings ------------------------------------------------------------------------------------------------

    def _saved(self, key: str, default):
        return self.app.store.settings.get(key, {}).get(self.tab, default)

    def _save(self, key: str, value) -> None:
        self.app.store.settings.setdefault(key, {})[self.tab] = value
        self.app.store.save_settings()

    def colors(self, c: dict) -> None:
        f = c["fonts"]
        self.tree.tag_configure("new", font=f["bold"])
        self.tree.tag_configure("dim", foreground=c["dim"])
        self.tree.tag_configure("flag", foreground=c["good"])
        self.tree.tag_configure("warn", foreground=c["error"])
        # Rows alternate between two shades, so a long row is easy to follow across.
        self.tree.tag_configure("even", background=c["surface"])
        self.tree.tag_configure("odd", background=c["stripe"])

    # ---- which columns, in what order ----------------------------------------------------------------------------

    def hidden(self) -> list[str]:
        return self._saved("hidden_columns", DEFAULT_HIDDEN.get(self.tab, []))

    def order(self) -> list[str]:
        """Every column in the order the user put them; columns added since go where the tab first has them."""
        saved = [c for c in self._saved("column_order", []) if c in self.columns]
        for n, c in enumerate(self.columns):
            if c not in saved:
                saved.insert(min(n, len(saved)), c)
        return saved

    def apply_columns(self) -> None:
        hidden = set(self.hidden())
        self.tree["displaycolumns"] = [c for c in self.order() if c not in hidden] or self.columns

    def toggle_column(self, cid: str) -> None:
        hidden = list(self.hidden())
        hidden.remove(cid) if cid in hidden else hidden.append(cid)
        self._save("hidden_columns", hidden)
        self.apply_columns()

    def move_column(self, cid: str, before: str | None) -> None:
        """Put `cid` just before `before` (at the end when None)."""
        order = [c for c in self.order() if c != cid]
        order.insert(order.index(before) if before in order else len(order), cid)
        self._save("column_order", order)
        self.apply_columns()

    def step_column(self, cid: str, step: int) -> None:
        shown = list(self.tree["displaycolumns"])
        i = shown.index(cid)
        j = max(0, min(len(shown) - 1, i + step))
        if i == j:
            return
        target = shown[j]
        order = [c for c in self.order() if c != cid]
        k = order.index(target) + (1 if step > 0 else 0)
        order.insert(k, cid)
        self._save("column_order", order)
        self.apply_columns()

    def reset_columns(self) -> None:
        for key in ("column_order", "hidden_columns", "column_widths"):
            self.app.store.settings.get(key, {}).pop(self.tab, None)
        self.app.store.save_settings()
        for cid in self.columns:
            self.tree.column(cid, width=COL[cid][1])
        self.apply_columns()

    def _column_at(self, x: int) -> str | None:
        col = self.tree.identify_column(x)  # "#3": the third column shown
        shown = list(self.tree["displaycolumns"])
        try:
            return shown[int(col.lstrip("#")) - 1]
        except (ValueError, IndexError):
            return None

    # ---- dragging headings, and remembering widths ---------------------------------------------------------------

    def _press(self, event) -> None:
        region = self.tree.identify_region(event.x, event.y)
        self._drag, self._dragged = None, False
        if region == "heading":
            self._drag = {"col": self._column_at(event.x), "x": event.x, "moving": False}
        elif region == "separator":
            self._drag = {"resize": True}

    def _motion(self, event) -> None:
        d = self._drag
        if d and d.get("col") and not d["moving"] and abs(event.x - d["x"]) > 12:
            d["moving"] = True
            self.tree.configure(cursor="fleur")

    def _release(self, event) -> None:
        d, self._drag = self._drag, None
        if not d:
            return
        if d.get("resize"):
            self._save("column_widths", {c: self.tree.column(c, "width") for c in self.columns})
            return
        if not d.get("moving"):
            return
        self._dragged = True
        # Tk sends the heading's click right after this, if it does at all; past that, clicks are clicks again.
        self.after_idle(lambda: setattr(self, "_dragged", False))
        self.tree.configure(cursor="")
        target = self._column_at(event.x)
        col = d["col"]
        if not target or target == col:
            return
        shown = list(self.tree["displaycolumns"])
        # Dropped on a column's right half: after it; on its left half: before it.
        bbox_x = sum(self.tree.column(c, "width") for c in shown[:shown.index(target)]) - self.tree.xview()[0] * \
            sum(self.tree.column(c, "width") for c in shown)
        after = event.x > bbox_x + self.tree.column(target, "width") / 2
        i = shown.index(target) + (1 if after else 0)
        before = shown[i] if i < len(shown) else None
        if before == col:
            return
        self.move_column(col, before)

    def _double(self, event) -> None:
        if self.tree.identify_region(event.x, event.y) == "cell":
            self.app.open_repo()

    def _menu(self, event) -> None:
        if self.tree.identify_region(event.x, event.y) in ("heading", "separator"):
            cid = self._column_at(event.x)
            m = tk.Menu(self, tearoff=False)
            if cid:
                name = COL[cid][0]
                m.add_command(label=f"Sort by {name}", command=lambda: self.sort_by(cid, cycle=False))
                m.add_command(label="No sorting (the list's own order)", command=self.unsort,
                              state="normal" if self.sort_col else "disabled")
                m.add_separator()
                m.add_command(label=f"Move {name} left", command=lambda: self.step_column(cid, -1))
                m.add_command(label=f"Move {name} right", command=lambda: self.step_column(cid, 1))
                m.add_command(label=f"Hide {name}", command=lambda: self.toggle_column(cid))
                m.add_separator()
            shown = tk.Menu(m, tearoff=False)
            hidden = set(self.hidden())
            self._menu_vars = []  # Tk forgets a checkbutton's state if its variable is garbage collected
            for c in self.order():
                var = tk.BooleanVar(value=c not in hidden)
                self._menu_vars.append(var)
                shown.add_checkbutton(label=COL[c][0], variable=var, command=lambda c=c: self.toggle_column(c))
            theme.style_menu(shown, self.app.c)
            m.add_cascade(label="Columns", menu=shown)
            m.add_command(label="Reset columns (order, shown, widths)", command=self.reset_columns)
            theme.style_menu(m, self.app.c)
            m.tk_popup(event.x_root, event.y_root)
            return
        self.app.context_menu(event)

    # ---- rows and sorting ----------------------------------------------------------------------------------------

    def fill(self, rows: list[tuple[str, dict, tuple]]) -> None:
        """rows: (iid, values by column id, tags)."""
        selected = set(self.tree.selection())
        self.tree.delete(*self.tree.get_children())
        for iid, values, tags in rows:
            self.tree.insert("", "end", iid=iid, values=[values.get(c, "") for c in self.columns], tags=tags)
        self.default_order = [r[0] for r in rows]
        if self.sort_col:
            self._sort()
        else:
            self._stripe()
        keep = [i for i in selected if self.tree.exists(i)]
        if keep:
            self.tree.selection_set(keep)
            self.tree.see(keep[0])

    def selection(self) -> list[str]:
        return list(self.tree.selection())

    def sort_by(self, col: str, cycle: bool = True) -> None:
        """A click on a heading sorts by it, a second click reverses, a third goes back to the list's own order."""
        if self._dragged:
            self._dragged = False
            return
        first_desc = COL[col][2] in ("date", "num")  # newest and biggest first: that's what's asked of them
        if self.sort_col != col or not cycle:
            self.sort_col, self.sort_desc = col, first_desc
        elif self.sort_desc == first_desc:
            self.sort_desc = not first_desc
        else:
            self.unsort()
            return
        self._save("sort", [self.sort_col, self.sort_desc])
        self._sort()

    def unsort(self) -> None:
        self.sort_col, self.sort_desc = None, False
        self._save("sort", None)
        for n, iid in enumerate(i for i in self.default_order if self.tree.exists(i)):
            self.tree.move(iid, "", n)
        self._stripe()
        self._headings()

    def _sort(self) -> None:
        col = self.sort_col
        if COL[col][2] == "num":  # "402,811" sorts as the number it is
            rows = [((int(d) if (d := re.sub(r"[^0-9]", "", str(self.tree.set(i, col)))) else ""), i)
                    for i in self.tree.get_children("")]
        else:
            rows = [(str(self.tree.set(i, col)).lower(), i) for i in self.tree.get_children("")]
        filled = sorted((r for r in rows if r[0]), reverse=self.sort_desc)
        empty = [r for r in rows if not r[0]]
        for n, (_, iid) in enumerate(filled + empty):
            self.tree.move(iid, "", n)
        self._stripe()
        self._headings()

    def _stripe(self) -> None:
        """Shade every other row, by where it is now."""
        for n, iid in enumerate(self.tree.get_children("")):
            tags = [t for t in self.tree.item(iid, "tags") if t not in ("odd", "even")]
            self.tree.item(iid, tags=[*tags, "odd" if n % 2 else "even"])

    def _headings(self) -> None:
        for cid in self.columns:
            arrow = (" ▼" if self.sort_desc else " ▲") if cid == self.sort_col else ""
            self.tree.heading(cid, text=COL[cid][0] + arrow)


class DecisionDialog(tk.Toplevel):
    """Asks why: a reason to wait (for Waiting) and a note."""

    def __init__(self, app: "App", title: str, message: str, reasons: dict | None = None, reason: str = "",
                 note: str = "", prompt: str = "Waiting for:"):
        super().__init__(app.root)
        self.result: tuple[str, str] | None = None
        self.title(title)
        self.transient(app.root)
        self.resizable(False, False)
        body = ttk.Frame(self, padding=16)
        body.pack(fill="both", expand=True)
        ttk.Label(body, text=message, font=app.c["fonts"]["bold"], wraplength=440).pack(anchor="w")
        self.reason = tk.StringVar(value=reason or (next(iter(reasons)) if reasons else ""))
        if reasons:
            ttk.Label(body, text=prompt, style="Muted.TLabel").pack(anchor="w", pady=(10, 2))
            for n, (rid, label) in enumerate(reasons.items(), start=1):
                key = str(n % 10) if n <= 10 else ""  # 1-9, then 0 for the tenth
                ttk.Radiobutton(body, text=f"{key or ' '}  {label}", value=rid, variable=self.reason).pack(anchor="w", pady=1)
                if key:
                    self.bind(key, lambda e, r=rid: self.reason.set(r) if e.widget is not self.entry else None)
        ttk.Label(body, text="Note (optional):", style="Muted.TLabel").pack(anchor="w", pady=(10, 2))
        self.note = tk.StringVar(value=note)
        self.entry = ttk.Entry(body, textvariable=self.note, width=58)
        self.entry.pack(fill="x")
        buttons = ttk.Frame(body)
        buttons.pack(fill="x", pady=(14, 0))
        ttk.Button(buttons, text="Cancel", command=self.destroy).pack(side="right")
        ttk.Button(buttons, text="OK", style="Accent.TButton", command=self.ok).pack(side="right", padx=6)
        self.bind("<Return>", lambda e: self.ok())
        self.bind("<Escape>", lambda e: self.destroy())
        theme.dark_title_bar(self, app.c["dark"])
        self.entry.focus_set()
        self.grab_set()
        self.update_idletasks()
        x = app.root.winfo_rootx() + (app.root.winfo_width() - self.winfo_width()) // 2
        y = app.root.winfo_rooty() + (app.root.winfo_height() - self.winfo_height()) // 3
        self.geometry(f"+{max(x, 0)}+{max(y, 0)}")
        self.wait_window()

    def ok(self) -> None:
        self.result = (self.reason.get(), self.note.get().strip())
        self.destroy()


class App:
    def __init__(self, root: tk.Tk, store: Store):
        self.root = root
        self.store = store
        self.result: Comparison | None = None
        self.candidates: dict[str, Candidate] = {}
        self.quiver_rows: dict[str, Item] = {}
        self.events: queue.Queue = queue.Queue()
        self.refreshing = False
        self.job = "refresh"
        self.dirty: set[str] = set()
        self._search_after = None
        self._links = 0

        root.title(f"Quiver Scout · {updater.version_text()}" if updater.current_build() else "Quiver Scout")
        set_icon(root)
        root.minsize(960, 520)
        self._restore_window()
        self.c = theme.apply(root, store.settings.get("theme", "system"))
        self._menu()
        self._layout()
        self._keys()
        self.recolor()
        self.reload()
        if not self.store.meta("refresh"):
            self.log("No data yet. Press Refresh (F5) to read Quiver and the listing sites.")
        root.bind("<FocusIn>", self._focus)
        updater.cleanup()  # the exe an update left behind
        self.update_info: dict | None = None
        if self.store.settings.get("check_updates", True):
            root.after(3000, lambda: self.check_updates(quiet=True))

    # ---- layout --------------------------------------------------------------------------------------------------

    def _menu(self) -> None:
        f = tk.Menu(self.root, tearoff=False)
        f.add_command(label="Refresh", accelerator="F5", command=self.refresh)
        f.add_command(label="Full refresh (read everything again)", accelerator="Shift+F5",
                      command=lambda: self.refresh(full=True))
        f.add_separator()
        f.add_command(label="Check links (new and due)", accelerator="Ctrl+L", command=self.check_links)
        f.add_command(label="Check every link again", command=lambda: self.check_links(everything=True))
        f.add_command(label="Check Down in Quiver against live Quiver", command=self.check_quiver_live)
        f.add_separator()
        f.add_command(label="Export this list as CSV…", accelerator="Ctrl+E", command=self.export_view)
        f.add_command(label="Export every project as CSV…", command=self.export_all)
        f.add_command(label="Export Quiver catalog as CSV…", command=self.export_quiver)
        f.add_separator()
        f.add_command(label="Import decisions or taken-down list…", command=self.import_shared)
        f.add_command(label="Export decisions…", command=lambda: self.export_shared("decisions"))
        f.add_command(label="Export taken-down list…", command=lambda: self.export_shared("taken-down"))
        f.add_separator()
        f.add_command(label="Settings…", command=self.settings_dialog)
        f.add_command(label="Open data folder", command=lambda: open_folder(str(self.store.dir)))
        f.add_separator()
        f.add_command(label="Quit", command=self.quit)
        v = tk.Menu(self.root, tearoff=False)
        self.theme_var = tk.StringVar(value=self.store.settings.get("theme", "system"))
        for mode, label in (("system", "Theme: same as system"), ("light", "Theme: light"), ("dark", "Theme: dark")):
            v.add_radiobutton(label=label, value=mode, variable=self.theme_var, command=self.set_theme)
        v.add_separator()
        v.add_command(label="Choose columns: right-click a column heading", state="disabled")
        h = tk.Menu(self.root, tearoff=False)
        h.add_command(label="Check for updates…", command=lambda: self.check_updates(quiet=False))
        h.add_command(label="Keyboard shortcuts", command=self.shortcuts)
        h.add_command(label="About", command=lambda: messagebox.showinfo(
            "Quiver Scout", f"Quiver Scout {updater.version_text()}\n\nFinds recomps and ports listed on other sites that "
            "Quiver Launcher doesn't have yet.\n\nData folder:\n" + str(self.store.dir)
            + "\n\nDecisions file:\n" + str(self.store.decisions.path)))
        self.menus = [("File", f), ("View", v), ("Help", h)]
        if sys.platform == "darwin":
            # The Mac's menu bar is at the top of the screen, and follows the system's theme by itself.
            bar = tk.Menu(self.root)
            for label, menu in self.menus:
                bar.add_cascade(label=label, menu=menu)
            self.root.config(menu=bar)
            return
        # Windows draws a window's own menu bar itself, always light; this one follows the theme.
        bar = ttk.Frame(self.root, style="Menubar.TFrame", padding=(4, 2))
        bar.pack(fill="x")
        for label, menu in self.menus:
            mb = ttk.Menubutton(bar, text=label, menu=menu, style="Menubar.TMenubutton", underline=0, width=len(label))
            mb.pack(side="left")
            self.root.bind(f"<Alt-{label[0].lower()}>", lambda e, m=menu, b=mb: m.tk_popup(
                b.winfo_rootx(), b.winfo_rooty() + b.winfo_height()))

    def _layout(self) -> None:
        top = ttk.Frame(self.root, padding=(10, 8, 10, 4))
        top.pack(fill="x")
        self.refresh_btn = ttk.Button(top, text="Refresh  (F5)", style="Accent.TButton", command=self.refresh)
        self.refresh_btn.pack(side="left")
        self.progress = ttk.Progressbar(top, length=220, mode="determinate")
        self.progress.pack(side="left", padx=10)
        self.status = ttk.Label(top, text="")
        self.status.pack(side="left", fill="x", expand=True)
        self.refreshed = ttk.Label(top, text="", style="Muted.TLabel")
        self.refreshed.pack(side="right")

        filters = ttk.Frame(self.root, padding=(10, 2, 10, 6))
        filters.pack(fill="x")
        ttk.Label(filters, text="Search").pack(side="left")
        self.search = tk.StringVar()
        self.search.trace_add("write", lambda *_: self._search_soon())
        self.search_entry = ttk.Entry(filters, textvariable=self.search, width=22)
        self.search_entry.pack(side="left", padx=(6, 12))
        # Which sites: a drop-down of checkboxes, as there are more sites than fit in a row.
        self.source_vars = {}
        self.sources_menu = tk.Menu(self.root, tearoff=False)
        for sid, label in EXTERNAL.items():
            v = tk.BooleanVar(value=True)
            v.trace_add("write", lambda *_: self._filters_changed())
            self.source_vars[sid] = v
            self.sources_menu.add_checkbutton(label=label, variable=v)
        self.sources_menu.add_separator()
        self.sources_menu.add_command(label="All sites", command=lambda: self._set_all(self.source_vars, True))
        self.sources_menu.add_command(label="No sites", command=lambda: self._set_all(self.source_vars, False))
        self.sources_text = tk.StringVar()
        ttk.Menubutton(filters, textvariable=self.sources_text, menu=self.sources_menu, width=19).pack(side="left")
        # The original platform, and what the projects run on: drop-downs like the sites', their entries from the
        # data (see _value_menu).
        self.platform_vars: dict[str, tk.BooleanVar] = {}
        self.platforms_menu = tk.Menu(self.root, tearoff=False)
        self.platforms_text = tk.StringVar()
        ttk.Menubutton(filters, textvariable=self.platforms_text, menu=self.platforms_menu, width=19).pack(
            side="left", padx=(6, 0))
        self.system_vars: dict[str, tk.BooleanVar] = {}
        self.systems_menu = tk.Menu(self.root, tearoff=False)
        self.systems_text = tk.StringVar()
        ttk.Menubutton(filters, textvariable=self.systems_text, menu=self.systems_menu, width=19).pack(
            side="left", padx=(6, 0))
        self._batch = False
        self._labels()
        ttk.Separator(filters, orient="vertical").pack(side="left", fill="y", padx=10)
        self.new_only = self._check(filters, "New")
        self.has_release = self._check(filters, "Has a release")
        self.hide_same_app = self._check(filters, "Hide apps in Quiver")
        self.multi_only = self._check(filters, "2+ sites")

        # The bottom is packed before the lists so it keeps its size and the lists take what's left.
        bottom = ttk.Frame(self.root, padding=(10, 0, 10, 8))
        bottom.pack(side="bottom", fill="x")
        actions = ttk.Frame(bottom, padding=(0, 8, 0, 0))
        actions.pack(side="bottom", fill="x")
        self.btn_open = ttk.Button(actions, text="Open repository  ⏎", command=self.open_repo)
        self.btn_pages = ttk.Button(actions, text="Listing pages  Ctrl+⏎", command=self.open_pages)
        self.btn_copy = ttk.Button(actions, text="Copy URL  Ctrl+C", command=self.copy_urls)
        self.btn_quiver = ttk.Button(actions, text="Open in Quiver  Q", command=self.open_quiver)
        self.btn_wait = ttk.Button(actions, text="Wait…  W", command=self.wait_selected)
        self.btn_black = ttk.Button(actions, text="Blacklist…  Del", command=self.blacklist_selected)
        self.btn_back = ttk.Button(actions, text="Back to list  ⌫", command=self.undecide_selected)
        for b in (self.btn_open, self.btn_pages, self.btn_quiver, self.btn_copy, self.btn_wait, self.btn_black,
                  self.btn_back):
            b.pack(side="left", padx=(0, 6))
        self.count = ttk.Label(actions, text="", style="Muted.TLabel")
        self.count.pack(side="right")
        # Details of the selected project: shown only with one selected, and only as tall as what it says.
        self.details = tk.Text(bottom, height=1, wrap="word", state="disabled", relief="flat", padx=10, pady=6,
                               highlightthickness=0, borderwidth=0, font="TkDefaultFont", cursor="")
        self.details.tag_bind("link", "<Enter>", lambda e: self.details.config(cursor="hand2"))
        self.details.tag_bind("link", "<Leave>", lambda e: self.details.config(cursor=""))

        self.tabs = ttk.Notebook(self.root)
        self.tabs.pack(fill="both", expand=True, padx=10)
        self.tables = {tab: Table(self.tabs, self, tab) for tab in TABS}
        logframe = ttk.Frame(self.tabs)
        self.logbox = tk.Text(logframe, height=10, wrap="word", state="disabled", relief="flat", padx=8, pady=6,
                              highlightthickness=0, borderwidth=0)
        self.logbox.pack(fill="both", expand=True)
        for tab, table in self.tables.items():
            self.tabs.add(table, text=TABS[tab][0])
        self.tabs.add(logframe, text="Log")
        self.tabs.bind("<<NotebookTabChanged>>", lambda e: self._tab_changed())
        self._update_buttons()

    # ---- window size and place ---------------------------------------------------------------------------------

    def _restore_window(self) -> None:
        saved = self.store.settings.get("window", {})
        geometry = saved.get("main", "")
        self.root.geometry(geometry if on_screen(self.root, geometry) else "1440x860")
        if saved.get("main_zoomed"):
            try:
                self.root.state("zoomed")  # Windows, macOS
            except tk.TclError:
                try:
                    self.root.attributes("-zoomed", True)  # Linux
                except tk.TclError:
                    pass
        self._geometry_after = None
        self.root.bind("<Configure>", self._window_moved, add="+")
        self.root.protocol("WM_DELETE_WINDOW", self.quit)

    def _zoomed(self) -> bool:
        try:
            if self.root.state() == "zoomed":
                return True
        except tk.TclError:
            pass
        try:
            return bool(self.root.attributes("-zoomed"))
        except tk.TclError:
            return False

    def _window_moved(self, event) -> None:
        if event.widget is not self.root:
            return
        if self._geometry_after:
            self.root.after_cancel(self._geometry_after)
        self._geometry_after = self.root.after(600, self._save_window)

    def _save_window(self) -> None:
        """Keep the size and place it has when not maximized, and whether it's maximized."""
        self._geometry_after = None
        w = self.store.settings.setdefault("window", {})
        zoomed = self._zoomed()
        changed = w.get("main_zoomed") != zoomed
        w["main_zoomed"] = zoomed
        if not zoomed and self.root.state() == "normal":
            geometry = self.root.geometry()
            changed |= w.get("main") != geometry
            w["main"] = geometry
        if changed:
            self.store.save_settings()

    def quit(self) -> None:
        if self._geometry_after:
            self.root.after_cancel(self._geometry_after)
        try:
            self._save_window()
        finally:
            self.root.destroy()

    @staticmethod
    def _label(prefix: str, names: dict[str, str], variables: dict[str, tk.BooleanVar], what: str) -> str:
        on = [k for k, v in variables.items() if v.get()]
        if len(on) == len(variables):
            return f"{prefix}: any"
        if len(on) == 1:
            return f"{prefix}: {names.get(on[0], on[0])}"
        return f"{prefix}: {len(on)} of {len(variables)} {what}"

    def _labels(self) -> None:
        self.sources_text.set(self._label("Listed on", EXTERNAL, self.source_vars, "sites"))
        self.systems_text.set(self._label("Runs on", {}, self.system_vars, "systems") if self.system_vars
                              else "Runs on: any")
        self.platforms_text.set(self._label("Platform", {}, self.platform_vars, "platforms") if self.platform_vars
                                else "Platform: any")

    def _filters_changed(self) -> None:
        if not self._batch:
            self._labels()
            self.render()

    def _set_all(self, variables: dict[str, tk.BooleanVar], on: bool) -> None:
        """Tick or untick a whole drop-down, filtering the lists once rather than once per box."""
        self._batch = True
        try:
            for v in variables.values():
                v.set(on)
        finally:
            self._batch = False
        self._filters_changed()

    def _systems_menu(self) -> None:
        """Fill the Platform and Runs on drop-downs from the data."""
        self.platform_vars = self._value_menu(self.platforms_menu, self.platform_vars, lambda c: c.platforms, "platforms")
        self.system_vars = self._value_menu(self.systems_menu, self.system_vars, lambda c: c.systems, "systems")
        self._labels()

    def _value_menu(self, menu: tk.Menu, old_vars: dict, values_of, what: str) -> dict:
        """A drop-down of every value projects have (a platform, a system), most common first with a count, and Not
        known for those with none. One that was unticked stays unticked; one new since the last refresh starts
        ticked. Returns its variables."""
        counts: dict[str, int] = {}
        for c in self.result.candidates if self.result else []:
            for s in values_of(c) or [NOT_KNOWN]:
                counts[s] = counts.get(s, 0) + 1
        order = sorted((s for s in counts if s != NOT_KNOWN), key=lambda s: (-counts[s], s.lower()))
        order += [NOT_KNOWN] if NOT_KNOWN in counts else []
        old = {s: v.get() for s, v in old_vars.items()}
        new: dict[str, tk.BooleanVar] = {}
        menu.delete(0, "end")
        for s in order:
            v = tk.BooleanVar(value=old.get(s, True))
            v.trace_add("write", lambda *_: self._filters_changed())
            new[s] = v
            menu.add_checkbutton(label=f"{s}  ({counts[s]})", variable=v)
        menu.add_separator()
        menu.add_command(label=f"All {what}", command=lambda: self._set_all(new, True))
        menu.add_command(label=f"No {what}", command=lambda: self._set_all(new, False))
        return new

    def _check(self, parent, text: str) -> tk.BooleanVar:
        v = tk.BooleanVar(value=False)
        v.trace_add("write", lambda *_: self.render())
        ttk.Checkbutton(parent, text=text, variable=v).pack(side="left", padx=3)
        return v

    def _keys(self) -> None:
        r = self.root
        r.bind("<F5>", lambda e: self.refresh())
        r.bind("<Shift-F5>", lambda e: self.refresh(full=True))
        r.bind("<Control-f>", lambda e: (self.search_entry.focus_set(), self.search_entry.select_range(0, "end")))
        r.bind("<Control-e>", lambda e: self.export_view())
        r.bind("<Control-l>", lambda e: self.check_links())
        r.bind("<Control-Return>", lambda e: self.open_pages())
        r.bind("<Escape>", lambda e: self.search.set(""))
        for n in range(len(TABS)):
            r.bind(f"<Control-Key-{n + 1}>", lambda e, n=n: self.tabs.select(n))
        for t in self.tables.values():
            t.tree.bind("<Delete>", lambda e: self.delete_key())
            t.tree.bind("<BackSpace>", lambda e: self.undecide_selected())
            t.tree.bind("<KeyPress-w>", lambda e: self.wait_selected())
            t.tree.bind("<KeyPress-q>", lambda e: self.open_quiver())
            t.tree.bind("<Control-c>", lambda e: self.copy_urls())
            t.tree.bind("<Control-a>", lambda e, t=t: t.tree.selection_set(t.tree.get_children()))

    def shortcuts(self) -> None:
        messagebox.showinfo("Keyboard shortcuts", "\n".join([
            "F5\tRefresh (only what changed)",
            "Shift+F5\tFull refresh (read everything again)",
            "Enter / double-click\tOpen repository",
            "Ctrl+Enter\tOpen the listing site pages",
            "Q\tOpen the Quiver listing (the app, or Quiver's apps for the same game)",
            "Ctrl+C\tCopy URLs of the selected rows",
            "W\tWait for it (on Waiting: change why)",
            "Delete\tBlacklist (on Waiting and Blacklist: back to the list)",
            "Backspace\tBack to the list, from Waiting or Blacklist",
            "Ctrl+A\tSelect every row in the list",
            "Ctrl+1 … Ctrl+5\tSwitch tabs",
            "Ctrl+F / Esc\tSearch / clear the search",
            "Ctrl+E\tExport the current list as CSV",
            "Right-click a row\tMore actions",
            "Click a heading\tSort; again to reverse; a third time for the default order",
            "Drag a heading\tMove the column",
            "Right-click a heading\tMove, hide, show or reset columns",
        ]))

    # ---- theme ---------------------------------------------------------------------------------------------------

    def set_theme(self) -> None:
        self.store.settings["theme"] = self.theme_var.get()
        self.store.save_settings()
        self.c = theme.apply(self.root, self.theme_var.get())
        self.recolor()

    def recolor(self) -> None:
        c = self.c
        for _, menu in self.menus:
            theme.style_menu(menu, c)
        theme.style_menu(self.sources_menu, c)
        theme.style_menu(self.systems_menu, c)
        theme.style_menu(self.platforms_menu, c)
        for t in self.tables.values():
            t.colors(c)
        for text in (self.details, self.logbox):
            text.configure(background=c["surface"], foreground=c["fg"], insertbackground=c["fg"],
                           selectbackground=c["select"], selectforeground=c["on_select"])
        d = self.details
        d.configure(background=c["bg"])
        d.tag_configure("h", font=c["fonts"]["heading"])
        d.tag_configure("label", foreground=c["muted"])
        d.tag_configure("link", foreground=c["link"], underline=True)
        d.tag_configure("flag", foreground=c["good"])
        d.tag_configure("warn", foreground=c["error"])
        self.logbox.tag_configure("error", foreground=c["error"])

    # ---- data ----------------------------------------------------------------------------------------------------

    def _focus(self, event) -> None:
        # Another maintainer (or a sync) may have changed the shared decisions file.
        if event.widget is self.root and self.store.decisions.reload():
            self.reload()
            self.status.config(text="Decisions file changed on disk; reloaded.", style="TLabel")

    def reload(self) -> None:
        self.result = engine.compare(self.store)
        self.candidates = {c.key: c for c in self.result.candidates}
        self.quiver_rows = {f"q{n}": q for n, q in enumerate(self.result.quiver)}
        r = self.result.refresh
        if r.get("refreshed_at"):
            prev = f" (previous: {r['previous_refresh']})" if r.get("previous_refresh") else ""
            self.refreshed.config(text=f"Last refresh {r['refreshed_at']}{prev}")
        if r.get("errors"):
            self.status.config(text="Last refresh had errors, see the Log tab", style="Error.TLabel")
        self._systems_menu()
        self.render()

    def _search_soon(self) -> None:
        if self._search_after:
            self.root.after_cancel(self._search_after)
        self._search_after = self.root.after(250, self.render)

    def _matches(self, text: str, *fields: str) -> bool:
        return all(any(word in (f or "").lower() for f in fields) for word in text.split())

    def _filtered(self, cands: list[Candidate], site_filters: bool) -> list[Candidate]:
        r = self.result
        text = self.search.get().strip().lower()
        sources = {s for s, v in self.source_vars.items() if v.get()}
        systems = {s for s, v in self.system_vars.items() if v.get()}
        every_system = len(systems) == len(self.system_vars)
        platforms = {s for s, v in self.platform_vars.items() if v.get()}
        every_platform = len(platforms) == len(self.platform_vars)
        out = []
        for c in cands:
            if site_filters:
                if c.sources and not sources.intersection(c.sources):
                    continue
                if self.new_only.get() and not r.is_new(c):
                    continue
                if self.has_release.get() and not c.released:
                    continue
                if self.hide_same_app.get() and c.same_app:
                    continue
                if self.multi_only.get() and len(c.sources) < 2:
                    continue
                if not every_system and not systems.intersection(c.systems or [NOT_KNOWN]):
                    continue
                if not every_platform and not platforms.intersection(c.platforms or [NOT_KNOWN]):
                    continue
            d = c.decision or {}
            if text and not self._matches(text, c.title, c.game, c.url, c.author, c.platform, c.method,
                                          d.get("note", ""), d.get("by", ""), reason_label(d)):
                continue
            out.append(c)
        return out

    def _values(self, c: Candidate) -> dict:
        d = c.decision or {}
        if c.in_quiver:
            state = "in Quiver now"
        elif not c.listed:
            state = "no longer listed"
        else:
            state = ""
        return {
            "title": c.title, "game": c.game, "platform": c.platform, "method": c.method,
            "sources": ", ".join(EXTERNAL[s] for s in c.sources), "host": c.host, "released": c.released,
            "version": c.version, "pushed": c.pushed, "added": c.added, "runs_on": c.runs_on, **stat_values(c),
            "first_seen": "" if c.first_seen == "initial" else c.first_seen,
            "same_app": same_app_text(c),
            "same_game": f"yes ({len(c.same_game)})" if c.same_game else "",
            "reason": reason_label(d), "note": d.get("note", ""),
            "since": d.get("date", ""), "by": d.get("by", ""), "state": state, "url": c.url,
            **link_values(c.link),
        }

    def _lists(self) -> dict[str, list[Candidate]]:
        r = self.result
        return {
            "missing": self._filtered(r.missing, True),
            "other": self._filtered(r.other_sites, True),
            "taken": self._filtered(r.taken_down, True),
            "waiting": self._filtered(r.decided(WAITING), False),
            "blacklist": self._filtered(r.decided(BLACKLIST), False),
        }

    def render(self) -> None:
        """Count every list; fill only the one on screen (the others when they're shown)."""
        if not self.result:
            return
        r = self.result
        self.lists = self._lists()
        text = self.search.get().strip().lower()
        self.quiver_shown = [iid for iid, q in self.quiver_rows.items()
                             if not text or self._matches(text, q.title, q.game, q.url, q.slug, q.author)]
        down = {q.key for q in r.quiver_down}
        self.qdown_shown = [iid for iid in self.quiver_shown if self.quiver_rows[iid].key in down]
        totals = {"missing": len(r.missing), "other": len(r.other_sites), "taken": len(r.taken_down),
                  "waiting": len(r.decided(WAITING)),
                  "blacklist": len(r.decided(BLACKLIST)), "quiver": len(r.quiver), "qdown": len(r.quiver_down)}
        for tab, table in self.tables.items():
            shown = {"quiver": len(self.quiver_shown), "qdown": len(self.qdown_shown)}.get(tab) \
                if tab in QUIVER_TABS else len(self.lists[tab])
            label = f"{TABS[tab][0]}  {shown}" + (f" / {totals[tab]}" if shown != totals[tab] else "")
            self.tabs.tab(table, text=label)
        new = sum(1 for c in r.missing + r.other_sites if r.is_new(c))
        self.count.config(text=f"{sum(1 for c in r.candidates if c.listed)} listed elsewhere · "
                               f"{len(r.missing)} missing · {new} new")
        self.dirty = set(TABS)
        self._fill(self.current())
        self.show_details()

    def _fill(self, tab: str) -> None:
        if tab not in self.dirty:
            return
        self.dirty.discard(tab)
        r = self.result
        if tab in QUIVER_TABS:
            also: dict[str, list[str]] = {}
            for c in r.candidates:
                for q in c.in_quiver:
                    for s in c.sources:
                        if EXTERNAL[s] not in also.setdefault(q.key, []):
                            also[q.key].append(EXTERNAL[s])
            rows = []
            for iid in (self.quiver_shown if tab == "quiver" else self.qdown_shown):
                q = self.quiver_rows[iid]
                link = r.links.get(q.key)
                down = (link or {}).get("status") in TAKEN_DOWN_STATES
                tags = ("flag",) if tab == "qdown" and (link or {}).get("replacements") else ("warn",) if down else ()
                listed = self.candidates.get(q.key)  # what the listing sites say about it
                rows.append((iid, {**link_values(link), **(stat_values(listed) if listed else {}),
                                   "title": q.title, "game": q.game, "released": q.released,
                                   "version": q.version,
                                   "added": q.added, "also_on": ", ".join(also.get(q.key, [])), "slug": q.slug,
                                   "url": q.url}, tags))
            self.tables[tab].fill(rows)
            return
        rows = []
        for c in self.lists[tab]:
            tags = ()
            if tab == "taken":
                tags = ("warn",)
            elif tab in ("missing", "other") and r.is_new(c):
                tags = ("new",)
            elif tab in ("waiting", "blacklist"):
                tags = ("flag",) if c.in_quiver else ("dim",) if not c.listed else ()
            rows.append((c.key, self._values(c), tags))
        self.tables[tab].fill(rows)

    def _tab_changed(self) -> None:
        if self.result and self.current() in TABS:
            self._fill(self.current())
        self._update_buttons()
        self.show_details()

    # ---- selection and actions -----------------------------------------------------------------------------------

    def current(self) -> str:
        i = self.tabs.index(self.tabs.select())
        return list(TABS)[i] if i < len(TABS) else "log"

    def selected_candidates(self) -> list[Candidate]:
        t = self.tables.get(self.current())
        if not t or self.current() in QUIVER_TABS:
            return []
        return [self.candidates[k] for k in t.selection() if k in self.candidates]

    def selected_links(self) -> list[tuple[str, list[str]]]:
        """(repository URL, listing pages) of each selected row."""
        if self.current() in QUIVER_TABS:
            out = []
            for iid in self.tables[self.current()].selection():
                q = self.quiver_rows[iid]
                c = self.candidates.get(q.key)
                out.append((q.url, [q.page] + (list(c.pages.values()) if c else [])))
            return out
        return [(c.url, list(c.pages.values())) for c in self.selected_candidates()]

    def _update_buttons(self) -> None:
        cur = self.current()
        on = lambda yes: "normal" if yes else "disabled"  # noqa: E731
        for b in (self.btn_open, self.btn_pages, self.btn_copy):
            b.config(state=on(cur != "log"))
        self.btn_wait.config(state=on(cur in ("missing", "other", "taken", "waiting", "blacklist")),
                             text="Change why…  W" if cur == "waiting" else "Wait…  W")
        self.btn_black.config(state=on(cur in ("missing", "other", "taken", "waiting", "blacklist")),
                              text="Change reason…" if cur == "blacklist" else "Blacklist…  Del")
        self.btn_back.config(state=on(cur in ("waiting", "blacklist")))
        self.btn_quiver.config(state=on(bool(self.quiver_pages())))

    def open_repo(self) -> None:
        links = self.selected_links()
        if len(links) > 10 and not messagebox.askyesno("Open links", f"Open {len(links)} repositories in your browser?"):
            return
        for url, _ in links:
            open_url(url)

    def quiver_pages(self) -> list[tuple[str, str]]:
        """(title, Quiver page) for the selection: the Quiver app itself, the app with the same repository, or else
        Quiver's apps for the same game."""
        if self.current() in QUIVER_TABS:
            apps = [self.quiver_rows[i] for i in self.tables[self.current()].selection() if i in self.quiver_rows]
        else:
            apps = [q for c in self.selected_candidates() for q in (c.in_quiver or c.same_app or c.same_game)]
        seen, out = set(), []
        for q in apps:
            if q.page and q.page not in seen:
                seen.add(q.page)
                out.append((q.title, q.page))
        return out

    def open_quiver(self) -> None:
        pages = self.quiver_pages()
        if not pages:
            self.status.config(text="Quiver has no app for the selection, or for its game.", style="TLabel")
            return
        if len(pages) > 10 and not messagebox.askyesno("Open links", f"Open {len(pages)} Quiver pages in your browser?"):
            return
        for _, page in pages:
            open_url(page)

    def open_pages(self) -> None:
        pages = [p for _, ps in self.selected_links() for p in ps if p]
        if len(pages) > 10 and not messagebox.askyesno("Open links", f"Open {len(pages)} pages in your browser?"):
            return
        for p in pages:
            open_url(p)

    def copy_urls(self) -> None:
        urls = [url for url, _ in self.selected_links()]
        if urls:
            self.root.clipboard_clear()
            self.root.clipboard_append("\n".join(urls))
            self.status.config(text=f"Copied {len(urls)} URL(s)", style="TLabel")

    def delete_key(self) -> None:
        if self.current() in ("missing", "other", "taken"):
            self.blacklist_selected()
        elif self.current() in ("waiting", "blacklist"):
            self.undecide_selected()

    def _next_row_after(self, keys: list[str]) -> str | None:
        tree = self.tables[self.current()].tree
        children = list(tree.get_children())
        if not keys or keys[-1] not in children:
            return None
        rest = [i for i in children[children.index(keys[-1]) + 1:] if i not in keys]
        before = [i for i in children[:children.index(keys[0])] if i not in keys]
        return (rest or before[-1:] or [None])[0]

    def _after_decision(self, keys: list[str], message: str) -> None:
        """Re-render, keeping the cursor where it was, so you can work down the list from the keyboard."""
        tab = self.current()
        nxt = self._next_row_after(keys)
        self.result = engine.compare(self.store)
        self.candidates = {c.key: c for c in self.result.candidates}
        self.render()
        tree = self.tables[tab].tree
        if nxt and tree.exists(nxt):
            tree.selection_set(nxt)
            tree.focus(nxt)
            tree.see(nxt)
        tree.focus_set()
        self.status.config(text=message, style="TLabel")

    def _describe(self, cands: list[Candidate]) -> str:
        return (cands[0].title or cands[0].url) if len(cands) == 1 else f"{len(cands)} projects"

    def wait_selected(self) -> None:
        if self.current() not in ("missing", "other", "taken", "waiting", "blacklist"):
            return
        cands = self.selected_candidates()
        if not cands:
            return
        d = cands[0].decision or {}
        editing = self.current() == "waiting"
        default = d.get("reason") if editing else ("host" if self.current() == "other" else "")
        dlg = DecisionDialog(self, "Wait", f"Wait for {self._describe(cands)}?", WAIT_REASONS, reason=default or "",
                             note=d.get("note", "") if editing and len(cands) == 1 else "")
        if not dlg.result:
            return
        engine.decide(self.store, WAITING, cands, reason=dlg.result[0], note=dlg.result[1])
        keys = [c.key for c in cands]
        self._after_decision(keys, f"Waiting for {self._describe(cands)}: {WAIT_REASONS[dlg.result[0]]}")

    def blacklist_selected(self) -> None:
        """Blacklist the selection, with a reason; on the Blacklist tab, change the reason."""
        if self.current() not in ("missing", "other", "taken", "waiting", "blacklist"):
            return
        cands = self.selected_candidates()
        if not cands:
            return
        editing = self.current() == "blacklist"
        d = (cands[0].decision or {}) if editing else {}
        dlg = DecisionDialog(self, "Blacklist", (f"Why is {self._describe(cands)} blacklisted?" if editing
                                                 else f"Blacklist {self._describe(cands)}?"),
                             BLACKLIST_REASONS, reason=d.get("reason", "") if editing else "",
                             note=d.get("note", "") if editing and len(cands) == 1 else "", prompt="Reason:")
        if not dlg.result:
            return
        engine.decide(self.store, BLACKLIST, cands, reason=dlg.result[0], note=dlg.result[1])
        self._after_decision([c.key for c in cands],
                             f"Blacklisted {self._describe(cands)}: {BLACKLIST_REASONS.get(dlg.result[0], '')}")

    def _quick_blacklist(self, reason: str) -> None:
        cands = self.selected_candidates()
        if cands:
            engine.decide(self.store, BLACKLIST, cands, reason=reason)
            self._after_decision([c.key for c in cands],
                                 f"Blacklisted {self._describe(cands)}: {BLACKLIST_REASONS[reason]}")

    def undecide_selected(self) -> None:
        if self.current() not in ("waiting", "blacklist"):
            return
        cands = self.selected_candidates()
        if not cands:
            return
        engine.undecide(self.store, [c.key for c in cands])
        self._after_decision([c.key for c in cands], f"{self._describe(cands)} back in the list")

    def context_menu(self, event) -> None:
        tree = event.widget
        row = tree.identify_row(event.y)
        if row and row not in tree.selection():
            tree.selection_set(row)
        cur = self.current()
        m = tk.Menu(self.root, tearoff=False)
        m.add_command(label="Open repository", command=self.open_repo)
        m.add_command(label="Open listing pages", command=self.open_pages)
        quiver = self.quiver_pages()
        m.add_command(label="Open in Quiver" if len(quiver) <= 1 else f"Open in Quiver ({len(quiver)} apps)",
                      command=self.open_quiver, state="normal" if quiver else "disabled")
        m.add_command(label="Copy URL", command=self.copy_urls)
        m.add_command(label="Check link now", command=self.check_selected_links,
                      state="disabled" if self.refreshing else "normal")
        down_keys = self._selected_down_keys()
        if cur in QUIVER_TABS:
            slugs = [self.quiver_rows[i].slug for i in self.tables[cur].selection() if i in self.quiver_rows]
            m.add_command(label="Check live in Quiver (archived? repository changed?)",
                          command=lambda: self.check_quiver_live(slugs),
                          state="normal" if slugs and not self.refreshing else "disabled")
        m.add_command(label="Find replacements now", command=self.find_selected_replacements,
                      state="normal" if down_keys and not self.refreshing else "disabled")
        link = self.result.links.get(down_keys[0]) if len(down_keys) == 1 else None
        for r in ((link or {}).get("replacements") or [])[:6]:
            m.add_command(label=f"Open replacement: {r['url'].split('://', 1)[-1]} ({r['how']})",
                          command=lambda u=r["url"]: open_url(u))
        if cur in ("missing", "other", "taken", "waiting", "blacklist"):
            m.add_separator()
            if cur in ("missing", "other", "taken", "blacklist"):
                wait = tk.Menu(m, tearoff=False)
                for rid, label in WAIT_REASONS.items():
                    wait.add_command(label=label, command=lambda r=rid: self._quick_wait(r))
                wait.add_separator()
                wait.add_command(label="With a note…", command=self.wait_selected)
                m.add_cascade(label="Wait for…", menu=wait)
            else:
                m.add_command(label="Change why…", command=self.wait_selected)
            black = tk.Menu(m, tearoff=False)
            for rid, label in BLACKLIST_REASONS.items():
                black.add_command(label=label, command=lambda r=rid: self._quick_blacklist(r))
            black.add_separator()
            black.add_command(label="With a note…", command=self.blacklist_selected)
            theme.style_menu(black, self.c)
            m.add_cascade(label="Change reason to…" if cur == "blacklist" else "Blacklist for…", menu=black)
            if cur in ("waiting", "blacklist"):
                m.add_command(label="Back to the list", command=self.undecide_selected)
            cands = self.selected_candidates()
            if len(cands) == 1 and (cands[0].same_app or cands[0].same_game):
                m.add_separator()
                for q in cands[0].same_app[:8]:
                    m.add_command(label=f"Quiver may have this app as: {q.title}", command=lambda u=q.page or q.url: open_url(u))
                for q in [q for q in cands[0].same_game if q not in cands[0].same_app][:6]:
                    m.add_command(label=f"Quiver has the same game: {q.title}", command=lambda u=q.page or q.url: open_url(u))
        m.tk_popup(event.x_root, event.y_root)

    def check_quiver_live(self, slugs: list[str] | None = None) -> None:
        """Ask Quiver's live catalog about the apps in Down in Quiver (or the selected ones): archived ones and ones
        whose repository was changed leave the tab."""
        if self.refreshing:
            return
        self.log("--- Asking live Quiver about Down in Quiver")

        def work(say):
            counts = engine.verify_quiver_live(self.store, say, slugs=slugs)
            return {"checked": 0, "live": counts}
        self._start("links", "Asking Quiver…", work)

    def _selected_down_keys(self) -> list[str]:
        keys = [self._key_of(u) for u, _ in self.selected_links()]
        return [k for k in keys if (self.result.links.get(k) or {}).get("status") in TAKEN_DOWN_STATES]

    def find_selected_replacements(self) -> None:
        keys = self._selected_down_keys()
        if keys and not self.refreshing:
            self.log("--- Looking for replacements")
            self._start("links", "Looking for replacements…", lambda say: {"checked": 0, "replacements":
                        engine.find_replacements(self.store, keys, say)})

    def check_selected_links(self) -> None:
        keys = [url_key for url_key in (self._key_of(u) for u, _ in self.selected_links()) if url_key]
        if keys:
            self.check_links(keys=keys)

    def _key_of(self, url: str) -> str:
        from .urls import canonical
        c = canonical(url)
        return c[0] if c else ""

    def _quick_wait(self, reason: str) -> None:
        cands = self.selected_candidates()
        if cands:
            engine.decide(self.store, WAITING, cands, reason=reason)
            self._after_decision([c.key for c in cands], f"Waiting for {self._describe(cands)}: {WAIT_REASONS[reason]}")

    # ---- details pane --------------------------------------------------------------------------------------------

    def _link(self, text: str, url: str) -> None:
        self._links += 1
        tag = f"link{self._links}"
        self.details.insert("end", text, ("link", tag))
        self.details.tag_bind(tag, "<Button-1>", lambda e, u=url: open_url(u))

    def _line(self, label: str) -> None:
        self.details.insert("end", f"\n{label}  ", "label")

    def show_details(self) -> None:
        self._update_buttons()
        d = self.details
        d.config(state="normal")
        d.delete("1.0", "end")
        cur = self.current()
        try:
            if cur in QUIVER_TABS:
                sel = self.tables[cur].selection()
                if len(sel) == 1:
                    self._quiver_details(self.quiver_rows[sel[0]])
                elif sel:
                    d.insert("end", f"{len(sel)} selected", "h")
            elif cur in TABS:
                cands = self.selected_candidates()
                if len(cands) == 1:
                    self._candidate_details(cands[0])
                elif cands:
                    d.insert("end", f"{len(cands)} selected", "h")
        finally:
            d.config(state="disabled")
            self._fit_details()

    def _fit_details(self) -> None:
        """Show the details only when there are some, exactly as tall as their lines (wrapped), at most 10."""
        d = self.details
        if not d.get("1.0", "end-1c").strip():
            d.pack_forget()
            return
        if not d.winfo_ismapped():
            d.pack(side="top", fill="x", pady=(8, 0))
        # Lines as written, or more when long ones wrap (which Tk can only tell once the pane has its width).
        lines = int(d.index("end-1c").split(".")[0])
        d.update_idletasks()
        try:
            shown = d.count("1.0", "end-1c", "displaylines")
            shown = (shown[0] if isinstance(shown, tuple) else shown) or 0
        except tk.TclError:
            shown = 0
        # Height counts lines of the body font; the title line is in a bigger one.
        d.configure(height=min(max(lines, shown) + 1, 11))
        # The list just got shorter; keep the selected row in sight.
        t = self.tables.get(self.current())
        if t and t.tree.focus():
            t.tree.see(t.tree.focus())

    def _quiver_details(self, q: Item) -> None:
        d = self.details
        d.insert("end", q.title, "h")
        d.insert("end", f"   {q.game}")
        self._line("Repository")
        self._link(q.url, q.url)
        if q.extra.get("live"):
            d.insert("end", "   changed in Quiver (live), not yet in the cached catalog", "flag")
        self._line("Quiver page")
        self._link(q.page, q.page)
        facts = [f for f in (f"latest release {q.version} on {q.released}" if q.released else "",
                             f"added {q.added}" if q.added else "", f"by {q.author}" if q.author else "") if f]
        if facts:
            self._line("About")
            d.insert("end", " · ".join(facts))
        c = self.candidates.get(q.key)
        if c and c.sources:
            self._line("Also listed on")
            for s in c.sources:
                self._link(EXTERNAL[s], c.pages.get(s, ""))
                d.insert("end", "   ")
        self._link_line(self.result.links.get(q.key))

    def _link_line(self, link: dict | None) -> None:
        """What the last link check found, and where a moved one went."""
        if not link or not link.get("status") or link.get("status") == "suspect":
            return
        from .linkcheck import LABELS, TAKEN_DOWN
        d = self.details
        self._line("Link")
        status = link["status"]
        d.insert("end", LABELS.get(status, status), "warn" if status in TAKEN_DOWN else "flag" if status == "ok" else ())
        if status == "moved" and link.get("moved_to"):
            d.insert("end", " to ")
            self._link(link["moved_to"], link["moved_to"])
        bits = [link.get("detail", ""), f"checked {link.get('checked', '')}", f"by {link['by']}" if link.get("by") else ""]
        d.insert("end", "   " + " · ".join(b for b in bits if b), "label")
        if status in TAKEN_DOWN:
            found = link.get("replacements") or []
            self._line("Possible replacements")
            if not found:
                d.insert("end", "none found" + (f" ({link['replacements_checked']})" if link.get("replacements_checked")
                                                else ", not looked for yet: right-click, Find replacements now"), "label")
            for r in found[:6]:
                self._link(r["url"].split("://", 1)[-1], r["url"])
                d.insert("end", f" {r['how']}" + (f", {r['where']}" if r.get("where") else "") + "   ", "label")

    def _candidate_details(self, c: Candidate) -> None:
        d = self.details
        d.insert("end", c.title or "(no name)", "h")
        if c.game:
            d.insert("end", f"   {c.game}" + (f" ({c.platform})" if c.platform else ""))
        self._line("Repository")
        self._link(c.url, c.url)
        if not c.supported_host:
            d.insert("end", f"   {c.host} isn't a code host Quiver reads", "warn")
        elif c.forge:
            d.insert("end", f"   {c.host} is a {'Forgejo' if c.forge == 'forgejo' else 'Gitea'} server", "flag")
        facts = [f for f in (c.method, f"by {c.author}" if c.author else "") if f]
        if facts:
            d.insert("end", "   " + " · ".join(facts), "label")
        for s in c.sources:
            i = c.listings[s]
            self._line(EXTERNAL[s])
            if c.pages.get(s):
                self._link("page", c.pages[s])
                d.insert("end", "   ")
            bits = [f"release {i.version or '?'} on {i.released}" if i.released else "",
                    f"last push {i.pushed}" if i.pushed else "", f"listed {i.added}" if i.added else ""]
            bits += [f"{k.replace('_', ' ')} {v}" for k, v in i.extra.items()
                     if k in ("builds", "stars", "status", "completeness", "ai", "state", "runs_on")]
            x = i.extra
            if x.get("downloads"):
                bits.append(f"{int(x['downloads']):,} downloads")
            if x.get("players"):
                bits.append(f"played on {int(x['players']):,} PCs in 90 days")
            if x.get("reports"):
                bits.append(f"score {x.get('score')} from {int(x['reports']):,} reports")
            d.insert("end", " · ".join(b for b in bits if b))
            if i.source == "decompbuddy" and not i.extra.get("builds"):
                d.insert("end", "   no builds published", "warn")
        if not c.listed:
            self._line("Listed on")
            d.insert("end", "no listing site has it any more", "label")
        if c.in_quiver:
            self._line("In Quiver as")
            for q in c.in_quiver:
                self._link(q.title, q.page or q.url)
                d.insert("end", "   ")
            d.insert("end", "(you can take it off this list)", "flag")
        else:
            if c.same_app:
                self._line("Quiver likely has this app as")
                for q in c.same_app[:6]:
                    self._link(q.title, q.page or q.url)
                    d.insert("end", f" ({q.url.split('://', 1)[-1]}, same name and creator)   ", "label")
            others = [q for q in c.same_game if q not in c.same_app]
            if others:
                self._line("Other Quiver apps for this game")
                for q in others[:6]:
                    self._link(q.title, q.page or q.url)
                    d.insert("end", "   ")
        self._link_line(c.link)
        dec = c.decision
        if dec:
            self._line("Waiting for" if dec.get("list") == WAITING else "Blacklisted")
            what = reason_label(dec)
            d.insert("end", " · ".join(x for x in (what, dec.get("note", ""), f"since {dec.get('date', '')}",
                                                    f"by {dec['by']}" if dec.get("by") else "") if x))

    # ---- refresh -------------------------------------------------------------------------------------------------

    def log(self, message: str, error: bool = False) -> None:
        self.logbox.config(state="normal")
        self.logbox.insert("end", message + "\n", ("error",) if error else ())
        self.logbox.see("end")
        self.logbox.config(state="disabled")

    def refresh(self, full: bool = False) -> None:
        if self.refreshing:
            return
        if full and not messagebox.askyesno(
                "Full refresh", "Read everything again, ignoring what's cached? PlayPorted alone is about 2,100 pages "
                "and takes a few minutes."):
            return
        self.log("--- Full refresh started" if full else "--- Refresh started")
        self._start("refresh", "Refreshing…", lambda say: engine.refresh(self.store, say, full=full))

    def check_links(self, everything: bool = False, keys: list[str] | None = None) -> None:
        """Check links that are due, or every link, or the selected rows' (keys)."""
        if self.refreshing:
            return
        if everything and not messagebox.askyesno(
                "Check every link", "Check every project's link again, even ones checked lately? With a GitHub token "
                "that's a few hundred requests; without one, thousands of page loads."):
            return
        if keys is None and not self.store.tokens().get("github"):
            self.log("No GitHub token: GitHub links are checked one page at a time. Add a token in File → Settings → "
                     "API keys to check thousands in a minute.")
        self.log("--- Link check started")
        self._start("links", "Checking links…",
                    lambda say: engine.check_links(self.store, say, keys=keys, everything=everything))

    def _start(self, job: str, text: str, work_fn) -> None:
        self.refreshing = True
        self.job = job
        self.refresh_btn.config(state="disabled")
        self.status.config(text=text, style="TLabel")

        def work():
            try:
                result = work_fn(lambda m, d=0, t=0: self.events.put(("progress", m, d, t)))
                self.events.put(("done", result, 0, 0))
            except Exception as e:  # noqa: BLE001
                self.events.put(("failed", str(e), 0, 0))

        threading.Thread(target=work, daemon=True).start()
        self.root.after(100, self._poll)

    def _poll(self) -> None:
        last_progress = None
        try:
            while True:
                kind, a, done, total = self.events.get_nowait()
                if kind == "progress":
                    last_progress = (a, done, total)
                    if not NOISY.search(a):
                        self.log(a, error="failed" in a)
                elif kind in ("done", "failed"):
                    self.refreshing = False
                    self.refresh_btn.config(state="normal")
                    self.progress.stop()
                    self.progress.config(mode="determinate", value=0)
                    if kind == "failed":
                        self.log("ERROR " + a, error=True)
                        what = {"refresh": "Refresh", "update": "Update"}.get(self.job, "Link check")
                        self.status.config(text=f"{what} failed: {a}", style="Error.TLabel")
                        if self.job == "update":
                            messagebox.showerror("Update", f"The update failed:\n\n{a}", parent=self.root)
                        return
                    if self.job == "update":
                        self._install_update(a)
                        return
                    self.reload()
                    if self.job == "links":
                        down, qdown = len(self.result.taken_down), len(self.result.quiver_down)
                        found = a.get("replacements", 0)
                        live = a.get("live") or {}
                        text = (f"Links checked: {a.get('checked', 0)}. Taken down: {qdown} Quiver app(s), {down} listed "
                                f"project(s)." + (f" Possible replacements for {found}." if found else "")
                                + (f" Quiver: {live.get('archived', 0)} archived, {live.get('changed', 0)} repository "
                                   f"changed." if live.get("checked") else ""))
                        self.status.config(text=text, style="Error.TLabel" if qdown else "TLabel")
                        self.log(text)
                        return
                    for e in a.get("errors", []):
                        self.log("ERROR " + e, error=True)
                    took = sum(s.get("seconds", 0) for s in a.get("sources", {}).values())
                    self.log(f"Finished in {took:.0f} s. {a.get('new', 0)} project(s) seen for the first time.")
                    if not a.get("errors"):
                        self.status.config(text=f"Refreshed. {a.get('new', 0)} new project(s).", style="TLabel")
                    return
        except queue.Empty:
            pass
        if last_progress:
            msg, done, total = last_progress
            self.status.config(text=msg, style="TLabel")
            if total:
                self.progress.stop()
                self.progress.config(mode="determinate", maximum=total, value=done)
            else:
                self.progress.config(mode="indeterminate")
                self.progress.start(15)
        self.root.after(100, self._poll)

    # ---- export and settings -------------------------------------------------------------------------------------

    def _ask_path(self, name: str) -> str:
        return filedialog.asksaveasfilename(parent=self.root, defaultextension=".csv", initialfile=name,
                                            filetypes=[("CSV", "*.csv"), ("All files", "*.*")])

    def export_view(self) -> None:
        cur = self.current()
        if cur == "log":
            return
        self._fill(cur)
        if cur in QUIVER_TABS:
            items = [self.quiver_rows[i] for i in self.tables[cur].tree.get_children()]
            path = self._ask_path("quiver-catalog.csv")
            if path:
                n = engine.write_quiver_csv(path, items, self.result.links)
                self.status.config(text=f"Wrote {n} rows to {path}", style="TLabel")
            return
        rows = [self.candidates[k] for k in self.tables[cur].tree.get_children() if k in self.candidates]
        path = self._ask_path(f"{cur}.csv")
        if path:
            n = engine.write_candidates_csv(path, rows)
            self.status.config(text=f"Wrote {n} rows to {path}", style="TLabel")

    def export_all(self) -> None:
        path = self._ask_path("all-projects.csv")
        if path and self.result:
            n = engine.write_candidates_csv(path, self.result.candidates)
            self.status.config(text=f"Wrote {n} rows to {path}", style="TLabel")

    def export_quiver(self) -> None:
        path = self._ask_path("quiver-catalog.csv")
        if path and self.result:
            n = engine.write_quiver_csv(path, self.result.quiver, self.result.links)
            self.status.config(text=f"Wrote {n} rows to {path}", style="TLabel")

    # ---- updates -------------------------------------------------------------------------------------------------

    def check_updates(self, quiet: bool = True) -> None:
        """Look for a newer build on Releases in the background. `quiet`: on start, say nothing unless there's one."""
        token = self.store.tokens().get("github", "")

        def work():
            try:
                info = updater.latest(token)
                self.root.after(0, lambda: self._update_found(info, quiet))
            except updater.UpdateError as e:
                if not quiet:
                    self.root.after(0, lambda: messagebox.showwarning("Updates", str(e), parent=self.root))
            except Exception as e:  # noqa: BLE001
                if not quiet:
                    self.root.after(0, lambda: messagebox.showwarning("Updates", f"Couldn't check: {e}", parent=self.root))

        threading.Thread(target=work, daemon=True).start()

    def _update_found(self, info: dict | None, quiet: bool) -> None:
        mine = updater.current_build()
        if not info or info["build"] <= mine:
            if not quiet:
                messagebox.showinfo("Updates", f"You have the newest build: {updater.version_text()}.", parent=self.root)
            return
        self.update_info = info
        self.status.config(text=f"{info['title']} is available: Help → Check for updates.", style="TLabel")
        if quiet and self.store.settings.get("skip_build") == info["build"]:
            return
        notes = info["notes"].strip()
        notes = (notes[:700] + "…") if len(notes) > 700 else notes
        if not updater.frozen():
            if messagebox.askyesno("Update available", f"{info['title']} is available; this copy is running from "
                                   f"source.\n\n{notes}\n\nOpen the release page?", parent=self.root):
                open_url(info["page"])
            return
        if messagebox.askyesno("Update available", f"{info['title']} is available (you have Build {mine}).\n\n{notes}"
                               "\n\nDownload it and restart Quiver Scout now?", parent=self.root):
            self.download_update(info)
        else:
            self.store.settings["skip_build"] = info["build"]  # not asked again until a newer one
            self.store.save_settings()

    def download_update(self, info: dict) -> None:
        if self.refreshing:
            messagebox.showinfo("Update", "Wait for the refresh or link check to finish, then update.", parent=self.root)
            return
        import tempfile
        from pathlib import Path
        token = self.store.tokens().get("github", "")
        dest = Path(tempfile.gettempdir()) / f"QuiverScout-build-{info['build']}.exe"
        self.log(f"--- Downloading {info['title']}")
        self._start("update", "Downloading the update…", lambda say: updater.download(info, token, dest, say))

    def _install_update(self, new_exe) -> None:
        try:
            updater.install(new_exe)
        except Exception as e:  # noqa: BLE001
            messagebox.showerror("Update", f"Couldn't put the new build in place:\n\n{e}", parent=self.root)
            return
        self.quit()

    def import_shared(self) -> None:
        """Merge a decisions file or taken-down list someone sent into this computer's."""
        path = filedialog.askopenfilename(parent=self.root, title="Import decisions or taken-down list",
                                          filetypes=[("JSON", "*.json"), ("All files", "*.*")])
        if not path:
            return
        try:
            kind, counts = engine.import_file(self.store, path)
        except (OSError, ValueError) as e:
            messagebox.showerror("Import", f"Couldn't import it:\n\n{e}", parent=self.root)
            return
        what = "decisions" if kind == "decisions" else "taken-down links"
        self.reload()
        msg = (f"Imported {what} from {os.path.basename(path)}:\n\n"
               f"{counts['added']} new, {counts['updated']} newer than yours (replaced yours), "
               f"{counts['kept']} you already had or had newer (kept yours).")
        self.log(msg.replace("\n\n", " "))
        self.status.config(text=f"Imported {counts['added'] + counts['updated']} {what}.", style="TLabel")
        messagebox.showinfo("Import", msg, parent=self.root)

    def export_shared(self, which: str) -> None:
        name = "decisions.json" if which == "decisions" else "taken-down.json"
        path = filedialog.asksaveasfilename(parent=self.root, defaultextension=".json", initialfile=name,
                                            filetypes=[("JSON", "*.json"), ("All files", "*.*")])
        if path:
            n = engine.export_file(self.store, which, path)
            self.status.config(text=f"Wrote {n} entries to {path}", style="TLabel")

    def settings_dialog(self) -> None:
        s = self.store.settings
        w = tk.Toplevel(self.root)
        w.title("Settings")
        w.transient(self.root)
        w.resizable(False, False)
        place = s.get("window", {}).get("settings", "")
        if on_screen(self.root, place):
            w.geometry(place)

        def close() -> None:
            pos = f"+{w.winfo_x()}+{w.winfo_y()}"
            win = s.setdefault("window", {})
            if win.get("settings") != pos:
                win["settings"] = pos
                self.store.save_settings()
            w.destroy()

        w.protocol("WM_DELETE_WINDOW", close)
        outer = ttk.Frame(w, padding=12)
        outer.pack(fill="both")
        tabs = ttk.Notebook(outer)
        tabs.pack(fill="both")
        bold = self.c["fonts"]["bold"]

        def page(title: str) -> ttk.Frame:
            f = ttk.Frame(tabs, padding=14)
            tabs.add(f, text=title)
            return f

        def heading(parent, r: int, text: str, hint: str = "") -> int:
            ttk.Label(parent, text=text, font=bold).grid(row=r, column=0, columnspan=4, sticky="w", pady=(12 if r else 0, 0))
            if hint:
                ttk.Label(parent, text=hint, style="Muted.TLabel").grid(row=r + 1, column=0, columnspan=4, sticky="w")
            return r + 2

        # ---- Sites
        sites = page("Sites")
        r = heading(sites, 0, "Sites to read on Refresh")
        src = {}
        for sid, label in EXTERNAL.items():
            src[sid] = tk.BooleanVar(value=s["sources"].get(sid, True))
            ttk.Checkbutton(sites, text=label, variable=src[sid]).grid(row=r, column=0, columnspan=4, sticky="w", pady=1)
            r += 1
        full = tk.BooleanVar(value=bool(s.get("unbaked_full_scan")))
        ttk.Checkbutton(sites, text="unbaked: read every game's record, not only recent additions\n"
                        "(about 6,000 requests the first time; later only games with new pushes)",
                        variable=full).grid(row=r, column=0, columnspan=4, sticky="w", pady=(8, 0))
        clones = tk.BooleanVar(value=bool(s.get("include_clones")))
        ttk.Checkbutton(sites, text="Include clones: new games in the style of an old one, which OSGameClones,\n"
                        "Cloned Games and OSGL list many of (not ports of the game itself)",
                        variable=clones).grid(row=r + 1, column=0, columnspan=4, sticky="w", pady=(6, 0))
        links_on = tk.BooleanVar(value=bool(s.get("check_links", True)))
        ttk.Checkbutton(sites, text="Check links that are due after each refresh (needs a GitHub token,\n"
                        "see API keys; without one, use File → Check links)",
                        variable=links_on).grid(row=r + 2, column=0, columnspan=4, sticky="w", pady=(6, 0))

        def path_row(parent, r, label, value, hint, save, kind):
            r = heading(parent, r, label, hint)
            var = tk.StringVar(value=value)
            ttk.Entry(parent, textvariable=var, width=60).grid(row=r, column=0, sticky="we", pady=(4, 0))
            ask = filedialog.asksaveasfilename if save else filedialog.askopenfilename
            ttk.Button(parent, text="Browse…", command=lambda: var.set(
                ask(parent=w, filetypes=[kind, ("All files", "*.*")]) or var.get())).grid(row=r, column=1, padx=4)
            ttk.Button(parent, text="Clear", command=lambda: var.set("")).grid(row=r, column=2)
            return var, r + 1

        csv_var, _ = path_row(sites, r + 3, "Quiver catalog", s.get("quiver_csv", ""),
                              "Empty: read api.quiverlauncher.com. Or pick a CSV export with a URL column\n"
                              "(and title, game, slug columns if it has them).", False, ("CSV", "*.csv"))

        # ---- Sharing
        share = page("Sharing")
        dec_var, r = path_row(share, 0, "Decisions file (Waiting and Blacklist)", s.get("decisions_path", ""),
                              "Empty: decisions.json in the data folder. Point it at a shared folder or a git\n"
                              "checkout to share it with other maintainers. A new file starts with yours.",
                              True, ("JSON", "*.json"))
        down_var, r = path_row(share, r, "Taken-down list", s.get("taken_down_path", ""),
                               "Links found taken down or moved. Empty: taken-down.json in the data folder.\n"
                               "Shared, maintainers use each other's checks instead of checking again.",
                               True, ("JSON", "*.json"))
        r = heading(share, r, "Your name", "Put on your decisions and link checks, for maintainers sharing them.")
        name_var = tk.StringVar(value=s.get("your_name", ""))
        ttk.Entry(share, textvariable=name_var, width=30).grid(row=r, column=0, sticky="w", pady=(4, 0))

        # ---- API keys
        keys = page("API keys")
        tokens = self.store.tokens()
        token_vars = {}
        r = heading(keys, 0, "Tokens for checking links",
                    "Kept in tokens.json in the data folder, not in settings.json. Never share that file.\n"
                    "Neither token needs any permissions: public repositories are all it reads.")
        from .linkcheck import GITHUB_TOKEN_PAGE, GITLAB_TOKEN_PAGE
        for site, label, url, hint in (
                ("github", "GitHub token", GITHUB_TOKEN_PAGE,
                 "Checks 50 repositories a request instead of loading each page. Create a classic token\n"
                 "with no scopes ticked, and paste it here."),
                ("gitlab", "GitLab token", GITLAB_TOKEN_PAGE,
                 "Optional: raises GitLab's limits. The link opens the form with read_api ticked.")):
            r = heading(keys, r, label, hint)
            var = tk.StringVar(value=tokens.get(site, ""))
            token_vars[site] = var
            entry = ttk.Entry(keys, textvariable=var, width=48, show="•")
            entry.grid(row=r, column=0, sticky="we", pady=(4, 0))
            ttk.Button(keys, text="Create a token ↗", command=lambda u=url: open_url(u)).grid(row=r, column=1, padx=4)
            ttk.Button(keys, text="Show", command=lambda e=entry: e.configure(
                show="" if e.cget("show") else "•")).grid(row=r, column=2)
            result = ttk.Label(keys, text="", style="Muted.TLabel")
            result.grid(row=r + 1, column=0, columnspan=4, sticky="w")
            ttk.Button(keys, text="Test", command=lambda st=site, v=var, lab=result: self._test_token(st, v.get(), lab)
                       ).grid(row=r, column=3, padx=(4, 0))
            r += 2
        updates_on = tk.BooleanVar(value=bool(s.get("check_updates", True)))
        ttk.Checkbutton(keys, text="Check for a newer build when Quiver Scout starts",
                        variable=updates_on).grid(row=r, column=0, columnspan=4, sticky="w", pady=(10, 0))

        def save():
            s["sources"] = {k: v.get() for k, v in src.items()}
            s["unbaked_full_scan"] = full.get()
            s["include_clones"] = clones.get()
            s["check_links"] = links_on.get()
            s["check_updates"] = updates_on.get()
            s["quiver_csv"] = csv_var.get().strip()
            s["decisions_path"] = dec_var.get().strip()
            s["taken_down_path"] = down_var.get().strip()
            s["your_name"] = name_var.get().strip()
            self.store.save_settings()
            self.store.save_tokens({k: v.get() for k, v in token_vars.items()})
            close()
            self.reload()
            self.status.config(text="Settings saved. Refresh (F5) to read the sites with them.", style="TLabel")

        buttons = ttk.Frame(outer)
        buttons.pack(fill="x", pady=(12, 0))
        ttk.Button(buttons, text="Cancel", command=close).pack(side="right")
        ttk.Button(buttons, text="Save", style="Accent.TButton", command=save).pack(side="right", padx=6)
        theme.dark_title_bar(w, self.c["dark"])
        w.grab_set()

    def _test_token(self, site: str, token: str, label: ttk.Label) -> None:
        """Ask GitHub or GitLab who the token belongs to, in the background, and say so under it."""
        token = token.strip()
        if not token:
            label.configure(text="No token to test.", style="Muted.TLabel")
            return
        label.configure(text="Testing…", style="Muted.TLabel")

        def work():
            import json as _json
            import urllib.error
            import urllib.request
            if site == "github":
                req = urllib.request.Request("https://api.github.com/user", headers={
                    "Authorization": f"bearer {token}", "User-Agent": "quiver-scout"})
            else:
                req = urllib.request.Request("https://gitlab.com/api/v4/user", headers={
                    "PRIVATE-TOKEN": token, "User-Agent": "quiver-scout"})
            try:
                with urllib.request.urlopen(req, timeout=20) as resp:
                    who = _json.loads(resp.read())
                    left = resp.headers.get("X-RateLimit-Remaining")
                text = f"Works: {who.get('login') or who.get('username')}" + (f", {left} requests left this hour" if left else "")
                style = "TLabel"
            except urllib.error.HTTPError as e:
                text, style = f"Refused (HTTP {e.code}): check the token.", "Error.TLabel"
            except Exception as e:  # noqa: BLE001
                text, style = f"Couldn't ask: {e}", "Error.TLabel"
            self.root.after(0, lambda: label.winfo_exists() and label.configure(text=text, style=style))

        threading.Thread(target=work, daemon=True).start()


def run(data_dir: str | None = None) -> int:
    root = tk.Tk()
    App(root, Store(data_dir))
    root.mainloop()
    return 0
