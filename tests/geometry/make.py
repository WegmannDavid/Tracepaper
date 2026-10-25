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
  one region touching another, one inside another's hole, an ellipse; with notes, and
  a stress field with one value for each element.
- clip.step, with frames in .clip/: a 2D snap clip pushed into its groove, its arm
  bent as far as the groove's bump pushes its hook, at each insertion depth from 0 to
  6 mm; clip.tp.json gives the axis and each frame's deflection, force, strain (with a
  maximum) and pull-out force (with a minimum), the strain in the arm as a field, a .vtu
  file for each frame, a key as notes, and each frame's own dimensions and status. The
  arm's centre line is a Bézier curve, with its ends and handles as points, and the
  point where the hook touches the bump is a point too. Where they touch, the bump's
  push on the hook is a field too: forces at three bare points, square to the bump.
- beam.step: a 2D cantilever and its support, with fields as a FE solver gives them
  (worked out as a beam here): the stress on a mesh of quadrilaterals, with a maximum,
  the displacement, which also bends the mesh, the load along its top side on a mesh of
  lines, and the support's reactions along its fixed end, as forces at the nodes of
  another.
- snap.step: a detail of 15 mm, the clip at 3 mm in, scaled down by SNAP: a dozen
  dimensions on features 0.07 to 3.5 mm apart, an angle, and a key as notes, all of
  which have to stay small enough to read the drawing under them.
- grid.step: 64 parts, all one shape placed 64 times.
- corner.step: a window's corner in the colours the CAD gives it, as CadQuery writes
  them: a steel frame, a glass pane that is see-through, two brass screws, and a seal
  left without one.

Coordinates are millimetres, z up. Only corner.step sets colours.
"""
import json
import math
import os
import sys
from pathlib import Path
from typing import Optional

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


def profile_meta() -> dict:
    return {
        "title": "Profile",
        "source": "tests/geometry/make.py",
        "regenerate": "python3 tests/geometry/make.py profile",
        "notes": "A channel, a gasket against it, a pin in its hole, and an insert.\nThe gasket's stress is one value for each element.",
        "annotations": [
            {"id": "d-width", "dim": "linear", "p1": [-30, 12], "p2": [30, 12], "dir": "x", "offset": 6},
            {"id": "d-hole", "dim": "diameter", "center": [16, 0], "r": 4, "angle": 60, "offset": 6},
            {"id": "d-corner", "dim": "radius", "center": [-27, -9], "r": 3, "angle": 225, "offset": 4},
            {"id": "d-slot", "dim": "angular", "center": [-12, 2], "p1": [0, 2], "p2": [0, 6.3677], "offset": 10},
        ],
        "fields": [{"name": "stress", "unit": "MPa", "file": "profile.stress.vtu", "max": 6}],
    }


def profile_field() -> str:
    """The gasket's stress, MPa, as a grid of quadrilaterals with one value each, highest where
    it is pressed against the channel."""
    nx, ny, x0, x1, y0, y1 = 6, 12, 30.0, 42.0, -12.0, 12.0
    points = [(x0 + (x1 - x0) * i / nx, y0 + (y1 - y0) * j / ny, 0.0) for j in range(ny + 1) for i in range(nx + 1)]
    cells, stress = [], []
    for j in range(ny):
        for i in range(nx):
            a = j * (nx + 1) + i
            cells.append((a, a + 1, a + nx + 2, a + nx + 1))
            stress.append(round(8 * (1 - (i + 0.5) / nx) * (1 - abs(j + 0.5 - ny / 2) / ny), 3))
    return vtu(points, cells, [9] * len(cells), cell_data={"stress": stress})


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


def vtu(points, cells, types, point_data=None, cell_data=None) -> str:
    """A .vtu file, ASCII: points [(x, y, z)], cells [(node, ...)] of VTK types, and point and
    cell data {name: values}, a value a number or a vector (x, y, z)."""
    nums = lambda vs: " ".join(f"{v:.6g}" for v in vs)  # noqa: E731

    def arrays(data):
        out = []
        for name, values in (data or {}).items():
            vector = isinstance(values[0], tuple)
            flat = [c for v in values for c in v] if vector else values
            out.append(f'<DataArray type="Float64" Name="{name}"{" NumberOfComponents=\"3\"" if vector else ""} '
                       f'format="ascii">{nums(flat)}</DataArray>')
        return "".join(out)

    offsets, n = [], 0
    for c in cells:
        n += len(c)
        offsets.append(n)
    return f'''<?xml version="1.0"?>
<VTKFile type="UnstructuredGrid" version="1.0" byte_order="LittleEndian" header_type="UInt32">
  <UnstructuredGrid>
    <Piece NumberOfPoints="{len(points)}" NumberOfCells="{len(cells)}">
      <Points><DataArray type="Float64" NumberOfComponents="3" format="ascii">{nums(c for p in points for c in p)}</DataArray></Points>
      <Cells>
        <DataArray type="Int32" Name="connectivity" format="ascii">{nums(i for c in cells for i in c)}</DataArray>
        <DataArray type="Int32" Name="offsets" format="ascii">{nums(offsets)}</DataArray>
        <DataArray type="UInt8" Name="types" format="ascii">{nums(types)}</DataArray>
      </Cells>
      <PointData>{arrays(point_data)}</PointData>
      <CellData>{arrays(cell_data)}</CellData>
    </Piece>
  </UnstructuredGrid>
</VTKFile>
'''


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
    return vtu(points, cells, [5] * len(cells), {"strain": strain, "u": u})


PULL_MIN = 15.0  # N: the least the clip must hold once it has snapped in


def pull_out(depth: float) -> float:
    """The force it takes to pull the clip out (N): once snapped in, to push the hook over the
    bump's steep side; before, nothing holds it."""
    snapped = 24 - depth - ARM + 1 <= BUMP[-1][1]  # the hook's point under the bump
    inertia = WIDTH_Z * ARM_T ** 3 / 12
    over = BUMP[1][0] - (-ARM_T / 2 - HOOK)  # how far the hook must move aside to pass
    return round(2.5 * 3 * E * inertia * over / ARM ** 3, 2) if snapped else 0.0


def clip_annotations(depth: float) -> list:
    """The clip's dimensions at an insertion depth: the bump's height, always; how deep the clip
    is in and how far its arm is bent, where there is anything to measure."""
    d = bending(depth)["deflection"]
    tip = 24 - depth - ARM
    out = [{"id": "d-bump", "dim": "linear", "p1": [WALL, 2.5], "p2": [BUMP[1][0], 2.5], "dir": "x", "offset": -4,
            "text-at": -1.5, "label": "1.6", "value": 1.6, "source": "BUMP"}]
    if depth > 0:
        out.append({"id": "d-depth", "dim": "linear", "p1": [9, 24], "p2": [9, 24 - depth], "dir": "y", "offset": -3,
                    "label": f"s = {depth:g}", "value": depth})
    if d > 0.01:
        out.append({"id": "d-deflection", "dim": "linear", "p1": [ARM_T / 2, tip], "p2": [ARM_T / 2 + d, tip],
                    "dir": "x", "offset": -7, "label": f"δ = {d:.2f}", "value": d, "source": "deflection"})
    return out


KEY = ["Key",
       f"a  arm length, {ARM:g} mm (was 18 mm)",
       f"b  arm thickness, {ARM_T:g} mm (was 2.2 mm)",
       "s  insertion depth",
       "δ  how far the hook is pushed aside"]


def clip_status(depth: float) -> str:
    if pull_out(depth):
        return f"s = {depth:g} mm: snapped in"
    return f"s = {depth:g} mm: " + ("riding the bump" if bending(depth)["deflection"] else "not touching yet")


def contact_field(depth: float) -> Optional[str]:
    """Where the bump pushes the hook at an insertion depth, as a .vtu file: the force on the
    clip (N), square to the bump's side, as big across as it takes to bend the arm, spread
    over three bare points along the side, a quarter, a half and a quarter of it; or None
    where they don't touch."""
    hook = (-ARM_T / 2 - HOOK, 24 - depth - ARM + 1)
    d = max(0.0, wall_x(hook[1]) - hook[0])
    if not d or not BUMP[-1][1] < hook[1] < BUMP[0][1]:
        return None
    (x0, y0), (x1, y1) = next((a, b) for a, b in zip(BUMP, BUMP[1:]) if b[1] <= hook[1] <= a[1])
    side = math.hypot(x1 - x0, y1 - y0)
    along, normal = ((x1 - x0) / side, (y1 - y0) / side), ((y0 - y1) / side, (x1 - x0) / side)
    push = bending(depth)["force"] / normal[0]
    at = (hook[0] + d, hook[1])
    points = [(at[0] + k * 0.15 * along[0], at[1] + k * 0.15 * along[1], 0.0) for k in (-1, 0, 1)]
    forces = [(w * push * normal[0], w * push * normal[1], 0.0) for w in (0.25, 0.5, 0.25)]
    return vtu(points, [(0,), (1,), (2,)], [1] * 3, {"f": forces})


def clip_meta() -> dict:
    return {
        "title": "Snap clip in its groove",
        "source": "tests/geometry/make.py",
        "regenerate": "python3 tests/geometry/make.py clip",
        "axes": [{"name": "insertion", "unit": "mm"}],
        "notes": KEY,
        "annotations": clip_annotations(0),
        "quantities": [{"name": "deflection", "unit": "mm"}, {"name": "force", "unit": "N"},
                       {"name": "strain", "unit": "%", "max": 1.0},
                       {"name": "pull-out force", "unit": "N", "min": PULL_MIN}],
        "fields": [{"name": "strain", "unit": "%", "max": 1.0, "displacement": "u"},
                   {"name": "contact force", "unit": "N", "value": "f"}],
        "frames": [{"at": {"insertion": d}, "step": f".clip/insertion={d:g}.step",
                    "values": {**bending(d), "pull-out force": pull_out(d)},
                    "fields": {"strain": f".clip/insertion={d:g}.vtu",
                               **({"contact force": f".clip/contact={d:g}.vtu"} if contact_field(d) else {})},
                    "annotations": clip_annotations(d), "notes": [*KEY, "", clip_status(d)]} for d in DEPTHS],
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
    foot = cq.Workplane("XY").center(4, root + 4).rect(10, 8)  # off the wall's side
    clip = cq.Face.makeFromWires(arm.val()).fuse(cq.Face.makeFromWires(foot.val())).clean()
    wall = cq.Workplane("XY").polyline([(-12, -12), (WALL, -12), *BUMP[::-1], (WALL, 30), (-12, 30)]).close()
    a = cq.Assembly(name="clip detail")
    a.add(clip, name="clip")
    a.add(cq.Face.makeFromWires(wall.val()), name="wall")
    # The arm's centre line, a cantilever's bending line: a cubic, so exactly a Bézier curve, as
    # a curve, its ends and its handles as points; and where the hook touches the bump.
    poles = [cq.Vector(0, root, 0), cq.Vector(0, root - ARM / 3, 0), cq.Vector(d / 2, root - 2 * ARM / 3, 0),
             cq.Vector(d, root - ARM, 0)]
    line = cq.Assembly(name="centre line")
    line.add(cq.Edge.makeBezier(poles), name="curve")
    line.add(cq.Compound.makeCompound([cq.Vertex.makeVertex(*poles[k].toTuple()) for k in (0, 3)]), name="ends")
    line.add(cq.Compound.makeCompound([cq.Vertex.makeVertex(*poles[k].toTuple()) for k in (1, 2)]), name="handles")
    a.add(line)
    if d > 0 and BUMP[-1][1] < hook[1] < BUMP[0][1]:
        a.add(cq.Vertex.makeVertex(hook[0] + d, hook[1], 0), name="contact C")
    return a


# The beam: a cantilever of BEAM_L by BEAM_H, BEAM_T thick, held at x = 0, under a load along
# its top side that grows from nothing there to BEAM_Q at its free end.
BEAM_L, BEAM_H, BEAM_T, BEAM_Q = 60.0, 8.0, 10.0, 2.0  # mm, mm, mm, N/mm
BEAM_NX, BEAM_NY = 30, 4  # its mesh's elements along and across it


def beam_moment(x: float) -> float:
    """The bending moment at x (N mm), of the load beyond it: positive where it bends the
    beam down, the top side in tension."""
    return BEAM_Q / BEAM_L * ((BEAM_L ** 3 - x ** 3) / 3 - x * (BEAM_L ** 2 - x ** 2) / 2)


def beam_bending(steps: int = 600) -> tuple:
    """How far the beam's axis comes down (v, mm) and turns (v'), at each of steps + 1
    points along it: EI v'' = -M, held level at x = 0."""
    inertia = BEAM_T * BEAM_H ** 3 / 12
    dx = BEAM_L / steps
    v, slope = [0.0], [0.0]
    for i in range(steps):
        m0, m1 = beam_moment(i * dx), beam_moment((i + 1) * dx)
        slope.append(slope[-1] - (m0 + m1) / 2 * dx / (E * inertia))
        v.append(v[-1] + (slope[-2] + slope[-1]) / 2 * dx)
    return v, slope


def beam_fields() -> dict:
    """The beam's fields as .vtu files, by name: on a mesh of quadrilaterals, the stress along
    it (MPa) and the displacement (mm); the load along its top side (N/mm) on a mesh of lines;
    and the support's reactions (N) at the nodes of its fixed end, on lines too."""
    inertia = BEAM_T * BEAM_H ** 3 / 12
    v, slope = beam_bending()
    at = lambda x: round(x / BEAM_L * (len(v) - 1))  # noqa: E731
    points, stress, u = [], [], []
    for j in range(BEAM_NY + 1):
        for i in range(BEAM_NX + 1):
            x, y = BEAM_L * i / BEAM_NX, BEAM_H * j / BEAM_NY
            c = y - BEAM_H / 2  # from the axis, up
            points.append((x, y, 0.0))
            stress.append(beam_moment(x) * c / inertia)
            u.append((-c * slope[at(x)], v[at(x)], 0.0))
    row = BEAM_NX + 1
    quads = [(j * row + i, j * row + i + 1, (j + 1) * row + i + 1, (j + 1) * row + i)
             for j in range(BEAM_NY) for i in range(BEAM_NX)]
    top = [(BEAM_L * i / BEAM_NX, BEAM_H, 0.0) for i in range(BEAM_NX + 1)]
    load = [BEAM_Q * x / BEAM_L for x, _, _ in top]
    end = [(0.0, BEAM_H * j / BEAM_NY, 0.0) for j in range(BEAM_NY + 1)]
    dy = BEAM_H / BEAM_NY
    share = [dy / 2 if j in (0, BEAM_NY) else dy for j in range(BEAM_NY + 1)]
    shear = [w * (1 - (2 * (y - BEAM_H / 2) / BEAM_H) ** 2) for w, (_, y, _) in zip(share, end)]
    held = BEAM_Q * BEAM_L / 2  # all the load, up
    reactions = [(-beam_moment(0) * (y - BEAM_H / 2) / inertia * BEAM_T * w, held * s / sum(shear), 0.0)
                 for w, s, (_, y, _) in zip(share, shear, end)]
    lines = lambda n: [(k, k + 1) for k in range(n)]  # noqa: E731
    return {"beam.vtu": vtu(points, quads, [9] * len(quads), {"stress": stress, "u": u}),
            "beam.load.vtu": vtu(top, lines(BEAM_NX), [3] * BEAM_NX, {"load": load}),
            "beam.reaction.vtu": vtu(end, lines(BEAM_NY), [3] * BEAM_NY, {"reaction": reactions})}


def beam_meta() -> dict:
    return {
        "title": "Cantilever",
        "source": "tests/geometry/make.py",
        "regenerate": "python3 tests/geometry/make.py beam",
        "notes": "Held at its left end, loaded along its top side, more towards its free end.\n"
                 "Worked out as a beam, as test data: a FE solver's would look like it.",
        "fields": [{"name": "stress", "unit": "MPa", "max": 20, "file": "beam.vtu", "displacement": "u"},
                   {"name": "displacement", "unit": "mm", "file": "beam.vtu", "value": "u", "displacement": "u"},
                   {"name": "load", "unit": "N/mm", "file": "beam.load.vtu"},
                   {"name": "reaction", "unit": "N", "file": "beam.reaction.vtu"}],
    }


def beam() -> cq.Assembly:
    """Regions in the plane z = 0: the beam, and the support it is held in."""
    a = cq.Assembly(name="cantilever")
    a.add(cq.Sketch().push([(BEAM_L / 2, BEAM_H / 2)]).rect(BEAM_L, BEAM_H)._faces, name="beam")
    a.add(cq.Sketch().push([(-4, BEAM_H / 2)]).rect(8, BEAM_H + 12)._faces, name="support")
    return a


SNAP, SNAP_DEPTH = 0.35, 3.0  # the detail: the clip this much smaller, this far in


def scaled(a: cq.Assembly, k: float) -> cq.Assembly:
    """An assembly of shapes, k times as big, about the origin."""
    out = cq.Assembly(name=a.name)
    for child in a.children:
        out.add(scaled(child, k) if child.children else child.obj.scale(k), name=child.name)
    return out


def snap_meta() -> dict:
    """The detail's dimensions, in the clip's millimetres and then scaled, and its key."""
    k, d = SNAP, bending(SNAP_DEPTH)["deflection"]
    root, tip = 24 - SNAP_DEPTH, 24 - SNAP_DEPTH - ARM
    hook_x = d - ARM_T / 2  # the arm's left side at its tip, where the hook sticks out
    dims = [  # id, label, p1, p2, dir, offset, text-at
        ("d-arm", "a", (ARM_T / 2, root), (ARM_T / 2 + d, tip), "y", -4, 0.5),
        ("d-thickness", "b", (-ARM_T / 2, root - 2), (ARM_T / 2, root - 2), "x", -1.5, 1.6),
        ("d-hook", "h", (hook_x - HOOK, tip + 1), (hook_x, tip + 1), "x", -2.5, 0.5),
        ("d-gap", "g", (WALL, root - 2), (-ARM_T / 2, root - 2), "x", 1.5, -0.6),
        ("d-bump", "e", (WALL, 2.5), (BUMP[1][0], 2.5), "x", -6, 0.5),
        ("d-ramp", None, BUMP[0], BUMP[1], "y", 2.5, 0.5),
        ("d-flat", None, BUMP[1], BUMP[2], "y", 2.5, 0.5),
        ("d-back", None, BUMP[2], BUMP[3], "y", 2.5, 1.8),
        ("d-depth", "s", (9, 24), (9, root), "y", -2, 0.5),
        ("d-foot", None, (-1, root + 8), (9, root + 8), "x", 1.5, 0.5),
        ("d-foot-h", None, (9, root), (9, root + 8), "y", -4, 0.5),
        ("d-deflection", "δ", (ARM_T / 2, tip), (ARM_T / 2 + d, tip), "x", -2, 1.8),
    ]
    out = []
    for id, letter, p1, p2, dir, offset, at in dims:
        p1, p2 = [round(k * c, 6) for c in p1], [round(k * c, 6) for c in p2]
        value = abs(p2[0] - p1[0]) if dir == "x" else abs(p2[1] - p1[1])
        out.append({"id": id, "dim": "linear", "p1": p1, "p2": p2, "dir": dir, "offset": round(k * offset, 6),
                    "text-at": at, "value": round(value, 4),
                    **({"label": f"{letter} = {value:.3g}"} if letter else {})})
    out.append({"id": "d-ramp-angle", "dim": "angular", "center": [round(k * c, 6) for c in BUMP[0]],
                "p1": [round(k * WALL, 6), round(k * BUMP[3][1], 6)], "p2": [round(k * c, 6) for c in BUMP[1]],
                "offset": round(k * 2.2, 6), "text-at": 0.6})
    return {
        "title": "Snap clip, a detail of 15 mm",
        "source": "tests/geometry/make.py",
        "regenerate": "python3 tests/geometry/make.py snap",
        "notes": ["Key",
                  "a  arm length",
                  "b  arm thickness",
                  "h  the hook, how far it sticks out of the arm",
                  "g  the gap between the groove's wall and the arm",
                  "e  the bump on the wall, which the hook rides over",
                  "s  insertion depth",
                  "δ  how far the hook is pushed aside",
                  f"The clip of clip.step, {SNAP:g} times as big, {SNAP * SNAP_DEPTH:g} mm in."],
        "annotations": out,
    }


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


def corner() -> cq.Assembly:
    frame = cq.Workplane("XY").box(60, 30, 8, centered=False).union(
        cq.Workplane("XY").box(8, 30, 60, centered=False)).faces(">Z[1]").workplane(origin=(0, 0, 8)).pushPoints(
        [(30, 5), (48, 5)]).hole(3, 6)
    a = cq.Assembly(name="corner")
    a.add(frame, name="frame", color=cq.Color(0.42, 0.45, 0.5))
    a.add(cq.Workplane("XY").box(44, 4, 44, centered=False).translate((14, 13, 14)), name="glass",
          color=cq.Color(0.65, 0.82, 0.95, 0.35))
    seal = cq.Workplane("XY").box(46, 4, 4, centered=False).translate((10, 13, 9)).union(
        cq.Workplane("XY").box(4, 4, 46, centered=False).translate((9, 13, 10)))
    a.add(seal, name="seal")
    screws = cq.Assembly(name="Screws")
    for i, x in enumerate((30, 48), 1):
        screw = cq.Workplane("XY").circle(1.5).extrude(-6).faces(">Z").workplane().circle(3).extrude(2)
        screws.add(screw.translate((x, 5, 8)), name=f"screw {i}", color=cq.Color(0.8, 0.65, 0.3))
    a.add(screws)
    return a


if __name__ == "__main__":
    only = set(sys.argv[1:])
    for name, make in [("primitives", primitives), ("bracket", bracket), ("freeform", freeform),
                       ("hinge", hinge), ("profile", profile), ("grid", grid), ("corner", corner), ("beam", beam)]:
        if not only or name in only:
            save(make(), f"{name}.step")
    if not only or "profile" in only:
        (HERE / "profile.stress.vtu").write_text(profile_field())
        (HERE / "profile.tp.json").write_text(json.dumps(profile_meta(), indent=2) + "\n")
        print("profile.tp.json")
    if not only or "hinge" in only:
        for a in OPENING:
            save(hinge(a), f".hinge/opening={a}.step")
        (HERE / "hinge.tp.json").write_text(json.dumps(hinge_meta(), indent=2) + "\n")
        print("hinge.tp.json")
    if not only or "snap" in only:
        save(scaled(clip_at(SNAP_DEPTH), SNAP), "snap.step")
        (HERE / "snap.tp.json").write_text(json.dumps(snap_meta(), indent=2) + "\n")
        print("snap.tp.json")
    if not only or "beam" in only:
        for name, text in beam_fields().items():
            (HERE / name).write_text(text)
        (HERE / "beam.tp.json").write_text(json.dumps(beam_meta(), indent=2) + "\n")
        print("beam.tp.json")
    if not only or "clip" in only:
        save(clip_at(DEPTHS[0]), "clip.step")
        for depth in DEPTHS:
            save(clip_at(depth), f".clip/insertion={depth:g}.step")
            (HERE / f".clip/insertion={depth:g}.vtu").write_text(clip_field(depth))
            contact = HERE / f".clip/contact={depth:g}.vtu"
            if contact_field(depth):
                contact.write_text(contact_field(depth))
            else:
                contact.unlink(missing_ok=True)
        (HERE / "clip.tp.json").write_text(json.dumps(clip_meta(), indent=2) + "\n")
        print("clip.tp.json")
