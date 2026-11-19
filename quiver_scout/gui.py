"""The maintainer window: lists of what's missing from Quiver, what it has, and what was blacklisted."""

from __future__ import annotations

import os
import queue
import subprocess
import sys
import threading
import tkinter as tk
import webbrowser
from tkinter import filedialog, messagebox, simpledialog, ttk

from . import __version__, engine
from .engine import Candidate, Comparison
from .sources import EXTERNAL, Item
from .store import Store

MISSING_COLUMNS = [
    ("title", "Project", 230),
    ("game", "Game", 220),
    ("platform", "Platform", 70),
    ("method", "Method", 110),
    ("sources", "Listed on", 170),
    ("url", "URL", 300),
    ("first_seen", "First seen", 110),
    ("same_game", "Game in Quiver", 110),
]
QUIVER_COLUMNS = [
    ("title", "App", 260),
    ("game", "Game", 240),
    ("url", "URL", 340),
    ("also_on", "Also listed on", 200),
    ("slug", "Slug", 200),
]
BLACKLIST_COLUMNS = [
    ("title", "Project", 240),
    ("game", "Game", 220),
    ("url", "URL", 320),
    ("reason", "Reason", 260),
    ("date", "Blacklisted", 90),
]


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


class Table(ttk.Frame):
    """A Treeview with scrollbars and click-to-sort headings."""

    def __init__(self, master, columns, on_select=None, on_activate=None, on_menu=None):
        super().__init__(master)
        self.columns = columns
        self.tree = ttk.Treeview(self, columns=[c[0] for c in columns], show="headings", selectmode="extended")
        for cid, label, width in columns:
            self.tree.heading(cid, text=label, command=lambda c=cid: self.sort_by(c))
            self.tree.column(cid, width=width, minwidth=50, stretch=cid in ("title", "game", "url", "reason"))
        ys = ttk.Scrollbar(self, orient="vertical", command=self.tree.yview)
        xs = ttk.Scrollbar(self, orient="horizontal", command=self.tree.xview)
        self.tree.configure(yscrollcommand=ys.set, xscrollcommand=xs.set)
        self.tree.grid(row=0, column=0, sticky="nsew")
        ys.grid(row=0, column=1, sticky="ns")
        xs.grid(row=1, column=0, sticky="ew")
        self.rowconfigure(0, weight=1)
        self.columnconfigure(0, weight=1)
        self.tree.tag_configure("new", font=("TkDefaultFont", 9, "bold"))
        self.tree.tag_configure("dim", foreground="#777777")
        self.sort_col, self.sort_desc = None, False
        if on_select:
            self.tree.bind("<<TreeviewSelect>>", lambda e: on_select())
        if on_activate:
            self.tree.bind("<Double-1>", lambda e: on_activate())
            self.tree.bind("<Return>", lambda e: on_activate())
        if on_menu:
            self.tree.bind("<Button-3>", on_menu)
            self.tree.bind("<Button-2>", on_menu)

    def fill(self, rows: list[tuple[str, tuple, tuple]]) -> None:
        """rows: (iid, values, tags)."""
        selected = set(self.tree.selection())
        self.tree.delete(*self.tree.get_children())
        for iid, values, tags in rows:
            self.tree.insert("", "end", iid=iid, values=values, tags=tags)
        if self.sort_col:
            self._sort()
        keep = [i for i in selected if self.tree.exists(i)]
        if keep:
            self.tree.selection_set(keep)
            self.tree.see(keep[0])

    def selection(self) -> list[str]:
        return list(self.tree.selection())

    def sort_by(self, col: str) -> None:
        self.sort_desc = not self.sort_desc if self.sort_col == col else False
        self.sort_col = col
        self._sort()

    def _sort(self) -> None:
        col = self.sort_col
        rows = [(str(self.tree.set(i, col)).lower(), i) for i in self.tree.get_children("")]
        rows.sort(reverse=self.sort_desc)
        for n, (_, iid) in enumerate(rows):
            self.tree.move(iid, "", n)
        for cid, label, _ in self.columns:
            arrow = (" ▼" if self.sort_desc else " ▲") if cid == col else ""
            self.tree.heading(cid, text=label + arrow)


class App:
    def __init__(self, root: tk.Tk, store: Store):
        self.root = root
        self.store = store
        self.result: Comparison | None = None
        self.candidates: dict[str, Candidate] = {}
        self.quiver_rows: dict[str, Item] = {}
        self.events: queue.Queue = queue.Queue()
        self.refreshing = False

        root.title("Quiver Scout")
        root.geometry("1400x820")
        root.minsize(900, 500)
        self._menu()
        self._layout()
        self._keys()
        self.reload()
        if not self.store.snapshot:
            self.log("No data yet. Press Refresh (F5) to read Quiver and the listing sites.")

    # ---- layout --------------------------------------------------------------------------------------------------

    def _menu(self) -> None:
        bar = tk.Menu(self.root)
        f = tk.Menu(bar, tearoff=False)
        f.add_command(label="Refresh", accelerator="F5", command=self.refresh)
        f.add_separator()
        f.add_command(label="Export this list as CSV…", accelerator="Ctrl+E", command=self.export_view)
        f.add_command(label="Export every project as CSV…", command=self.export_all)
        f.add_command(label="Export Quiver catalog as CSV…", command=self.export_quiver)
        f.add_separator()
        f.add_command(label="Settings…", command=self.settings_dialog)
        f.add_command(label="Open data folder", command=lambda: open_folder(str(self.store.dir)))
        f.add_separator()
        f.add_command(label="Quit", command=self.root.destroy)
        bar.add_cascade(label="File", menu=f)
        h = tk.Menu(bar, tearoff=False)
        h.add_command(label="Keyboard shortcuts", command=self.shortcuts)
        h.add_command(label="About", command=lambda: messagebox.showinfo(
            "Quiver Scout", f"Quiver Scout {__version__}\n\nFinds recomps and ports listed on other sites that "
            "Quiver Launcher doesn't have yet.\n\nData folder:\n" + str(self.store.dir)))
        bar.add_cascade(label="Help", menu=h)
        self.root.config(menu=bar)

    def _layout(self) -> None:
        top = ttk.Frame(self.root, padding=(8, 8, 8, 4))
        top.pack(fill="x")
        self.refresh_btn = ttk.Button(top, text="Refresh  (F5)", command=self.refresh)
        self.refresh_btn.pack(side="left")
        self.progress = ttk.Progressbar(top, length=220, mode="determinate")
        self.progress.pack(side="left", padx=8)
        self.status = ttk.Label(top, text="")
        self.status.pack(side="left", fill="x", expand=True)
        self.refreshed = ttk.Label(top, text="", foreground="#666666")
        self.refreshed.pack(side="right")

        filters = ttk.Frame(self.root, padding=(8, 0, 8, 4))
        filters.pack(fill="x")
        ttk.Label(filters, text="Search:").pack(side="left")
        self.search = tk.StringVar()
        self.search.trace_add("write", lambda *_: self.render())
        self.search_entry = ttk.Entry(filters, textvariable=self.search, width=36)
        self.search_entry.pack(side="left", padx=(4, 12))
        ttk.Label(filters, text="Listed on:").pack(side="left")
        self.source_vars = {}
        for sid, label in EXTERNAL.items():
            v = tk.BooleanVar(value=True)
            v.trace_add("write", lambda *_: self.render())
            self.source_vars[sid] = v
            ttk.Checkbutton(filters, text=label, variable=v).pack(side="left", padx=2)
        ttk.Separator(filters, orient="vertical").pack(side="left", fill="y", padx=8)
        self.new_only = tk.BooleanVar(value=False)
        self.new_only.trace_add("write", lambda *_: self.render())
        ttk.Checkbutton(filters, text="New in last refresh only", variable=self.new_only).pack(side="left", padx=2)
        self.hide_same_game = tk.BooleanVar(value=False)
        self.hide_same_game.trace_add("write", lambda *_: self.render())
        ttk.Checkbutton(filters, text="Hide games Quiver already has", variable=self.hide_same_game).pack(side="left", padx=2)
        self.multi_only = tk.BooleanVar(value=False)
        self.multi_only.trace_add("write", lambda *_: self.render())
        ttk.Checkbutton(filters, text="On 2+ sites", variable=self.multi_only).pack(side="left", padx=2)

        panes = ttk.PanedWindow(self.root, orient="vertical")
        panes.pack(fill="both", expand=True, padx=8)
        self.tabs = ttk.Notebook(panes)
        self.missing = Table(self.tabs, MISSING_COLUMNS, self.show_details, self.open_repo, self.context_menu)
        self.in_quiver = Table(self.tabs, QUIVER_COLUMNS, self.show_details, self.open_repo, self.context_menu)
        self.blacklisted = Table(self.tabs, BLACKLIST_COLUMNS, self.show_details, self.open_repo, self.context_menu)
        logframe = ttk.Frame(self.tabs)
        self.logbox = tk.Text(logframe, height=10, wrap="word", state="disabled")
        self.logbox.pack(fill="both", expand=True)
        self.tabs.add(self.missing, text="Missing")
        self.tabs.add(self.in_quiver, text="In Quiver")
        self.tabs.add(self.blacklisted, text="Blacklist")
        self.tabs.add(logframe, text="Log")
        self.tabs.bind("<<NotebookTabChanged>>", lambda e: (self._update_buttons(), self.show_details()))
        panes.add(self.tabs, weight=4)

        bottom = ttk.Frame(panes)
        panes.add(bottom, weight=1)
        actions = ttk.Frame(bottom, padding=(0, 6, 0, 4))
        actions.pack(fill="x")
        self.btn_open = ttk.Button(actions, text="Open repository  (Enter)", command=self.open_repo)
        self.btn_pages = ttk.Button(actions, text="Open listing pages  (Ctrl+Enter)", command=self.open_pages)
        self.btn_copy = ttk.Button(actions, text="Copy URL  (Ctrl+C)", command=self.copy_urls)
        self.btn_black = ttk.Button(actions, text="Blacklist…  (Del)", command=self.blacklist_selected)
        self.btn_unblack = ttk.Button(actions, text="Remove from blacklist  (Del)", command=self.unblacklist_selected)
        for b in (self.btn_open, self.btn_pages, self.btn_copy, self.btn_black, self.btn_unblack):
            b.pack(side="left", padx=(0, 6))
        self.count = ttk.Label(actions, text="")
        self.count.pack(side="right")
        self.details = tk.Text(bottom, height=7, wrap="word", state="disabled", relief="flat", padx=6, pady=4,
                               background=self.root.cget("background"), font="TkDefaultFont")
        self.details.pack(fill="both", expand=True)
        self.details.tag_configure("h", font=("TkDefaultFont", 11, "bold"))
        self.details.tag_configure("label", foreground="#666666")
        self.details.tag_configure("link", foreground="#1a5fb4", underline=True)
        self.details.tag_bind("link", "<Enter>", lambda e: self.details.config(cursor="hand2"))
        self.details.tag_bind("link", "<Leave>", lambda e: self.details.config(cursor=""))
        self._links = 0
        self._update_buttons()

    def _keys(self) -> None:
        r = self.root
        r.bind("<F5>", lambda e: self.refresh())
        r.bind("<Control-f>", lambda e: (self.search_entry.focus_set(), self.search_entry.select_range(0, "end")))
        r.bind("<Control-e>", lambda e: self.export_view())
        r.bind("<Control-Return>", lambda e: self.open_pages())
        r.bind("<Escape>", lambda e: self.search.set(""))
        for t in (self.missing, self.in_quiver, self.blacklisted):
            t.tree.bind("<Delete>", lambda e: self.delete_key())
            t.tree.bind("<Control-c>", lambda e: self.copy_urls())
            t.tree.bind("<Control-a>", lambda e, t=t: t.tree.selection_set(t.tree.get_children()))

    def shortcuts(self) -> None:
        messagebox.showinfo("Keyboard shortcuts", "\n".join([
            "F5\tRefresh every source",
            "Enter / double-click\tOpen repository",
            "Ctrl+Enter\tOpen the listing site pages",
            "Ctrl+C\tCopy URLs of the selected rows",
            "Delete\tBlacklist (or un-blacklist on the Blacklist tab)",
            "Ctrl+A\tSelect every row in the list",
            "Ctrl+F\tSearch",
            "Esc\tClear the search",
            "Ctrl+E\tExport the current list as CSV",
            "Right-click\tMore actions",
        ]))

    # ---- data ----------------------------------------------------------------------------------------------------

    def reload(self) -> None:
        self.result = engine.compare(self.store)
        self.candidates = {c.key: c for c in self.result.candidates}
        r = self.result
        if r.refreshed_at:
            prev = f" (previous: {r.previous_refresh})" if r.previous_refresh else ""
            self.refreshed.config(text=f"Last refresh: {r.refreshed_at}{prev}")
        if r.errors:
            self.status.config(text="Last refresh had errors, see the Log tab", foreground="#b00020")
        self.render()

    def _matches(self, text: str, *fields: str) -> bool:
        return all(any(word in (f or "").lower() for f in fields) for word in text.split())

    def render(self) -> None:
        r = self.result
        if not r:
            return
        text = self.search.get().strip().lower()
        sources = {s for s, v in self.source_vars.items() if v.get()}

        missing_rows = []
        for c in r.missing:
            if not sources.intersection(c.sources):
                continue
            if self.new_only.get() and not r.is_new(c):
                continue
            if self.hide_same_game.get() and c.same_game:
                continue
            if self.multi_only.get() and len(c.sources) < 2:
                continue
            if text and not self._matches(text, c.title, c.game, c.url, c.author, c.platform, c.method):
                continue
            seen = "" if c.first_seen == "initial" else c.first_seen
            values = (c.title, c.game, c.platform, c.method, ", ".join(EXTERNAL[s] for s in c.sources), c.url, seen,
                      f"yes ({len(c.same_game)})" if c.same_game else "")
            missing_rows.append((c.key, values, ("new",) if r.is_new(c) else ()))
        self.missing.fill(missing_rows)

        also: dict[str, list[str]] = {}
        for c in r.candidates:
            for q in c.in_quiver:
                also.setdefault(q.key, [])
                for s in c.sources:
                    if EXTERNAL[s] not in also[q.key]:
                        also[q.key].append(EXTERNAL[s])
        self.quiver_rows = {}
        quiver_rows = []
        for n, q in enumerate(r.quiver):
            if text and not self._matches(text, q.title, q.game, q.url, q.slug, q.author):
                continue
            iid = f"q{n}"
            self.quiver_rows[iid] = q
            quiver_rows.append((iid, (q.title, q.game, q.url, ", ".join(also.get(q.key, [])), q.slug), ()))
        self.in_quiver.fill(quiver_rows)

        black_rows = []
        listed = {c.key for c in r.candidates}
        for key, b in self.store.blacklist.items():
            if text and not self._matches(text, b.get("title"), b.get("game"), b.get("url"), b.get("reason")):
                continue
            c = self.candidates.get(key)
            tags = () if key in listed else ("dim",)
            black_rows.append((key, (b.get("title", ""), b.get("game", ""), b.get("url", ""), b.get("reason", ""),
                                     b.get("date", "")), tags))
        self.blacklisted.fill(black_rows)

        new = sum(1 for c in r.missing if r.is_new(c))
        self.tabs.tab(self.missing, text=f"Missing ({len(missing_rows)}" + (f" / {len(r.missing)})" if len(missing_rows) != len(r.missing) else ")"))
        self.tabs.tab(self.in_quiver, text=f"In Quiver ({len(r.quiver)})")
        self.tabs.tab(self.blacklisted, text=f"Blacklist ({len(self.store.blacklist)})")
        self.count.config(text=f"{len(r.candidates)} on other sites · {len(r.missing)} missing · {new} new")
        self.show_details()

    # ---- selection and actions -----------------------------------------------------------------------------------

    def current(self) -> str:
        return {0: "missing", 1: "quiver", 2: "blacklist"}.get(self.tabs.index(self.tabs.select()), "log")

    def _table(self) -> Table | None:
        return {"missing": self.missing, "quiver": self.in_quiver, "blacklist": self.blacklisted}.get(self.current())

    def selected(self) -> list[tuple[str, str, list[str], str]]:
        """(key, url, listing pages, title) for each selected row."""
        t = self._table()
        out = []
        for iid in t.selection() if t else []:
            if self.current() == "quiver":
                q = self.quiver_rows.get(iid)
                if q:
                    c = self.candidates.get(q.key)
                    out.append((q.key, q.url, [q.page] + (list(c.pages.values()) if c else []), q.title))
            else:
                c = self.candidates.get(iid)
                b = self.store.blacklist.get(iid, {})
                out.append((iid, c.url if c else b.get("url", ""), list(c.pages.values()) if c else [],
                            c.title if c else b.get("title", "")))
        return out

    def _update_buttons(self) -> None:
        cur = self.current()
        state = lambda on: "normal" if on else "disabled"  # noqa: E731
        for b in (self.btn_open, self.btn_pages, self.btn_copy):
            b.config(state=state(cur != "log"))
        self.btn_black.config(state=state(cur == "missing"))
        self.btn_unblack.config(state=state(cur == "blacklist"))

    def open_repo(self) -> None:
        rows = self.selected()
        if len(rows) > 10 and not messagebox.askyesno("Open links", f"Open {len(rows)} repositories in your browser?"):
            return
        for _, url, _, _ in rows:
            open_url(url)

    def open_pages(self) -> None:
        pages = [p for _, _, ps, _ in self.selected() for p in ps if p]
        if len(pages) > 10 and not messagebox.askyesno("Open links", f"Open {len(pages)} pages in your browser?"):
            return
        for p in pages:
            open_url(p)

    def copy_urls(self) -> None:
        urls = [url for _, url, _, _ in self.selected()]
        if urls:
            self.root.clipboard_clear()
            self.root.clipboard_append("\n".join(urls))
            self.status.config(text=f"Copied {len(urls)} URL(s)", foreground="")

    def delete_key(self) -> None:
        if self.current() == "missing":
            self.blacklist_selected()
        elif self.current() == "blacklist":
            self.unblacklist_selected()

    def blacklist_selected(self) -> None:
        if self.current() != "missing":
            return
        keys = self.missing.selection()
        cands = [self.candidates[k] for k in keys if k in self.candidates]
        if not cands:
            return
        what = cands[0].title or cands[0].url if len(cands) == 1 else f"{len(cands)} projects"
        reason = simpledialog.askstring("Blacklist", f"Blacklist {what}?\n\nReason (optional):", parent=self.root)
        if reason is None:
            return
        # Keep the cursor near where it was, for working down the list.
        children = self.missing.tree.get_children()
        after = [i for i in children[children.index(keys[-1]) + 1:] if i not in keys][:1] if keys[-1] in children else []
        engine.blacklist(self.store, cands, reason.strip())
        self.status.config(text=f"Blacklisted {what}", foreground="")
        self.render()
        if after and self.missing.tree.exists(after[0]):
            self.missing.tree.selection_set(after[0])
            self.missing.tree.focus(after[0])
            self.missing.tree.see(after[0])
        self.missing.tree.focus_set()

    def unblacklist_selected(self) -> None:
        keys = self.blacklisted.selection()
        if not keys:
            return
        engine.unblacklist(self.store, keys)
        self.reload()
        self.status.config(text=f"Removed {len(keys)} from the blacklist", foreground="")

    def context_menu(self, event) -> None:
        tree = event.widget
        row = tree.identify_row(event.y)
        if row and row not in tree.selection():
            tree.selection_set(row)
        m = tk.Menu(self.root, tearoff=False)
        m.add_command(label="Open repository", command=self.open_repo)
        m.add_command(label="Open listing pages", command=self.open_pages)
        m.add_command(label="Copy URL", command=self.copy_urls)
        if self.current() == "missing":
            m.add_separator()
            m.add_command(label="Blacklist…", command=self.blacklist_selected)
            sel = self.selected()
            if len(sel) == 1:
                c = self.candidates.get(sel[0][0])
                for q in (c.same_game if c else [])[:8]:
                    m.add_command(label=f"Open Quiver's repo for the same game: {q.title}", command=lambda u=q.url: open_url(u))
        elif self.current() == "blacklist":
            m.add_separator()
            m.add_command(label="Remove from blacklist", command=self.unblacklist_selected)
        m.tk_popup(event.x_root, event.y_root)

    # ---- details pane --------------------------------------------------------------------------------------------

    def _link(self, text: str, url: str) -> None:
        self._links += 1
        tag = f"link{self._links}"
        self.details.insert("end", text, ("link", tag))
        self.details.tag_bind(tag, "<Button-1>", lambda e, u=url: open_url(u))

    def show_details(self) -> None:
        d = self.details
        d.config(state="normal")
        d.delete("1.0", "end")
        sel = self.selected() if self.current() != "log" else []
        if len(sel) == 1:
            key = sel[0][0]
            c = self.candidates.get(key)
            if self.current() == "quiver":
                q = self.quiver_rows[self._table().selection()[0]]
                d.insert("end", q.title, "h")
                d.insert("end", f"   {q.game}\n")
                d.insert("end", "Repository: ", "label")
                self._link(q.url, q.url)
                d.insert("end", "\nQuiver page: ", "label")
                self._link(q.page, q.page)
                if q.author:
                    d.insert("end", f"\nDeveloper: {q.author}", "label")
                if c:
                    d.insert("end", "\nAlso listed on: ", "label")
                    for s, page in c.pages.items():
                        self._link(EXTERNAL[s], page)
                        d.insert("end", "  ")
            elif c or key in self.store.blacklist:
                b = self.store.blacklist.get(key)
                d.insert("end", (c.title if c else b.get("title")) or "(no name)", "h")
                game = c.game if c else b.get("game", "")
                if game:
                    d.insert("end", f"   {game}" + (f" ({c.platform})" if c and c.platform else ""))
                d.insert("end", "\nRepository: ", "label")
                self._link(sel[0][1], sel[0][1])
                if c:
                    facts = [f for f in (c.method, f"by {c.author}" if c.author else "", f"added {c.added}" if c.added else "") if f]
                    if facts:
                        d.insert("end", "   " + " · ".join(facts), "label")
                    d.insert("end", "\nListed on: ", "label")
                    for s in c.sources:
                        page = c.pages.get(s)
                        if page:
                            self._link(EXTERNAL[s], page)
                        else:
                            d.insert("end", EXTERNAL[s])
                        d.insert("end", "  ")
                    if c.same_game:
                        d.insert("end", "\nQuiver already has this game from another repository: ", "label")
                        for q in c.same_game[:6]:
                            self._link(q.title, q.url)
                            d.insert("end", "  ")
                    if c.in_quiver:
                        d.insert("end", "\nAlready in Quiver as: ", "label")
                        for q in c.in_quiver:
                            self._link(q.title, q.page or q.url)
                            d.insert("end", "  ")
                else:
                    d.insert("end", "\nNo listing site has it any more.", "label")
                if b:
                    d.insert("end", f"\nBlacklisted {b.get('date', '')}" + (f": {b['reason']}" if b.get("reason") else ""), "label")
        elif len(sel) > 1:
            d.insert("end", f"{len(sel)} selected", "h")
        d.config(state="disabled")

    # ---- refresh -------------------------------------------------------------------------------------------------

    def log(self, message: str) -> None:
        self.logbox.config(state="normal")
        self.logbox.insert("end", message + "\n")
        self.logbox.see("end")
        self.logbox.config(state="disabled")

    def refresh(self) -> None:
        if self.refreshing:
            return
        self.refreshing = True
        self.refresh_btn.config(state="disabled")
        self.status.config(text="Refreshing…", foreground="")
        self.log("--- Refresh started")

        def work():
            try:
                snap = engine.refresh(self.store, lambda m, d=0, t=0: self.events.put(("progress", m, d, t)))
                self.events.put(("done", snap, 0, 0))
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
                    if not a.split(":")[-1].strip().startswith(("port pages", "game records")):
                        self.log(a)
                elif kind == "done":
                    self.refreshing = False
                    self.refresh_btn.config(state="normal")
                    self.progress.config(mode="determinate", value=0)
                    for e in a.get("errors", []):
                        self.log("ERROR " + e)
                    self.reload()
                    new = sum(1 for c in self.result.missing if self.result.is_new(c))
                    if not a.get("errors"):
                        self.status.config(text=f"Refreshed. {new} new project(s) missing from Quiver.", foreground="")
                    return
                elif kind == "failed":
                    self.refreshing = False
                    self.refresh_btn.config(state="normal")
                    self.log("ERROR " + a)
                    self.status.config(text=f"Refresh failed: {a}", foreground="#b00020")
                    return
        except queue.Empty:
            pass
        if last_progress:
            msg, done, total = last_progress
            self.status.config(text=msg, foreground="")
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
        if cur == "quiver":
            items = [self.quiver_rows[i] for i in self.in_quiver.tree.get_children()]
            path = self._ask_path("quiver-catalog.csv")
            if path:
                n = engine.write_quiver_csv(path, items)
                self.status.config(text=f"Wrote {n} rows to {path}", foreground="")
            return
        table = self.blacklisted if cur == "blacklist" else self.missing
        rows = [self.candidates[k] for k in table.tree.get_children() if k in self.candidates]
        path = self._ask_path("blacklist.csv" if cur == "blacklist" else "missing.csv")
        if path:
            n = engine.write_candidates_csv(path, rows)
            self.status.config(text=f"Wrote {n} rows to {path}", foreground="")

    def export_all(self) -> None:
        path = self._ask_path("all-projects.csv")
        if path and self.result:
            n = engine.write_candidates_csv(path, self.result.candidates)
            self.status.config(text=f"Wrote {n} rows to {path}", foreground="")

    def export_quiver(self) -> None:
        path = self._ask_path("quiver-catalog.csv")
        if path and self.result:
            n = engine.write_quiver_csv(path, self.result.quiver)
            self.status.config(text=f"Wrote {n} rows to {path}", foreground="")

    def settings_dialog(self) -> None:
        s = self.store.settings
        w = tk.Toplevel(self.root)
        w.title("Settings")
        w.transient(self.root)
        w.resizable(False, False)
        body = ttk.Frame(w, padding=12)
        body.pack(fill="both")

        ttk.Label(body, text="Sites to read on Refresh", font=("TkDefaultFont", 10, "bold")).grid(row=0, column=0, columnspan=3, sticky="w")
        src = {}
        for n, (sid, label) in enumerate(EXTERNAL.items(), start=1):
            src[sid] = tk.BooleanVar(value=s["sources"].get(sid, True))
            ttk.Checkbutton(body, text=label, variable=src[sid]).grid(row=n, column=0, columnspan=3, sticky="w")
        row = len(EXTERNAL) + 1
        full = tk.BooleanVar(value=bool(s.get("unbaked_full_scan")))
        ttk.Checkbutton(body, text="unbaked: read every game's record, not only recent additions\n"
                        "(about 6,000 requests the first time; later only changed games)", variable=full).grid(
            row=row, column=0, columnspan=3, sticky="w", pady=(6, 0))

        def path_row(r, label, value, hint, save):
            ttk.Label(body, text=label, font=("TkDefaultFont", 10, "bold")).grid(row=r, column=0, columnspan=3, sticky="w", pady=(12, 0))
            ttk.Label(body, text=hint, foreground="#666666").grid(row=r + 1, column=0, columnspan=3, sticky="w")
            var = tk.StringVar(value=value)
            ttk.Entry(body, textvariable=var, width=60).grid(row=r + 2, column=0, sticky="we")
            ttk.Button(body, text="Browse…", command=lambda: var.set(
                (filedialog.asksaveasfilename if save else filedialog.askopenfilename)(
                    parent=w, filetypes=[("CSV" if not save else "JSON", "*.csv" if not save else "*.json"), ("All files", "*.*")])
                or var.get())).grid(row=r + 2, column=1, padx=4)
            ttk.Button(body, text="Clear", command=lambda: var.set("")).grid(row=r + 2, column=2)
            return var

        csv_var = path_row(row + 1, "Quiver catalog", s.get("quiver_csv", ""),
                           "Empty: read api.quiverlauncher.com. Or pick a CSV export with a URL column\n"
                           "(and title, game, slug columns if it has them).", False)
        black_var = path_row(row + 4, "Blacklist file", s.get("blacklist_path", ""),
                             "Empty: blacklist.json in the data folder. Point it at a shared folder\n"
                             "(Dropbox, a synced drive, a git checkout) to share it with other maintainers.", True)

        def save():
            s["sources"] = {k: v.get() for k, v in src.items()}
            s["unbaked_full_scan"] = full.get()
            s["quiver_csv"] = csv_var.get().strip()
            s["blacklist_path"] = black_var.get().strip()
            self.store.save_settings()
            w.destroy()
            self.reload()
            self.status.config(text="Settings saved. Refresh (F5) to read the sites with them.", foreground="")

        buttons = ttk.Frame(body)
        buttons.grid(row=row + 7, column=0, columnspan=3, sticky="e", pady=(14, 0))
        ttk.Button(buttons, text="Cancel", command=w.destroy).pack(side="right")
        ttk.Button(buttons, text="Save", command=save).pack(side="right", padx=6)
        w.grab_set()


def run(data_dir: str | None = None) -> int:
    root = tk.Tk()
    style = ttk.Style(root)
    if sys.platform.startswith("linux") and "clam" in style.theme_names():
        style.theme_use("clam")
    App(root, Store(data_dir))
    root.mainloop()
    return 0
