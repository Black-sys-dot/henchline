"""The henchmen peeking at you from the README: each one rising up behind the edge of the page, its eyes darting
about, a blink now and then, and a giggle. Animated SVG (SMIL), which GitHub shows in a README image.

    python3 docs/peeks.py        (writes docs/peek_<agent>.svg; the henchmen are call/android/avatars.py's)
"""

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "call/android"))
from avatars import FLAVOURS, NAVY, PUPIL, shapes, svg  # noqa: E402

EYES = {"one": [(54, 47.5, 6.8, 3.2)], "two": [(45.5, 47.5, 4.8, 2.2), (62.5, 47.5, 4.8, 2.2)]}  # cx, cy, glass, pupil
EDGE = 62  # where the page's edge (the "desk") hides the rest of him
GIGGLES = {"claude": "hee hee!", "cursor": "pssst…", "codex": "tee hee", "handsfree": "boss-a!"}


def peek(name: str, body: str, light: str, glow: str, eyes: str, delay: float) -> str:
    drawn = [(kind, a) for kind, a in shapes(body, light, glow, eyes)
             if a.get("fill") not in (NAVY, PUPIL, "#FFFFFF")]  # no backdrop; pupils and glints move, drawn below
    look = (f'<animateTransform attributeName="transform" type="translate" dur="5s" begin="{delay}s" '
            'repeatCount="indefinite" values="0 0;0 0;-2.4 0.4;-2.4 0.4;2.4 0.4;2.4 0.4;0 0" '
            'keyTimes="0;0.2;0.3;0.5;0.6;0.85;1"/>')
    pupils = []
    for cx, cy, glass, r in EYES.get(eyes, []):
        lid = (f'<clipPath id="glass-{name}-{cx}"><circle cx="{cx}" cy="{cy}" r="{glass}"/></clipPath>'
               f'<rect clip-path="url(#glass-{name}-{cx})" x="{cx - glass}" y="{cy - glass}" width="{2 * glass}" '
               f'height="0" fill="{body}"><animate attributeName="height" dur="4.3s" begin="{delay}s" '
               f'repeatCount="indefinite" values="0;0;{2 * glass};0" keyTimes="0;0.92;0.96;1"/></rect>')
        pupils.append(f'<g>{look}<circle cx="{cx}" cy="{cy}" r="{r}" fill="{PUPIL}"/>'
                      f'<circle cx="{cx - r * 0.7}" cy="{cy - r * 0.7}" r="{r * 0.4}" fill="#FFFFFF"/></g>{lid}')
    if eyes == "visor":
        pupils.append(f'<g>{look}<rect x="50" y="45.5" width="8" height="4" rx="2" fill="{PUPIL}"/></g>')
    giggle = (f'<text x="84" y="34" font-family="Comic Sans MS, Chalkboard, sans-serif" font-size="9" fill="{light}" '
              f'opacity="0">{GIGGLES[name]}<animate attributeName="opacity" dur="6s" begin="{delay + 1.5}s" '
              'repeatCount="indefinite" values="0;0;1;1;0" keyTimes="0;0.6;0.65;0.85;0.9"/></text>')
    hands = "".join(f'<ellipse cx="{x}" cy="{EDGE + 0.6}" rx="4" ry="2.4" fill="{body}"/>' for x in (38, 70))  # gripping the edge
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="10 0 100 72" width="200" height="144">'
            f'<clipPath id="above-{name}"><rect x="0" y="0" width="120" height="{EDGE}"/></clipPath>'
            f'<g clip-path="url(#above-{name})"><g>'
            # up from behind the edge, then a giggle's bob every six seconds
            f'<animateTransform attributeName="transform" type="translate" dur="0.9s" begin="{delay}s" fill="freeze" '
            f'values="0 40;0 -3;0 0" keyTimes="0;0.7;1" additive="sum"/>'
            f'<animateTransform attributeName="transform" type="translate" dur="6s" begin="{delay + 1.5}s" '
            'repeatCount="indefinite" values="0 0;0 0;0 -2;0 0;0 -2;0 0" keyTimes="0;0.6;0.66;0.72;0.78;0.84" '
            'additive="sum"/>'
            f'<g>{svg(drawn, top=False)}{"".join(pupils)}</g></g></g>'
            f'{giggle}<rect x="10" y="{EDGE}" width="100" height="3" rx="1.5" fill="#8B93A1"/>{hands}</svg>')


def main() -> None:
    for i, (name, (body, light, glow, eyes)) in enumerate(FLAVOURS.items()):
        (HERE / f"peek_{name}.svg").write_text(peek(name, body, light, glow, eyes, delay=0.4 + i * 0.7))


if __name__ == "__main__":
    main()
