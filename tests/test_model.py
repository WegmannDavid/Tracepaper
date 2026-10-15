import json
import struct
import tempfile
import unittest
from pathlib import Path

from tracepaper import model as M

BINARY = bytes(range(10))  # some buffer data, not a multiple of 4 long


def write_test_glb(path: Path, nodes: list, roots: list) -> None:
    gltf = {"asset": {"version": "2.0"}, "scene": 0, "scenes": [{"nodes": roots}], "nodes": nodes,
            "buffers": [{"byteLength": len(BINARY)}]}
    M.write_glb(path, gltf, BINARY)


ASSEMBLY = [  # what CadQuery writes: one top node, the assembly, turned to y up
    {"name": "window", "children": [1, 4, 5], "rotation": [-0.7071, 0, 0, 0.7071]},
    {"name": "Steel", "children": [2, 3]},
    {"name": "Z profile", "mesh": 0},
    {"name": "angle", "mesh": 1},
    {"name": "Glass", "mesh": 2},
    {"children": [6]},  # an unnamed node: its children count as the parent's
    {"name": "screw", "mesh": 3},
]


class ModelTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "window.glb"
        write_test_glb(self.path, json.loads(json.dumps(ASSEMBLY)), [0])

    def hidden(self):
        gltf, _ = M.read_glb(self.path)
        return sorted(n["name"] for n in gltf["nodes"] if n.get("extras", {}).get("tracepaper", {}).get("hidden"))

    def test_tree_leaves_out_the_assembly_node(self):
        gltf, _ = M.read_glb(self.path)
        self.assertEqual(M.tree(gltf), [("Steel", 1), ("Steel / Z profile", 2), ("Steel / angle", 3),
                                        ("Glass", 4), ("screw", 6)])

    def test_several_top_nodes_are_all_shown(self):
        write_test_glb(self.path, [{"name": "a"}, {"name": "b", "children": [2]}, {"name": "c"}], [0, 1])
        gltf, _ = M.read_glb(self.path)
        self.assertEqual(M.tree(gltf), [("a", 0), ("b", 1), ("b / c", 2)])

    def test_mark_hidden_by_path_and_by_name(self):
        M.mark_hidden(self.path, ["Steel / Z profile", "Glass"])
        self.assertEqual(self.hidden(), ["Glass", "Z profile"])
        M.mark_hidden(self.path, ["angle"])  # just a name: wherever it is
        self.assertEqual(self.hidden(), ["Glass", "Z profile", "angle"])

    def test_file_stays_a_valid_glb(self):
        M.mark_hidden(self.path, ["screw"])
        data = self.path.read_bytes()
        magic, version, length = struct.unpack_from("<III", data, 0)
        self.assertEqual((magic, version, length), (M.GLB_MAGIC, 2, len(data)))
        json_length = struct.unpack_from("<I", data, 12)[0]
        self.assertEqual(json_length % 4, 0)
        gltf, binary = M.read_glb(self.path)
        self.assertEqual(binary[:len(BINARY)], BINARY)
        self.assertEqual(gltf["nodes"][0]["rotation"], ASSEMBLY[0]["rotation"])

    def test_unknown_node(self):
        with self.assertRaises(ValueError) as caught:
            M.mark_hidden(self.path, ["Steel / missing"])
        self.assertIn("Steel / Z profile", str(caught.exception))  # it says what there is

    def test_export_with_cadquery(self):
        try:
            import cadquery as cq
        except ImportError:
            self.skipTest("CadQuery not installed")
        assy = cq.Assembly(name="bracket")
        assy.add(cq.Workplane().box(10, 10, 2), name="body", color=cq.Color("gray"))
        assy.add(cq.Workplane().cylinder(4, 2), name="screw", color=cq.Color("orange"))
        out = M.export(assy, self.path, hidden=["screw"])
        self.assertEqual(out, self.path)
        self.assertEqual(self.hidden(), ["screw"])
        self.assertEqual([k for k, _ in M.tree(M.read_glb(self.path)[0])], ["body", "screw"])
        self.assertEqual(list(self.path.parent.glob("*.tmp")), [])


if __name__ == "__main__":
    unittest.main()
