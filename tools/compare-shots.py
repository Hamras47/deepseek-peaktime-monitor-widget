"""Compare two captures of the card and say whether the countdown moved.

    .venv\\Scripts\\python.exe tools\\compare-shots.py tmp\\clock-a.png tmp\\clock-b.png

Made for one question: is the countdown actually counting?  Capture the card with
tools\\capture-topmost.ps1, wait a few seconds, capture again, and run this.

A plain pixel diff is useless here: the card is translucent, so *any* change in the
desktop behind it changes every pixel of the card, and the status dot pulses by
design.  What is stable is the ink — the countdown is painted in solid white over a
dark card — so this isolates the bright pixels in the countdown band and compares
those masks only.  A wallpaper change cannot move them, and a number that did not
change cannot either.
"""

from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image, ImageChops

#: capture-window.ps1 grabs this many physical pixels of desktop around the card.
MARGIN = 28
#: Only near-white counts. The card is translucent, so a bright window showing
#: through it lands around 200-215; the countdown is painted #ffffff on top of
#: whatever that is.
INK = 245

if len(sys.argv) != 3:
    print(__doc__)
    raise SystemExit(2)


def countdown_ink(path: str) -> Image.Image:
    image = Image.open(Path(path)).convert("L")
    left, top = MARGIN, MARGIN
    width, height = image.size[0] - MARGIN * 2, image.size[1] - MARGIN * 2
    # The countdown band inside the card: below the badge, above the footer line.
    band = (
        left + 4,
        top + int(height * 0.26),
        left + width - 4,
        top + int(height * 0.74),
    )
    return image.crop(band).point(lambda value: 255 if value >= INK else 0)


first, second = (countdown_ink(path) for path in sys.argv[1:])
ink_a, ink_b = first.histogram()[255], second.histogram()[255]
moved = ImageChops.difference(first, second).histogram()[255]

print(f"ink pixels    {ink_a} -> {ink_b}")
print(f"ink moved     {moved} px")

if not ink_a or not ink_b:
    print("\n=> no ink in the countdown band - was the card actually in the shot?")
    raise SystemExit(2)

# A ticking clock repaints a good part of the glyphs.  The dot's pulse cannot, and
# neither can the wallpaper, because both are excluded by the ink mask.
verdict = moved >= 30
print(f"\n=> {'the countdown is counting' if verdict else 'the countdown is STUCK'}")
raise SystemExit(0 if verdict else 1)
