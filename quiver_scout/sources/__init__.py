from . import playported, quiver, recomplauncher, unbaked
from .base import Item

# The listing sites compared against Quiver, in display order: id -> label.
EXTERNAL = {
    "recomplauncher": "RecompLauncher",
    "unbaked": "unbaked",
    "playported": "PlayPorted",
}

__all__ = ["EXTERNAL", "Item", "playported", "quiver", "recomplauncher", "unbaked"]
