"""One name for each platform and method, whichever way a site writes it: ps2, PS2 and PlayStation 2 are all
"PlayStation 2"; gba and Game Boy Advance are "Game Boy Advance"."""

from __future__ import annotations

import re

# Canonical name -> the other ways sites write it (compared lowercase, letters and digits only).
PLATFORMS = {
    "PC": ["pc", "pc windows", "windows", "win", "pc (windows)"],
    "DOS": ["dos", "ms-dos", "ibm pc", "ibm pc (ms-dos)", "pc booter"],
    "Mac": ["mac", "macos", "classic mac os", "osx", "mac os"],
    "Linux": ["linux"],
    "Web": ["web", "browser", "web/flash", "adobe flash", "flash", "html5"],
    "Mobile": ["mobile", "java phone", "j2me", "ipod"],
    "Android": ["android"],
    "iOS": ["ios"],
    "Apple TV": ["apple tv", "apple-tv", "tvos"],
    "Arcade": ["arcade", "mame"],
    "NES": ["nes", "famicom", "nintendo entertainment system"],
    "SNES": ["snes", "super nintendo", "super famicom", "super nintendo entertainment system"],
    "Nintendo 64": ["n64", "nintendo 64"],
    "GameCube": ["gc", "gcn", "gamecube", "nintendo gamecube"],
    "Wii": ["wii", "nintendo wii"],
    "Wii U": ["wiiu", "wii u", "nintendo wii u"],
    "Switch": ["switch", "nintendo switch", "ns"],
    "Switch 2": ["switch2", "switch 2", "nintendo switch 2", "ns2"],
    "Game Boy": ["gb", "game boy", "gameboy"],
    "Game Boy Color": ["gbc", "game boy color", "gameboy color"],
    "Game Boy Advance": ["gba", "game boy advance", "gameboy advance"],
    "Nintendo DS": ["ds", "nds", "nintendo ds"],
    "Nintendo 3DS": ["3ds", "nintendo 3ds"],
    "Virtual Boy": ["vb", "virtual boy"],
    "PlayStation": ["ps", "ps1", "psx", "playstation", "playstation 1", "sony playstation"],
    "PlayStation 2": ["ps2", "playstation 2"],
    "PlayStation 3": ["ps3", "playstation 3"],
    "PlayStation 4": ["ps4", "playstation 4"],
    "PlayStation 5": ["ps5", "playstation 5"],
    "PSP": ["psp", "playstation portable"],
    "PS Vita": ["vita", "psvita", "ps vita", "playstation vita"],
    "Xbox": ["xbox", "og xbox", "original xbox"],
    "Xbox 360": ["x360", "xbox 360", "xbox360"],
    "Xbox One": ["xone", "xbox one"],
    "Genesis / Mega Drive": ["gen", "genesis", "mega drive", "megadrive", "sega genesis", "sega mega drive", "md"],
    "Master System": ["sms", "master system", "sega master system"],
    "Game Gear": ["gg", "game gear", "sega game gear"],
    "Sega CD": ["segacd", "sega cd", "mega cd"],
    "32X": ["32x", "s32x", "sega 32x"],
    "Sega CD / 32X": ["sega cd/32x"],
    "Saturn": ["saturn", "sega saturn"],
    "Dreamcast": ["dc", "dreamcast", "sega dreamcast"],
    "3DO": ["3do"],
    "CD-i": ["cdi", "cd-i"],
    "Neo Geo": ["neo geo", "neogeo"],
    "PC Engine": ["pc engine", "pce", "turbografx-16", "turbografx 16", "tg16"],
    "PC-98": ["pc-98", "pc98", "pc-98/pc-88"],
    "X68000": ["x68000"],
    "FM Towns": ["fm towns"],
    "MSX": ["msx"],
    "Amiga": ["ami", "amiga"],
    "Atari 2600": ["atari 2600", "2600"],
    "Atari 8-bit": ["atari 8-bit", "atari 8bit"],
    "Atari ST": ["atari st"],
    "Atari Jaguar": ["jaguar", "atari jaguar"],
    "Atari Lynx": ["lynx", "atari lynx"],
    "Atari": ["atari"],
    "Commodore 64": ["c64", "commodore 64", "commodore 64/8-bit"],
    "ZX Spectrum": ["zx spectrum", "zx-spectrum"],
    "Amstrad CPC": ["amstrad cpc"],
    "Apple II": ["apple ii", "apple-ii"],
    "BBC Micro": ["bbc micro"],
    "Other": ["other", "others", "none"],
}

METHODS = {
    "Recomp": ["recomp", "recompilation", "recompiled"],
    "Static recomp": ["static recomp", "static recompilation"],
    "Decomp": ["decomp", "decompilation", "decompiled"],
    "Port": ["port"],
    "Source port": ["source port"],
    "Remake": ["remake", "reimplementation"],
    "Source release": ["source release", "official"],
    "Game": ["game"],
    "Tool": ["tool"],
    "Emulator": ["emulator"],
}


def _key(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", text.lower())


def _index(table: dict[str, list[str]]) -> dict[str, str]:
    out = {}
    for name, aliases in table.items():
        for a in [name, *aliases]:
            out[_key(a)] = name
    return out


_PLATFORM = _index(PLATFORMS)
_METHOD = _index(METHODS)


def _tidy(word: str) -> str:
    """A name no table knows: short lowercase ones are probably abbreviations (ngp -> NGP), longer ones words."""
    if word.islower():
        return word.upper() if len(word) <= 4 else word[0].upper() + word[1:]
    return word


def platform(text: str) -> str:
    """'ps2, PlayStation 2, n64' -> 'PlayStation 2, Nintendo 64' (each one once)."""
    out: list[str] = []
    for part in re.split(r",\s*", text or ""):
        part = part.strip()
        if not part:
            continue
        name = _PLATFORM.get(_key(part), _tidy(part))
        if name not in out:
            out.append(name)
    return ", ".join(out)


def method(text: str) -> str:
    out: list[str] = []
    for part in re.split(r",\s*", text or ""):
        part = part.strip()
        if part:
            name = _METHOD.get(_key(part), part[0].upper() + part[1:])
            if name not in out:
                out.append(name)
    return ", ".join(out)
