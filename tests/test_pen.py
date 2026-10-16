import http.client
import json
import math
import os
import queue
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

from tracepaper import pen as P
from tracepaper import server as S
from tracepaper.server import make_server

PEN_JS = Path(__file__).resolve().parents[1] / "tracepaper" / "viewer" / "pen.js"


def ev(kind, code, value):
    return P.EVENT.pack(0, 0, kind, code, value)


def report(*events):
    return b"".join(ev(*e) for e in events) + ev(P.EV_SYN, P.SYN_REPORT, 0)


HOVER = report((P.EV_KEY, P.BTN_TOOL_PEN, 1), (P.EV_ABS, P.ABS_X, 1000), (P.EV_ABS, P.ABS_Y, 2000))
TOUCH = report((P.EV_KEY, P.BTN_TOUCH, 1), (P.EV_ABS, P.ABS_PRESSURE, 4095))


class FakePen:
    """Notes what the server asks of the tablet."""
    size_mm = P.tablet_size(90)
    pixels = P.screen_size(90)
    running = False

    def __init__(self):
        self.calls = []

    def connect(self, hub):
        self.calls.append("connect")

    def disconnect(self):
        self.calls.append("disconnect")

    def show(self, png, done=0):
        self.calls.append(("show", png, done))
        return True


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
            {"x": 10, "y": 20, "p": 0, "tool": "pen", "touch": False, "done": 0},
            {"x": 10, "y": 20, "p": 1, "tool": "pen", "touch": True, "done": 0},
        ])

    def test_strokes_are_known_by_when_the_pen_touched_down(self):
        d = P.Decoder(90)
        at = lambda sec, usec, *e: P.EVENT.pack(sec, usec, *e)
        syn = lambda sec, usec: at(sec, usec, P.EV_SYN, P.SYN_REPORT, 0)
        frames = d.feed(at(5, 1, P.EV_KEY, P.BTN_TOOL_PEN, 1) + syn(5, 1)
                        + at(7, 250, P.EV_KEY, P.BTN_TOUCH, 1) + syn(7, 250)  # touches down
                        + at(7, 900, P.EV_ABS, P.ABS_X, 50) + syn(7, 900)
                        + at(8, 10, P.EV_KEY, P.BTN_TOUCH, 0) + syn(8, 10))  # lifts
        # The tablet's screen program reads the same events, with the same times (tablet/main.cpp).
        self.assertEqual([(f["touch"], f["done"]) for f in frames],
                         [(False, 0), (True, 0), (True, 0), (False, 7_000_250)])

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


class RoutingTest(unittest.TestCase):
    """Frames go to the view used last; every view hears who that is."""

    FRAME = {"x": 1, "y": 2, "p": 0, "tool": "pen", "touch": False}

    @staticmethod
    def drain(q):
        items = []
        while not q.empty():
            items.append(q.get_nowait())
        return items

    def frames(self, q):
        return [d for k, d in self.drain(q) if k == "pen"]

    def test_routing(self):
        hub = P.PenHub((209.67, 157.25))
        a = hub.subscribe("a")
        self.assertEqual(hub.status["active"], "a")
        b = hub.subscribe("b")  # a newly opened view that draws takes the pen
        model = hub.subscribe("m", draws=False)  # a 3D view doesn't
        self.assertEqual(hub.status["active"], "b")
        self.drain(a), self.drain(b), self.drain(model)

        hub.publish_frame(self.FRAME)
        self.assertEqual((self.frames(a), self.frames(b)), ([], [self.FRAME]))

        hub.set_active("a")  # the mouse was used in a
        self.assertTrue(all(("status", hub.status) in self.drain(q) for q in (a, b, model)))
        hub.publish_frame(self.FRAME)
        self.assertEqual((self.frames(a), self.frames(b)), ([self.FRAME], []))

        hub.set_active("m")  # in the 3D view: nobody draws
        hub.publish_frame(self.FRAME)
        self.assertEqual((self.frames(a), self.frames(b), self.frames(model)), ([], [], []))

        hub.unsubscribe(model)  # closed: the newest view that draws takes over
        self.assertEqual(hub.status["active"], "b")

    def test_reconnecting_view_replaces_its_old_stream(self):
        hub = P.PenHub((1, 1))
        old = hub.subscribe("a")
        new = hub.subscribe("a")
        self.assertIn(("close", None), self.drain(old))
        hub.publish_frame(self.FRAME)
        self.assertEqual(self.frames(new), [self.FRAME])
        hub.unsubscribe(old)  # its stream ending late doesn't take the view away
        self.assertEqual(hub.status["active"], "a")


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

    def post(self, path, client, page=True, body=None):
        port = self.server.server_address[1]
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        headers = {"Host": f"127.0.0.1:{port}", **({"X-Tracepaper": "1"} if page else {})}
        conn.request("POST", path, body=body or json.dumps({"client": client}).encode(), headers=headers)
        r = conn.getresponse()
        body = r.read()
        conn.close()
        return r.status, json.loads(body) if r.status == 200 else body

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
        self.assertEqual(self.post("/api/pen/connect", "a")[0], 404)

    def test_streams_status_and_frames(self):
        self.start_server(FakePen())
        r = self.get("/api/pen")
        self.assertEqual((r.status, r.getheader("Content-Type")), (200, "text/event-stream"))
        kind, status = self.next_event(r)
        self.assertEqual((kind, status["state"], status["width"], status["height"], status["pixels"]),
                         ("status", "off", 209.67, 157.25, [1872, 1404]))  # nothing connects until a page asks
        self.server.pen_hub.publish_status("connected", "test")
        self.server.pen_hub.publish_frame({"x": 1, "y": 2, "p": 0, "tool": "pen", "touch": False})
        self.assertEqual(self.next_event(r)[1]["state"], "connected")
        self.assertEqual(self.next_event(r), ("pen", {"x": 1, "y": 2, "p": 0, "tool": "pen", "touch": False}))

    def test_pages_connect_and_disconnect(self):
        pen = FakePen()
        self.start_server(pen)
        self.assertEqual(self.post("/api/pen/connect", "a", page=False)[0], 403)  # only from a page
        status, body = self.post("/api/pen/connect", "a")
        self.assertEqual((status, body["active"], pen.calls), (200, "a", ["connect"]))  # and it draws there
        self.assertEqual(self.post("/api/pen/active", "b")[1]["active"], "b")
        self.assertEqual(self.post("/api/pen/disconnect", "b")[0], 200)
        self.assertEqual(pen.calls, ["connect", "disconnect"])
        self.assertEqual(self.post("/api/pen/nonsense", "b")[0], 404)

    def test_pictures_for_the_tablet_come_from_the_window_it_draws_in(self):
        pen = FakePen()
        self.start_server(pen)
        self.post("/api/pen/active", "a")
        self.assertEqual(self.post("/api/pen/view?client=b", None, body=b"png of b")[0], 409)
        self.assertEqual(self.post("/api/pen/view?client=a&done=1700000000123456", None, body=b"png of a"),
                         (200, {"shown": True}))
        self.assertEqual(pen.calls, [("show", b"png of a", 1700000000123456)])
        self.assertEqual(self.post("/api/pen/view?client=a", None, page=False, body=b"x")[0], 403)

    def test_lets_the_tablet_go_after_the_last_window_closes(self):
        for name, value in (("KEEPALIVE", 0.05), ("LAST_WINDOW", 0.3)):
            self.addCleanup(setattr, S, name, getattr(S, name))
            setattr(S, name, value)
        pen = FakePen()
        self.start_server(pen)
        r = self.get("/api/pen?client=a")
        self.next_event(r)
        r.close()  # the window closes
        deadline = time.monotonic() + 5
        while "disconnect" not in pen.calls and time.monotonic() < deadline:
            time.sleep(0.02)
        self.assertEqual(pen.calls, ["disconnect"])

        # A window that reloads comes back before the tablet is let go.
        r = self.get("/api/pen?client=b")
        self.next_event(r)
        r.close()
        again = self.get("/api/pen?client=b")
        self.next_event(again)
        time.sleep(1)
        self.assertEqual(pen.calls, ["disconnect"])


# Stands in for ssh running a script on the tablet. Copying the screen program: notes
# what arrived. The session: notes how it was started (from the settings on the script's
# first line), says it is ready, sends a pen report, then notes what it is sent until
# Tracepaper disconnects, and that it cleaned up.
FAKE_TABLET = f"""
import struct, sys
log = open(sys.argv[1], "a")
script = sys.argv[-1]
if "TRACEPAPER-READY" not in script:
    log.write("copied " + str(len(sys.stdin.buffer.read())) + " bytes\\n")
    sys.exit(0)
settings = [t for t in script.splitlines()[0].split() if t.startswith(("take=", "screen="))]
log.write("start " + " ".join(settings) + "\\n")
log.flush()
E = struct.Struct("<IIHHi")
out = sys.stdout.buffer
out.write({P.READY!r})
for kind, code, value in [(1, 0x140, 1), (3, 0, 1000), (3, 1, 2000), (0, 0, 0), (1, 0x14A, 1), (0, 0, 0)]:
    out.write(E.pack(0, 0, kind, code, value))
out.flush()
log.write("received " + repr(sys.stdin.buffer.read().strip(b"\\n")) + "\\n")
log.write("app started again\\n")
"""

# Notes how it was started, then fails like ssh does: with a message and an exit status.
FAILING = """
import sys
open(sys.argv[1], "a").write("start " + sys.argv[-1].splitlines()[0].split()[1] + "\\n")
sys.stderr.write(sys.argv[2])
sys.exit(int(sys.argv[3]))
"""


class ConnectionTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.log = Path(tmp.name) / "log"
        self.addCleanup(setattr, P, "RETRY", P.RETRY)
        P.RETRY = 0.05

    def starts(self):
        return self.log.read_text().splitlines() if self.log.exists() else []

    def collect(self, q, until, timeout=10):
        got, deadline = [], time.monotonic() + timeout
        while not until(got):
            got.append(q.get(timeout=max(0.01, deadline - time.monotonic())))
        return got

    @staticmethod
    def ended(got):
        """An attempt to connect, then off."""
        states = [d["state"] for k, d in got if k == "status"]
        return "connecting" in states and states[-1] == "off"

    def failing(self, message, status):
        return P.RemarkablePen(command=[sys.executable, "-c", FAILING, str(self.log), message, str(status)],
                               screen=None)

    def test_session_streams_and_disconnects_cleanly(self):
        pen = P.RemarkablePen(rotation=90, command=[sys.executable, "-c", FAKE_TABLET, str(self.log)], screen=None)
        hub = P.PenHub(pen.size_mm)
        q = hub.subscribe()
        self.assertEqual(hub.status["state"], "off")
        pen.connect(hub)
        got = self.collect(q, lambda g: sum(k == "pen" for k, _ in g) == 2)
        frames = [d for k, d in got if k == "pen"]
        self.assertEqual([(f["x"], f["y"], f["touch"]) for f in frames], [(10, 20, False), (10, 20, True)])
        self.assertIn(("status", "connected"), [(k, d["state"]) for k, d in got if k == "status"])
        pen.connect(hub)  # from another window: already connected
        self.assertFalse(pen.show(b"picture"))  # no screen program here
        pen.disconnect()
        # One session, cleaned up.
        self.assertEqual(self.starts(), ["start take=1 screen=0", "received b''", "app started again"])
        self.assertEqual((hub.status["state"], hub.status["message"], pen.running), ("off", "", False))

    def test_shows_pictures_on_the_tablet(self):
        program = self.log.with_name("tracepaper-screen")
        program.write_bytes(b"\x7fELF the screen program")
        pen = P.RemarkablePen(command=[sys.executable, "-c", FAKE_TABLET, str(self.log)], screen=program)
        hub = P.PenHub(pen.size_mm, pen.pixels)
        q = hub.subscribe()
        pen.connect(hub)
        self.collect(q, lambda g: any(k == "status" and d["state"] == "connected" for k, d in g))
        self.assertTrue(hub.status["screen"])
        self.assertTrue(pen.show(b"PNG\nwith a newline", 1700000000123456))
        pen.disconnect()
        self.assertEqual(self.starts(), [f"copied {program.stat().st_size} bytes", "start take=1 screen=1",
                                         "received b'view 18 1700000000123456\\nPNG\\nwith a newline'",
                                         "app started again"])
        self.assertFalse(pen.show(b"picture"))  # disconnected

    def test_retries_without_taking_the_tablet_over(self):
        pen = self.failing("no route to host", 255)
        hub = P.PenHub(pen.size_mm)
        q = hub.subscribe()
        pen.connect(hub)
        got = self.collect(q, lambda g: sum(k == "status" and d["state"] == "error" for k, d in g) >= 3)
        pen.disconnect()
        self.assertIn("no route to host", [d["message"] for k, d in got if d["state"] == "error"])
        starts = self.starts()
        # Only pressing Connect takes the tablet from another viewer; trying again doesn't.
        self.assertEqual((starts[0], set(starts[1:])), ("start take=1", {"start take=0"}))

    def test_stops_when_another_viewer_has_the_tablet_or_it_was_disconnected_there(self):
        for status, message in ((P.TAKEN, "taken by another viewer"), (P.BUSY, "in use by another viewer"),
                                (P.LEFT, "disconnected on the tablet")):
            with self.subTest(message):
                self.log.unlink(missing_ok=True)
                pen = self.failing(message, status)
                hub = P.PenHub(pen.size_mm)
                q = hub.subscribe()
                pen.connect(hub)
                self.collect(q, self.ended)
                self.assertEqual((hub.status["message"], pen.running), (message, False))
                time.sleep(4 * P.RETRY)
                self.assertEqual(self.starts(), ["start take=1"])  # and doesn't try again
                pen.connect(hub)  # until Connect is pressed again
                self.collect(q, self.ended)
                self.assertEqual(self.starts(), ["start take=1"] * 2)



@unittest.skipUnless(shutil.which("bash") and Path("/proc/self/cmdline").exists(), "needs bash and /proc")
class SessionScriptTest(unittest.TestCase):
    """The script that runs on the tablet (tracepaper/tablet/session.sh), run here with stand-ins
    for the tablet's app (systemctl), its pen and the screen program."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = d = Path(tmp.name)
        self.log = d / "log"
        (d / "bin").mkdir()
        self.script(d / "bin" / "systemctl", f'echo "systemctl $*" >> {self.log}')
        (d / "devices").write_text('N: Name="Wacom I2C Digitizer"\nH: Handlers=event1\n')
        (d / "input").mkdir()
        (d / "input" / "event1").write_bytes(b"PEN EVENTS")
        self.program = d / "tracepaper-screen"
        self.script(self.program, f"""
if [ "$1" = --receive ]; then head -c "$3" > "$2.new" && echo "$4" > "$2.done" && mv "$2.new" "$2"; exit; fi
echo "screen $*" >> {self.log}
exec sleep 60""")

    @staticmethod
    def script(path, body):
        path.write_text("#!/bin/sh\n" + body + "\n")
        path.chmod(0o755)

    def lines(self):
        return self.log.read_text().splitlines() if self.log.exists() else []

    def start(self, owner, take=1, ready=True):
        d = self.dir
        script = P.session(owner=owner, take=take, screen=1, turn=90, program=self.program, lock=d / "lock",
                           view=d / "view.png", devices=d / "devices", input=d / "input")
        proc = subprocess.Popen(["bash", "-c", script], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, env={**os.environ, "PATH": f"{d / 'bin'}:{os.environ['PATH']}"})
        self.addCleanup(lambda: proc.poll() is None and proc.kill())
        if ready:
            self.assertEqual(proc.stdout.readline(), P.READY)
        return proc

    def end(self, proc):
        """Its exit status and what it said."""
        _, err = proc.communicate(timeout=10)
        return proc.returncode, err.decode().strip()

    def test_session(self):
        proc = self.start("a")
        self.assertEqual(proc.stdout.read(10), b"PEN EVENTS")  # the pen, streamed
        self.assertEqual((self.dir / "lock").read_text().split()[1], "a")
        proc.stdin.write(b"view 5 123\nhello\n\n")  # a picture between heartbeats
        proc.stdin.flush()
        deadline = time.monotonic() + 5
        while not (self.dir / "view.png").exists() and time.monotonic() < deadline:
            time.sleep(0.02)
        self.assertEqual((self.dir / "view.png").read_bytes(), b"hello")
        self.assertEqual((self.dir / "view.png.done").read_text(), "123\n")
        proc.stdin.close()  # the viewer disconnects
        self.assertEqual(self.end(proc)[0], 0)
        self.assertEqual(self.lines(), ["systemctl stop xochitl",
                                        f"screen {self.dir}/view.png 90 {self.dir}/input/event1",
                                        "systemctl reset-failed xochitl", "systemctl start xochitl"])
        self.assertEqual(sorted(p.name for p in self.dir.iterdir()), ["bin", "devices", "input", "log",
                                                                      "tracepaper-screen"])  # cleaned up

    def test_takes_over_or_steps_aside(self):
        a = self.start("a")
        # Trying again after a dropped connection doesn't take another viewer's tablet.
        self.assertEqual(self.end(self.start("c", take=0, ready=False)), (P.BUSY, "in use by another viewer"))
        self.assertIsNone(a.poll())
        b = self.start("b")  # Connect pressed in another viewer
        self.assertEqual(self.end(a), (P.TAKEN, "taken by another viewer"))
        self.assertNotIn("systemctl start xochitl", self.lines())  # the app stays stopped for b
        self.assertEqual((self.dir / "lock").read_text().split()[1], "b")
        # The same viewer trying again takes over its own session, from before the connection dropped.
        b_again = self.start("b", take=0)
        self.assertEqual(self.end(b)[0], P.TAKEN)
        b_again.stdin.close()
        self.assertEqual(self.end(b_again)[0], 0)
        self.assertEqual(self.lines().count("systemctl start xochitl"), 1, self.lines())

    def test_disconnect_on_the_tablet(self):
        proc = self.start("a")
        session = int((self.dir / "lock").read_text().split()[0])
        os.kill(session, signal.SIGUSR2)  # what the screen's Disconnect button does
        self.assertEqual(self.end(proc), (P.LEFT, "disconnected on the tablet"))
        self.assertEqual(self.lines()[-1], "systemctl start xochitl")


if __name__ == "__main__":
    unittest.main()
