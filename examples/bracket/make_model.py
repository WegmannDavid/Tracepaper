#!/usr/bin/env python3
"""Example: the bracket as a 3D model, bracket.glb, for Tracepaper's 3D view.

Needs CadQuery. Uses the same numbers as make_sheets.py; the model is what the
sheets are views of: the C profile extruded along y, with the mount hole and a
screw in it.
"""
import sys
from pathlib import Path

import cadquery as cq

sys.path.insert(0, str(Path(__file__).resolve().parent))
from make_sheets import FILLET, FLANGE_END, HEIGHT, HERE, HOLE_D, HOLE_X, HOLE_Y, LENGTH, T, WIDTH  # noqa: E402

import tracepaper.model  # noqa: E402  (make_sheets put the checkout on the path)


def bracket() -> cq.Workplane:
    top = HEIGHT - T
    profile = [(0, 0), (LENGTH, 0), (LENGTH, T), (T, T), (T, top), (FLANGE_END, top), (FLANGE_END, HEIGHT),
               (0, HEIGHT)]
    body = cq.Workplane("XZ").polyline(profile).close().extrude(-WIDTH)  # the XZ plane faces -y
    for z in (T, top):  # the inner fillets, where the upright meets the base and the flange
        body = body.edges(cq.selectors.BoxSelector((T - 0.1, -1, z - 0.1), (T + 0.1, WIDTH + 1, z + 0.1))).fillet(FILLET)
    hole = cq.Workplane("XY").center(HOLE_X, HOLE_Y).circle(HOLE_D / 2).extrude(T)
    return body.cut(hole)


def screw() -> cq.Workplane:
    shaft = cq.Workplane("XY").circle(HOLE_D / 2 - 0.2).extrude(-12)
    head = cq.Workplane("XY").workplane(offset=T).polygon(6, 10).extrude(4)
    return shaft.union(head.translate((0, 0, -T))).translate((HOLE_X, HOLE_Y, T))


def assembly() -> cq.Assembly:
    assy = cq.Assembly(name="bracket")
    assy.add(bracket(), name="body", color=cq.Color(0.62, 0.66, 0.72))
    assy.add(screw(), name="screw", color=cq.Color(0.85, 0.6, 0.2))
    return assy


if __name__ == "__main__":
    print(tracepaper.model.export(assembly(), HERE / "bracket.glb"))
