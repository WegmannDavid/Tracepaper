import http.client
import json
import os
import tempfile
import threading
import unittest
from unittest import mock
from pathlib import Path

from tracepaper import server as S
from tracepaper.server import make_server, view_of_sketch

try:
    from test_plate import write_3mf  # run by `unittest discover tests`, which puts tests/ on the path
except ImportError:
    from tests.test_plate import write_3mf

SHEET = '<svg xmlns="http://www.w3.org/2000/svg" width="10mm" height="10mm" viewBox="0 -10 10 10"/>'
MODEL = b"glTF fake model"


class ServerTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        (self.dir / "a.svg").write_text(SHEET)
        (self.dir / "b.svg").write_text(SHEET)
        (self.dir / "b.sketch.svg").write_text(SHEET)
        (self.dir / "notes.txt").write_text("secret")
        (self.dir / "models").mkdir()
        (self.dir / "models" / "part.glb").write_bytes(MODEL)
        (self.dir / ".git").mkdir()
        (self.dir / ".git" / "hidden.svg").write_text(SHEET)
        self.server = make_server(self.dir / "b.svg", 0)
        self.port = self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.tmp.cleanup()

    def request(self, method, path, body=None, headers=None, host=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        headers = {"Host": host or f"127.0.0.1:{self.port}", **(headers or {})}
        conn.request(method, path, body=body, headers=headers)
        r = conn.getresponse()
        data = r.read()
        conn.close()
        return r.status, data

    def put_sketch(self, name, body, **headers):
        return self.request("PUT", f"/api/sketch/{name}", body.encode(), {"X-Tracepaper": "1", **headers})

    def test_listing(self):
        status, data = self.request("GET", "/api/views")
        listing = json.loads(data)
        self.assertEqual(status, 200)
        self.assertEqual(listing["initial"], "b.svg")
        self.assertEqual([(v["name"], v["kind"]) for v in listing["views"]],
                         [("a.svg", "sheet"), ("b.svg", "sheet"), ("models/part.glb", "model")])
        self.assertEqual([bool(v["sketch"]) for v in listing["views"]], [False, True, False])
        self.assertTrue(self.server.url.endswith("/view/b.svg"))

    def test_lists_views_in_folders_at_any_depth(self):
        deep = self.dir / "models" / "a" / "b" / "c" / "d"
        deep.mkdir(parents=True)
        (deep / "deep.glb").write_bytes(MODEL)
        (self.dir / "venv" / "lib").mkdir(parents=True)
        (self.dir / "venv" / "lib" / "icon.svg").write_text(SHEET)  # a Python environment's files aren't views
        listing = json.loads(self.request("GET", "/api/views")[1])
        self.assertEqual([v["name"] for v in listing["views"]],
                         ["a.svg", "b.svg", "models/a/b/c/d/deep.glb", "models/part.glb"])
        self.assertIn(b"model.js", self.request("GET", "/view/models/a/b/c/d/deep.glb")[1])

    def test_slicer_projects_are_models(self):
        write_3mf(self.dir / "plate.3mf", [("piece", (50, 50, 5), 10, 1)])
        (self.dir / "broken.3mf").write_text("not a zip")
        listing = json.loads(self.request("GET", "/api/views")[1])
        self.assertIn(("plate.3mf", "model"), [(v["name"], v["kind"]) for v in listing["views"]])
        self.assertIn(b"model.js", self.request("GET", "/view/plate.3mf")[1])
        with mock.patch.object(S.plate, "to_glb", wraps=S.plate.to_glb) as convert:
            status, data = self.request("GET", "/api/file/plate.3mf")
            self.assertEqual(self.request("GET", "/api/file/plate.3mf")[1], data)
        self.assertEqual((status, data[:4]), (200, b"glTF"))
        self.assertEqual(convert.call_count, 1)  # kept for the next window, until the file changes
        status, data = self.request("GET", "/api/file/broken.3mf")
        self.assertEqual(status, 422)
        self.assertIn("broken.3mf is not a 3MF file", json.loads(data)["error"])
        self.assertEqual(self.put_sketch("plate.3mf", SHEET)[0], 200)
        self.assertTrue((self.dir / "plate.3mf.sketch.svg").is_file())
        self.assertEqual(view_of_sketch("plate.3mf.sketch.svg"), "plate.3mf")

    def test_pages(self):
        self.assertIn(b"project.js", self.request("GET", "/")[1])
        self.assertIn(b"app.js", self.request("GET", "/view/a.svg")[1])
        self.assertIn(b"model.js", self.request("GET", "/view/models/part.glb")[1])
        self.assertEqual(self.request("GET", "/view/notes.txt")[0], 404)
        self.assertEqual(self.request("GET", "/viewer/vendor/three/three.module.js")[0], 200)

    def test_files(self):
        self.assertEqual(self.request("GET", "/api/file/a.svg"), (200, SHEET.encode()))
        self.assertEqual(self.request("GET", "/api/file/models%2Fpart.glb"), (200, MODEL))
        self.assertEqual(self.request("GET", "/api/sketch/b.svg")[0], 200)
        self.assertEqual(self.request("GET", "/api/sketch/a.svg")[0], 404)
        self.assertEqual(self.request("GET", "/api/sketch/models%2Fpart.glb")[0], 404)
        (self.dir / "models" / "part.glb.sketch.svg").write_text(SHEET)  # a sketch over the model
        self.assertEqual(self.request("GET", "/api/sketch/models%2Fpart.glb"), (200, SHEET.encode()))
        listing = json.loads(self.request("GET", "/api/views")[1])
        self.assertTrue(listing["views"][-1]["sketch"])
        self.assertEqual(len(listing["views"]), 3)  # not a view of its own

    def test_only_listed_files_are_reachable(self):
        for name in ("notes.txt", "..%2Fa.svg", "b.sketch.svg", "%2Fetc%2Fpasswd", "missing.svg",
                     ".git%2Fhidden.svg"):
            with self.subTest(name=name):
                self.assertEqual(self.request("GET", f"/api/file/{name}")[0], 404)
        for path in ("/viewer/../server.py", "/viewer/%2E%2E/server.py", "/viewer/vendor/three/README.md"):
            with self.subTest(path=path):
                self.assertEqual(self.request("GET", path)[0], 404)

    def test_save_writes_sketch_next_to_sheet(self):
        status, data = self.put_sketch("a.svg", SHEET)
        self.assertEqual(status, 200)
        self.assertEqual((self.dir / "a.sketch.svg").read_text(), SHEET)
        self.assertTrue(json.loads(data)["mtime"])
        # Over a model, it is the model's name and .sketch.svg (SPEC.md §3.1).
        self.assertEqual(self.put_sketch("models%2Fpart.glb", SHEET)[0], 200)
        self.assertEqual((self.dir / "models" / "part.glb.sketch.svg").read_text(), SHEET)

    def test_save_rejects_bad_requests(self):
        self.assertEqual(self.request("PUT", "/api/sketch/a.svg", SHEET.encode())[0], 403)  # no header
        self.assertEqual(self.put_sketch("a.svg", "<svg")[0], 400)
        self.assertEqual(self.put_sketch("a.svg", "<html/>")[0], 400)
        self.assertEqual(self.put_sketch("notes.txt", SHEET)[0], 404)
        self.assertFalse((self.dir / "a.sketch.svg").exists())

    def test_rejects_foreign_host(self):
        self.assertEqual(self.request("GET", "/api/views", host="evil.example:80")[0], 403)

    def test_no_pen_without_a_tablet(self):
        self.assertEqual(self.request("GET", "/api/pen")[0], 404)
        self.assertEqual(self.request("POST", "/api/pen/active", b'{"client": "x"}', {"X-Tracepaper": "1"})[0], 404)


if __name__ == "__main__":
    unittest.main()


GEOMETRY = Path(__file__).resolve().parent / "geometry"
try:
    import OCP  # noqa: F401
    HAVE_OCP = True
except ImportError:
    HAVE_OCP = False


class SceneServerTest(unittest.TestCase):
    """Scenes (STEP files) and the views of them: copies of tests/geometry's."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        for name in ("bracket.step", "bracket.tp.json", "profile.step", "clip.step", "clip.tp.json"):
            (self.dir / name).write_bytes((GEOMETRY / name).read_bytes())
        (self.dir / ".clip").mkdir()
        for f in (GEOMETRY / ".clip").iterdir():
            (self.dir / ".clip" / f.name).write_bytes(f.read_bytes())
        self.server = make_server(self.dir, 0)
        self.port = self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.tmp.cleanup()

    request = ServerTest.request
    put_sketch = ServerTest.put_sketch

    def listing(self):
        return [(v["name"], v["kind"]) for v in json.loads(self.request("GET", "/api/views")[1])["views"]]

    def test_listing(self):
        # A 3D scene, its named views as sheets; a 2D scene as a sheet; frames' files not at all.
        self.assertEqual(self.listing(), [("bracket.step", "model"), ("bracket.step#section-a", "sheet"),
                                          ("bracket.step#top", "sheet"), ("clip.step", "sheet"),
                                          ("profile.step", "sheet")])
        self.assertEqual(view_of_sketch("bracket.step#x=20.sketch.svg"), "bracket.step#x=20")
        self.assertEqual(view_of_sketch("bracket.step.sketch.svg"), "bracket.step")

    def test_views_change_with_their_json(self):
        before = {v["name"]: v["mtime"] for v in json.loads(self.request("GET", "/api/views")[1])["views"]}
        meta = self.dir / "bracket.tp.json"
        meta.write_text(meta.read_text())
        os.utime(meta, ns=(int(before["bracket.step"]) + 10 ** 9,) * 2)
        after = {v["name"]: v["mtime"] for v in json.loads(self.request("GET", "/api/views")[1])["views"]}
        self.assertNotEqual(before["bracket.step#section-a"], after["bracket.step#section-a"])
        self.assertEqual(before["profile.step"], after["profile.step"])

    @unittest.skipUnless(HAVE_OCP, "needs OCP")
    def test_files(self):
        status, data = self.request("GET", "/api/file/bracket.step")
        self.assertEqual((status, data[:4]), (200, b"glTF"))
        for name in ("bracket.step%23section-a", "bracket.step%23x%3D20", "bracket.step%23front", "profile.step"):
            with self.subTest(name=name):
                status, data = self.request("GET", f"/api/file/{name}")
                self.assertEqual(status, 200)
                self.assertIn(b"urn:tracepaper:0", data)
        self.assertEqual(self.request("GET", "/view/bracket.step%23x%3D20")[0], 200)  # drawn on the fly
        self.assertEqual(self.request("GET", "/api/file/bracket.step%23sideways")[0], 404)
        self.assertEqual(self.request("GET", "/api/file/profile.step%23..%2F..%2Fetc")[0], 404)

    @unittest.skipUnless(HAVE_OCP, "needs OCP")
    def test_frames(self):
        status, data = self.request("GET", "/api/frames/clip.step")
        info = json.loads(data)
        self.assertEqual((status, len(info["frames"])), (200, 13))
        self.assertEqual(info["frames"][5]["at"], {"insertion": 2.5})
        self.assertNotIn("step", info["frames"][5])  # where the files are is the server's business
        status, data = self.request("GET", "/api/file/clip.step?frame=5")
        self.assertEqual(status, 200)
        self.assertIn(b'frame="insertion=2.5"', data)
        self.assertIn(b'tp:field="strain"', data)
        self.assertEqual(self.request("GET", "/api/file/clip.step?frame=13")[0], 422)
        self.assertEqual(json.loads(self.request("GET", "/api/frames/bracket.step")[1]), {})

    def test_sketch_of_a_view_drawn_on_the_fly(self):
        sheet = SHEET
        self.assertEqual(self.put_sketch("bracket.step%23x%3D20", sheet)[0], 200)
        self.assertEqual((self.dir / "bracket.step#x=20.sketch.svg").read_text(), sheet)
        # Now listed, so the project page says a sketch waits there.
        self.assertIn(("bracket.step#x=20", "sheet"), self.listing())
        self.assertEqual(self.put_sketch("bracket.step%23nowhere", sheet)[0], 404)
