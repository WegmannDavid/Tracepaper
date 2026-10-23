#!/usr/bin/env python3
"""Test geometry for Tracepaper's scenes: STEP files of exact solids, regions and curves.

Run it to (re)write the .step files next to it. Needs CadQuery. Each scene covers
something the viewer has to get right when it shows, slices or projects them:

- primitives.step: one solid on each kind of analytic surface: plane, cylinder,
  cone, sphere and torus. Plane sections of them give lines, circles, ellipses,
  parabolas and hyperbolas.
- bracket.step: a machined part, with fillets, a hole and a screw in it.
- freeform.step: B-spline surfaces (a loft, a sweep, a revolved spline), a box
  with all edges rounded (cylinders, spheres where three meet), and the sweep's
  path as a curve of its own.
- hinge.step, with frames in .hinge/: an assembly three levels deep, with parts
  touching, a pin inside its knuckles, and the same screw placed four times; the
  door's side turned round the pin from 0 to 90° in its frames.
- profile.step: a 2D scene: regions in the plane z = 0, with holes and arcs,
  one region touching another, one inside another's hole, an ellipse.
- clip.step, with frames in .clip/: a 2D snap clip pushed into its groove, its arm
  bent as far as the groove's bump pushes its hook, at each insertion depth from 0 to
  6 mm; clip.tp.json gives the axis and each frame's deflection, force and strain,
  and the strain in the arm as a field, a .vtu file for each frame.
- grid.step: 64 parts, all one shape placed 64 times.

Coordinates are millimetres, z up. Nothing here sets a colour.
"""
import json
import math
import os
import sys
from pathlib import Path

import cadquery as cq

HERE = Path(__file__).resolve().parent


def save(assembly: cq.Assembly, name: str) -> None:
    path = HERE / name
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    assembly.export(str(tmp), exportType="STEP")
    os.replace(tmp, path)
    print(path.relative_to(HERE))


def on_ground(shape: cq.Shape, x: float) -> cq.Shape:
    """shape moved to stand on z = 0, centred on (x, 0)."""
    box = shape.BoundingBox()
    return shape.translate(cq.Vector(x - box.center.x, -box.center.y, -box.zmin))


def primitives() -> cq.Assembly:
    a = cq.Assembly(name="primitives")
    shapes = {
        "box": cq.Solid.makeBox(20, 20, 20),
        "cylinder": cq.Solid.makeCylinder(10, 20),
        "cone": cq.Solid.makeCone(10, 4, 20),
        "sphere": cq.Solid.makeSphere(10, angleDegrees1=-90, angleDegrees2=90),
        "torus": cq.Solid.makeTorus(10, 3),
    }
    for i, (name, shape) in enumerate(shapes.items()):
        a.add(on_ground(shape, 30 * i), name=name)
    return a


# The bracket: a C profile along y with a mount hole, as in examples/bracket.
LENGTH, WIDTH, HEIGHT, T, FILLET = 60, 30, 40, 5, 3
HOLE_X, HOLE_Y, HOLE_D = 40, 15, 6
FLANGE_END = HOLE_X - HOLE_D / 2


def bracket() -> cq.Assembly:
    top = HEIGHT - T
    profile = [(0, 0), (LENGTH, 0), (LENGTH, T), (T, T), (T, top), (FLANGE_END, top), (FLANGE_END, HEIGHT),
               (0, HEIGHT)]
    body = cq.Workplane("XZ").polyline(profile).close().extrude(-WIDTH)
    for z in (T, top):
        body = body.edges(cq.selectors.BoxSelector((T - 0.1, -1, z - 0.1), (T + 0.1, WIDTH + 1, z + 0.1))).fillet(FILLET)
    body = body.cut(cq.Workplane("XY").center(HOLE_X, HOLE_Y).circle(HOLE_D / 2).extrude(T))
    shaft = cq.Workplane("XY").circle(HOLE_D / 2 - 0.2).extrude(-12)
    head = cq.Workplane("XY").workplane(offset=T).polygon(6, 10).extrude(4)
    screw = shaft.union(head.translate((0, 0, -T))).translate((HOLE_X, HOLE_Y, T))
    a = cq.Assembly(name="bracket")
    a.add(body, name="body")
    a.add(screw, name="screw")
    return a


def freeform() -> cq.Assembly:
    a = cq.Assembly(name="freeform")
    loft = cq.Workplane("XY").circle(8).workplane(offset=20).rect(14, 8).loft()
    a.add(loft, name="loft")
    path = cq.Wire.makeHelix(pitch=6, height=24, radius=8).translate(cq.Vector(35, 0, 0))
    start = path.startPoint()
    tangent = path.tangentAt(0)
    ring = cq.Wire.makeCircle(1.5, start, tangent)
    spring = cq.Solid.sweep(ring, [], path, isFrenet=True)
    a.add(spring, name="spring")
    a.add(path, name="spring path")  # a curve, no volume
    vase = (cq.Workplane("XZ").moveTo(0, 0).lineTo(9, 0).spline([(6, 8), (10, 18), (5, 26)], includeCurrent=True)
            .lineTo(0, 26).close().revolve(360, (0, 0, 0), (0, 1, 0)))
    a.add(vase.translate((70, 0, 0)), name="vase")
    a.add(cq.Workplane("XY").box(20, 14, 10).edges().fillet(3).translate((100, 0, 5)), name="rounded box")
    return a


OPENING = [0, 15, 30, 45, 60, 75, 90]  # degrees the door is turned round the pin, for the hinge's frames


def hinge_meta() -> dict:
    return {
        "title": "Hinge",
        "source": "tests/geometry/make.py",
        "regenerate": "python3 tests/geometry/make.py hinge",
        "hidden": ["Frame / door"],
        "views": [{"name": "across", "title": "across the pin", "kind": "section", "origin": [10, 0, 0],
                   "normal": [1, 0, 0], "xdir": [0, 1, 0]}],
        "axes": [{"name": "opening", "unit": "°"}],
        "frames": [{"at": {"opening": a}, "step": f".hinge/opening={a}.step"} for a in OPENING],
    }


def hinge(opening: float = 0) -> cq.Assembly:
    """A butt hinge between a door and its post: two leaves whose knuckles take turns round
    a pin along x, screwed on with the same screw in four places; the door's side turned
    by opening degrees round the pin."""
    length, width, t, r = 60, 25, 2, 4  # leaf length along x, width, thickness; knuckle radius
    knuckles = 3
    seg = length / knuckles

    def leaf(side: int, mine) -> cq.Workplane:
        plate = cq.Workplane("XY").box(length, width, t, centered=(False, False, False))
        plate = plate.translate((0, r if side > 0 else -r - width, -t / 2))
        for k in mine:
            knuckle = cq.Workplane("YZ").circle(r).circle(1.6).extrude(seg - 0.4).translate((k * seg + 0.2, 0, 0))
            plate = plate.union(knuckle)
        for x in (length * 0.25, length * 0.75):
            hole = cq.Workplane("XY").circle(1.8).extrude(10, both=True).translate((x, side * (r + width / 2), 0))
            plate = plate.cut(hole)
        return plate

    pin = cq.Workplane("YZ").circle(1.6).extrude(length + 2).translate((-1, 0, 0))
    screw = (cq.Workplane("XY").circle(1.6).extrude(-12)
             .union(cq.Solid.makeCone(1.6, 3.2, 1.6, pnt=cq.Vector(0, 0, -1.6))))  # countersunk head
    turn = cq.Location(cq.Vector(0, 0, 0), cq.Vector(1, 0, 0), -opening)  # round the pin, the door downwards
    a = cq.Assembly(name="hinge")
    leaves = cq.Assembly(name="Leaves")
    leaves.add(leaf(+1, range(0, knuckles, 2)), name="leaf 1")
    leaves.add(leaf(-1, range(1, knuckles, 2)), name="leaf 2", loc=turn)
    a.add(leaves)
    a.add(pin, name="pin")
    screws = cq.Assembly(name="Screws")
    n = 0
    for side in (+1, -1):
        for x in (length * 0.25, length * 0.75):
            n += 1
            at = cq.Location((x, side * (r + width / 2), t / 2))
            screws.add(screw, name=f"screw {n}", loc=turn * at if side < 0 else at)
    a.add(screws)
    frame = cq.Assembly(name="Frame")
    frame.add(cq.Workplane("XY").box(length + 20, 40, 20).translate((length / 2, r + 20, -t / 2 - 10)), name="post")
    frame.add(cq.Workplane("XY").box(length + 20, 40, 20).translate((length / 2, -r - 20, -t / 2 - 10)), name="door",
              loc=turn)
    a.add(frame)
    return a


def profile() -> cq.Assembly:
    """Regions in the plane z = 0."""
    channel = (cq.Sketch().rect(60, 24).vertices().fillet(3).reset()
               .push([(16, 0)]).circle(4, mode="s").reset()
               .push([(-12, 2)]).slot(14, 5, angle=20, mode="s"))
    gasket = cq.Sketch().push([(36, 0)]).rect(12, 24)  # its left side lies on the channel's right one
    pin = cq.Sketch().push([(16, 0)]).circle(2.5)  # in the channel's round hole
    insert = cq.Sketch().push([(0, -24)]).ellipse(14, 6)
    a = cq.Assembly(name="profile")
    a.add(channel._faces, name="channel")
    a.add(gasket._faces, name="gasket")
    a.add(pin._faces, name="pin")
    a.add(insert._faces, name="insert")
    return a


# The snap clip: a foot with an arm hanging from it, of length ARM and thickness ARM_T, with
# a hook at its tip, pushed down into a groove whose wall has a bump the hook rides over.
ARM, ARM_T, HOOK = 20.0, 2.0, 1.6
WALL = -ARM_T / 2 - HOOK - 0.2  # the groove's wall, 0.2 mm left of the hook
BUMP = [(WALL, 4.0), (WALL + 1.6, 2.5), (WALL + 1.6, 1.5), (WALL, 1.0)]  # top to bottom
DEPTHS = [0.5 * i for i in range(13)]  # insertion, 0 to 6 mm


def wall_x(y: float) -> float:
    """Where the wall is at height y: on its bump, or else at WALL."""
    for (x0, y0), (x1, y1) in zip(BUMP, BUMP[1:]):
        if y1 <= y <= y0:
            return x0 + (x1 - x0) * (y0 - y) / (y0 - y1) if y0 != y1 else max(x0, x1)
    return WALL


E, WIDTH_Z = 2000.0, 10.0  # PETG's modulus, N/mm², and the clip's width out of the drawing, mm


def bending(depth: float) -> dict:
    """The arm as a cantilever at an insertion depth: how far its tip is pushed aside (mm),
    the force that takes (N) and the strain at its root (%)."""
    hook = (-ARM_T / 2 - HOOK, 24 - depth - ARM + 1)
    d = max(0.0, wall_x(hook[1]) - hook[0])
    inertia = WIDTH_Z * ARM_T ** 3 / 12
    return {"deflection": round(d, 4), "force": round(3 * E * inertia * d / ARM ** 3, 3),
            "strain": round(100 * 3 * ARM_T * d / (2 * ARM ** 2), 4)}


def clip_field(depth: float) -> str:
    """The strain in the clip's arm at an insertion depth, as a .vtu file: a mesh of the arm
    unbent, triangles in the drawing's plane, the bending strain at each node (%, tension
    positive) and the displacement that bends the mesh into place (u)."""
    d = bending(depth)["deflection"]
    root = 24 - depth
    peak = 100 * 3 * ARM_T * d / (2 * ARM ** 2)  # at the root, on the surface
    along, across = 40, 6
    points, strain, u = [], [], []
    for i in range(along + 1):
        t = i / along
        for j in range(across + 1):
            n = -1 + 2 * j / across  # across the arm, -1 on its left side and 1 on its right
            points.append((n * ARM_T / 2, root - ARM * t, 0.0))
            strain.append(-peak * (1 - t) * n)  # bent to the right: its left side in tension
            u.append((d * t * t * (3 - t) / 2, 0.0, 0.0))
    cells = []
    for i in range(along):
        for j in range(across):
            a, b = i * (across + 1) + j, i * (across + 1) + j + 1
            c, e = a + across + 1, b + across + 1
            cells += [(a, b, e), (a, e, c)]
    nums = lambda vs: " ".join(f"{v:.6g}" for v in vs)  # noqa: E731
    return f'''<?xml version="1.0"?>
<VTKFile type="UnstructuredGrid" version="1.0" byte_order="LittleEndian" header_type="UInt32">
  <UnstructuredGrid>
    <Piece NumberOfPoints="{len(points)}" NumberOfCells="{len(cells)}">
      <Points><DataArray type="Float64" NumberOfComponents="3" format="ascii">{nums(c for p in points for c in p)}</DataArray></Points>
      <Cells>
        <DataArray type="Int32" Name="connectivity" format="ascii">{nums(i for c in cells for i in c)}</DataArray>
        <DataArray type="Int32" Name="offsets" format="ascii">{nums(3 * (k + 1) for k in range(len(cells)))}</DataArray>
        <DataArray type="UInt8" Name="types" format="ascii">{nums(5 for _ in cells)}</DataArray>
      </Cells>
      <PointData Scalars="strain" Vectors="u">
        <DataArray type="Float64" Name="strain" format="ascii">{nums(strain)}</DataArray>
        <DataArray type="Float64" Name="u" NumberOfComponents="3" format="ascii">{nums(c for v in u for c in v)}</DataArray>
      </PointData>
    </Piece>
  </UnstructuredGrid>
</VTKFile>
'''


def clip_meta() -> dict:
    return {
        "title": "Snap clip in its groove",
        "source": "tests/geometry/make.py",
        "regenerate": "python3 tests/geometry/make.py clip",
        "axes": [{"name": "insertion", "unit": "mm"}],
        "quantities": [{"name": "deflection", "unit": "mm"}, {"name": "force", "unit": "N"},
                       {"name": "strain", "unit": "%", "limit": 1.0}],
        "fields": [{"name": "strain", "unit": "%", "limit": 1.0, "displacement": "u"}],
        "frames": [{"at": {"insertion": d}, "step": f".clip/insertion={d:g}.step", "values": bending(d),
                    "fields": {"strain": f".clip/insertion={d:g}.vtu"}} for d in DEPTHS],
    }


def clip_at(depth: float) -> cq.Assembly:
    """The clip and the groove at an insertion depth: the arm bent as a cantilever, as far as
    the bump pushes its hook aside."""
    root = 24 - depth  # where the arm hangs from the foot
    tip = root - ARM
    hook = (-ARM_T / 2 - HOOK, tip + 1)  # the hook's point, unbent
    d = max(0.0, wall_x(hook[1]) - hook[0])  # how far the bump pushes it aside

    def centre(s: float) -> tuple:  # along the arm from its root (s = 0) to its tip (s = 1)
        return (d * s * s * (3 - s) / 2, root - ARM * s)

    n = 12
    right = [(x + ARM_T / 2, y) for x, y in (centre(i / n) for i in range(n + 1))]
    left = [(x - ARM_T / 2, y) for x, y in (centre(i / n) for i in range(n + 1))]
    tip_x = centre(1)[0]
    above_hook = centre(1 - 1 / ARM)
    arm = (cq.Workplane("XY").moveTo(*right[0]).spline(right[1:], includeCurrent=True)
           .lineTo(*left[-1]).lineTo(tip_x - ARM_T / 2 - HOOK, tip + 1).lineTo(above_hook[0] - ARM_T / 2, tip + 1)
           .spline(left[-2::-1], includeCurrent=True).close())
    foot = cq.Workplane("XY").center(0, root + 4).rect(10, 8)
    clip = cq.Face.makeFromWires(arm.val()).fuse(cq.Face.makeFromWires(foot.val())).clean()
    wall = cq.Workplane("XY").polyline([(-12, -12), (WALL, -12), *BUMP[::-1], (WALL, 30), (-12, 30)]).close()
    a = cq.Assembly(name="clip detail")
    a.add(clip, name="clip")
    a.add(cq.Face.makeFromWires(wall.val()), name="wall")
    return a


def grid() -> cq.Assembly:
    a = cq.Assembly(name="grid")
    plate = cq.Workplane("XY").box(20, 20, 4).edges("|Z").fillet(2).faces(">Z").workplane().pushPoints(
        [(-5, 0), (5, 0)]).hole(3)
    for i in range(8):
        row = cq.Assembly(name=f"row {i + 1}")
        for j in range(8):
            row.add(plate, name=f"plate {i + 1}.{j + 1}", loc=cq.Location((25 * j, 25 * i, 0)))  # one shape, placed 64 times
        a.add(row)
    return a


if __name__ == "__main__":
    only = set(sys.argv[1:])
    for name, make in [("primitives", primitives), ("bracket", bracket), ("freeform", freeform),
                       ("hinge", hinge), ("profile", profile), ("grid", grid)]:
        if not only or name in only:
            save(make(), f"{name}.step")
    if not only or "hinge" in only:
        for a in OPENING:
            save(hinge(a), f".hinge/opening={a}.step")
        (HERE / "hinge.tp.json").write_text(json.dumps(hinge_meta(), indent=2) + "\n")
        print("hinge.tp.json")
    if not only or "clip" in only:
        save(clip_at(DEPTHS[0]), "clip.step")
        for depth in DEPTHS:
            save(clip_at(depth), f".clip/insertion={depth:g}.step")
            (HERE / f".clip/insertion={depth:g}.vtu").write_text(clip_field(depth))
        (HERE / "clip.tp.json").write_text(json.dumps(clip_meta(), indent=2) + "\n")
        print("clip.tp.json")
