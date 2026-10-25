"""Scenes (SPEC.md §2): exact solids, regions and curves, read from STEP files.

A scene is a tree of named nodes whose leaves are shapes, as OpenCascade holds them:
boundary representations, faces on planes, cylinders, cones, spheres, tori and B-spline
surfaces, bounded by exact curves. Its parts are

- solids,
- faces without a volume: regions, when the whole scene lies in one plane (a 2D scene),
  else surfaces,
- curves: reference geometry, such as an axis or a path,
- points: reference geometry too, such as a spline's control points or a contact point.

The server shows a scene in 3D as triangles (glb) and draws exact sections and
projections of it (cut, project), which drawing.py turns into sheets. Curves come out
as SVG path data (path_d): lines, circular and elliptical arcs and polynomial splines
exactly, other curves (hyperbolas, rational splines) within TOLERANCE.

Needs OCP, OpenCascade's Python binding, which CadQuery and build123d install.
"""
from __future__ import annotations

import math
import struct
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator, Optional

from OCP.Bnd import Bnd_Box
from OCP.BRep import BRep_Builder, BRep_Tool
from OCP.BRepAdaptor import BRepAdaptor_Curve
from OCP.BRepAlgoAPI import BRepAlgoAPI_Common, BRepAlgoAPI_Section
from OCP.BRepBndLib import BRepBndLib
from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeFace, BRepBuilderAPI_Transform
from OCP.BRepLib import BRepLib, BRepLib_ToolTriangulatedShape
from OCP.BRepMesh import BRepMesh_IncrementalMesh
from OCP.BRepTools import BRepTools_WireExplorer
from OCP.BRepAdaptor import BRepAdaptor_Surface
from OCP.GeomAbs import (GeomAbs_BezierCurve, GeomAbs_BSplineCurve, GeomAbs_C1, GeomAbs_Circle, GeomAbs_Cone,
                         GeomAbs_Cylinder, GeomAbs_Ellipse, GeomAbs_Line, GeomAbs_Plane, GeomAbs_Sphere)
from OCP.GeomConvert import GeomConvert_ApproxCurve, GeomConvert_BSplineCurveToBezierCurve
from OCP.gp import gp_Ax2, gp_Ax3, gp_Dir, gp_Pln, gp_Pnt, gp_Trsf
from OCP.HLRAlgo import HLRAlgo_Projector
from OCP.HLRBRep import HLRBRep_Algo, HLRBRep_HLRToShape
from OCP.IFSelect import IFSelect_RetDone
from OCP.Quantity import Quantity_Color, Quantity_ColorRGBA
from OCP.ShapeUpgrade import ShapeUpgrade_ShapeDivideClosed
from OCP.STEPCAFControl import STEPCAFControl_Reader
from OCP.TCollection import TCollection_ExtendedString
from OCP.TDataStd import TDataStd_Name
from OCP.TDF import TDF_Label, TDF_LabelSequence
from OCP.TDocStd import TDocStd_Document
from OCP.TopAbs import (TopAbs_EDGE, TopAbs_FACE, TopAbs_FORWARD, TopAbs_REVERSED, TopAbs_SHELL, TopAbs_SOLID,
                        TopAbs_VERTEX, TopAbs_WIRE)
from OCP.TopExp import TopExp_Explorer
from OCP.TopLoc import TopLoc_Location
from OCP.TopoDS import TopoDS, TopoDS_Compound, TopoDS_Shape
from OCP.XCAFDoc import XCAFDoc_ColorTool, XCAFDoc_ColorType, XCAFDoc_DocumentTool

from .model import glb_bytes

SEP = " / "
TOLERANCE = 1e-4  # mm: how closely curves SVG can't draw exactly are approximated
MESH_DEFLECTION = 0.05  # mm: how far the 3D view's triangles may be from the faces
MESH_ANGLE = 0.3  # radians: and how far they may turn from one to the next
PLANAR = 1e-6  # mm and radians: how close faces must be to one plane to make a 2D scene
SCALE = 1e4  # fmt(): coordinates in path data to 0.1 µm


# ---- The tree ---------------------------------------------------------------------------

@dataclass
class Node:
    """A named node of a scene. key is its path of names ("Screws / screw 1"); a part has a
    shape, in the scene's coordinates, and a group has children. color is the colour the
    file gives a part, (r, g, b, opacity) from 0 to 1 in sRGB, as CSS has them, or None."""
    name: str
    key: str
    shape: Optional[TopoDS_Shape] = None
    children: list = field(default_factory=list)
    color: Optional[tuple] = None


@dataclass
class Plane:
    """A plane of a scene: point = origin + x·xdir + y·(normal × xdir), normal towards the viewer."""
    origin: tuple
    normal: tuple
    xdir: tuple

    @property
    def ydir(self) -> tuple:
        return _cross(self.normal, self.xdir)

    def ax3(self) -> gp_Ax3:
        return gp_Ax3(gp_Pnt(*self.origin), gp_Dir(*self.normal), gp_Dir(*self.xdir))


def _cross(a, b) -> tuple:
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])


def _unit(v) -> tuple:
    n = math.sqrt(sum(c * c for c in v))
    return tuple(c / n for c in v)


def plane(origin, normal, xdir) -> Plane:
    """A Plane, with normal and xdir made unit length and xdir square to normal."""
    n = _unit(normal)
    d = sum(a * b for a, b in zip(xdir, n))
    return Plane(tuple(float(c) for c in origin), n, _unit(tuple(a - d * b for a, b in zip(xdir, n))))


class Scene:
    """A scene read from a STEP file: its tree (roots) of named nodes."""

    def __init__(self, roots: list):
        self.roots = roots

    def nodes(self) -> Iterator[Node]:
        def walk(nodes):
            for n in nodes:
                yield n
                yield from walk(n.children)
        return walk(self.roots)

    def parts(self) -> list:
        return [n for n in self.nodes() if n.shape is not None]

    def flat(self) -> Optional[Plane]:
        """The plane a 2D scene lies in: one with no solids, whose faces all lie in one plane;
        else None. Seen from the side its normal points to (+z for a scene in z = 0), with x
        along the scene's x, or y if the plane is square to x."""
        found = None
        for part in self.parts():
            if _items(part.shape, TopAbs_SOLID):
                return None
            for face in _items(part.shape, TopAbs_FACE):
                s = BRepAdaptor_Surface(TopoDS.Face_s(face))
                if s.GetType() != GeomAbs_Plane:
                    return None
                pln = s.Plane()
                if found is None:
                    found = pln
                elif not found.Position().IsCoplanar(pln.Position(), PLANAR, PLANAR):
                    return None
        if found is None:
            return None
        n = found.Axis().Direction()
        normal = (n.X(), n.Y(), n.Z())
        k = max(range(3), key=lambda i: abs(normal[i]))
        if normal[k] < 0:
            normal = tuple(-c for c in normal)
        o = found.Location()
        d = sum(a * b for a, b in zip((o.X(), o.Y(), o.Z()), normal))
        origin = tuple(d * c for c in normal)  # the plane's point nearest the scene's origin
        xdir = (1, 0, 0) if abs(normal[0]) < 1 - PLANAR else (0, 1, 0)
        return plane(origin, normal, xdir)

    def box(self) -> tuple:
        """(x0, y0, z0, x1, y1, z1) round everything."""
        b = Bnd_Box()
        for part in self.parts():
            BRepBndLib.Add_s(part.shape, b)
        return b.Get() if not b.IsVoid() else (0, 0, 0, 0, 0, 0)


def find_nodes(scene: Scene, entries) -> set:
    """The keys of the nodes entries name: each a key ("Screws / screw 1"), or a plain name,
    which names every node of that name. Raises ValueError for one that names none."""
    if isinstance(entries, str):
        entries = [entries]
    nodes = list(scene.nodes())
    out = set()
    for entry in entries:
        found = {n.key for n in nodes if n.key == entry or (SEP not in str(entry) and n.name == entry)}
        if not found:
            raise ValueError(f"no node {entry!r}; the scene's nodes are: {', '.join(n.key for n in nodes)}")
        out |= found
    return out


def has_faces(shape: TopoDS_Shape) -> bool:
    """Whether a part has faces: else it is curves or points, reference geometry."""
    return TopExp_Explorer(shape, TopAbs_FACE).More()


def _items(shape: TopoDS_Shape, kind) -> list:
    out, e = [], TopExp_Explorer(shape, kind)
    while e.More():
        out.append(e.Current())
        e.Next()
    return out


def _name(label: TDF_Label) -> Optional[str]:
    attr = TDataStd_Name()
    if label.FindAttribute(TDataStd_Name.GetID_s(), attr):
        text = attr.Get().ToExtString()
        return text if text and not text.isdigit() else None  # a bare number: OpenCascade's own label
    return None


_SURFACES = (XCAFDoc_ColorType.XCAFDoc_ColorSurf, XCAFDoc_ColorType.XCAFDoc_ColorGen,
             XCAFDoc_ColorType.XCAFDoc_ColorCurv)  # which of a label's colours counts, first


def _color(label: Optional[TDF_Label], kinds=_SURFACES) -> Optional[tuple]:
    """The colour a label gives, (r, g, b, opacity) in sRGB, or None."""
    for kind in kinds if label is not None else ():
        c = Quantity_ColorRGBA()
        if XCAFDoc_ColorTool.GetColor_s(label, kind, c):
            rgb = c.GetRGB()  # OpenCascade's are linear; STEP files and CSS have sRGB
            return tuple(Quantity_Color.Convert_LinearRGB_To_sRGB_s(v) for v in (rgb.Red(), rgb.Green(), rgb.Blue())) \
                + (c.Alpha(),)
    return None


def read_step(path) -> Scene:
    """The scene in a STEP file: its assemblies as groups and its shapes as parts, named as
    the file names them. Instances keep the names of their own (screw 1, screw 2); a single
    top assembly is left out, so that its parts are the top level. A part has the colour
    the file gives it: its instance's, its shape's, the commonest of its solids' or faces',
    or the nearest group's. Raises ValueError for a file OpenCascade can't read."""
    doc = TDocStd_Document(TCollection_ExtendedString("XmlOcaf"))
    reader = STEPCAFControl_Reader()
    reader.SetNameMode(True)
    reader.SetColorMode(True)
    reader.SetLayerMode(False)
    if reader.ReadFile(str(path)) != IFSelect_RetDone or not reader.Transfer(doc):
        raise ValueError(f"{path} is not a STEP file OpenCascade can read")
    tool = XCAFDoc_DocumentTool.ShapeTool_s(doc.Main())

    def color(labels, shape: TopoDS_Shape, above: Optional[tuple]) -> Optional[tuple]:
        kinds = _SURFACES if has_faces(shape) else _SURFACES[::-1]  # curves: their own colour first
        for label in labels:
            if (c := _color(label, kinds)) is not None:
                return c
        subs = TDF_LabelSequence()
        tool.GetSubShapes_s(labels[-1], subs)
        found = Counter(c for i in range(1, subs.Length() + 1) if (c := _color(subs.Value(i), kinds)) is not None)
        return found.most_common(1)[0][0] if found else above

    def node(label: TDF_Label, above: str, location: TopLoc_Location, fallback: str,
             inherited: Optional[tuple]) -> Optional[Node]:
        name, instance = _name(label), None
        if tool.IsReference_s(label):
            target = TDF_Label()
            tool.GetReferredShape_s(label, target)
            location = location.Multiplied(tool.GetLocation_s(label))
            instance, label, name = label, target, name or _name(target)
        name = name or fallback
        key = f"{above}{SEP}{name}" if above else name
        if tool.IsAssembly_s(label):
            kids = TDF_LabelSequence()
            tool.GetComponents_s(label, kids)
            own = _color(instance) or _color(label)
            children = _children([kids.Value(i) for i in range(1, kids.Length() + 1)], key, location, own or inherited)
            return Node(name, key, children=children)
        shape = tool.GetShape_s(label)
        if shape.IsNull():
            return None
        return Node(name, key, shape=shape.Moved(location), color=color([instance, label], shape, inherited))

    def _children(labels, above, location, inherited=None) -> list:
        out, seen = [], {}
        for i, label in enumerate(labels, 1):
            n = node(label, above, location, f"part {i}", inherited)
            if n is None:
                continue
            count = seen[n.name] = seen.get(n.name, 0) + 1
            if count > 1:  # names must tell siblings apart
                n = _renamed(n, f"{n.name} ({count})", above)
            out.append(n)
        return out

    free = TDF_LabelSequence()
    tool.GetFreeShapes(free)
    roots = _children([free.Value(i) for i in range(1, free.Length() + 1)], "", TopLoc_Location())
    if len(roots) == 1 and roots[0].children:
        roots = [_renamed(c, c.name, "") for c in roots[0].children]
    return Scene(roots)


def _renamed(n: Node, name: str, above: str) -> Node:
    key = f"{above}{SEP}{name}" if above else name
    return Node(name, key, n.shape, [_renamed(c, c.name, key) for c in n.children], n.color)


# ---- 3D: triangles for the viewer ---------------------------------------------------------

def glb(scene: Scene, colors: dict, hidden=()) -> bytes:
    """The scene as a glTF binary for the 3D view (SPEC.md §2.8): a node per tree node, each
    part's faces a primitive each, in the part's colour from colors (key -> (r, g, b), sRGB
    from 0 to 1) and as see-through as the file makes it, millimetres with z up turned into
    glTF's y up on the root. Faces only: curves aren't drawn in 3D yet. Nodes whose keys are
    in hidden start hidden."""
    data = bytearray()
    views, accessors, meshes, materials, nodes = [], [], [], [], []
    hidden = set(hidden)

    def add(blob: bytes, target: int) -> int:
        views.append({"buffer": 0, "byteOffset": len(data), "byteLength": len(blob), "target": target})
        data.extend(blob)
        data.extend(b"\0" * (-len(data) % 4))
        return len(views) - 1

    def material(n: Node) -> int:
        rgb = [Quantity_Color.Convert_sRGB_To_LinearRGB_s(v) for v in colors.get(n.key, (0.6, 0.6, 0.6))]
        opacity = n.color[3] if n.color else 1.0
        m = {"name": n.key, "pbrMetallicRoughness": {"baseColorFactor": [*rgb, opacity],  # linear, as glTF's are
                                                     "metallicFactor": 0, "roughnessFactor": 0.7},
             "doubleSided": True}
        if opacity < 1:
            m["alphaMode"] = "BLEND"
        materials.append(m)
        return len(materials) - 1

    def part_mesh(n: Node) -> Optional[int]:
        BRepMesh_IncrementalMesh(n.shape, MESH_DEFLECTION, False, MESH_ANGLE, True)
        m = material(n)
        primitives = []
        for face in _items(n.shape, TopAbs_FACE):
            face = TopoDS.Face_s(face)
            loc = TopLoc_Location()
            tri = BRep_Tool.Triangulation_s(face, loc)
            if tri is None or tri.NbTriangles() == 0:
                continue
            if not tri.HasNormals():
                BRepLib_ToolTriangulatedShape.ComputeNormals_s(face, tri)
            trsf = loc.Transformation()
            flip = face.Orientation() == TopAbs_REVERSED
            nn = tri.NbNodes()
            pos, nor = [], []
            for i in range(1, nn + 1):
                p = tri.Node(i).Transformed(trsf)
                pos += (p.X(), p.Y(), p.Z())
                d = tri.Normal(i).Transformed(trsf)
                nor += (-d.X(), -d.Y(), -d.Z()) if flip else (d.X(), d.Y(), d.Z())
            idx = []
            for i in range(1, tri.NbTriangles() + 1):
                a, b, c = tri.Triangle(i).Get()
                idx += (a - 1, c - 1, b - 1) if flip else (a - 1, b - 1, c - 1)
            big = nn > 65535
            accessors.append({"bufferView": add(struct.pack(f"<{len(pos)}f", *pos), 34962), "componentType": 5126,
                              "count": nn, "type": "VEC3", "min": [min(pos[k::3]) for k in range(3)],
                              "max": [max(pos[k::3]) for k in range(3)]})
            position = len(accessors) - 1
            accessors.append({"bufferView": add(struct.pack(f"<{len(nor)}f", *nor), 34962), "componentType": 5126,
                              "count": nn, "type": "VEC3"})
            normal = len(accessors) - 1
            accessors.append({"bufferView": add(struct.pack(f"<{len(idx)}{'I' if big else 'H'}", *idx), 34963),
                              "componentType": 5125 if big else 5123, "count": len(idx), "type": "SCALAR"})
            primitives.append({"attributes": {"POSITION": position, "NORMAL": normal},
                               "indices": len(accessors) - 1, "material": m})
        if not primitives:
            return None
        meshes.append({"name": n.key, "primitives": primitives})
        return len(meshes) - 1

    def gltf_node(n: Node) -> int:
        entry = {"name": n.name}
        if n.key in hidden:
            entry["extras"] = {"tracepaper": {"hidden": True}}
        if n.shape is not None:
            mesh = part_mesh(n)
            if mesh is not None:
                entry["mesh"] = mesh
        kids = [gltf_node(c) for c in n.children]
        if kids:
            entry["children"] = kids
        nodes.append(entry)
        return len(nodes) - 1

    tops = [gltf_node(n) for n in scene.roots]
    # z up into glTF's y up, as CAD exports carry it: a quarter turn about x on the root.
    nodes.append({"name": "scene", "rotation": [-math.sqrt(0.5), 0, 0, math.sqrt(0.5)], "children": tops})
    gltf = {"asset": {"version": "2.0", "generator": "Tracepaper"}, "scene": 0,
            "scenes": [{"nodes": [len(nodes) - 1]}], "nodes": nodes, "meshes": meshes, "materials": materials,
            "accessors": accessors, "bufferViews": views, "buffers": [{"byteLength": len(data)}]}
    if not meshes:
        for key in ("meshes", "materials", "accessors", "bufferViews", "buffers"):
            del gltf[key]
        data = bytearray()
    return glb_bytes(gltf, bytes(data))


# ---- 2D: exact sections and projections -----------------------------------------------------

def _to_plane(shape: TopoDS_Shape, pl: Plane) -> TopoDS_Shape:
    """shape moved so that pl is the xy plane: its points' x and y are their plane coordinates."""
    t = gp_Trsf()
    t.SetTransformation(pl.ax3())  # from the global system into pl's
    return BRepBuilderAPI_Transform(shape, t, True).Shape()


def _half_size(scene: Scene) -> float:
    x0, y0, z0, x1, y1, z1 = scene.box()
    return max(1.0, math.dist((x0, y0, z0), (x1, y1, z1)))


@dataclass
class Cut:
    """What a plane cuts from one part, in plane coordinates (the plane is z = 0): regions
    (faces) from a solid, curves (edges) from a surface, and its points that lie in the plane,
    [(x, y)]."""
    node: Node
    regions: list
    curves: list
    points: list = field(default_factory=list)


def cut(scene: Scene, pl: Plane) -> list:
    """The section of each part through pl: [Cut], in the tree's order, parts the plane
    misses left out."""
    size = _half_size(scene) * 4
    sheet = BRepBuilderAPI_MakeFace(gp_Pln(pl.ax3()), -size, size, -size, size).Face()
    out = []
    for part in scene.parts():
        regions, curves = [], []
        for solid in _items(part.shape, TopAbs_SOLID):
            common = BRepAlgoAPI_Common(solid, sheet)
            if common.IsDone():
                regions += _items(_to_plane(common.Shape(), pl), TopAbs_FACE)
        loose = _loose_faces(part.shape)
        if loose is not None:
            section = BRepAlgoAPI_Section(loose, sheet)
            if section.IsDone():
                curves += _items(_to_plane(section.Shape(), pl), TopAbs_EDGE)
        points = [(x, y) for x, y, z in _points(part.shape, pl) if abs(z) <= PLANAR]
        if regions or curves or points:
            out.append(Cut(part, regions, curves, points))
    return out


def _points(shape: TopoDS_Shape, pl: Plane) -> list:
    """The points of shape, its vertices on no edge, in pl's coordinates: [(x, y, distance
    from the plane)]."""
    on_edges = set()
    for edge in _items(shape, TopAbs_EDGE):
        on_edges.update(v.__hash__() for v in _items(edge, TopAbs_VERTEX))
    out = []
    for v in _items(shape, TopAbs_VERTEX):
        if v.__hash__() in on_edges:
            continue
        p = BRep_Tool.Pnt_s(TopoDS.Vertex_s(v))
        d = (p.X() - pl.origin[0], p.Y() - pl.origin[1], p.Z() - pl.origin[2])
        out.append(tuple(sum(a * b for a, b in zip(d, axis)) for axis in (pl.xdir, pl.ydir, pl.normal)))
    return out


def _loose_faces(shape: TopoDS_Shape) -> Optional[TopoDS_Shape]:
    """The faces of shape that bound no solid, as one compound, or None."""
    in_solids = set()
    for solid in _items(shape, TopAbs_SOLID):
        in_solids.update(f.__hash__() for f in _items(solid, TopAbs_FACE))
    loose = [f for f in _items(shape, TopAbs_FACE) if f.__hash__() not in in_solids]
    return _compound(loose) if loose else None


def _loose_edges(shape: TopoDS_Shape) -> list:
    """The edges of shape that bound no face: curves."""
    on_faces = set()
    for face in _items(shape, TopAbs_FACE):
        on_faces.update(e.__hash__() for e in _items(face, TopAbs_EDGE))
    return [e for e in _items(shape, TopAbs_EDGE) if e.__hash__() not in on_faces]


def _compound(shapes) -> TopoDS_Compound:
    c, b = TopoDS_Compound(), BRep_Builder()
    b.MakeCompound(c)
    for s in shapes:
        b.Add(c, s)
    return c


@dataclass
class Flat:
    """A part of a 2D scene in plane coordinates: its regions (faces), curves (edges) and
    points [(x, y)]."""
    node: Node
    regions: list
    curves: list
    points: list = field(default_factory=list)


def flat(scene: Scene, pl: Plane) -> list:
    """Each part of a 2D scene in pl's coordinates: [Flat]."""
    out = []
    for part in scene.parts():
        moved = _to_plane(part.shape, pl)
        out.append(Flat(part, _items(moved, TopAbs_FACE), _loose_edges(moved),
                        [(x, y) for x, y, _ in _points(part.shape, pl)]))
    return out


@dataclass
class Projection:
    """A part's edges as seen along a plane's normal, in plane coordinates: visible ones
    (sharp edges and outlines), smooth ones (where faces meet tangentially) and hidden ones,
    and its points [(x, y)], all of them."""
    node: Node
    visible: list
    smooth: list
    hidden: list
    points: list = field(default_factory=list)


SIMPLE_SURFACES = {GeomAbs_Plane, GeomAbs_Cylinder, GeomAbs_Cone, GeomAbs_Sphere}


def _for_hidden_lines(shape: TopoDS_Shape) -> TopoDS_Shape:
    """shape as hidden line removal gets it right: with its closed tori and freeform faces split
    in two, since OpenCascade's loses outlines where such a face's seam lies on one, and the
    edges between faces marked as smooth where they are, so that the splits don't show."""
    if all(BRepAdaptor_Surface(TopoDS.Face_s(f)).GetType() in SIMPLE_SURFACES for f in _items(shape, TopAbs_FACE)):
        return shape
    divide = ShapeUpgrade_ShapeDivideClosed(shape)
    divide.SetNbSplitPoints(1)
    divide.Perform()
    out = divide.Result()
    BRepLib.EncodeRegularity_s(out, 1e-3)
    return out


def project(scene: Scene, pl: Plane) -> list:
    """Each part as seen looking at pl along its normal, with hidden lines: [Projection].
    Exact: OpenCascade's hidden line removal on the faces themselves."""
    algo = HLRBRep_Algo()
    parts = scene.parts()
    shapes = {}
    for part in parts:
        if _items(part.shape, TopAbs_EDGE):  # points only: nothing to hide
            shapes[part.key] = _for_hidden_lines(part.shape)
            algo.Add(shapes[part.key])
    algo.Projector(HLRAlgo_Projector(gp_Ax2(gp_Pnt(*pl.origin), gp_Dir(*pl.normal), gp_Dir(*pl.xdir))))
    algo.Update()
    algo.Hide()
    hlr = HLRBRep_HLRToShape(algo)
    out = []

    def edges(s: Optional[TopoDS_Shape]) -> list:
        return [] if s is None or s.IsNull() else _items(s, TopAbs_EDGE)

    for part in parts:
        s = shapes.get(part.key)
        visible = edges(hlr.VCompound(s)) + edges(hlr.OutLineVCompound(s)) if s else []
        smooth = edges(hlr.Rg1LineVCompound(s)) if s else []
        hidden = edges(hlr.HCompound(s)) + edges(hlr.OutLineHCompound(s)) if s else []
        points = [(x, y) for x, y, _ in _points(part.shape, pl)]
        if visible or smooth or hidden or points:
            out.append(Projection(part, visible, smooth, hidden, points))
    return out


def bounds(shapes) -> Optional[tuple]:
    """(x0, y0, x1, y1) round shapes that lie in z = 0, or None if there are none."""
    b = Bnd_Box()
    for s in shapes:
        BRepBndLib.Add_s(s, b, False)
    if b.IsVoid():
        return None
    x0, y0, _, x1, y1, _ = b.Get()
    return x0, y0, x1, y1


# ---- Curves as SVG path data -------------------------------------------------------------------

def fmt(x: float) -> str:
    v = round(x * SCALE) / SCALE
    return f"{v:.4f}".rstrip("0").rstrip(".") if v else "0"  # and no "-0"


def _pt(p) -> str:
    return f"{fmt(p.X())} {fmt(p.Y())}"


def _edge_commands(edge, start_new: bool, last) -> tuple:
    """SVG commands drawing edge in its direction of travel, in the xy plane: (commands,
    its start point, its end point). Starts with M unless start_new is False and it starts
    where last ended."""
    edge = TopoDS.Edge_s(edge)
    c = BRepAdaptor_Curve(edge)
    u0, u1 = c.FirstParameter(), c.LastParameter()
    back = edge.Orientation() == TopAbs_REVERSED
    a, b = (u1, u0) if back else (u0, u1)
    p0, p1 = c.Value(a), c.Value(b)
    out = []
    if start_new or last is None or p0.Distance(last) > 1e-7:
        out.append(f"M{_pt(p0)}")
    kind = c.GetType()
    if kind == GeomAbs_Line:
        out.append(f"L{_pt(p1)}")
    elif kind in (GeomAbs_Circle, GeomAbs_Ellipse):
        conic = c.Circle() if kind == GeomAbs_Circle else c.Ellipse()
        rx = conic.Radius() if kind == GeomAbs_Circle else conic.MajorRadius()
        ry = rx if kind == GeomAbs_Circle else conic.MinorRadius()
        ax = conic.Position()
        turn = math.degrees(math.atan2(ax.XDirection().Y(), ax.XDirection().X())) if kind == GeomAbs_Ellipse else 0
        ccw = (ax.Direction().Z() > 0) != back  # parameters run counter-clockwise about the axis
        span = abs(u1 - u0)
        steps = 2 if span > math.pi * 1.5 else 1  # a whole conic, or most of one: two arcs
        for k in range(1, steps + 1):
            q = c.Value(a + (b - a) * k / steps)
            large = int(span / steps > math.pi)
            out.append(f"A{fmt(rx)} {fmt(ry)} {fmt(turn)} {large} {int(ccw)} {_pt(q)}")
    else:
        out += _spline_commands(c, kind, a, b)
    return out, p0, p1


def _beziers(curve, u0: float, u1: float) -> list:
    """A B-spline's piece from u0 to u1 as Bézier curves: [(degree, rational, poles)]."""
    conv = GeomConvert_BSplineCurveToBezierCurve(curve, min(u0, u1), max(u0, u1), 1e-9)
    out = []
    for i in range(1, conv.NbArcs() + 1):
        arc = conv.Arc(i)
        out.append((arc.Degree(), arc.IsRational(), [arc.Pole(j) for j in range(1, arc.NbPoles() + 1)]))
    return out[::-1] if u0 > u1 else out


def _spline_commands(c, kind, a: float, b: float) -> list:
    """SVG commands for a curve from parameter a to b, other than a line or conic: exact
    for polynomial splines of degree 3 or less, else approximated within TOLERANCE."""
    curve = None
    if kind == GeomAbs_BSplineCurve:
        curve = c.BSpline()
    elif kind == GeomAbs_BezierCurve:
        from OCP.GeomConvert import GeomConvert
        curve = GeomConvert.CurveToBSplineCurve_s(c.Bezier())
    pieces = _beziers(curve, a, b) if curve is not None else []
    if not pieces or any(rational or degree > 3 for degree, rational, _ in pieces):
        # Cubic pieces within TOLERANCE, over c's own range, from its first parameter to its
        # last (C2 would raise the degree instead of adding pieces).
        spline = GeomConvert_ApproxCurve(c, TOLERANCE, GeomAbs_C1, 400, 3).Curve()
        pieces = _beziers(spline, spline.FirstParameter(), spline.LastParameter())
        if any(degree > 3 for degree, _, _ in pieces):  # not seen, but then: a fine polyline
            n = 256
            return [f"L{_pt(c.Value(a + (b - a) * k / n))}" for k in range(1, n + 1)]
        if a > b:
            pieces = pieces[::-1]
    out = []
    for degree, _, poles in pieces:
        if a > b:
            poles = poles[::-1]
        if degree == 1:
            out.append(f"L{_pt(poles[1])}")
        elif degree == 2:
            out.append(f"Q{_pt(poles[1])} {_pt(poles[2])}")
        else:
            out.append(f"C{_pt(poles[1])} {_pt(poles[2])} {_pt(poles[3])}")
    return out


def region_d(face) -> str:
    """A face in the xy plane as path data: each of its wires, closed. Fill it evenodd."""
    face = TopoDS.Face_s(face)
    out = []
    for wire in _items(face, TopAbs_WIRE):
        explorer = BRepTools_WireExplorer(TopoDS.Wire_s(wire), face)
        last, first = None, True
        while explorer.More():
            edge = explorer.Current()
            cmds, _, last = _edge_commands(edge.Oriented(explorer.Orientation()), first, last)
            out += cmds
            first = False
            explorer.Next()
        if not first:
            out.append("Z")
    return " ".join(out)


def curves_d(edges) -> str:
    """Edges in the xy plane as path data, joined where one ends where the next starts."""
    out, last = [], None
    for edge in edges:
        cmds, _, last = _edge_commands(edge, False, last)
        out += cmds
    return " ".join(out)
