from . import clonedgames, decompbuddy, osgameclones, playported, quiver, recomplauncher, trilarion, unbaked
from .base import Context, Item

# The listing sites compared against Quiver, in display order: id -> label.
EXTERNAL = {
    "recomplauncher": "RecompLauncher",
    "decompbuddy": "Decomp Buddy",
    "unbaked": "unbaked",
    "playported": "PlayPorted",
    "osgameclones": "OSGameClones",
    "clonedgames": "Cloned Games",
    "trilarion": "OSGL",
}

__all__ = ["EXTERNAL", "Context", "Item", "clonedgames", "decompbuddy", "osgameclones", "playported", "quiver",
           "recomplauncher", "trilarion", "unbaked"]
