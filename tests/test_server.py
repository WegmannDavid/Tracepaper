import http.client
import json
import tempfile
import threading
import unittest
from pathlib import Path

from tracepaper.server import make_server

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
