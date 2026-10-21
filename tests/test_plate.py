import json
import tempfile
import unittest
import zipfile
from pathlib import Path

from tracepaper import model as M
from tracepaper import plate as P

CORE_NS = "http://schemas.microsoft.com/3dmanufacturing/core/2015/02"
PROD_NS = "http://schemas.microsoft.com/3dmanufacturing/production/2015/06"
RELS = ('<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns='
        '"http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Target="/3D/3dmodel.model" '
        'Id="rel-1" Type="http://schemas.microsoft.com/3dmanufacturing/2013/01/3dmodel"/></Relationships>')
P1S = {"printable_area": ["0x0", "256x0", "256x256", "0x256"], "bed_exclude_area": ["0x0", "18x0", "18x28", "0x28"],
       "printable_height": "250", "filament_colour": ["#00FF00", "#FF0000"]}


def cube(size: float) -> str:
    """A cube's mesh, size wide and centred on the origin."""
    h = size / 2
    corners = [(x, y, z) for z in (-h, h) for y in (-h, h) for x in (-h, h)]
    # Each side counter-clockwise seen from outside: as a solid's are.
    sides = [(0, 2, 3, 1), (4, 5, 7, 6), (0, 1, 5, 4), (2, 6, 7, 3), (0, 4, 6, 2), (1, 3, 7, 5)]
    tris = [t for a, b, c, d in sides for t in ((a, b, c), (a, c, d))]
    return ("<mesh><vertices>" + "".join(f'<vertex x="{x}" y="{y}" z="{z}"/>' for x, y, z in corners)
            + "</vertices><triangles>" + "".join(f'<triangle v1="{a}" v2="{b}" v3="{c}"/>' for a, b, c in tris)
            + "</triangles></mesh>")


def write_3mf(path: Path, pieces, settings=P1S, plates: int = 1) -> Path:
    """A project as OrcaSlicer saves it: each piece (name, (x, y, z) of its centre, size,
    extruder) an object of its own file, placed by a component; a piece with a modifier."""
    objects, items, configs = [], [], []
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("_rels/.rels", RELS)
        for k, (name, (x, y, zc), size, extruder) in enumerate(pieces):
            oid, part = 2 * k + 2, f"3D/Objects/piece_{k}.model"
            z.writestr(part, f'<model unit="millimeter" xmlns="{CORE_NS}"><resources><object id="1" type="model">'
                             f'{cube(size)}</object><object id="2" type="model">{cube(size / 4)}</object>'
                             f'</resources></model>')
            objects.append(f'<object id="{oid}" type="model"><components>'
                           f'<component p:path="/{part}" objectid="1" transform="1 0 0 0 1 0 0 0 1 {x} {y} {zc}"/>'
                           f'<component p:path="/{part}" objectid="2" transform="1 0 0 0 1 0 0 0 1 {x} {y} {zc}"/>'
                           f'</components></object>')
            items.append(f'<item objectid="{oid}" transform="1 0 0 0 1 0 0 0 1 0 0 0" printable="1"/>')
            configs.append(f'<object id="{oid}"><metadata key="name" value="{name}"/>'
                           f'<metadata key="extruder" value="{extruder}"/>'
                           f'<part id="1" subtype="normal_part"><metadata key="name" value="{name}"/></part>'
                           f'<part id="2" subtype="modifier_part"><metadata key="name" value="infill"/></part>'
                           f'</object>')
        z.writestr("3D/3dmodel.model", f'<model unit="millimeter" xmlns="{CORE_NS}" xmlns:p="{PROD_NS}" '
                                       f'requiredextensions="p"><resources>{"".join(objects)}</resources>'
                                       f'<build>{"".join(items)}</build></model>')
        plate = '<plate><metadata key="plater_id" value="1"/></plate>'
        z.writestr("Metadata/model_settings.config", f'<config>{"".join(configs)}{plate * plates}</config>')
        z.writestr("Metadata/project_settings.config", json.dumps(settings))
    return path


class PlateTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)

    def convert(self, path: Path) -> dict:
        out = self.dir / "out.glb"
        out.write_bytes(P.to_glb(path))
        gltf, _ = M.read_glb(out)
        return gltf

    def primitives(self, gltf, name):
        node = next(n for n in gltf["nodes"] if n.get("name") == name)
        return gltf["meshes"][node["mesh"]]["primitives"]

    def bounds(self, gltf, name, material=None):
        """[x0, y0, z0, x1, y1, z1] around a node's faces, or those of one material."""
        found = [gltf["accessors"][p["attributes"]["POSITION"]] for p in self.primitives(gltf, name)
                 if material is None or p["material"] == material]
        return [round(f(a["min" if k < 3 else "max"][k % 3] for a in found), 3)
                for k, f in enumerate((min, min, min, max, max, max))]

    def test_plate_and_pieces_where_the_slicer_put_them(self):
        gltf = self.convert(write_3mf(self.dir / "p.3mf", [("left.stl", (50, 60, 5), 10, 1),
                                                           ("right.stl", (150, 60, 10), 20, 2),
                                                           ("left.stl", (50, 160, 5), 10, 1)]))
        self.assertEqual([k for k, _ in M.tree(gltf)], ["Plate", "left", "right", "left (2)", "Build volume"])
        self.assertEqual(self.bounds(gltf, "left"), [45, 55, 0, 55, 65, 10])
        self.assertEqual(self.bounds(gltf, "right"), [140, 50, 0, 160, 70, 20])
        bed, excluded = sorted({p["material"] for p in self.primitives(gltf, "Plate")})
        self.assertEqual(self.bounds(gltf, "Plate", bed), [0, 0, -P.BED, 256, 256, 0])
        self.assertEqual(self.bounds(gltf, "Plate", excluded), [0, 0, 0, 18, 28, P.EXCLUDED])  # kept by the printer
        self.assertEqual(self.bounds(gltf, "Build volume"), [0, 0, 0, 256, 256, 250])
        primitives = self.primitives(gltf, "right")
        self.assertEqual(len(primitives), 6)  # a cube's faces, without the modifier's
        self.assertEqual({tuple(gltf["materials"][p["material"]]["pbrMetallicRoughness"]["baseColorFactor"])
                          for p in primitives}, {(1, 0, 0, 1)})  # the second filament's colour
        volume = next(n for n in gltf["nodes"] if n.get("name") == "Build volume")
        self.assertTrue(volume["extras"]["tracepaper"]["hidden"])
        self.assertEqual(gltf["nodes"][0]["rotation"], P.Y_UP)  # z up, as CadQuery's models

    def test_faces_split_at_sharp_folds(self):
        # A cube with its sides cut in two along a fold of 10°, every triangle with vertices of
        # its own, as some writers save meshes: still one face for each side.
        positions, indices = P._box(0, 0, 0, 10, 10, 10)
        loose = P.array("f", [v for i in indices for v in positions[3 * i: 3 * i + 3]])
        faces = P._faces(loose, P.array("I", range(len(indices))))
        self.assertEqual(len(faces), 6)
        self.assertEqual(sorted(len(i) // 3 for _, i in faces), [2] * 6)
        bent = P.array("f", [0, 0, 0, 10, 0, 0, 10, 10, 0, 0, 10, 0, 20, 0, 1.76, 20, 10, 1.76])
        self.assertEqual(len(P._faces(bent, P.array("I", [0, 1, 2, 0, 2, 3, 1, 4, 5, 1, 5, 2]))), 1)
        sharp = P.array("f", [0, 0, 0, 10, 0, 0, 10, 10, 0, 0, 10, 0, 20, 0, 10, 20, 10, 10])
        self.assertEqual(len(P._faces(sharp, P.array("I", [0, 1, 2, 0, 2, 3, 1, 4, 5, 1, 5, 2]))), 2)

    def test_boxes_face_out(self):
        positions, indices = P._box(1, 2, 3, 11, 22, 33)
        point = lambda i: positions[3 * i: 3 * i + 3]  # noqa: E731
        centre = (6, 12, 18)
        for t in range(0, len(indices), 3):
            a, b, c = (point(i) for i in indices[t: t + 3])
            u, v = [b[k] - a[k] for k in range(3)], [c[k] - a[k] for k in range(3)]
            normal = (u[1] * v[2] - u[2] * v[1], u[2] * v[0] - u[0] * v[2], u[0] * v[1] - u[1] * v[0])
            out = [(a[k] + b[k] + c[k]) / 3 - centre[k] for k in range(3)]
            self.assertGreater(sum(n * o for n, o in zip(normal, out)), 0, (a, b, c))

    def test_several_plates_side_by_side(self):
        gltf = self.convert(write_3mf(self.dir / "p.3mf", [("a", (50, 50, 5), 10, 1)], plates=3))
        self.assertEqual([k for k, _ in M.tree(gltf)][:3], ["Plate 1", "Plate 2", "Plate 3"])
        step = 256 * (1 + P.PLATE_GAP)  # two plates to a row, then the next row in front
        self.assertEqual(self.bounds(gltf, "Plate 2")[:2], [round(step, 3), 0])
        self.assertEqual(self.bounds(gltf, "Plate 3")[:2], [0, round(-step, 3)])

    def test_any_3mf_file(self):
        path = self.dir / "plain.3mf"
        with zipfile.ZipFile(path, "w") as z:  # no relationships, no slicer metadata, in cm
            z.writestr("3D/3dmodel.model",
                       f'<model unit="centimeter" xmlns="{CORE_NS}"><resources><object id="5" name="block">'
                       f'{cube(2)}</object></resources><build>'
                       f'<item objectid="5" transform="0 1 0 -1 0 0 0 0 1 10 0 1"/></build></model>')
        gltf = self.convert(path)
        self.assertEqual([k for k, _ in M.tree(gltf)], ["block"])
        self.assertEqual(self.bounds(gltf, "block"), [90, -10, 0, 110, 10, 20])

    def test_not_a_3mf_file(self):
        bad = self.dir / "bad.3mf"
        bad.write_text("not a zip")
        with self.assertRaisesRegex(ValueError, "bad.3mf is not a 3MF file"):
            P.to_glb(bad)


if __name__ == "__main__":
    unittest.main()
