"""Draws Quiver Scout's icon: a blue arrowhead, flying up and to the right, on a dark rounded square.

    python tools/make_icon.py

writes quiver_scout/assets/icon.png (256 px, for the window) and icon.ico (16 to 256 px, for the exe). Needs Pillow,
which only this script does: the app itself doesn't.
"""

import math
from pathlib import Path

from PIL import Image, ImageDraw

S = 1024  # drawn large, then scaled down smoothly
OUT = Path(__file__).resolve().parent.parent / "quiver_scout" / "assets"

BACKGROUND = (30, 36, 51, 255)
HEAD = (79, 140, 255, 255)
SHAFT = (219, 230, 255, 255)
FLETCHING = (127, 178, 255, 255)


def turned(points, degrees=45, centre=(S / 2, S / 2 + 10)):
    a = math.radians(degrees)
    return [(centre[0] + x * math.cos(a) - y * math.sin(a), centre[1] + x * math.sin(a) + y * math.cos(a)) for x, y in points]


def draw() -> Image.Image:
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((24, 24, S - 24, S - 24), radius=210, fill=BACKGROUND)
    # The arrow, drawn pointing up around the centre, then turned 45 degrees to point up and right.
    shaft = [(-44, -230), (44, -230), (44, 330), (-44, 330)]  # its top runs under the head, so they join
    fletching_left = [(-44, 170), (-175, 290), (-175, 425), (-44, 305)]
    fletching_right = [(44, 170), (175, 290), (175, 425), (44, 305)]
    head = [(0, -445), (235, -85), (0, -165), (-235, -85)]
    for shape, colour in ((fletching_left, FLETCHING), (fletching_right, FLETCHING), (shaft, SHAFT), (head, HEAD)):
        d.polygon(turned(shape), fill=colour)
    return img


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    big = draw()
    big.resize((256, 256), Image.LANCZOS).save(OUT / "icon.png")
    big.save(OUT / "icon.ico", sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    print("wrote", OUT / "icon.png", "and", OUT / "icon.ico")


if __name__ == "__main__":
    main()
