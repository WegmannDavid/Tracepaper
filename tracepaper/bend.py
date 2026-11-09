"""A part bent along a path (SPEC.md §2.13, "follow"), for the exact drawings.

OpenCascade can't bend a solid, so the bent part is made of flat faces: the part's faces
as triangles, cut across wherever the path's arcs lie, each corner put where the path
puts it (motion.Path.place), and the triangles that then lie in one plane joined again.
So what stays straight keeps its flat faces whole, and an arc of the path shows as short
straight pieces, within BEND of the true curve. The edges between those pieces aren't the
part's own: soft() names them, for a projection to leave out all but its outlines.

The pages bend the 3D view's triangles the same way, live (viewer/bend.js).
"""
from __future__ import annotations

import math
from bisect import bisect_left, bisect_right

from OCP.BRep import BRep_Builder, BRep_Tool
from OCP.BRepAdaptor import BRepAdaptor_Curve, BRepAdaptor_Surface
from OCP.BRepBuilderAPI import (BRepBuilderAPI_Copy, BRepBuilderAPI_MakeEdge, BRepBuilderAPI_MakeFace,
                                BRepBuilderAPI_MakeSolid, BRepBuilderAPI_MakeVertex, BRepBuilderAPI_MakeWire)
from OCP.BRepGProp import BRepGProp
from OCP.BRepMesh import BRepMesh_IncrementalMesh
from OCP.GCPnts import GCPnts_QuasiUniformDeflection
from OCP.GeomAbs import GeomAbs_Plane
from OCP.gp import gp_Pnt
from OCP.GProp import GProp_GProps
from OCP.ShapeUpgrade import ShapeUpgrade_UnifySameDomain
from OCP.TopAbs import TopAbs_EDGE, TopAbs_FACE, TopAbs_REVERSED, TopAbs_SOLID, TopAbs_VERTEX
from OCP.TopExp import TopExp, TopExp_Explorer
from OCP.TopLoc import TopLoc_Location
from OCP.TopoDS import TopoDS, TopoDS_Compound, TopoDS_Shape, TopoDS_Shell
from OCP.TopTools import TopTools_IndexedDataMapOfShapeListOfShape

BEND = 0.005  # mm: how far the straight pieces of a bend may be from its true curve
BEND_ANGLE = 0.1  # radians: and how far they may turn from one to the next, at most
MESH_ANGLE = 0.1  # radians: the same for the part's own curved faces
SOFT = 0.12  # radians: flat faces meeting at less than this are pieces of one curved face
WELD = 1e-7  # mm: corners of triangles closer than this are one point
FLAT = 1e-6  # mm and radians: how close faces must be to one plane to be joined


def _items(shape: TopoDS_Shape, kind) -> list:
    out, e = [], TopExp_Explorer(shape, kind)
    while e.More():
        out.append(e.Current())
        e.Next()
    return out


def stations(path, by: float, reach: float) -> list:
    """Where to cut a part at rest across, as lengths along the path's first line, for it
    moved by mm along the path: at the ends of each of the path's arcs and between them,
    close enough for pieces within BEND of the arc as far as reach from the path."""
    out = []
    for u0, u1 in path.arcs:
        radius = path.round + reach
        step = min(BEND_ANGLE, 2 * math.acos(max(0.0, 1 - BEND / radius)))
        count = max(1, math.ceil((u1 - u0) / path.round / step))
        out += [u0 - by + (u1 - u0) * k / count for k in range(count + 1)]
    return sorted(out)


def split(tri: tuple, s: list, cuts: list, between) -> list:
    """A triangle (three numbers of points) cut across at cuts, lengths in rising order: the
    triangles it comes apart into, turning the same way. s: each point's length; between(i,
    j, k): the number of the point between points i and j at cuts[k], which it makes."""
    order = sorted(range(3), key=lambda k: (s[tri[k]], k))
    a, b, c = (tri[k] for k in order)
    first, last = bisect_right(cuts, s[a]), bisect_left(cuts, s[c])  # those strictly between a and c
    if first >= last:
        return [tri]

    def right(k: int) -> int:  # the point at cuts[k] on the side b is on
        if cuts[k] == s[b]:
            return b
        return between(a, b, k) if cuts[k] < s[b] else between(b, c, k)

    left = [a] + [between(a, c, k) for k in range(first, last)] + [c]
    turned = order not in ([0, 1, 2], [1, 2, 0], [2, 0, 1])
    out = []
    for j in range(len(left) - 1):
        low = s[a] if j == 0 else cuts[first + j - 1]
        high = s[c] if j == len(left) - 2 else cuts[first + j]
        ring = [left[j]]
        if j > 0:
            ring.append(right(first + j - 1))
        if low < s[b] < high or (j == 0 and s[b] == s[a]) or (j == len(left) - 2 and s[b] == s[c]):
            ring.append(b)
        if j < len(left) - 2:
            ring.append(right(first + j))
        ring.append(left[j + 1])
        ring = [p for i, p in enumerate(ring) if p != ring[i - 1]]
        for i in range(1, len(ring) - 1):
            out.append((ring[0], ring[i + 1], ring[i]) if turned else (ring[0], ring[i], ring[i + 1]))
    return out


def _triangles(faces: list) -> tuple:
    """The triangles of meshed faces, turned as the faces are, with corners at the same
    place made one point: ([(x, y, z)], [(i, j, k)])."""
    points, found, triangles = [], {}, []

    def number(p) -> int:
        key = (round(p.X() / WELD), round(p.Y() / WELD), round(p.Z() / WELD))
        if key not in found:
            found[key] = len(points)
            points.append((p.X(), p.Y(), p.Z()))
        return found[key]

    for face in faces:
        location = TopLoc_Location()
        mesh = BRep_Tool.Triangulation_s(TopoDS.Face_s(face), location)
        if mesh is None:
            continue
        trsf = location.Transformation()
        ids = [number(mesh.Node(i).Transformed(trsf)) for i in range(1, mesh.NbNodes() + 1)]
        for i in range(1, mesh.NbTriangles() + 1):
            a, b, c = (ids[k - 1] for k in mesh.Triangle(i).Get())
            if len({a, b, c}) == 3:
                triangles.append((a, c, b) if face.Orientation() == TopAbs_REVERSED else (a, b, c))
    return points, triangles


def _faces_bent(faces: list, path, by: float, closed: bool) -> TopoDS_Shape:
    """Meshed faces bent along path, as flat faces: a solid if they close one, else a shell."""
    points, triangles = _triangles(faces)
    cuts = stations(path, by, max((abs(path.aside(p)) for p in points), default=0.0))
    s = [path.along(p) for p in points]
    for i, v in enumerate(s):  # a corner as good as on a cut is on it: no slivers
        k = bisect_left(cuts, v - WELD)
        if k < len(cuts) and abs(cuts[k] - v) <= WELD:
            s[i] = cuts[k]
    made = {}

    def between(i: int, j: int, k: int) -> int:
        key = (min(i, j), max(i, j), k)
        if key not in made:
            lo, hi = key[0], key[1]
            t = (cuts[k] - s[lo]) / (s[hi] - s[lo])
            points.append(tuple(p + t * (q - p) for p, q in zip(points[lo], points[hi])))
            s.append(cuts[k])
            made[key] = len(points) - 1
        return made[key]

    pieces = [t for tri in triangles for t in split(tri, s, cuts, between)]
    placed = [path.place(p, by) for p in points]
    builder, vertices, edges = BRep_Builder(), {}, {}

    def vertex(i: int):
        if i not in vertices:
            vertices[i] = BRepBuilderAPI_MakeVertex(gp_Pnt(*placed[i])).Vertex()
        return vertices[i]

    def edge(i: int, j: int):
        key = (min(i, j), max(i, j))
        if key not in edges:
            edges[key] = BRepBuilderAPI_MakeEdge(vertex(key[0]), vertex(key[1])).Edge()
        return edges[key] if key == (i, j) else edges[key].Reversed()

    shell = TopoDS_Shell()
    builder.MakeShell(shell)
    for a, b, c in pieces:
        pa, pb, pc = placed[a], placed[b], placed[c]
        if min(math.dist(pa, pb), math.dist(pb, pc), math.dist(pc, pa)) <= WELD:
            continue
        wire = BRepBuilderAPI_MakeWire(TopoDS.Edge_s(edge(a, b)), TopoDS.Edge_s(edge(b, c)), TopoDS.Edge_s(edge(c, a)))
        face = BRepBuilderAPI_MakeFace(wire.Wire(), True)
        if face.IsDone():
            builder.Add(shell, face.Face())
    out = shell
    if closed:
        out = BRepBuilderAPI_MakeSolid(shell).Solid()
        props = GProp_GProps()
        BRepGProp.VolumeProperties_s(out, props)
        if props.Mass() < 0:
            out = out.Reversed()
    unify = ShapeUpgrade_UnifySameDomain(out, True, True, False)
    unify.SetLinearTolerance(FLAT)
    unify.SetAngularTolerance(FLAT)
    unify.Build()
    return unify.Shape()


def bent(shape: TopoDS_Shape, path, by: float) -> TopoDS_Shape:
    """shape, lying straight along path's first line, bent along the path and moved by mm
    along it: its solids as solids of flat faces, its other faces as shells, its curves as
    lines of points and its points. shape itself is left as it is."""
    copy = BRepBuilderAPI_Copy(shape, True, False).Shape()
    BRepMesh_IncrementalMesh(copy, BEND, False, MESH_ANGLE, False)
    builder, out = BRep_Builder(), TopoDS_Compound()
    builder.MakeCompound(out)
    in_solids, on_faces, on_edges = set(), set(), set()
    for solid in _items(copy, TopAbs_SOLID):
        faces = _items(solid, TopAbs_FACE)
        in_solids.update(f.__hash__() for f in faces)
        builder.Add(out, _faces_bent(faces, path, by, True))
    faces = _items(copy, TopAbs_FACE)
    loose = [f for f in faces if f.__hash__() not in in_solids]
    if loose:
        builder.Add(out, _faces_bent(loose, path, by, False))
    for face in faces:
        on_faces.update(e.__hash__() for e in _items(face, TopAbs_EDGE))
    reach = path.round  # curves: cut as for points as far from the path as its arcs' radius
    for e in _items(copy, TopAbs_EDGE):
        on_edges.update(v.__hash__() for v in _items(e, TopAbs_VERTEX))
        if e.__hash__() in on_faces:
            continue
        curve = BRepAdaptor_Curve(TopoDS.Edge_s(e))
        found = GCPnts_QuasiUniformDeflection(curve, BEND)
        if not found.IsDone() or found.NbPoints() < 2:
            continue
        line = [(p.X(), p.Y(), p.Z()) for p in (found.Value(i) for i in range(1, found.NbPoints() + 1))]
        cuts = stations(path, by, reach)
        step = min((b - a for a, b in zip(cuts, cuts[1:]) if b > a), default=math.inf)
        fine = [line[0]]
        for p, q in zip(line, line[1:]):
            count = max(1, math.ceil(abs(path.along(q) - path.along(p)) / step)) if math.isfinite(step) else 1
            fine += [tuple(a + (b - a) * k / count for a, b in zip(p, q)) for k in range(1, count + 1)]
        placed = [gp_Pnt(*path.place(p, by)) for p in fine]
        for p, q in zip(placed, placed[1:]):
            if p.Distance(q) > WELD:
                builder.Add(out, BRepBuilderAPI_MakeEdge(p, q).Edge())
    for v in _items(copy, TopAbs_VERTEX):
        if v.__hash__() not in on_edges:
            p = BRep_Tool.Pnt_s(TopoDS.Vertex_s(v))
            builder.Add(out, BRepBuilderAPI_MakeVertex(gp_Pnt(*path.place((p.X(), p.Y(), p.Z()), by))).Vertex())
    return out


def soft(shape: TopoDS_Shape) -> list:
    """The edges of a bent shape between flat faces that are pieces of one curved face,
    meeting at less than SOFT: [(end, end, a face's normal, the other's)], as (x, y, z)."""
    found = TopTools_IndexedDataMapOfShapeListOfShape()
    TopExp.MapShapesAndAncestors_s(shape, TopAbs_EDGE, TopAbs_FACE, found)
    out = []
    for i in range(1, found.Extent() + 1):
        normals = []
        for face in found.FindFromIndex(i):
            surface = BRepAdaptor_Surface(TopoDS.Face_s(face))
            if surface.GetType() != GeomAbs_Plane:
                break
            d = surface.Plane().Axis().Direction()
            sign = -1.0 if face.Orientation() == TopAbs_REVERSED else 1.0
            normals.append((sign * d.X(), sign * d.Y(), sign * d.Z()))
        if len(normals) != 2:
            continue
        cosine = sum(a * b for a, b in zip(*normals))
        if cosine < math.cos(SOFT):
            continue
        curve = BRepAdaptor_Curve(TopoDS.Edge_s(found.FindKey(i)))
        p, q = curve.Value(curve.FirstParameter()), curve.Value(curve.LastParameter())
        out.append(((p.X(), p.Y(), p.Z()), (q.X(), q.Y(), q.Z()), normals[0], normals[1]))
    return out
