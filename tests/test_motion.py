"""Motion (SPEC.md §2.13): formulas, sliders and moves, the scene posed by them, and the
server drawing it there. The pages' side is in test_browser.py, against the same cases."""
import http.client
import json
import math
import re
import tempfile
import threading
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path
from urllib.parse import quote

from tracepaper import motion as M

try:
    import OCP  # noqa: F401
    HAVE_OCP = True
except ImportError:
    HAVE_OCP = False

HERE = Path(__file__).resolve().parent
GEOMETRY = HERE / "geometry"
TP = "{urn:tracepaper:0}"
SVG = "{http://www.w3.org/2000/svg}"


class FormulaTest(unittest.TestCase):
    def test_the_cases_both_sides_share(self):
        cases = json.loads((HERE / "formulas.json").read_text())
        for text, values, want in cases["values"]:
            with self.subTest(text):
                got = M.evaluate(M.parse(text), values)
                if want is None:
                    self.assertFalse(math.isfinite(got), got)
                else:
                    self.assertAlmostEqual(got, want, delta=1e-9 * max(1, abs(want)))
        for text, want in cases["errors"]:
            with self.subTest(text), self.assertRaisesRegex(ValueError, "^" + re.escape(want)):
                M.parse(text)

    def test_names(self):
        self.assertEqual(M.names(M.parse("12 * min(handle, 90) / lift + pi")), {"handle", "lift"})
        self.assertEqual(M.parse(42), ("n", 42.0))


SASH = json.loads((GEOMETRY / "sash.tp.json").read_text())


class MotionTest(unittest.TestCase):
    def test_sliders_definitions_and_moves(self):
        motion = M.read(SASH)
        self.assertEqual([(s.name, s.unit, s.lo, s.hi, s.start, s.on) for s in motion.sliders],
                         [("handle", "°", 0, 180, 0, None), ("tilt", "°", 0, 12, 0, ("handle", 180)),
                          ("opening", "°", 0, 90, 0, ("handle", 90))])
        self.assertEqual(len(motion.sliders[0].draw), 19)  # the handle's track: an arc
        self.assertEqual(motion.values({"handle": 135}), {"handle": 135, "tilt": 0, "opening": 0, "lift": 12})
        self.assertEqual(motion.amounts({"opening": 30, "handle": 45}), [45, 6, 0, 30])
        self.assertEqual(motion.label({"opening": 30}), "handle=0 tilt=0 opening=30")
        self.assertEqual(motion.to_json()["sliders"][1], {"name": "tilt", "unit": "°", "range": [0, 12], "start": 0,
                                                           "on": {"handle": 180}, "draw": [[0, 0], [0, 0.8]]})
        self.assertEqual(motion.pose("opening=30, handle=1e1"), {"opening": 30, "handle": 10})
        self.assertIsNone(M.read({"title": "no motion"}))
        # A move whose formula has no value there is left out.
        broken = M.read({"sliders": [{"name": "a", "range": [-1, 1]}],
                         "moves": [{"node": "x", "slide": {"along": [1, 0, 0], "by": "sqrt(a)"}}]})
        self.assertEqual(broken.amounts({"a": -1}), [None])
        self.assertEqual(broken.sliders[0].start, 0)  # 0 when in range, else the lowest

    def test_what_is_wrong(self):
        def wrong(meta, message):
            with self.assertRaisesRegex(ValueError, message):
                M.read(meta)

        slider = {"name": "a", "range": [0, 1]}
        wrong({"sliders": [{"name": "1st", "range": [0, 1]}]}, r"sliders\[0\]: name must be letters")
        wrong({"sliders": [{"name": "sin", "range": [0, 1]}]}, r"the name sin is taken")
        wrong({"sliders": [slider, slider]}, r"sliders\[1\]: the name a is taken")
        wrong({"sliders": [{"name": "a", "range": [1, 0]}]}, r"range must be \[lowest, highest\]")
        wrong({"sliders": [{"name": "a", "range": [0, 1], "start": 2}]}, r"start must be a number in its range")
        wrong({"sliders": [slider], "define": {"b": "a +"}}, r"define\.b: expected a number")
        wrong({"sliders": [slider], "define": {"b": "c"}}, r"define\.b: unknown name 'c'; the sliders and definitions "
                                                          r"before it are a")
        wrong({"sliders": [slider], "moves": [{"node": "x", "turn": {"axis": [0, 0, 1], "by": "a"},
                                               "slide": {"along": [1, 0, 0], "by": "a"}}]}, r"either \"turn\"")
        wrong({"sliders": [slider], "moves": [{"node": "x", "turn": {"axis": [0, 0, 0], "by": "a"}}]},
              r"moves\[0\]\.turn: its direction must not be")
        wrong({"sliders": [slider], "moves": [{"node": "x", "slide": {"along": [1, 0], "by": "a"}}]},
              r"moves\[0\]\.slide\.along must be three numbers")
        wrong({"sliders": [slider], "moves": [{"node": "x", "slide": {"along": [1, 0, 0]}}]}, r"by must say how far")
        wrong({"sliders": [slider], "moves": [{"node": "x", "slide": {"along": [1, 0, 0], "by": "hadle"}}]},
              r"moves\[0\]\.slide\.by: unknown name 'hadle'")
        wrong({"sliders": [slider, {"name": "b", "range": [0, 1], "on": {"c": 0}}]},
              r"sliders\[1\] \(b\): on: 'c' is not a slider before it; they are a")
        wrong({"sliders": [slider, {"name": "b", "range": [0, 1], "on": {"a": 2}}]},
              r"on: a branches at a number in its range, 0 to 1")
        wrong({"sliders": [slider, {"name": "b", "range": [0, 1], "on": {"a": 0, "c": 1}}]}, r"on must be \{slider: value\}")
        wrong({"sliders": [{"name": "a", "range": [0, 1], "draw": [[0, 0]]}]}, r"draw must be two points or more")
        wrong({"sliders": [{"name": "a", "range": [0, 1], "draw": [[1, 1], [1, 1]]}]}, r"must not all be the same")
        wrong({"sliders": [{"name": "a", "range": [0, 1], "closed": "yes"}]}, r"closed must be true or false")
        with self.assertRaisesRegex(ValueError, "no slider 'opning'; the sliders are handle, tilt, opening"):
            M.read(SASH).pose("opning=3")
        with self.assertRaisesRegex(ValueError, "handle: 'x' is not a number"):
            M.read(SASH).pose("handle=x")


@unittest.skipUnless(HAVE_OCP, "needs OCP")
class PosedTest(unittest.TestCase):
    def test_parts_where_the_sliders_put_them(self):
        from tracepaper import scene as S
        sash = S.read_step(GEOMETRY / "sash.step")
        motion = M.read(SASH)

        def box(scene, key):
            part = next(n for n in scene.nodes() if n.key == key)
            return [round(v) for v in S.Scene([part]).box()]

        def pose(given):
            keys = [S.find_nodes(sash, [m.node]) for m in motion.moves]
            return S.posed(sash, [(k, m.kind, m.axis, m.through, a)
                                  for m, k, a in zip(motion.moves, keys, motion.amounts(given))])

        self.assertEqual(box(sash, "Sash / profile"), [2, 5, 2, 598, 55, 798])
        self.assertEqual(box(pose({"opening": 90}), "Sash / profile"), [2, 55, 2, 52, 651, 798])  # about (2, 55)
        turned = pose({"opening": 90, "handle": 90})
        self.assertEqual(box(turned, "Sash / bolt"), [17, 643, 612, 37, 651, 652])  # with the sash, and 12 up
        self.assertEqual(box(turned, "frame"), box(sash, "frame"))
        self.assertEqual(box(sash, "Sash / bolt"), [590, 20, 600, 598, 40, 640])  # the scene itself as it was
        tilted = box(pose({"handle": 180, "tilt": 12}), "Sash / profile")  # in, about its foot
        self.assertEqual((tilted[2], tilted[4] > 55 + 150, tilted[5] < 798), (2, True, True))


@unittest.skipUnless(HAVE_OCP, "needs OCP")
class ServerTest(unittest.TestCase):
    def setUp(self):
        from tracepaper.server import make_server
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)
        for name in ("sash.step", "sash.tp.json"):
            (self.dir / name).write_bytes((GEOMETRY / name).read_bytes())
        self.server = make_server(self.dir, 0)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)

    def get(self, path):
        conn = http.client.HTTPConnection("127.0.0.1", self.server.server_address[1], timeout=60)
        conn.request("GET", path, headers={"Host": f"127.0.0.1:{self.server.server_address[1]}"})
        r = conn.getresponse()
        data = r.read()
        conn.close()
        return r.status, data

    def test_motion_and_drawings_at_a_pose(self):
        status, data = self.get("/api/motion/sash.step%23plan")
        self.assertEqual(status, 200)
        info = json.loads(data)
        self.assertEqual([m["keys"] for m in info["moves"]], [["Sash / handle"], ["Sash / bolt"], ["Sash"], ["Sash"]])
        self.assertEqual(info["sliders"][2]["on"], {"handle": 90})
        self.assertEqual(info["define"], [["lift", "12 * min(handle, 90) / 90"]])

        def view(query=""):
            status, data = self.get(f"/api/file/sash.step%23plan{query}")
            self.assertEqual(status, 200, data)
            root = ET.fromstring(data)
            return root.find(f"{SVG}metadata/{TP}view").get("pose"), root.get("viewBox")

        rest, opened = view(), view("?pose=" + quote("opening=90"))
        self.assertEqual(rest[0], "handle=0 tilt=0 opening=0")
        self.assertEqual(opened[0], "handle=0 tilt=0 opening=90")
        self.assertNotEqual(rest[1], opened[1])  # the sash swung out
        self.assertEqual(view("?pose=" + quote("opening=0, handle=0")), rest)  # where they start: the same
        status, data = self.get("/api/file/sash.step%23plan?pose=" + quote("opning=90"))
        self.assertEqual((status, json.loads(data)), (422, {"error": "no slider 'opning'; the sliders are handle, tilt, opening"}))
        # The 3D view comes at rest: the page moves the parts itself.
        self.assertEqual(self.get("/api/file/sash.step?pose=" + quote("opening=90"))[1][:4], b"glTF")

    def test_a_move_naming_no_node(self):
        meta = {**SASH, "moves": [{"node": "Sahs", "turn": {"axis": [0, 0, 1], "by": "opening"}}]}
        (self.dir / "sash.tp.json").write_text(json.dumps(meta))
        status, data = self.get("/api/motion/sash.step")
        self.assertEqual(status, 422)
        self.assertIn(b"moves[0]: no node 'Sahs'", data)

    def test_render_at_a_pose(self):
        from tracepaper.render import render
        out = self.dir / "out" / "plan.png"
        report = render(str(self.dir / "sash.step#plan"), out=out, pose="opening=30")
        self.assertIn("\n  sliders: handle=0 tilt=0 opening=30\n", report)
        view = ET.parse(out.with_suffix(".svg")).getroot().find(f"{SVG}metadata/{TP}view")
        self.assertEqual(view.get("pose"), "handle=0 tilt=0 opening=30")
        with self.assertRaisesRegex(SystemExit, "no slider 'shut'"):
            render(str(self.dir / "sash.step#plan"), out=out, pose="shut=1")


if __name__ == "__main__":
    unittest.main()
