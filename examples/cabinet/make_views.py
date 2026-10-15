#!/usr/bin/env python3
"""Example: a small cabinet whose views have nested groups, for the viewer's tree.

Writes cabinet.glb (the assembly in 3D, door hidden at first) and section-A.svg
(a vertical cut through its middle) next to this file. Both views name the parts
the same way: Carcass / top, Shelves / shelf 1, Door / door panel, ...
Needs CadQuery.
"""
import sys
from pathlib import Path

import cadquery as cq

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # run from a checkout without installing

import tracepaper.model  # noqa: E402
from tracepaper.sheet import HATCH, PathBuilder, Sheet  # noqa: E402

HERE = Path(__file__).resolve().parent
SOURCE = "examples/cabinet/make_views.py"
REGENERATE = "python3 examples/cabinet/make_views.py"

# The model, in mm: x across, y from the front (y = 0) to the back, z up.
W, D, H = 600, 350, 800
T = 18                      # board thickness
BACK = 6                    # back panel, behind the carcass
SHELVES = (270, 530)        # height of each shelf's underside
SHELF_DEPTH = D - 20        # shelves stop short of the back
GAP = 2                     # around the door
HINGES = (100, 660)         # height of each hinge's underside

BIRCH, SHELF, HARDBOARD = cq.Color(0.86, 0.74, 0.55), cq.Color(0.9, 0.82, 0.66), cq.Color(0.55, 0.5, 0.45)
PAINT, STEEL = cq.Color(0.25, 0.42, 0.55), cq.Color(0.7, 0.72, 0.75)


def box(x0, y0, z0, x1, y1, z1) -> cq.Workplane:
    return cq.Workplane("XY").box(x1 - x0, y1 - y0, z1 - z0, centered=False).translate((x0, y0, z0))


def assembly() -> cq.Assembly:
    cabinet = cq.Assembly(name="cabinet")

    carcass = cq.Assembly(name="Carcass")
    carcass.add(box(0, 0, 0, T, D, H), name="left side", color=BIRCH)
    carcass.add(box(W - T, 0, 0, W, D, H), name="right side", color=BIRCH)
    carcass.add(box(T, 0, H - T, W - T, D, H), name="top", color=BIRCH)
    carcass.add(box(T, 0, 0, W - T, D, T), name="bottom", color=BIRCH)
    cabinet.add(carcass)

    shelves = cq.Assembly(name="Shelves")
    for i, z in enumerate(SHELVES, 1):
        shelves.add(box(T, 0, z, W - T, SHELF_DEPTH, z + T), name=f"shelf {i}", color=SHELF)
    cabinet.add(shelves)

    cabinet.add(box(0, D, 0, W, D + BACK, H), name="back panel", color=HARDBOARD)

    door = cq.Assembly(name="Door")
    door.add(box(GAP, -T - GAP, GAP, W - GAP, -GAP, H - GAP), name="door panel", color=PAINT)
    door.add(box(W - 70, -T - GAP - 25, H / 2 - 60, W - 58, -T - GAP, H / 2 + 60), name="handle", color=STEEL)
    cabinet.add(door)

    hardware = cq.Assembly(name="Hardware")
    hinges = cq.Assembly(name="Hinges")
    for i, z in enumerate(HINGES, 1):
        hinges.add(box(T, 0, z, T + 12, 35, z + 40), name=f"hinge {i}", color=STEEL)
    screws = cq.Assembly(name="Screws")
    n = 0
    for z in SHELVES:
        for y in (60, SHELF_DEPTH - 60):
            for x0 in (0, W - 30):  # through each side into the shelf's end
                n += 1
                pin = cq.Workplane("YZ").circle(2.5).extrude(30).translate((x0, y, z + T / 2))
                screws.add(pin, name=f"screw {n}", color=STEEL)
    hardware.add(hinges)
    hardware.add(screws)
    cabinet.add(hardware)
    return cabinet


def rect(y0, z0, y1, z1) -> PathBuilder:
    return PathBuilder().move(y0, z0).line(y1, z0).line(y1, z1).line(y0, z1).close()


def cut(layer, name: str, y0, z0, y1, z1):
    """A part cut by the section: its own group, hatched and outlined."""
    part = layer.sublayer(name)
    slug = name.replace(" ", "-")
    part.path(f"{slug}-cut", rect(y0, z0, y1, z1), fill=HATCH, stroke="none")
    part.path(slug, rect(y0, z0, y1, z1), source=name)


def section_a() -> Sheet:
    """Cut at x = W/2, seen from the right: sheet (x, y) is model (y, z), the front on the left."""
    sheet = Sheet("Cabinet, section A-A", source=SOURCE, pipeline="python", regenerate=REGENERATE,
                  kind="section", origin=(W / 2, 0, 0), normal=(1, 0, 0), xdir=(0, 1, 0), font_size=14,
                  margin=30)
    carcass = sheet.layer("Carcass")
    carcass.sublayer("left side", style="thin").path("left-side", rect(0, 0, D, H), source="left side, beyond the cut")
    cut(carcass, "top", 0, H - T, D, H)
    cut(carcass, "bottom", 0, 0, D, T)
    shelves = sheet.layer("Shelves")
    for i, z in enumerate(SHELVES, 1):
        cut(shelves, f"shelf {i}", 0, z, SHELF_DEPTH, z + T)
    back = sheet.layer("back panel")
    back.path("back-panel-cut", rect(D, 0, D + BACK, H), fill=HATCH, stroke="none")
    back.path("back-panel", rect(D, 0, D + BACK, H), source="back panel")
    door = sheet.layer("Door", visible=False)  # as in the model: hidden at first
    cut(door, "door panel", -T - GAP, GAP, -GAP, H - GAP)

    dims = sheet.layer("Dimensions")
    overall = dims.sublayer("Overall")
    overall.linear("d-height", (0, 0), (0, H), str(H), dir="y", offset=50, value=H)
    overall.linear("d-depth", (0, H), (D, H), str(D), dir="x", offset=40, value=D)
    heights = dims.sublayer("Shelves")
    for i, z in enumerate(SHELVES, 1):
        heights.linear(f"d-shelf-{i}", (D + BACK, 0), (D + BACK, z), str(z), dir="y", offset=-40 * i, value=z)
    door_dims = dims.sublayer("Door", visible=False)
    door_dims.linear("d-door", (-T - GAP, H - GAP), (-GAP, H - GAP), str(T), dir="x", offset=60, value=T)
    return sheet


if __name__ == "__main__":
    print(section_a().save(HERE / "section-A.svg"))
    print(tracepaper.model.export(assembly(), HERE / "cabinet.glb", hidden=["Door"]))
