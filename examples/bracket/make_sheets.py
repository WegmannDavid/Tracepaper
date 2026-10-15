#!/usr/bin/env python3
"""Example: an L-bracket drawn as two Tracepaper sheets.

The constants below stand in for a real CAD model. Run this script to (re)generate
section-A.svg and top.svg next to it, then open them with `tracepaper view`.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # run from a checkout without installing

from tracepaper.sheet import PathBuilder, Sheet, fmt  # noqa: E402

HERE = Path(__file__).resolve().parent
SOURCE = "examples/bracket/make_sheets.py"
REGENERATE = "python3 examples/bracket/make_sheets.py"

# The model, in mm. The base lies on the XY plane; the upright rises along z at x = 0..T.
LENGTH = 60   # base, along x
WIDTH = 30    # base, along y
HEIGHT = 40   # upright, along z
T = 5         # wall thickness of base and upright
FILLET = 3    # inner fillet between base and upright
HOLE_X, HOLE_Y, HOLE_D = 40, 15, 6


def section_a() -> Sheet:
    """Section A-A: cut at y = HOLE_Y, looking along +y. Sheet (x, y) is model (x, z)."""
    sheet = Sheet("Bracket, section A-A", source=SOURCE, pipeline="python", regenerate=REGENERATE,
                  kind="section", origin=(0, HOLE_Y, 0), normal=(0, -1, 0), xdir=(1, 0, 0))
    r = HOLE_D / 2
    upright = (PathBuilder().move(0, 0).line(HOLE_X - r, 0).line(HOLE_X - r, T).line(T + FILLET, T)
               .arc(T, T + FILLET, FILLET, ccw=False).line(T, HEIGHT).line(0, HEIGHT).close())
    base_end = PathBuilder().move(HOLE_X + r, 0).line(LENGTH, 0).line(LENGTH, T).line(HOLE_X + r, T).close()

    hatch = sheet.layer("Hatch")
    hatch.path("hatch-upright", upright)
    hatch.path("hatch-base-end", base_end)

    outline = sheet.layer("Outline")
    outline.path("cut-upright", upright, source="base and upright, left of the hole")
    outline.path("cut-base-end", base_end, source="base, right of the hole")

    center = sheet.layer("Center")
    center.line("hole-axis", (HOLE_X, -3), (HOLE_X, T + 3), source="mount hole")

    construction = sheet.layer("Construction", visible=False)
    construction.circle("fillet-circle", (T + FILLET, T + FILLET), FILLET, source="inner fillet")

    dims = sheet.layer("Dimensions")
    dims.linear("d-length", (0, 0), (LENGTH, 0), fmt(LENGTH), dir="x", offset=-16, value=LENGTH,
                refs="cut-upright cut-base-end", source="LENGTH")
    dims.linear("d-hole-x", (0, 0), (HOLE_X, 0), fmt(HOLE_X), dir="x", offset=-9, value=HOLE_X,
                refs="cut-upright hole-axis", source="HOLE_X")
    dims.linear("d-height", (0, 0), (0, HEIGHT), fmt(HEIGHT), dir="y", offset=9, value=HEIGHT,
                refs="cut-upright", source="HEIGHT")
    dims.linear("d-base-t", (LENGTH, 0), (LENGTH, T), fmt(T), dir="y", offset=-7, value=T,
                refs="cut-base-end", source="T")
    dims.linear("d-upright-t", (0, HEIGHT), (T, HEIGHT), fmt(T), dir="x", offset=6, value=T,
                refs="cut-upright", source="T")
    dims.linear("d-hole", (HOLE_X - r, T), (HOLE_X + r, T), f"⌀{fmt(HOLE_D)}", dir="x", offset=5,
                type="diameter", value=HOLE_D, refs="hole-axis", source="HOLE_D")
    dims.radius("d-fillet", (T + FILLET, T + FILLET), FILLET, angle=225, offset=-13,
                refs="cut-upright", source="FILLET")

    cons = sheet.layer("Constraints")
    cons.symbol("c-square", (0, 0), "⊥", offset=(-4, -4), type="perpendicular", refs="cut-upright")
    cons.symbol("c-equal-upright", (T, 30), "=", offset=(3, 0), type="equal", refs="d-upright-t d-base-t")
    cons.symbol("c-equal-base", (52, T), "=", offset=(0, 3), type="equal", refs="d-upright-t d-base-t")
    return sheet


def top_view() -> Sheet:
    """Top view, looking down the z axis. Sheet (x, y) is model (x, y)."""
    sheet = Sheet("Bracket, top view", source=SOURCE, pipeline="python", regenerate=REGENERATE,
                  kind="projection", origin=(0, 0, 0), normal=(0, 0, 1), xdir=(1, 0, 0))
    r = HOLE_D / 2

    outline = sheet.layer("Outline")
    outline.polyline("base", [(0, 0), (LENGTH, 0), (LENGTH, WIDTH), (0, WIDTH)], closed=True, source="base")
    outline.line("upright-face", (T, 0), (T, WIDTH), source="upright, inner face")
    outline.line("fillet-edge", (T + FILLET, 0), (T + FILLET, WIDTH), stroke_width=0.25,
                 source="inner fillet, tangent edge")
    outline.circle("hole", (HOLE_X, HOLE_Y), r, source="mount hole")

    center = sheet.layer("Center")
    center.line("hole-axis-x", (HOLE_X - r - 3, HOLE_Y), (HOLE_X + r + 3, HOLE_Y), source="mount hole")
    center.line("hole-axis-y", (HOLE_X, HOLE_Y - r - 3), (HOLE_X, HOLE_Y + r + 3), source="mount hole")

    cut = sheet.layer("Section lines")
    cut.line("cut-a", (-6, HOLE_Y), (LENGTH + 6, HOLE_Y), source="plane of section A-A")
    cut.symbol("cut-a-left", (-6, HOLE_Y), "A", offset=(-3, 0), type="section")
    cut.symbol("cut-a-right", (LENGTH + 6, HOLE_Y), "A", offset=(3, 0), type="section")

    dims = sheet.layer("Dimensions")
    dims.linear("d-length", (0, 0), (LENGTH, 0), fmt(LENGTH), dir="x", offset=-10, value=LENGTH,
                refs="base", source="LENGTH")
    dims.linear("d-width", (LENGTH, 0), (LENGTH, WIDTH), fmt(WIDTH), dir="y", offset=-20, value=WIDTH,
                refs="base", source="WIDTH")
    dims.linear("d-hole-x", (0, WIDTH), (HOLE_X, HOLE_Y), fmt(HOLE_X), dir="x", offset=8, value=HOLE_X,
                refs="base hole", source="HOLE_X")
    dims.linear("d-hole-y", (LENGTH, 0), (HOLE_X, HOLE_Y), fmt(HOLE_Y), dir="y", offset=-12, value=HOLE_Y,
                refs="base hole", source="HOLE_Y")
    dims.diameter("d-hole", (HOLE_X, HOLE_Y), r, angle=50, offset=7, refs="hole", source="HOLE_D")
    return sheet


if __name__ == "__main__":
    for name, sheet in (("section-A.svg", section_a()), ("top.svg", top_view())):
        print(sheet.save(HERE / name))
