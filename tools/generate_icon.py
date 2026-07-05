r"""
Generates the app icon (a stopwatch, in the app's indigo/emerald brand colors)
as a multi-resolution .ico, used both as:
  - assets/app_icon.ico       -> the .exe's file/taskbar icon (EmployeeShiftTracker.spec)
  - app/static/favicon.ico    -> the browser tab icon (linked from base.html)

Requires Pillow (not a runtime dependency of the app itself - only needed to
regenerate the icon, so it isn't in requirements.txt):

    .\.venv\Scripts\python.exe -m pip install pillow
    .\.venv\Scripts\python.exe tools\generate_icon.py
"""

from __future__ import annotations

import math
from pathlib import Path

from PIL import Image, ImageDraw

REPO_ROOT = Path(__file__).resolve().parent.parent

INDIGO = (79, 70, 229, 255)
EMERALD = (16, 185, 129, 255)
SLATE_700 = (51, 65, 85, 255)
SLATE_900 = (15, 23, 42, 255)
WHITE = (255, 255, 255, 255)

SIZE = 1024
CX, CY = SIZE // 2, SIZE // 2 + 60
RADIUS = 380


def point_on_clock(cx: float, cy: float, radius: float, angle_deg: float) -> tuple[float, float]:
    """(x, y) at `angle_deg` clockwise from 12 o'clock, `radius` from (cx, cy)."""
    rad = math.radians(angle_deg)
    return (cx + radius * math.sin(rad), cy - radius * math.cos(rad))


def draw_icon() -> Image.Image:
    img = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)

    # Top crown (the button used to start/stop a real stopwatch).
    crown_w, crown_h = 140, 130
    d.rounded_rectangle(
        [CX - crown_w // 2, CY - RADIUS - crown_h + 30, CX + crown_w // 2, CY - RADIUS + 40],
        radius=36, fill=EMERALD,
    )

    # Two side knobs, angled up-left / up-right.
    for angle in (-38, 38):
        kx, ky = point_on_clock(CX, CY, RADIUS - 10, angle)
        r = 70
        d.ellipse([kx - r, ky - r, kx + r, ky + r], fill=SLATE_700)

    # Main case (outer ring) and face.
    d.ellipse([CX - RADIUS, CY - RADIUS, CX + RADIUS, CY + RADIUS], fill=INDIGO)
    face_r = RADIUS - 70
    d.ellipse([CX - face_r, CY - face_r, CX + face_r, CY + face_r], fill=WHITE)

    # Tick marks every 30 degrees.
    for angle in range(0, 360, 30):
        outer = point_on_clock(CX, CY, face_r - 20, angle)
        inner = point_on_clock(CX, CY, face_r - 80, angle)
        d.line([outer, inner], fill=INDIGO, width=18)

    # Hands (classic "10:10" display angle) and center hub.
    hour = point_on_clock(CX, CY, face_r * 0.45, 300)
    minute = point_on_clock(CX, CY, face_r * 0.72, 60)
    d.line([(CX, CY), hour], fill=SLATE_900, width=34)
    d.line([(CX, CY), minute], fill=SLATE_900, width=26)
    hub_r = 34
    d.ellipse([CX - hub_r, CY - hub_r, CX + hub_r, CY + hub_r], fill=SLATE_900)

    return img


def main() -> None:
    img = draw_icon()
    sizes = [(16, 16), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]

    assets_dir = REPO_ROOT / "assets"
    assets_dir.mkdir(exist_ok=True)
    img.save(assets_dir / "app_icon.ico", sizes=sizes)

    static_dir = REPO_ROOT / "app" / "static"
    img.save(static_dir / "favicon.ico", sizes=sizes)

    print("Wrote assets/app_icon.ico and app/static/favicon.ico")


if __name__ == "__main__":
    main()
