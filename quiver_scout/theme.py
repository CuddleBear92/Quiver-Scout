"""Light and dark looks for the Tk window, and which one the system uses."""

from __future__ import annotations

import os
import subprocess
import sys
import tkinter as tk
from tkinter import font as tkfont
from tkinter import ttk

LIGHT = {
    "bg": "#f3f3f5", "surface": "#ffffff", "raised": "#e9e9ed", "field": "#ffffff", "border": "#cfd0d6",
    "fg": "#1d1d1f", "muted": "#6b6b72", "dim": "#9a9aa0", "accent": "#2f6fde", "on_accent": "#ffffff",
    "select": "#cfe0ff", "on_select": "#0b1f44", "link": "#1a5fb4", "error": "#c8372b", "good": "#1b8a3e",
    "stripe": "#f7f8fa",
}
DARK = {
    "bg": "#1e1f22", "surface": "#2b2d31", "raised": "#35373c", "field": "#232428", "border": "#3d3f45",
    "fg": "#e3e3e6", "muted": "#9a9ca3", "dim": "#6f7178", "accent": "#5b8def", "on_accent": "#ffffff",
    "select": "#2f4a7a", "on_select": "#ffffff", "link": "#7fb2ff", "error": "#ff6b6b", "good": "#4cc274",
    "stripe": "#26282c",
}


def system_prefers_dark() -> bool:
    try:
        if sys.platform == "win32":
            import winreg

            with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                                r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize") as k:
                return winreg.QueryValueEx(k, "AppsUseLightTheme")[0] == 0
        if sys.platform == "darwin":
            out = subprocess.run(["defaults", "read", "-g", "AppleInterfaceStyle"], capture_output=True, text=True, timeout=2)
            return "dark" in out.stdout.lower()
        if "dark" in os.environ.get("GTK_THEME", "").lower():
            return True
        out = subprocess.run(["gsettings", "get", "org.gnome.desktop.interface", "color-scheme"],
                             capture_output=True, text=True, timeout=2)
        return "dark" in out.stdout.lower()
    except Exception:
        return False


def palette(mode: str) -> dict:
    dark = system_prefers_dark() if mode == "system" else mode == "dark"
    return {**(DARK if dark else LIGHT), "dark": dark}


def dark_title_bar(window: tk.Misc, dark: bool) -> None:
    """Windows 10/11 draws the title bar itself; ask it for the dark one."""
    if sys.platform != "win32":
        return
    try:
        import ctypes

        window.update_idletasks()
        hwnd = ctypes.windll.user32.GetParent(window.winfo_id())
        value = ctypes.c_int(1 if dark else 0)
        for attribute in (20, 19):  # DWMWA_USE_IMMERSIVE_DARK_MODE, and its number before Windows 10 20H1
            if ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, attribute, ctypes.byref(value), ctypes.sizeof(value)) == 0:
                break
    except Exception:
        pass


def fonts(root: tk.Misc) -> dict:
    base = tkfont.nametofont("TkDefaultFont")
    size = base.actual("size")
    out = {}
    for name, opts in {"bold": {"weight": "bold"}, "heading": {"weight": "bold", "size": abs(size) + 3},
                       "small": {"size": max(abs(size) - 1, 8)}}.items():
        try:
            f = tkfont.nametofont(f"Scout{name}")
        except tk.TclError:
            f = tkfont.Font(root, name=f"Scout{name}", **base.actual())
        f.configure(**opts)
        out[name] = f
    return out


def apply(root: tk.Tk, mode: str) -> dict:
    """Style every ttk widget, and the plain Tk ones through the option database. Returns the palette."""
    c = palette(mode)
    style = ttk.Style(root)
    style.theme_use("clam")
    f = fonts(root)

    style.configure(".", background=c["bg"], foreground=c["fg"], fieldbackground=c["field"], bordercolor=c["border"],
                    lightcolor=c["bg"], darkcolor=c["bg"], troughcolor=c["raised"], selectbackground=c["select"],
                    selectforeground=c["on_select"], insertcolor=c["fg"], focuscolor=c["accent"])
    style.configure("TFrame", background=c["bg"])
    style.configure("Card.TFrame", background=c["surface"])
    style.configure("TLabel", background=c["bg"], foreground=c["fg"])
    style.configure("Muted.TLabel", foreground=c["muted"])
    style.configure("Error.TLabel", foreground=c["error"])
    style.configure("Card.TLabel", background=c["surface"])
    style.configure("TButton", background=c["raised"], foreground=c["fg"], bordercolor=c["border"],
                    lightcolor=c["raised"], darkcolor=c["raised"], padding=(10, 4), focusthickness=1)
    style.map("TButton",
              background=[("disabled", c["bg"]), ("pressed", c["select"]), ("active", c["border"])],
              foreground=[("disabled", c["dim"])],
              lightcolor=[("active", c["border"])], darkcolor=[("active", c["border"])])
    style.configure("Accent.TButton", background=c["accent"], foreground=c["on_accent"], lightcolor=c["accent"],
                    darkcolor=c["accent"], bordercolor=c["accent"])
    style.map("Accent.TButton", background=[("disabled", c["raised"]), ("active", c["link"])],
              foreground=[("disabled", c["dim"])])
    style.configure("TEntry", fieldbackground=c["field"], foreground=c["fg"], bordercolor=c["border"],
                    lightcolor=c["field"], darkcolor=c["field"], insertcolor=c["fg"], padding=3)
    style.map("TEntry", bordercolor=[("focus", c["accent"])], lightcolor=[("focus", c["accent"])])
    for w in ("TCheckbutton", "TRadiobutton"):
        style.configure(w, background=c["bg"], foreground=c["fg"], indicatorbackground=c["field"],
                        indicatorforeground=c["fg"], indicatormargin=(0, 0, 6, 0), upperbordercolor=c["border"],
                        lowerbordercolor=c["border"])
        style.map(w, background=[("active", c["bg"])], indicatorbackground=[("selected", c["accent"]), ("active", c["raised"])],
                  indicatorforeground=[("selected", c["on_accent"])], foreground=[("disabled", c["dim"])])
    style.configure("Treeview", background=c["surface"], fieldbackground=c["surface"], foreground=c["fg"],
                    bordercolor=c["border"], lightcolor=c["surface"], darkcolor=c["surface"],
                    rowheight=max(22, f["bold"].metrics("linespace") + 6))
    style.map("Treeview", background=[("selected", c["select"])], foreground=[("selected", c["on_select"])])
    style.configure("Treeview.Heading", background=c["raised"], foreground=c["fg"], bordercolor=c["border"],
                    lightcolor=c["raised"], darkcolor=c["raised"], relief="flat", padding=(6, 4))
    style.map("Treeview.Heading", background=[("active", c["border"])])
    style.configure("TNotebook", background=c["bg"], bordercolor=c["border"], tabmargins=(0, 4, 0, 0))
    style.configure("TNotebook.Tab", background=c["bg"], foreground=c["muted"], bordercolor=c["border"],
                    lightcolor=c["bg"], darkcolor=c["bg"], padding=(12, 5))
    style.map("TNotebook.Tab", background=[("selected", c["surface"]), ("active", c["raised"])],
              foreground=[("selected", c["fg"])], lightcolor=[("selected", c["surface"])])
    for orient in ("Vertical", "Horizontal"):
        style.configure(f"{orient}.TScrollbar", background=c["raised"], troughcolor=c["bg"], bordercolor=c["bg"],
                        arrowcolor=c["muted"], lightcolor=c["raised"], darkcolor=c["raised"], gripcount=0)
        style.map(f"{orient}.TScrollbar", background=[("active", c["border"])])
    style.configure("Horizontal.TProgressbar", background=c["accent"], troughcolor=c["raised"], bordercolor=c["bg"],
                    lightcolor=c["accent"], darkcolor=c["accent"])
    style.configure("TPanedwindow", background=c["bg"])
    style.configure("Sash", background=c["border"], sashthickness=5)
    style.configure("TSeparator", background=c["border"])
    style.configure("TCombobox", fieldbackground=c["field"], background=c["raised"], foreground=c["fg"],
                    arrowcolor=c["fg"], bordercolor=c["border"])
    style.configure("TMenubutton", background=c["raised"], foreground=c["fg"], bordercolor=c["border"],
                    lightcolor=c["raised"], darkcolor=c["raised"], arrowcolor=c["fg"], padding=(8, 3))
    style.map("TMenubutton", background=[("active", c["border"])])
    # The window's own menu bar: flat labels with no arrow, lit up under the pointer.
    style.configure("Menubar.TFrame", background=c["surface"])
    style.layout("Menubar.TMenubutton", [("Menubutton.border", {"sticky": "nswe", "children": [
        ("Menubutton.padding", {"sticky": "nswe", "children": [("Menubutton.label", {"sticky": "nswe"})]})]})])
    style.configure("Menubar.TMenubutton", background=c["surface"], foreground=c["fg"], borderwidth=0,
                    relief="flat", padding=(10, 3))
    style.map("Menubar.TMenubutton", background=[("active", c["select"]), ("pressed", c["select"])],
              foreground=[("active", c["on_select"])])

    root.configure(background=c["bg"])
    for pattern, value in {
        "*Menu.background": c["surface"], "*Menu.foreground": c["fg"], "*Menu.activeBackground": c["select"],
        "*Menu.activeForeground": c["on_select"], "*Menu.selectColor": c["fg"], "*Menu.relief": "flat",
        "*Menu.borderWidth": 1, "*Menu.activeBorderWidth": 0,
        "*Text.background": c["surface"], "*Text.foreground": c["fg"], "*Text.insertBackground": c["fg"],
        "*Text.selectBackground": c["select"], "*Text.selectForeground": c["on_select"],
        "*Toplevel.background": c["bg"], "*TCombobox*Listbox.background": c["surface"],
        "*TCombobox*Listbox.foreground": c["fg"],
    }.items():
        root.option_add(pattern, value)
    dark_title_bar(root, c["dark"])
    return {**c, "fonts": f}


def style_menu(menu: tk.Menu, c: dict) -> None:
    """Menus made before a theme change keep their old colors; this recolors one (and its submenus)."""
    try:
        menu.configure(background=c["surface"], foreground=c["fg"], activebackground=c["select"],
                       activeforeground=c["on_select"], selectcolor=c["fg"])
        last = menu.index("end")
        for i in range(0 if last is None else last + 1):
            if menu.type(i) == "cascade":
                style_menu(menu.nametowidget(menu.entrycget(i, "menu")), c)
    except tk.TclError:
        pass
