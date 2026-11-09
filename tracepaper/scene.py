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
as SVG path data (region_d, curves_d): lines, circular and elliptical arcs and
polynomial splines exactly, other curves (hyperbolas, rational splines) within TOLERANCE.
OpenCascade's hidden-line removal runs in a child process (_in_child), since on some
freeform faces it never returns.

Needs OCP, OpenCascade's Python binding, which CadQuery and build123d install.
"""
from __future__ import annotations

import io
import math
import multiprocessing
import struct
import warnings
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator, Optional

from OCP.Bnd import Bnd_Box
from OCP.BRep import BRep_Builder, BRep_Tool
from OCP.BRepAdaptor import BRepAdaptor_Curve
from OCP.BRepAlgoAPI import BRepAlgoAPI_Common, BRepAlgoAPI_Section
from OCP.BRepBndLib import BRepBndLib
from OCP.BRepBuilderAPI import BRepBuilderAPI_Copy, BRepBuilderAPI_MakeFace, BRepBuilderAPI_Transform
from OCP.BRepGProp import BRepGProp
from OCP.BRepLib import BRepLib, BRepLib_ToolTriangulatedShape
from OCP.BRepMesh import BRepMesh_IncrementalMesh
from OCP.BRepTools import BRepTools, BRepTools_WireExplorer
from OCP.BRepAdaptor import BRepAdaptor_Surface
from OCP.GeomAbs import (GeomAbs_BezierCurve, GeomAbs_BSplineCurve, GeomAbs_C1, GeomAbs_Circle, GeomAbs_Cone,
                         GeomAbs_Cylinder, GeomAbs_Ellipse, GeomAbs_Line, GeomAbs_Plane, GeomAbs_Sphere,
                         GeomAbs_Torus)
from OCP.GeomConvert import GeomConvert_ApproxCurve, GeomConvert_BSplineCurveToBezierCurve
from OCP.gp import gp_Ax1, gp_Ax2, gp_Ax3, gp_Dir, gp_Pln, gp_Pnt, gp_Trsf, gp_Vec
from OCP.GProp import GProp_GProps
from OCP.HLRAlgo import HLRAlgo_Projector
from OCP.HLRBRep import HLRBRep_Algo, HLRBRep_HLRToShape, HLRBRep_PolyAlgo, HLRBRep_PolyHLRToShape
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
JOIN = 1 / SCALE  # mm: edges whose ends are closer than this are drawn on from one to the next
HLR_SECONDS = 30  # how long OpenCascade's exact hidden-line removal may take before the mesh's is used
HLR_MESH = 3e-5  # of the scene's size, and at least HLR_MESH_LEAST mm: how far the mesh it then uses
HLR_MESH_LEAST = 1e-3  # may be from the faces,
HLR_ANGLE = 0.1  # radians: and how far its triangles may turn from one to the next
STRAY = 1e-3  # of a part's size, and at least STRAY_LEAST mm: how far its projected edges may
STRAY_LEAST = 0.01  # run outside it before the exact hidden-line removal is taken to have failed
STRAY_SAMPLES = 32  # points along each edge looked at for that
LENGTH_SLACK = 1e-3  # of a section's length: how far a Boolean's regions may miss the plane's edges
SPREAD = 0.25  # how much an outline must run in one main direction for hatching to follow it
SNAP = 5  # degrees: outlines within this of the sheet's x and y are hatched at 45° to them


# ---- The tree ---------------------------------------------------------------------------

@dataclass
class Node:
    """A named node of a scene. key is its path of names ("Screws / screw 1"); a part has a
    shape, in the scene's coordinates, and a group has children. color is the colour the
    file gives a part, (r, g, b, opacity) from 0 to 1 in sRGB, as CSS has them, or None.
    soft: for a part bent along a path, the edges between its flat faces that aren't its
    own (bend.soft), which a projection leaves out."""
    name: str
    key: str
    shape: Optional[TopoDS_Shape] = None
    children: list = field(default_factory=list)
    color: Optional[tuple] = None
    soft: Optional[list] = None


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


def posed(scene: Scene, moves: list) -> Scene:
    """The scene with its nodes moved (SPEC.md §2.13): moves [(keys, kind, axis, through,
    amount)] in order, each turning the nodes keys names about axis through a point by amount
    degrees ("turn") or sliding them along axis by amount mm ("slide"), all given in the
    scene's coordinates at rest; or, with a motion.Path for axis, bending their parts along
    it, moved amount mm along it ("follow"): that first, before any turn or slide. A node
    inside a moved one moves with it. The scene itself is left as it is: the moved shapes
    share its geometry, placed elsewhere, but for the bent ones, which are made anew.
    Raises ValueError for a node following two paths."""
    own, follow = {}, {}
    for keys, kind, axis, through, amount in moves:
        if kind == "follow":
            for key in keys:
                if key in follow:
                    raise ValueError(f"{key} follows two paths; a node follows one")
                follow[key] = (axis, amount)
            continue
        t = gp_Trsf()
        if kind == "turn":
            t.SetRotation(gp_Ax1(gp_Pnt(*through), gp_Dir(*axis)), math.radians(amount))
        else:
            t.SetTranslation(gp_Vec(*(amount * c for c in _unit(axis))))
        for key in keys:
            own[key] = t.Multiplied(own[key]) if key in own else t  # after the moves before it

    def walk(nodes, above: Optional[gp_Trsf], along: Optional[tuple]) -> list:
        out = []
        for n in nodes:
            at, path = above, along
            if n.key in own:
                at = own[n.key] if at is None else at.Multiplied(own[n.key])  # its own, then the nodes' around it
            if n.key in follow:
                if path is not None:
                    raise ValueError(f"{n.key} follows two paths, its own and that of a node around it; "
                                     f"a node follows one")
                path = follow[n.key]
            shape, soft = n.shape, n.soft
            if shape is not None and path is not None:
                from . import bend
                shape = bend.bent(shape, *path)
                soft = bend.soft(shape)
            if shape is not None and at is not None:
                shape = shape.Moved(TopLoc_Location(at))
                soft = soft and [tuple(_moved(v, at, k < 2) for k, v in enumerate(e)) for e in soft]
            out.append(Node(n.name, n.key, shape, walk(n.children, at, path), n.color, soft))
        return out

    return Scene(walk(scene.roots, None, None))


def _moved(v: tuple, t: gp_Trsf, point: bool) -> tuple:
    """A point, or a direction, moved by t."""
    moved = gp_Pnt(*v).Transformed(t) if point else gp_Vec(*v).Transformed(t)
    return (moved.X(), moved.Y(), moved.Z())


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
    [(x, y)]; and outlines, the edges of a solid's section that OpenCascade's Boolean
    couldn't make regions of (_missed)."""
    node: Node
    regions: list
    curves: list
    points: list = field(default_factory=list)
    outlines: list = field(default_factory=list)


def cut(scene: Scene, pl: Plane) -> list:
    """The section of each part through pl: [Cut], in the tree's order, parts the plane
    misses left out."""
    size = _half_size(scene) * 4
    sheet = BRepBuilderAPI_MakeFace(gp_Pln(pl.ax3()), -size, size, -size, size).Face()
    out = []
    for part in scene.parts():
        regions, curves, outlines = [], [], []
        for solid in _items(part.shape, TopAbs_SOLID):
            common = BRepAlgoAPI_Common(solid, sheet)
            found = common.Shape() if common.IsDone() else None
            if found is not None:
                regions += _items(_to_plane(found, pl), TopAbs_FACE)
            if not _analytic(solid):
                missed = _missed(solid, sheet, found)
                if missed is not None:
                    outlines += _items(_to_plane(missed, pl), TopAbs_EDGE)
        loose = _loose_faces(part.shape)
        if loose is not None:
            section = BRepAlgoAPI_Section(loose, sheet)
            if section.IsDone():
                curves += _items(_to_plane(section.Shape(), pl), TopAbs_EDGE)
        points = [(x, y) for x, y, z in _points(part.shape, pl) if abs(z) <= PLANAR]
        if regions or curves or points or outlines:
            out.append(Cut(part, regions, curves, points, outlines))
    return out


ANALYTIC = {GeomAbs_Plane, GeomAbs_Cylinder, GeomAbs_Cone, GeomAbs_Sphere, GeomAbs_Torus}


def _analytic(shape: TopoDS_Shape) -> bool:
    """Whether all of shape's faces lie on planes, cylinders, cones, spheres and tori."""
    return all(BRepAdaptor_Surface(TopoDS.Face_s(f)).GetType() in ANALYTIC for f in _items(shape, TopAbs_FACE))


def _length(shape: TopoDS_Shape) -> float:
    """How long shape's edges are together, each counted once."""
    props = GProp_GProps()
    BRepGProp.LinearProperties_s(shape, props, True)
    return props.Mass()


def _missed(solid: TopoDS_Shape, sheet: TopoDS_Shape, regions: Optional[TopoDS_Shape]) -> Optional[TopoDS_Shape]:
    """The edges where sheet cuts solid, if the regions OpenCascade's Boolean gave for that
    (a shape, or None) don't add up to them, else None. On some freeform faces it leaves part
    or all of a section out without an error, for a plane along a knot line of a loft, where
    its section by the same plane is right."""
    section = BRepAlgoAPI_Section(solid, sheet)
    if not section.IsDone():
        return None
    edges = section.Shape()
    want = _length(edges)
    got = _length(regions) if regions is not None else 0.0
    return edges if abs(want - got) > LENGTH_SLACK * want + JOIN else None


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

    Exact: OpenCascade's hidden-line removal on the faces themselves. On some freeform faces
    (lofts through sections of lines and arcs) that goes wrong: outlines kilometres long, or
    no answer at all. So it runs in a child process, and if its edges run outside their
    parts, it fails or it takes longer than HLR_SECONDS, the drawing comes from OpenCascade's
    hidden-line removal on a fine mesh of the faces instead, to within HLR_MESH of the
    scene's size. Raises ValueError if that fails too."""
    parts = scene.parts()
    lined = [p for p in parts if _items(p.shape, TopAbs_EDGE)]  # points only: nothing to hide
    shapes = [p.shape for p in lined]
    found = _in_child(_hidden_lines, (shapes, pl, None), HLR_SECONDS) if shapes else []
    if found is None:
        mesh = max(HLR_MESH_LEAST, _half_size(scene) * HLR_MESH)
        found = _in_child(_hidden_lines, (shapes, pl, mesh), HLR_SECONDS)
    if found is None:
        raise ValueError("OpenCascade's hidden-line removal failed on this view, exactly and on a mesh")
    edges = {p.key: e for p, e in zip(lined, found)}
    out = []
    for part in parts:
        visible, smooth, hidden = edges.get(part.key, ([], [], []))
        if part.soft:
            visible, hidden = (_own_edges(found, part.soft, pl) for found in (visible, hidden))
        points = [(x, y) for x, y, _ in _points(part.shape, pl)]
        if visible or smooth or hidden or points:
            out.append(Projection(part, visible, smooth, hidden, points))
    return out


def _own_edges(edges: list, soft: list, pl: Plane) -> list:
    """edges, of a bent part seen along pl's normal, without those that lie on one of soft
    (bend.soft), the edges between the flat pieces of its curved faces: but for where such a
    face turns away from view, its outline there."""
    def flat(p) -> tuple:
        d = (p[0] - pl.origin[0], p[1] - pl.origin[1], p[2] - pl.origin[2])
        return (sum(a * b for a, b in zip(d, pl.xdir)), sum(a * b for a, b in zip(d, pl.ydir)))

    def facing(n) -> float:
        return sum(a * b for a, b in zip(n, pl.normal))

    inner = [(flat(p), flat(q)) for p, q, n1, n2 in soft if facing(n1) * facing(n2) > PLANAR]
    if not inner:
        return edges
    # By where they are, in squares the size of a usual one.
    size = max(JOIN, sum(math.dist(p, q) for p, q in inner) / len(inner))
    cells = {}
    for p, q in inner:
        for i in range(math.floor(min(p[0], q[0]) / size) - 1, math.floor(max(p[0], q[0]) / size) + 2):
            for j in range(math.floor(min(p[1], q[1]) / size) - 1, math.floor(max(p[1], q[1]) / size) + 2):
                cells.setdefault((i, j), []).append((p, q))

    def on(point: tuple, p: tuple, q: tuple) -> bool:
        d, w = (q[0] - p[0], q[1] - p[1]), (point[0] - p[0], point[1] - p[1])
        length = math.hypot(*d)
        if length <= JOIN:
            return math.hypot(*w) <= JOIN
        t = (w[0] * d[0] + w[1] * d[1]) / length
        return -JOIN <= t <= length + JOIN and abs(w[0] * d[1] - w[1] * d[0]) / length <= JOIN

    out = []
    for edge in edges:
        c = BRepAdaptor_Curve(TopoDS.Edge_s(edge))
        u0, u1 = c.FirstParameter(), c.LastParameter()
        a, m, b = ((v.X(), v.Y()) for v in (c.Value(u0), c.Value((u0 + u1) / 2), c.Value(u1)))
        near = cells.get((math.floor(m[0] / size), math.floor(m[1] / size)), [])
        if not any(on(a, p, q) and on(m, p, q) and on(b, p, q) for p, q in near):
            out.append(edge)
    return out


def _hidden_lines(shapes: list, pl: Plane, mesh: Optional[float]) -> Optional[list]:
    """The edges of shapes, seen along pl's normal and hiding each other, in pl's coordinates:
    [(visible, smooth, hidden)] for each. By OpenCascade's exact hidden-line removal if mesh
    is None, and None if any of its edges run outside their shape; else on a mesh of the
    faces, within mesh mm."""
    projector = HLRAlgo_Projector(gp_Ax2(gp_Pnt(*pl.origin), gp_Dir(*pl.normal), gp_Dir(*pl.xdir)))
    if mesh is None:
        prepared = [_for_hidden_lines(s) for s in shapes]
        algo = HLRBRep_Algo()
        for s in prepared:
            algo.Add(s)
        algo.Projector(projector)
        algo.Update()
        algo.Hide()
        hlr = HLRBRep_HLRToShape(algo)
    else:
        # A copy of each, so that the mesh doesn't stay on the scene's faces for the 3D view.
        prepared = [_for_hidden_lines(BRepBuilderAPI_Copy(s, False, False).Shape()) for s in shapes]
        algo = HLRBRep_PolyAlgo()
        for s in prepared:
            BRepMesh_IncrementalMesh(s, mesh, False, HLR_ANGLE, False)  # not in parallel: a fork has no threads
            algo.Load(s)
        algo.Projector(projector)
        algo.Update()
        hlr = HLRBRep_PolyHLRToShape()
        hlr.Update(algo)

    def edges(s: Optional[TopoDS_Shape]) -> list:
        return [] if s is None or s.IsNull() else [e for e in _items(s, TopAbs_EDGE) if _drawable(e)]

    out = [(edges(hlr.VCompound(s)) + edges(hlr.OutLineVCompound(s)), edges(hlr.Rg1LineVCompound(s)),
            edges(hlr.HCompound(s)) + edges(hlr.OutLineHCompound(s))) for s in prepared]
    if mesh is None and any(_strays(shape, [e for kind in found for e in kind], pl)
                            for shape, found in zip(shapes, out)):
        return None
    return out


def _drawable(edge: TopoDS_Shape) -> bool:
    """Whether an edge has a length to draw: hidden-line removal leaves bits of none."""
    c = BRepAdaptor_Curve(TopoDS.Edge_s(edge))
    u0, u1 = c.FirstParameter(), c.LastParameter()
    p = c.Value(u0)
    return any(p.Distance(c.Value(u0 + (u1 - u0) * k / 4)) > JOIN for k in (1, 2, 3, 4))


def _strays(shape: TopoDS_Shape, edges: list, pl: Plane) -> bool:
    """Whether any of edges, of shape seen along pl's normal, runs outside shape there."""
    if not edges:
        return False
    box = Bnd_Box()
    BRepBndLib.AddOptimal_s(_to_plane(shape, pl), box, False, False)
    x0, y0, _, x1, y1, _ = box.Get()
    slack = max(STRAY_LEAST, STRAY * max(x1 - x0, y1 - y0))
    x0, y0, x1, y1 = x0 - slack, y0 - slack, x1 + slack, y1 + slack
    for edge in edges:
        c = BRepAdaptor_Curve(TopoDS.Edge_s(edge))
        u0, u1 = c.FirstParameter(), c.LastParameter()
        for k in range(STRAY_SAMPLES + 1):
            p = c.Value(u0 + (u1 - u0) * k / STRAY_SAMPLES)
            if not (x0 <= p.X() <= x1 and y0 <= p.Y() <= y1):
                return True
    return False


def _in_child(work, args: tuple, seconds: float) -> Optional[list]:
    """work(*args), which gives [(edges, ...)] or None, worked out in a child process: None if
    it says so, fails, crashes or takes longer than seconds, which in OpenCascade's own code
    holds up the whole process. Where there is no fork (Windows), in this process."""
    if "fork" not in multiprocessing.get_all_start_methods():
        try:
            return work(*args)
        except Exception:  # OpenCascade's errors, such as Standard_DomainError
            return None
    context = multiprocessing.get_context("fork")
    receive, send = context.Pipe(duplex=False)
    with warnings.catch_warnings():  # forking the viewer's server: the child only works this out
        warnings.simplefilter("ignore", DeprecationWarning)
        child = context.Process(target=_child, args=(send, work, args), daemon=True)
        child.start()
    send.close()
    try:
        found = receive.recv() if receive.poll(seconds) else None
    except (EOFError, OSError):  # it crashed
        found = None
    finally:
        if child.is_alive():
            child.kill()
        child.join()
        receive.close()
    return None if found is None else [tuple(_items(_unpacked(data), TopAbs_EDGE) for data in kinds)
                                       for kinds in found]


def _child(send, work, args: tuple) -> None:
    """In the child process: work(*args), sent back as a compound for each list of edges, in
    OpenCascade's BRep format, or None."""
    _die_with_parent()
    try:
        found = work(*args)
        send.send(None if found is None else [tuple(_packed(_compound(edges)) for edges in kinds)
                                              for kinds in found])
    except BaseException:
        send.send(None)


def _die_with_parent() -> None:
    """On Linux, have this process killed when the one that forked it ends, however it ends:
    else one stuck in OpenCascade outlives a viewer that was killed, holding its port."""
    try:
        import ctypes
        import signal
        ctypes.CDLL(None, use_errno=True).prctl(1, signal.SIGKILL)  # PR_SET_PDEATHSIG
    except (OSError, AttributeError):
        pass


def _packed(shape: TopoDS_Shape) -> bytes:
    out = io.BytesIO()
    BRepTools.Write_s(shape, out)  # to 17 digits; BinTools' binary format doesn't read back from memory
    return out.getvalue()


def _unpacked(data: bytes) -> TopoDS_Shape:
    shape = TopoDS_Shape()
    BRepTools.Read_s(shape, io.BytesIO(data), BRep_Builder())
    return shape


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


def _edge_commands(edge) -> tuple:
    """SVG commands drawing edge in its direction of travel, in the xy plane, on from its
    start point: (commands, its start point, its end point)."""
    edge = TopoDS.Edge_s(edge)
    c = BRepAdaptor_Curve(edge)
    u0, u1 = c.FirstParameter(), c.LastParameter()
    back = edge.Orientation() == TopAbs_REVERSED
    a, b = (u1, u0) if back else (u0, u1)
    p0, p1 = c.Value(a), c.Value(b)
    out = []
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
    try:
        pieces = _beziers(curve, a, b) if curve is not None else []
    except Exception:  # OpenCascade's Standard_DomainError, for a piece too short: approximated
        pieces = []
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
    """A face in the xy plane as path data: each of its wires, closed. Fill it evenodd.
    Each wire is one loop: its edges are drawn on from where the last one ended, which
    may be a little off where the next starts (within the vertex's tolerance), and joined
    by a line where they're further apart."""
    face = TopoDS.Face_s(face)
    out = []
    for wire in _items(face, TopAbs_WIRE):
        explorer = BRepTools_WireExplorer(TopoDS.Wire_s(wire), face)
        last = None
        while explorer.More():
            edge = explorer.Current()
            cmds, start, end = _edge_commands(edge.Oriented(explorer.Orientation()))
            if last is None:
                out.append(f"M{_pt(start)}")
            elif start.Distance(last) > JOIN:
                out.append(f"L{_pt(start)}")
            out += cmds
            last = end
            explorer.Next()
        if last is not None:
            out.append("Z")
    return " ".join(out)


def curves_d(edges) -> str:
    """Edges in the xy plane as path data, joined where one ends where the next starts."""
    out, last = [], None
    for edge in edges:
        cmds, start, end = _edge_commands(edge)
        if last is None or start.Distance(last) > JOIN:
            out.append(f"M{_pt(start)}")
        out += cmds
        last = end
    return " ".join(out)


def outline_direction(faces) -> float:
    """The direction the outlines of faces in the xy plane mostly run in, and square to it,
    in degrees from -45 to 45: sections are hatched at 45° to it (ISO 128-3). 0 for outlines
    mostly along x and y, within SNAP, and for those without a main direction, such as
    a ring's or an octagon's."""
    c = s = total = 0.0
    for face in faces:
        for edge in _items(face, TopAbs_EDGE):
            curve = BRepAdaptor_Curve(TopoDS.Edge_s(edge))
            u0, u1 = curve.FirstParameter(), curve.LastParameter()
            n = 1 if curve.GetType() == GeomAbs_Line else 16
            for k in range(n):
                p, v = gp_Pnt(), gp_Vec()
                curve.D1(u0 + (u1 - u0) * (k + 0.5) / n, p, v)
                length = v.Magnitude() * abs(u1 - u0) / n
                a = 4 * math.atan2(v.Y(), v.X())  # a direction, the opposite one and those square to it alike
                c, s, total = c + length * math.cos(a), s + length * math.sin(a), total + length
    if total <= 0 or math.hypot(c, s) < SPREAD * total:
        return 0.0
    angle = math.degrees(math.atan2(s, c)) / 4
    return 0.0 if abs(angle) < SNAP else angle
