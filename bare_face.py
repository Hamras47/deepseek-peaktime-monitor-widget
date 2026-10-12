"""The price window, drawn with no background (see bare.py): mode, countdown, next change.

Sizes and positions are the card's own (styles.css, narrow-card layout), so switching
between the card and this face changes the background and nothing else.
"""

from __future__ import annotations

from bare import Drawing, Sketch

WIDTH, HEIGHT = 200.0, 98.0
OFF_PEAK = (94, 231, 155)
PEAK = (255, 154, 162)


def render(payload: dict, ink: str, scale: float) -> Drawing:
    s = Sketch(WIDTH, HEIGHT, scale, ink)
    colour = PEAK if payload.get("is_peak") else OFF_PEAK
    s.pulse_dot(13, 17, 3, colour)
    s.text(22, 20.6, str(payload.get("mode_label", "")), 10, "display", rgb=colour, spacing=1.2)
    countdown = payload.get("countdown") or {}
    size = 22 if countdown.get("long") else 28
    width = s.text(10, 58, str(countdown.get("text", "--")), size, "display")
    s.text(10 + width + 7, 58, str(countdown.get("suffix", "")), 11, alpha=215)
    upcoming = payload.get("next") or {}
    nxt = PEAK if upcoming.get("is_peak") else OFF_PEAK
    x = 10 + s.text(10, 88, "\u2192", 12, "semibold", rgb=nxt) + 6
    x += s.text(x, 88, f"{upcoming.get('phrase', '')} ", 11.5, alpha=215)
    x += s.text(x, 88, str(upcoming.get("clock", "")), 11.5, "semibold")
    s.text(x, 88, f" {upcoming.get('abbr', '')}", 11.5, alpha=215)
    return s.finish()
