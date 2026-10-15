import http.client
import json
import math
import queue
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

from tracepaper import pen as P
from tracepaper.server import make_server

PEN_JS = Path(__file__).resolve().parents[1] / "tracepaper" / "viewer" / "pen.js"


def ev(kind, code, value):
    return P.EVENT.pack(0, 0, kind, code, value)


def report(*events):
    return b"".join(ev(*e) for e in events) + ev(P.EV_SYN, P.SYN_REPORT, 0)


HOVER = report((P.EV_KEY, P.BTN_TOOL_PEN, 1), (P.EV_ABS, P.ABS_X, 1000), (P.EV_ABS, P.ABS_Y, 2000))
TOUCH = report((P.EV_KEY, P.BTN_TOUCH, 1), (P.EV_ABS, P.ABS_PRESSURE, 4095))


class FakePen:
    size_mm = P.tablet_size(90)

    def start(self, hub):
        pass

    def stop(self):
        pass


class DecoderTest(unittest.TestCase):
    def test_orientation(self):
        # Sensor (10 mm, 20 mm): x runs up the upright screen, y left to right.
        expected = {0: (20, 199.67), 90: (10, 20), 180: (137.25, 10), 270: (199.67, 137.25)}
        for rotation, (x, y) in expected.items():
            with self.subTest(rotation=rotation):
                frame, = P.Decoder(rotation).feed(HOVER)
                self.assertEqual((frame["x"], frame["y"]), (x, y))
                w, h = P.tablet_size(rotation)
                self.assertTrue(0 <= x <= w and 0 <= y <= h)

    def test_landscape_size(self):
        self.assertEqual(P.tablet_size(90), (209.67, 157.25))
        self.assertEqual(P.tablet_size(270), (209.67, 157.25))
        self.assertEqual(P.tablet_size(0), (157.25, 209.67))

    def test_frames_across_chunks_and_changes_only(self):
        d = P.Decoder(90)
        data = HOVER + HOVER + TOUCH
        frames = []
        for i in range(0, len(data), 7):  # events split at odd places
            frames += d.feed(data[i:i + 7])
        self.assertEqual(frames, [
            {"x": 10, "y": 20, "p": 0, "tool": "pen", "touch": False},
            {"x": 10, "y": 20, "p": 1, "tool": "pen", "touch": True},
        ])

    def test_eraser_and_out_of_range(self):
        d = P.Decoder(90)
        d.feed(HOVER)
        frame, = d.feed(report((P.EV_KEY, P.BTN_TOOL_PEN, 0), (P.EV_KEY, P.BTN_TOOL_RUBBER, 1)))
        self.assertEqual(frame["tool"], "eraser")
        frame, = d.feed(report((P.EV_KEY, P.BTN_TOOL_RUBBER, 0), (P.EV_KEY, P.BTN_TOUCH, 1)))
        self.assertEqual((frame["tool"], frame["touch"]), (None, False))  # no touch without a tool


class MappingTest(unittest.TestCase):
    """viewer/pen.js keeps the tablet's proportions whatever the canvas's shape."""

    def test_aspect_ratio_is_kept(self):
        if not shutil.which("gjs"):
            self.skipTest("gjs not installed")
        tablet = {"width": 209.67, "height": 157.25}
        stages = [{"width": 1180, "height": 850}, {"width": 800, "height": 1000},
                  {"width": 1000, "height": 750}, {"width": 1920, "height": 400}]
        script = f"""
import * as P from '{PEN_JS.as_uri()}';
const tablet = {json.dumps(tablet)};
print(JSON.stringify({json.dumps(stages)}.map((s) => {{
  const a = P.activeArea(tablet, s), c = [tablet.width / 2, tablet.height / 2];
  const map = (p) => P.tabletToStage(p, tablet, s);
  return {{ area: a, topLeft: map([a.x, a.y]), bottomRight: map([a.x + a.width, a.y + a.height]),
            center: map(c), square: map([c[0] + 10, c[1] + 10]) }};
}})));
"""
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "mapping.js"
            path.write_text(script)
            out = json.loads(subprocess.run(["gjs", "-m", str(path)], capture_output=True, text=True,
                                            check=True).stdout)
        self.assertEqual(len(out), len(stages))
        for s, m in zip(stages, out):
            with self.subTest(stage=s):
                a, w, h = m["area"], s["width"], s["height"]
                close = lambda u, v: self.assertTrue(math.isclose(u, v, rel_tol=1e-9, abs_tol=1e-9), f"{u} != {v}")
                # The whole canvas is reachable, from the active area's corners...
                close(m["topLeft"][0], 0), close(m["topLeft"][1], 0)
                close(m["bottomRight"][0], w), close(m["bottomRight"][1], h)
                # ...the active area has the canvas's shape, fits the tablet and fills it one way...
                close(a["width"] / a["height"], w / h)
                self.assertTrue(a["x"] >= -1e-9 and a["y"] >= -1e-9)
                self.assertTrue(math.isclose(a["width"], tablet["width"]) or math.isclose(a["height"], tablet["height"]))
                # ...centred, and at one scale for both axes: a square stays square.
                close(m["center"][0], w / 2), close(m["center"][1], h / 2)
                dx, dy = m["square"][0] - m["center"][0], m["square"][1] - m["center"][1]
                close(dx, dy)
                close(dx, 10 / a["mmPerPx"])


class StreamTest(unittest.TestCase):
    def start_server(self, pen):
        self.tmp = tempfile.TemporaryDirectory()
        (Path(self.tmp.name) / "a.svg").write_text('<svg xmlns="http://www.w3.org/2000/svg"/>')
        self.server = make_server(Path(self.tmp.name), 0, pen)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.stop_server)

    def stop_server(self):
        if self.server.pen_hub:
            self.server.pen_hub.close()
        self.server.shutdown()
        self.server.server_close()
        self.tmp.cleanup()

    def get(self, path):
        port = self.server.server_address[1]
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        conn.request("GET", path, headers={"Host": f"127.0.0.1:{port}"})
        self.addCleanup(conn.close)
        return conn.getresponse()

    @staticmethod
    def next_event(response):
        kind = data = None
        while True:
            line = response.fp.readline().decode().rstrip("\n")
            if line.startswith("event: "):
                kind = line[7:]
            elif line.startswith("data: "):
                data = json.loads(line[6:])
            elif line == "" and kind:
                return kind, data

    def test_no_pen(self):
        self.start_server(None)
        self.assertEqual(self.get("/api/pen").status, 404)

    def test_streams_status_and_frames(self):
        self.start_server(FakePen())
        r = self.get("/api/pen")
        self.assertEqual((r.status, r.getheader("Content-Type")), (200, "text/event-stream"))
        kind, status = self.next_event(r)
        self.assertEqual((kind, status["state"], status["width"], status["height"]),
                         ("status", "connecting", 209.67, 157.25))
        self.server.pen_hub.publish_status("connected", "test")
        self.server.pen_hub.publish_frame({"x": 1, "y": 2, "p": 0, "tool": "pen", "touch": False})
        self.assertEqual(self.next_event(r)[1]["state"], "connected")
        self.assertEqual(self.next_event(r), ("pen", {"x": 1, "y": 2, "p": 0, "tool": "pen", "touch": False}))


# Stands in for the script on the tablet: says it is ready, sends a pen report,
# then waits for Tracepaper to disconnect and notes that it cleaned up.
FAKE_TABLET = f"""
import struct, sys
E = struct.Struct("<IIHHi")
out = sys.stdout.buffer
out.write({P.READY!r})
for kind, code, value in [(1, 0x140, 1), (3, 0, 1000), (3, 1, 2000), (0, 0, 0), (1, 0x14A, 1), (0, 0, 0)]:
    out.write(E.pack(0, 0, kind, code, value))
out.flush()
sys.stdin.read()
open(sys.argv[1], "w").write("app started again")
"""


class ConnectionTest(unittest.TestCase):
    def collect(self, q, until, timeout=10):
        got, deadline = [], time.monotonic() + timeout
        while not until(got):
            got.append(q.get(timeout=max(0.01, deadline - time.monotonic())))
        return got

    def test_session_streams_and_disconnects_cleanly(self):
        with tempfile.TemporaryDirectory() as tmp:
            marker = Path(tmp) / "restored"
            pen = P.RemarkablePen(rotation=90, command=[sys.executable, "-c", FAKE_TABLET, str(marker)])
            hub = P.PenHub(pen.size_mm)
            q = hub.subscribe()
            pen.start(hub)
            got = self.collect(q, lambda g: sum(k == "pen" for k, _ in g) == 2)
            frames = [d for k, d in got if k == "pen"]
            self.assertEqual([(f["x"], f["y"], f["touch"]) for f in frames], [(10, 20, False), (10, 20, True)])
            self.assertIn(("status", "connected"), [(k, d["state"]) for k, d in got if k == "status"])
            pen.stop()
            self.assertEqual(marker.read_text(), "app started again")
            self.assertEqual(hub.status["state"], "off")

    def test_reports_errors_and_retries(self):
        old, P.RETRY = P.RETRY, 0.05
        self.addCleanup(setattr, P, "RETRY", old)
        pen = P.RemarkablePen(command=[sys.executable, "-c", "import sys; sys.exit('no route to host')"])
        hub = P.PenHub(pen.size_mm)
        q = hub.subscribe()
        pen.start(hub)
        got = self.collect(q, lambda g: sum(k == "status" and d["state"] == "error" for k, d in g) >= 2)
        pen.stop()
        self.assertIn("no route to host", [d["message"] for k, d in got if d["state"] == "error"])


if __name__ == "__main__":
    unittest.main()
