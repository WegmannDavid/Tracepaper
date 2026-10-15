import http.client
import json
import tempfile
import threading
import unittest
from pathlib import Path

from tracepaper.server import make_server

SHEET = '<svg xmlns="http://www.w3.org/2000/svg" width="10mm" height="10mm" viewBox="0 -10 10 10"/>'


class ServerTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        (self.dir / "a.svg").write_text(SHEET)
        (self.dir / "b.svg").write_text(SHEET)
        (self.dir / "b.sketch.svg").write_text(SHEET)
        (self.dir / "notes.txt").write_text("secret")
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
        status, data = self.request("GET", "/api/sheets")
        listing = json.loads(data)
        self.assertEqual(status, 200)
        self.assertEqual(listing["initial"], "b.svg")
        self.assertEqual([s["name"] for s in listing["sheets"]], ["a.svg", "b.svg"])
        self.assertIsNone(listing["sheets"][0]["sketch"])
        self.assertIsNotNone(listing["sheets"][1]["sketch"])

    def test_serves_viewer_sheets_and_sketches(self):
        self.assertEqual(self.request("GET", "/")[0], 200)
        self.assertEqual(self.request("GET", "/viewer/app.js")[0], 200)
        self.assertEqual(self.request("GET", "/api/sheet/a.svg"), (200, SHEET.encode()))
        self.assertEqual(self.request("GET", "/api/sketch/b.svg")[0], 200)
        self.assertEqual(self.request("GET", "/api/sketch/a.svg")[0], 404)

    def test_only_listed_sheets_are_reachable(self):
        for name in ("notes.txt", "..%2Fa.svg", "b.sketch.svg", "%2Fetc%2Fpasswd", "missing.svg"):
            with self.subTest(name=name):
                self.assertEqual(self.request("GET", f"/api/sheet/{name}")[0], 404)
        self.assertEqual(self.request("GET", "/viewer/../server.py")[0], 404)

    def test_save_writes_sketch_next_to_sheet(self):
        status, data = self.put_sketch("a.svg", SHEET)
        self.assertEqual(status, 200)
        self.assertEqual((self.dir / "a.sketch.svg").read_text(), SHEET)
        self.assertTrue(json.loads(data)["mtime"])

    def test_save_rejects_bad_requests(self):
        self.assertEqual(self.request("PUT", "/api/sketch/a.svg", SHEET.encode())[0], 403)  # no header
        self.assertEqual(self.put_sketch("a.svg", "<svg")[0], 400)
        self.assertEqual(self.put_sketch("a.svg", "<html/>")[0], 400)
        self.assertEqual(self.put_sketch("notes.txt", SHEET)[0], 404)
        self.assertFalse((self.dir / "a.sketch.svg").exists())

    def test_rejects_foreign_host(self):
        self.assertEqual(self.request("GET", "/api/sheets", host="evil.example:80")[0], 403)


if __name__ == "__main__":
    unittest.main()
