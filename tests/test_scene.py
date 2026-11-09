"""Scenes from STEP files: their trees, and exact sections, projections and 2D drawings.

Uses the test geometry in tests/geometry (make.py there writes it). Skipped without OCP.
"""
import json
import math
import re
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

try:
    from tracepaper import drawing as D, scene as S
    from OCP.BRepAdaptor import BRepAdaptor_Curve
    from OCP.GCPnts import GCPnts_AbscissaPoint
    from OCP.TopoDS import TopoDS
except ImportError:  # no OpenCascade here
    S = None

from tracepaper import views as V
from tracepaper.model import read_glb

GEOMETRY = Path(__file__).resolve().parent / "geometry"
SVG, INK, TP = "{http://www.w3.org/2000/svg}", "{http://www.inkscape.org/namespaces/inkscape}", "{urn:tracepaper:0}"


def length(edges) -> float:
    # Along their curves: BRepGProp crashes on the edges hidden line removal makes.
    return sum(GCPnts_AbscissaPoint.Length_s(BRepAdaptor_Curve(TopoDS.Edge_s(e))) for e in edges)


def colours_elsewhere(path: Path) -> None:
    """A STEP file with colours where other CAD programs than CadQuery put them: on one of a
    box's two instances, on the solids inside a part (red, green, green), and on a group's
    instance rather than on the part in it."""
    from OCP.BRep import BRep_Builder
    from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox
    from OCP.gp import gp_Pnt, gp_Trsf, gp_Vec
    from OCP.Quantity import Quantity_Color, Quantity_ColorRGBA, Quantity_TypeOfColor
    from OCP.STEPCAFControl import STEPCAFControl_Writer
    from OCP.STEPControl import STEPControl_AsIs
    from OCP.TCollection import TCollection_ExtendedString
    from OCP.TDataStd import TDataStd_Name
    from OCP.TDocStd import TDocStd_Document
    from OCP.TopLoc import TopLoc_Location
    from OCP.TopoDS import TopoDS_Compound
    from OCP.XCAFDoc import XCAFDoc_ColorType, XCAFDoc_DocumentTool

    doc = TDocStd_Document(TCollection_ExtendedString("XmlOcaf"))
    tool = XCAFDoc_DocumentTool.ShapeTool_s(doc.Main())
    colours = XCAFDoc_DocumentTool.ColorTool_s(doc.Main())

    def named(label, name):
        TDataStd_Name.Set_s(label, TCollection_ExtendedString(name))
        return label

    def colour(label, r, g, b):
        rgb = Quantity_Color(r, g, b, Quantity_TypeOfColor.Quantity_TOC_sRGB)
        colours.SetColor(label, Quantity_ColorRGBA(rgb, 1.0), XCAFDoc_ColorType.XCAFDoc_ColorSurf)

    def at(x, y):
        t = gp_Trsf()
        t.SetTranslation(gp_Vec(x, y, 0))
        return TopLoc_Location(t)

    def box(x, size):
        return BRepPrimAPI_MakeBox(gp_Pnt(x, 0, 0), size, size, size).Shape()

    top = named(tool.NewShape(), "top")
    solids, builder = TopoDS_Compound(), BRep_Builder()
    builder.MakeCompound(solids)
    parts = [box(3 * i, 2) for i in range(3)]
    for solid in parts:
        builder.Add(solids, solid)
    blocks = named(tool.AddShape(solids, False), "blocks")
    for solid, rgb in zip(parts, [(1, 0, 0), (0, 1, 0), (0, 1, 0)]):
        colour(tool.AddSubShape(blocks, solid), *rgb)
    named(tool.AddComponent(top, blocks, TopLoc_Location()), "blocks")
    cube = named(tool.AddShape(box(0, 4), False), "cube")
    named(tool.AddComponent(top, cube, at(20, 0)), "plain")
    colour(named(tool.AddComponent(top, cube, at(30, 0)), "blue"), 0, 0, 1)
    group = named(tool.NewShape(), "group")
    named(tool.AddComponent(group, named(tool.AddShape(box(0, 3), False), "inner"), TopLoc_Location()), "inner")
    colour(named(tool.AddComponent(top, group, at(0, 20)), "group"), 1, 1, 0)
    tool.UpdateAssemblies()
    writer = STEPCAFControl_Writer()
    writer.Transfer(doc, STEPControl_AsIs)
    writer.Write(str(path))


@unittest.skipIf(S is None, "needs OCP")
class SceneTest(unittest.TestCase):
    def test_tree(self):
        hinge = S.read_step(GEOMETRY / "hinge.step")
        self.assertEqual([n.name for n in hinge.roots], ["Leaves", "pin", "Screws", "Frame"])  # not "hinge"
        keys = [n.key for n in hinge.nodes()]
        self.assertIn("Screws / screw 4", keys)  # each instance of the one screw under its own name
        self.assertIn("Frame / door", keys)
        self.assertEqual(len(hinge.parts()), 9)
        self.assertEqual(S.find_nodes(hinge, ["door", "Leaves / leaf 1"]), {"Frame / door", "Leaves / leaf 1"})
        with self.assertRaisesRegex(ValueError, "no node 'gate'"):
            S.find_nodes(hinge, ["gate"])

    def test_a_2d_scene_lies_in_its_plane(self):
        profile = S.read_step(GEOMETRY / "profile.step")
        pl = profile.flat()
        self.assertEqual((pl.origin, pl.normal, pl.xdir), ((0, 0, 0), (0, 0, 1), (1, 0, 0)))
        self.assertIsNone(S.read_step(GEOMETRY / "bracket.step").flat())
        regions = {f.node.key: [S.region_d(r) for r in f.regions] for f in S.flat(profile, pl)}
        # The channel: its outline with rounded corners, a round hole and a slot, exactly.
        channel = regions["channel"][0]
        self.assertEqual(channel.count("Z"), 3)
        self.assertIn("A3 3 0 0 1 -27 -12", channel)
        self.assertRegex(channel, r"M20 0 A4 4 0 [01] [01] 12 0 A4 4 0 [01] [01] 20 0 Z")
        self.assertRegex(regions["insert"][0], r"A14 6 0 [01] [01] -14 -24")  # an ellipse, as one

    def test_sections_are_exact(self):
        prims = S.read_step(GEOMETRY / "primitives.step")
        cut = {c.node.key: [S.region_d(r) for r in c.regions] for c in S.cut(prims, S.plane((0, 2, 0), (0, -1, 0), (1, 0, 0)))}
        self.assertEqual(cut["box"], ["M10 20 L-10 20 L-10 0 L10 0 L10 20 Z"])
        r = math.sqrt(100 - 4)  # the sphere, 2 mm off its centre
        self.assertIn(f"A{S.fmt(r)} {S.fmt(r)} 0", cut["sphere"][0])
        self.assertIn(" C", cut["cone"][0])  # hyperbolas, as cubic pieces
        self.assertEqual(len(cut["torus"]), 2)
        tilted = {c.node.key: S.region_d(c.regions[0]) for c in S.cut(prims, S.plane((0, 0, 8), (0, -0.5, 1), (1, 0, 0)))}
        # The cylinder cut at a slant: an ellipse whose long axis is along the slope.
        self.assertIn("A11.1803 10 90", tilted["cylinder"])

    def test_negative_coordinates_keep_their_sign(self):
        clip = S.read_step(GEOMETRY / ".clip" / "insertion=2.5.step")
        arm = next(f for f in S.flat(clip, clip.flat()) if f.node.key == "clip")
        xs = [float(v) for v in re.findall(r"-?\d+\.?\d*", " ".join(S.region_d(r) for r in arm.regions))[0::2]]
        self.assertLess(min(xs), -1)

    def test_projection_keeps_outlines_on_seams(self):
        prims = S.read_step(GEOMETRY / "primitives.step")
        torus = next(p for p in S.project(prims, S.plane((0, 0, 0), (0, -1, 0), (1, 0, 0))) if p.node.key == "torus")
        # From the front, a torus lying flat is a stadium: its outline all there, and no line
        # where it was split to find it.
        self.assertAlmostEqual(length(torus.visible), 2 * 20 + 2 * math.pi * 3, delta=0.05)
        box = next(p for p in S.project(prims, S.plane((0, 0, 0), (1, -1, 1), (1, 1, 0))) if p.node.key == "box")
        self.assertEqual(len(box.visible), 9)  # a cube's nine edges seen from a corner
        # its three others hidden, each 20 mm seen at a slant; and bits that its neighbour hides
        self.assertGreaterEqual(length(box.hidden), 3 * 20 * math.sqrt(2 / 3) - 0.01)

    def test_glb(self):
        hinge = S.read_step(GEOMETRY / "hinge.step")
        colors = {k: D.rgb(c) for k, c in D.colors(hinge).items()}
        gltf, binary = read_glb_bytes(S.glb(hinge, colors, {"Frame / door"}))
        names = {n.get("name") for n in gltf["nodes"]}
        self.assertTrue({"Leaves", "leaf 1", "Screws", "screw 3", "Frame", "door"} <= names)
        door = next(n for n in gltf["nodes"] if n.get("name") == "door")
        self.assertTrue(door["extras"]["tracepaper"]["hidden"])
        root = gltf["nodes"][gltf["scenes"][0]["nodes"][0]]
        self.assertAlmostEqual(root["rotation"][0], -math.sqrt(0.5))  # z up into glTF's y up
        self.assertEqual(len(gltf["materials"]), 9)  # a colour for each part
        self.assertGreater(len(binary), 1000)

    def test_parts_keep_the_file_s_colours(self):
        corner = S.read_step(GEOMETRY / "corner.step")
        color = {p.key: p.color for p in corner.parts()}
        for got, want in zip(color["frame"], (0.42, 0.45, 0.5, 1)):
            self.assertAlmostEqual(got, want, places=3)  # sRGB, as CadQuery was given it
        self.assertAlmostEqual(color["glass"][3], 0.35, places=3)
        self.assertIsNone(color["seal"])
        palette = D.colors(corner)
        self.assertEqual(palette["frame"], "#6b7380")
        self.assertEqual(palette["seal"], D.PALETTE[0])  # the first part without a colour
        self.assertEqual(palette["Screws / screw 1"], palette["Screws / screw 2"])
        gltf, _ = read_glb_bytes(S.glb(corner, {k: D.rgb(c) for k, c in palette.items()}))
        material = {m["name"]: m for m in gltf["materials"]}
        frame, glass = (material[k]["pbrMetallicRoughness"]["baseColorFactor"] for k in ("frame", "glass"))
        self.assertAlmostEqual(frame[0], 0.147, places=3)  # glTF's colours are linear: 0x6b is 0.147
        self.assertNotIn("alphaMode", material["frame"])
        self.assertAlmostEqual(glass[3], 0.35, places=3)  # see-through
        self.assertEqual(material["glass"]["alphaMode"], "BLEND")

    def test_colours_on_instances_solids_and_groups(self):
        with tempfile.TemporaryDirectory() as tmp:
            colours_elsewhere(Path(tmp) / "colours.step")
            scene = S.read_step(Path(tmp) / "colours.step")
        color = {p.key: p.color and tuple(round(c, 3) for c in p.color) for p in scene.parts()}
        self.assertEqual(color, {"blocks": (0, 1, 0, 1),  # the commonest of its solids'
                                 "plain": None, "blue": (0, 0, 1, 1),  # one shape, its instances' own
                                 "group / inner": (1, 1, 0, 1)})  # from the group


def read_glb_bytes(data: bytes):
    import tempfile
    with tempfile.NamedTemporaryFile(suffix=".glb") as f:
        f.write(data)
        f.flush()
        return read_glb(f.name)


def face(*loops):
    """A face in the plane z = 0 bounded by loops of (x, y) corners, the first the outside."""
    from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeFace, BRepBuilderAPI_MakePolygon
    from OCP.gp import gp_Pnt

    def wire(points):
        polygon = BRepBuilderAPI_MakePolygon()
        for x, y in points:
            polygon.Add(gp_Pnt(x, y, 0))
        polygon.Close()
        return polygon.Wire()

    make = BRepBuilderAPI_MakeFace(wire(loops[0]), True)
    for hole in loops[1:]:
        w = wire(hole)
        w.Reverse()
        make.Add(w)
    return make.Face()


def turned(points, degrees):
    a = math.radians(degrees)
    return [(x * math.cos(a) - y * math.sin(a), x * math.sin(a) + y * math.cos(a)) for x, y in points]


@unittest.skipIf(S is None, "needs OCP")
class HardCasesTest(unittest.TestCase):
    """What OpenCascade gets wrong, or gives in a form that is easy to get wrong."""

    def test_a_loop_is_one_path_however_far_apart_its_edges_ends_are(self):
        # A triangle whose middle edge runs 0.6 µm beside its corners, within their tolerance, as
        # a section of a torus can: drawn as one loop, closed where it started.
        from OCP.BRep import BRep_Builder
        from OCP.BRepBuilderAPI import (BRepBuilderAPI_MakeEdge, BRepBuilderAPI_MakeFace, BRepBuilderAPI_MakeVertex,
                                        BRepBuilderAPI_MakeWire)
        from OCP.gp import gp_Dir, gp_Lin, gp_Pnt

        def vertex(x, y):
            v = BRepBuilderAPI_MakeVertex(gp_Pnt(x, y, 0)).Vertex()
            BRep_Builder().UpdateVertex(v, 1e-6)
            return v

        a, b, c = vertex(0, 0), vertex(10, 0), vertex(10, 10)
        edges = [BRepBuilderAPI_MakeEdge(a, b).Edge(),
                 BRepBuilderAPI_MakeEdge(gp_Lin(gp_Pnt(10 + 6e-7, 0, 0), gp_Dir(0, 1, 0)), b, c).Edge(),
                 BRepBuilderAPI_MakeEdge(c, a).Edge()]
        triangle = BRepBuilderAPI_MakeFace(BRepBuilderAPI_MakeWire(*edges).Wire(), True).Face()
        d = S.region_d(triangle)
        self.assertEqual((d[0], d.count("M"), d.count("Z"), d[-1]), ("M", 1, 1, "Z"))
        self.assertEqual(S.curves_d(edges).count("M"), 1)  # within the path data's 0.1 µm: one line

    def test_pieces_of_curves_too_short_to_draw(self):
        from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeEdge
        from OCP.Geom import Geom_BSplineCurve
        from OCP.GeomAPI import GeomAPI_PointsToBSpline
        from OCP.gp import gp_Pnt
        from OCP.TColgp import TColgp_Array1OfPnt

        points = TColgp_Array1OfPnt(1, 5)
        for i, (x, y) in enumerate([(0, 0), (3, 2), (6, 1), (9, 4), (12, 0)], 1):
            points.SetValue(i, gp_Pnt(x, y, 0))
        curve = GeomAPI_PointsToBSpline(points).Curve()
        c = BRepAdaptor_Curve(BRepBuilderAPI_MakeEdge(curve).Edge())
        u = (c.FirstParameter() + c.LastParameter()) / 2
        # Converting a piece this short to Béziers raises Standard_DomainError: approximated instead.
        self.assertTrue(S._spline_commands(c, c.GetType(), u, u + 1e-12))
        self.assertFalse(S._drawable(BRepBuilderAPI_MakeEdge(curve, u, u + 1e-9).Edge()))
        self.assertTrue(S._drawable(BRepBuilderAPI_MakeEdge(curve).Edge()))

    def test_projection_without_stray_outlines(self):
        # Seen from the front, OpenCascade's exact hidden-line removal gives the duct an outline
        # metres long: the drawing comes from a mesh instead, within the duct.
        duct = S.read_step(GEOMETRY / "duct.step")
        x0, y0, z0, x1, y1, z1 = duct.box()
        found = S.project(duct, S.plane((0, 0, 0), *V.DIRECTIONS["front"]))
        edges = [e for p in found for e in p.visible + p.smooth + p.hidden]
        self.assertGreater(len(edges), 20)
        box = S.bounds(edges)
        self.assertGreater(box[0], x0 - 1)
        self.assertLess(box[2], x1 + 1)
        self.assertGreater(box[1], z0 - 1)
        self.assertLess(box[3], z1 + 1)
        self.assertGreater(box[2] - box[0], (x1 - x0) * 0.99)

    def test_hidden_line_removal_that_never_ends_or_crashes(self):
        import os
        import signal
        import time
        from unittest import mock

        bracket = S.read_step(GEOMETRY / "bracket.step")
        front = S.plane((0, 0, 0), *V.DIRECTIONS["front"])
        exact = S.project(bracket, front)
        real = S._hidden_lines
        for name, fail in (("hangs", lambda: time.sleep(60)), ("crashes", lambda: os.kill(os.getpid(), signal.SIGKILL))):
            with self.subTest(name), mock.patch.object(S, "HLR_SECONDS", 2):
                with mock.patch.object(S, "_hidden_lines", lambda shapes, pl, mesh: (
                        fail() if mesh is None else real(shapes, pl, mesh))):
                    start = time.monotonic()
                    meshed = S.project(bracket, front)
                self.assertLess(time.monotonic() - start, 20)
                self.assertEqual([p.node.key for p in meshed], [p.node.key for p in exact])
                self.assertTrue(all(p.visible for p in meshed))
        with mock.patch.object(S, "HLR_SECONDS", 1), mock.patch.object(S, "_hidden_lines", lambda *a: time.sleep(60)):
            with self.assertRaisesRegex(ValueError, "hidden-line removal failed"):
                S.project(bracket, front)

    def test_a_section_the_boolean_leaves_out_is_drawn_as_its_edges(self):
        # On some lofts, OpenCascade's Boolean gives nothing for a plane along a knot line,
        # without an error. Its section's edges are drawn then, so the part doesn't vanish.
        from unittest import mock

        class Nothing:
            def __init__(self, *args):
                pass

            def IsDone(self):
                return True

            def Shape(self):
                return S._compound([])

        duct = S.read_step(GEOMETRY / "duct.step")
        pl = S.plane((0, 0, 200), (0, 0, 1), (1, 0, 0))
        [whole] = S.cut(duct, pl)
        self.assertEqual((len(whole.regions), whole.outlines), (1, []))
        with mock.patch.object(S, "BRepAlgoAPI_Common", Nothing):
            [missed] = S.cut(duct, pl)
            sheet = D.draw(duct, V.view_spec({}, "z=200"), {}, "duct.step#z=200")
        self.assertEqual(missed.regions, [])
        self.assertAlmostEqual(S._length(S._compound(missed.outlines)), S._length(S._compound(
            [e for r in whole.regions for e in S._items(r, S.TopAbs_EDGE)])), places=3)
        edges = ET.fromstring(sheet.tostring().split("\n", 1)[1]).find(f".//{SVG}path[@id='air-section-edges']")
        self.assertIn("couldn't make regions", edges.find(f"{SVG}title").text)
        # Solids of planes, cylinders, cones, spheres and tori are left to the Boolean alone.
        with mock.patch.object(S, "BRepAlgoAPI_Common", Nothing):
            self.assertEqual(S.cut(S.read_step(GEOMETRY / "primitives.step"),
                                   S.plane((0, 0, 1), (0, 0, 1), (1, 0, 0))), [])

    def test_hatching_runs_at_45_degrees_to_the_main_outlines(self):
        wall = [(0, 0), (100, 0), (100, 6), (0, 6)]
        self.assertEqual(S.outline_direction([face(wall)]), 0)
        self.assertAlmostEqual(S.outline_direction([face(turned(wall, 30))]), 30, places=6)
        self.assertAlmostEqual(S.outline_direction([face(turned(wall, -20))]), -20, places=6)
        self.assertEqual(S.outline_direction([face(turned(wall, 3))]), 0)  # near enough the sheet's
        # A reducer's walls, slanting either way: each hatched at 45° to itself.
        self.assertAlmostEqual(S.outline_direction([face(turned(wall, 112))]), 22, places=6)
        self.assertAlmostEqual(S.outline_direction([face(turned(wall, 68))]), -22, places=6)
        ring = [(50 * math.cos(a), 50 * math.sin(a)) for a in (2 * math.pi * k / 90 for k in range(90))]
        self.assertEqual(S.outline_direction([face(ring, [(0.8 * x, 0.8 * y) for x, y in ring])]), 0)


@unittest.skipIf(S is None, "needs OCP")
class DrawingTest(unittest.TestCase):
    def test_section_with_annotations(self):
        scene = GEOMETRY / "bracket.step"
        meta = V.read_meta(scene)
        sheet = D.draw(S.read_step(scene), V.view_spec(meta, "section-a"), meta, "bracket.step#section-a",
                       hidden={"screw"})
        root = ET.fromstring(sheet.tostring())
        view = root.find(f"{SVG}metadata/{TP}view")
        self.assertEqual((view.get("kind"), view.get("normal"), view.get("view")), ("section", "0 -1 0", "section-a"))
        layers = {g.get(f"{INK}label"): g for g in root.findall(f"{SVG}g")}
        self.assertEqual(set(layers), {"body", "screw", "Dimensions", "Constraints"})
        self.assertEqual(layers["screw"].get("style"), "display:none")  # hidden at first
        self.assertEqual(layers["Dimensions"].get(f"{TP}role"), "annotations")
        d = root.find(f".//{SVG}g[@id='d-length']")
        self.assertEqual((d.get(f"{TP}dim"), d.get(f"{TP}offset")), ("linear", "-16"))
        self.assertIsNotNone(root.find(f".//{SVG}g[@id='d-corner']"))  # an angle
        cuts = [root.find(f".//{SVG}path[@id='body-cut-{i}']") for i in (1, 2)]  # either side of the hole
        self.assertIn("A3 3 0", cuts[1].get("d"))  # the fillets, exact
        self.assertTrue(all(c.get("fill").startswith("url(#tp-hatch") for c in cuts))

    def test_hidden_lines_are_a_layer_of_their_own(self):
        scene = GEOMETRY / "bracket.step"
        sheet = D.draw(S.read_step(scene), V.view_spec({}, "front"), {}, "bracket.step#front")
        root = ET.fromstring(sheet.tostring())
        hidden = root.find(f"{SVG}g[@{TP}role='hidden-lines']")
        self.assertEqual((hidden.get(f"{INK}label"), hidden.get("style")), ("Hidden lines", "display:none"))
        self.assertEqual(root.findall(f"{SVG}g")[0], hidden)  # under the parts
        self.assertIsNotNone(hidden.find(f".//{SVG}path[@id='body-hidden']"))

    def test_fields(self):
        scene = GEOMETRY / "clip.step"
        meta = V.read_meta(scene)
        frame = V.frames(scene, meta)["frames"][5]
        sheet = D.draw(S.read_step(frame["step"]), None, meta, "clip.step", fields=V.fields(scene, meta, frame),
                       ranges={"strain": (-2, 2)})
        root = ET.fromstring(sheet.tostring())
        layer = root.find(f".//{SVG}g[@{TP}field='strain']")
        self.assertEqual((layer.get(f"{TP}range"), layer.get(f"{TP}min"), layer.get(f"{TP}max"), layer.get(f"{TP}unit")),
                         ("-2 2", None, "1", "%"))
        # Under the parts, which are only outlined over it.
        names = [g.get(f"{INK}label") for g in root.findall(f"{SVG}g")]
        self.assertLess(names.index("Fields"), names.index("clip"))
        self.assertEqual(root.find(f".//{SVG}path[@id='clip-1']").get("fill"), None)
        tris = layer.findall(f"{SVG}g[@{TP}part='colours']/{SVG}path")
        self.assertEqual(len(tris), 480)
        # Each corner moved by its displacement, from where it was before.
        t = tris[-1]
        d = [float(v) for v in re.findall(r"-?\d+\.?\d*", t.get("d"))]
        p = [float(v) for v in t.get(f"{TP}p").split()]
        u = [float(v) for v in t.get(f"{TP}u").split()]
        for k in range(6):
            self.assertAlmostEqual(d[k], p[k] + u[k], places=3)

    def test_arrows_bands_and_elements(self):
        scene = GEOMETRY / "beam.step"
        meta = V.read_meta(scene)
        sheet = D.draw(S.read_step(scene), None, meta, "beam.step", fields=V.fields(scene, meta))
        root = ET.fromstring(sheet.tostring())
        layer = lambda name: root.find(f".//{SVG}g[@{TP}field='{name}']")  # noqa: E731
        part = lambda name, kind: layer(name).find(f"{SVG}g[@{TP}part='{kind}']")  # noqa: E731
        # A vector is arrows, its lengths' colours hidden at first; a number is colours alone.
        self.assertEqual(part("displacement", "colours").get("display"), "none")
        self.assertIsNone(part("stress", "colours").get("display"))
        self.assertIsNone(part("stress", "arrows"))
        # Over areas, arrows start at their points; on lines and points, as forces, they end there.
        self.assertEqual((layer("displacement").get(f"{TP}pivot"), layer("reaction").get(f"{TP}pivot")), ("tail", "tip"))
        # More than ARROWS_MAX of them are thinned out, evenly.
        at = [tuple(map(float, a.get(f"{TP}at").split())) for a in part("displacement", "arrows").findall(f"{SVG}path")]
        self.assertTrue(10 < len(at) < 155)
        self.assertGreaterEqual(min(math.dist(a, b) for i, a in enumerate(at) for b in at[:i]),
                                D.ARROW_GAP * sheet.font_size - 1e-6)
        # The longest is ARROW text heights long, and ends at its node.
        arrows = part("reaction", "arrows").findall(f"{SVG}path")
        self.assertEqual(len(arrows), 5)
        longest = max(arrows, key=lambda a: float(a.get(f"{TP}v")))
        d = [float(v) for v in re.findall(r"-?\d+\.?\d*(?:e-?\d+)?", longest.get("d"))]
        self.assertAlmostEqual(math.dist(d[0:2], d[4:6]), D.ARROW * sheet.font_size, places=3)
        self.assertEqual(d[4:6], [float(v) for v in longest.get(f"{TP}at").split()])
        # The load: a band along each line, its values at both ends.
        bands = part("load", "colours").findall(f"{SVG}path")
        self.assertEqual((len(bands), bands[-1].get("stroke-width"), bands[-1].get(f"{TP}v")), (30, "5", "1.9333 2"))
        # A quadrilateral's two triangles carry its number, for the mesh.
        tris = part("stress", "colours").findall(f"{SVG}path")
        self.assertEqual((len(tris), len({t.get(f"{TP}c") for t in tris})), (240, 120))
        # Fields with areas go under the parts.
        names = [g.get(f"{INK}label") for g in root.findall(f"{SVG}g")]
        self.assertLess(names.index("Fields"), names.index("beam"))

    def test_arrows_keep_one_scale_over_the_frames(self):
        from tracepaper.server import Scenes, View
        scenes, view = Scenes(), View("clip.step", "sheet", GEOMETRY / "clip.step")
        scale = {}
        for k in (0, 3, 5):
            layer = ET.fromstring(scenes.sheet(view, k).tostring()).find(f".//{SVG}g[@{TP}field='contact force']")
            scale[k] = None if layer is None else layer.get(f"{TP}scale")
        self.assertIsNone(scale[0])  # not touching yet
        self.assertIsNotNone(scale[3])
        self.assertEqual(scale[3], scale[5])

    def test_points_notes_and_a_frame_s_own_annotations(self):
        scene = GEOMETRY / "clip.step"
        meta = V.read_meta(scene)
        frames = V.frames(scene, meta)["frames"]

        def drawn(k):
            own = V.frame_meta(meta, frames[k])
            return ET.fromstring(D.draw(S.read_step(frames[k]["step"]), None, own, "clip.step").tostring())

        root = drawn(5)  # riding the bump
        dims = {g.get("id") for g in root.findall(f".//{SVG}g[@{TP}dim]")}
        self.assertEqual(dims, {"d-bump", "d-depth", "d-deflection"})
        self.assertEqual(root.find(f".//{SVG}g[@id='d-depth']").get(f"{TP}label"), "s = 2.5")
        notes = [t.text for t in root.findall(f".//{SVG}g[@{INK}label='Notes']/{SVG}text")]
        self.assertEqual((notes[0], notes[-1]), ("Key", "s = 2.5 mm: riding the bump"))
        # Points: dots a few pixels across, named in a tooltip; a curve dashed in its part's colour.
        contact = root.find(f".//{SVG}path[@id='contact-c-points']")
        self.assertEqual((contact.get("stroke-width"), contact.get("stroke-linecap"), contact.find(f"{SVG}title").text),
                         ("7", "round", "contact C"))
        handles = root.find(f".//{SVG}path[@id='centre-line-handles-points']")
        self.assertEqual(len(re.findall("M", handles.get("d"))), 2)
        curve = root.find(f".//{SVG}path[@id='centre-line-curve-curves']")
        self.assertEqual(curve.get("stroke-dasharray"), "6 4")
        self.assertIn(" C", curve.get("d"))  # the Bézier curve, as it is
        root = drawn(0)  # not touching yet: no contact, nothing bent, not in
        self.assertEqual({g.get("id") for g in root.findall(f".//{SVG}g[@{TP}dim]")}, {"d-bump"})
        self.assertIsNone(root.find(f".//{SVG}path[@id='contact-c-points']"))

    def test_text_is_sized_to_the_drawing(self):
        # About a 60th of the drawing, in the steps of ISO text heights: small enough on a detail
        # to read it under its dimensions, big enough on a building to read with it all in view.
        self.assertEqual([D.font_size(s) for s in (15, 23, 100, 150, 200, 1200)], [0.21875, 0.4375, 1.75, 2.5, 3.5, 20])
        self.assertEqual(D.font_size(42), D.font_size(44))  # a little bigger, the same
        scene = GEOMETRY / "snap.step"
        sheet = D.draw(S.read_step(scene), None, V.read_meta(scene), "snap.step")
        self.assertEqual(sheet.font_size, 0.21875)
        root = ET.fromstring(sheet.tostring())
        self.assertEqual(root.get("font-size"), "0.2188")
        # The notes and the dimensions round it no bigger than the drawing itself.
        self.assertLess(float(root.get("viewBox").split()[3]), 2 * 14.7)
        self.assertEqual(len(root.findall(f".//{SVG}g[@{TP}dim]")), 13)
        # A frame's text: the scene's, if given.
        self.assertEqual(D.draw(S.read_step(scene), None, {}, "snap.step", text=2.5).font_size, 2.5)

    def test_long_notes_are_broken_to_the_drawing_s_width(self):
        scene = S.read_step(GEOMETRY / "profile.step")
        width = lambda meta: float(ET.fromstring(D.draw(scene, None, meta, "profile.step").tostring())
                                   .get("viewBox").split()[2])
        long = {"notes": "Key: " + "a parameter = its value, " * 40}
        self.assertEqual(width(long), width({"notes": "Key"}))
        root = ET.fromstring(D.draw(scene, None, long, "profile.step").tostring())
        notes = [t.text for t in root.findall(f".//{SVG}g[@{INK}label='Notes']/{SVG}text")]
        self.assertGreater(len(notes), 2)
        self.assertEqual(" ".join(notes), long["notes"].strip())


class ViewsTest(unittest.TestCase):
    def test_views_drawn_on_the_fly(self):
        spec = V.view_spec({}, "x=12.5")
        self.assertEqual((spec["kind"], spec["origin"], spec["normal"], spec["xdir"]),
                         ("section", (12.5, 0.0, 0.0), (1, 0, 0), (0, 1, 0)))
        self.assertEqual(V.view_spec({}, "front")["kind"], "projection")
        self.assertIsNone(V.view_spec({}, "x=twelve"))
        self.assertIsNone(V.view_spec({}, "sideways"))
        self.assertEqual(V.split_name("a/b.step#x=1"), ("a/b.step", "x=1"))

    def test_named_views(self):
        meta = json.loads((GEOMETRY / "bracket.tp.json").read_text())
        self.assertEqual([v["name"] for v in V.named_views(meta)], ["section-a", "plan"])
        self.assertEqual(V.view_spec(meta, "plan")["xdir"], (1.0, 0.0, 0.0))
        # A standard direction's name would make bracket.step#top either.
        with self.assertRaisesRegex(ValueError, r"views\[0\]: top is the name of a standard direction"):
            V.named_views({"views": [{"name": "top", "normal": [0, 0, 1]}]})
        with self.assertRaisesRegex(ValueError, "name must be"):
            V.named_views({"views": [{"name": "a b", "normal": [0, 0, 1]}]})
        with self.assertRaisesRegex(ValueError, "not parallel"):
            V.named_views({"views": [{"name": "a", "normal": [0, 0, 1], "xdir": [0, 0, 2]}]})

    def test_frames(self):
        scene = GEOMETRY / "clip.step"
        f = V.frames(scene, V.read_meta(scene))
        self.assertEqual(f["axes"], [{"name": "insertion", "unit": "mm"}])
        self.assertEqual(len(f["frames"]), 13)
        self.assertEqual(f["frames"][3]["step"], (GEOMETRY / ".clip" / "insertion=1.5.step").resolve())
        self.assertEqual(f["quantities"][2], {"name": "strain", "unit": "%", "min": None, "max": 1.0})
        self.assertEqual(f["quantities"][3], {"name": "pull-out force", "unit": "N", "min": 15.0, "max": None})
        self.assertEqual(V.limits({"limit": 3}, "q"), (None, 3))  # as the first draft said max
        self.assertEqual(V.frames(scene, {}), {})
        bad = {"axes": [{"name": "a"}], "frames": [{"at": {"b": 1}, "step": "x.step"}]}
        with self.assertRaisesRegex(ValueError, "for each axis"):
            V.frames(scene, bad)
        outside = {"axes": [{"name": "a"}], "frames": [{"at": {"a": 1}, "step": "../x.step"}]}
        with self.assertRaisesRegex(ValueError, "in the scene's folder"):
            V.frames(scene, outside)

    def test_a_frame_gives_its_own(self):
        meta = {"annotations": [{"id": "a"}], "notes": "key", "title": "T",
                "views": [{"name": "s", "normal": [0, 0, 1], "annotations": [{"id": "b"}]},
                          {"name": "t", "normal": [1, 0, 0]}]}
        frame = {"meta": {"annotations": [{"id": "c"}], "views": [{"name": "s", "normal": [0, 1, 0]}]}}
        own = V.frame_meta(meta, frame)
        self.assertEqual((own["annotations"], own["notes"], own["title"]), ([{"id": "c"}], "key", "T"))
        self.assertEqual([v["name"] for v in own["views"]], ["s", "t"])
        self.assertEqual(V.view_spec(own, "s")["normal"], (0.0, 1.0, 0.0))  # the frame's, whole
        self.assertEqual(V.view_spec(own, "s")["annotations"], [])
        self.assertIs(V.frame_meta(meta, {"meta": {}}), meta)
        self.assertEqual(V.notes("a\nb"), ["a", "b"])
        with self.assertRaisesRegex(ValueError, "notes must be"):
            V.notes(3)

    def test_fields(self):
        scene = GEOMETRY / "clip.step"
        meta = V.read_meta(scene)
        frame = V.frames(scene, meta)["frames"][2]
        (field,) = V.fields(scene, meta, frame)
        self.assertEqual((field["name"], field["value"], field["displacement"]), ("strain", "strain", "u"))
        self.assertEqual(field["path"], (GEOMETRY / ".clip" / "insertion=1.vtu").resolve())
        self.assertEqual(V.fields(scene, meta), [])  # no file but the frames'


if __name__ == "__main__":
    unittest.main()
