"""End-to-end test of the viewer in headless Firefox, driven over Marionette.

Skipped when Firefox isn't installed. Set TRACEPAPER_SCREENSHOT=path.png to keep a
screenshot of the viewer with a sketch on it.
"""
import base64
import json
import os
import shutil
import socket
import subprocess
import tempfile
import threading
import time
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

from tracepaper.pen import tablet_size
from tracepaper.server import make_server

EXAMPLE = Path(__file__).resolve().parents[1] / "examples" / "bracket"
SVG = "{http://www.w3.org/2000/svg}"
INK = "{http://www.inkscape.org/namespaces/inkscape}"
TP = "{urn:tracepaper:0}"

PREFS = {
    "browser.shell.checkDefaultBrowser": False,
    "browser.startup.page": 0,
    "browser.startup.homepage_override.mstone": "ignore",
    "browser.aboutwelcome.enabled": False,
    "datareporting.policy.dataSubmissionEnabled": False,
    "toolkit.telemetry.reportingpolicy.firstRun": False,
    "app.update.disabledForTesting": True,
}

# Pointer helpers available to every script below.
HELPERS = """
const tp = window.tracepaper;
const stage = document.getElementById('tpv-stage');
const screen = (el, x, y) => new DOMPoint(x, y).matrixTransform(el.getScreenCTM());
const fire = (type, p, pointerType) => stage.dispatchEvent(new PointerEvent(type, {
  bubbles: true, cancelable: true, pointerId: 7, pointerType, isPrimary: true, clientX: p.x, clientY: p.y,
  button: type === 'pointermove' ? -1 : 0, buttons: type === 'pointerup' ? 0 : 1 }));
const stroke = (pts) => {
  tp.setTool('pen');
  fire('pointerdown', screen(tp.state.ref, ...pts[0]), 'pen');
  for (const p of pts.slice(1)) fire('pointermove', screen(tp.state.ref, ...p), 'pen');
  fire('pointerup', screen(tp.state.ref, ...pts[pts.length - 1]), 'pen');
};
const until = (cond, done, ms = 8000) => {
  const t0 = Date.now();
  (function check() {
    let v = null;
    try { v = cond(); } catch (e) { /* not ready yet */ }
    if (v) done(v); else if (Date.now() - t0 > ms) done(null); else setTimeout(check, 50);
  })();
};
const done = arguments[arguments.length - 1];
"""


class FakePen:
    """A tablet whose frames the test publishes itself."""
    size_mm = tablet_size(90)

    def start(self, hub):
        pass

    def stop(self):
        pass


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Marionette:
    """Just enough of Firefox's Marionette protocol for these tests."""

    def __init__(self, port: int, timeout: float = 30):
        deadline = time.time() + timeout
        while True:
            try:
                self.sock = socket.create_connection(("127.0.0.1", port), timeout=timeout)
                break
            except OSError:
                if time.time() > deadline:
                    raise
                time.sleep(0.2)
        self.buf = b""
        self.next_id = 0
        self._read()  # greeting

    def _read(self):
        while b":" not in self.buf:
            self.buf += self.sock.recv(65536)
        size, rest = self.buf.split(b":", 1)
        while len(rest) < int(size):
            rest += self.sock.recv(65536)
        self.buf = rest[int(size):]
        return json.loads(rest[: int(size)])

    def call(self, command: str, params=None):
        self.next_id += 1
        data = json.dumps([0, self.next_id, command, params or {}]).encode()
        self.sock.sendall(str(len(data)).encode() + b":" + data)
        while True:
            kind, msg_id, error, result = self._read()
            if kind == 1 and msg_id == self.next_id:
                if error:
                    raise RuntimeError(f"{command}: {error}")
                return result

    def run(self, script: str):
        return self.call("WebDriver:ExecuteAsyncScript", {"script": HELPERS + script, "args": []})["value"]


@unittest.skipUnless(shutil.which("firefox"), "firefox not installed")
class ViewerInFirefox(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.dir = Path(cls.tmp.name) / "sheets"
        cls.dir.mkdir()
        for f in EXAMPLE.glob("*.svg"):
            if not f.name.endswith(".sketch.svg"):
                shutil.copy(f, cls.dir)
        cls.server = make_server(cls.dir / "section-A.svg", 0, FakePen())
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()

        profile = Path(cls.tmp.name) / "profile"
        profile.mkdir()
        port = free_port()
        prefs = {**PREFS, "marionette.port": port}
        (profile / "user.js").write_text("".join(f"user_pref({json.dumps(k)}, {json.dumps(v)});\n"
                                                 for k, v in prefs.items()))
        cls.firefox = subprocess.Popen(
            ["firefox", "--headless", "--marionette", "--no-remote", "--profile", str(profile)],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            cls.m = Marionette(port)
            cls.m.call("WebDriver:NewSession", {"capabilities": {"alwaysMatch": {}}})
            cls.m.call("WebDriver:SetWindowRect", {"width": 1400, "height": 900})
        except BaseException:
            cls.tearDownClass()
            raise

    @classmethod
    def tearDownClass(cls):
        try:
            cls.m.call("Marionette:Quit", {"flags": ["eForceQuit"]})
        except Exception:
            pass
        try:
            cls.firefox.wait(timeout=10)
        except subprocess.TimeoutExpired:
            cls.firefox.kill()
        cls.server.pen_hub.close()
        cls.server.shutdown()
        cls.server.server_close()
        cls.tmp.cleanup()

    def open_viewer(self):
        # Leave without the "unsaved changes" prompt.
        self.m.call("WebDriver:ExecuteScript", {"script": "if (window.tracepaper) tracepaper.state.dirty = false;",
                                                "args": []})
        self.m.call("WebDriver:Navigate", {"url": self.server.url})
        self.assertTrue(self.m.run("until(() => window.tracepaper && tp.state.root && tp.state.name, done);"))

    def sketch(self) -> ET.Element:
        return ET.parse(self.dir / "section-A.sketch.svg").getroot()

    def test_sketch_round_trip(self):
        self.open_viewer()

        # Draw, drag the height dimension 6 mm to the left, show a hidden layer, save.
        result = self.m.run("""
            stroke([[52, 9], [56, 13], [60, 16], [64, 17]]);
            tp.setTool('move');
            const g = document.getElementById('d-height');
            const c = tp.A.geometry(tp.A.read(g), tp.fontSizeOf(g)).box.center;
            fire('pointerdown', screen(g.parentNode, c[0], c[1]), 'mouse');
            fire('pointermove', screen(g.parentNode, c[0] - 6, c[1]), 'mouse');
            fire('pointerup', screen(g.parentNode, c[0] - 6, c[1]), 'mouse');
            tp.setLayerVisible('Construction', true);
            const before = { undo: tp.state.undo.length, dirty: tp.state.dirty };
            tp.save().then(() => done({ ...before, saved: !tp.state.dirty,
              status: document.getElementById('tpv-status').textContent }));
        """)
        self.assertEqual(result["undo"], 2)
        self.assertTrue(result["dirty"])
        self.assertTrue(result["saved"])
        self.assertIn("section-A.sketch.svg", result["status"])

        if os.environ.get("TRACEPAPER_SCREENSHOT"):
            png = self.m.call("WebDriver:TakeScreenshot", {"full": False, "hash": False})["value"]
            Path(os.environ["TRACEPAPER_SCREENSHOT"]).write_bytes(base64.b64decode(png))

        root = self.sketch()
        self.assertEqual(root.get(f"{TP}sketch-of"), "section-A.svg")
        layers = {g.get(f"{INK}label"): g for g in root.findall(f"{SVG}g")}
        self.assertEqual(list(layers)[-1], "Sketch")
        strokes = layers["Sketch"].findall(f"{SVG}path")
        self.assertEqual(len(strokes), 1)
        xs = [float(v) for v in strokes[0].get("d").replace("M", "").replace("L", "").split()]
        self.assertEqual(len(xs), 8)
        for got, want in zip(xs, [52, 9, 56, 13, 60, 16, 64, 17]):
            self.assertAlmostEqual(got, want, delta=0.05)
        self.assertNotIn("display", layers["Construction"].get("style") or "")
        dim = root.find(f".//{SVG}g[@id='d-height']")
        self.assertEqual(dim.get(f"{TP}offset"), "15")
        self.assertIsNone(root.find(f".//*[@id='tpv-ref']"))
        self.assertIsNone(root.find(".//*[@class]"))

        # Reopening the viewer while the sketch is unhandled shows it again.
        self.open_viewer()
        reopened = self.m.run("until(() => tp.state.ink.children.length === 1 && tp.state.sketchMtime, done);")
        self.assertTrue(reopened)

        # The AI handles it: deletes the sketch file and regenerates the sheet.
        (self.dir / "section-A.sketch.svg").unlink()
        sheet = self.dir / "section-A.svg"
        sheet.write_text(sheet.read_text().replace('tp:offset="9"', 'tp:offset="15"'))
        cleared = self.m.run("""
            until(() => tp.state.ink.children.length === 0 && !tp.state.sketchMtime && !tp.state.dirty
              && document.getElementById('d-height').getAttributeNS(tp.A.TP_NS, 'offset') === '15', done);
        """)
        self.assertTrue(cleared)

        # Unsaved ink survives the sheet being regenerated underneath it.
        self.m.run("stroke([[20, 20], [25, 25]]); done(true);")
        sheet.write_text(sheet.read_text().replace('font-size="3.5"', 'font-size="3.5" '))
        kept = self.m.run("""
            const old = tp.state.root;
            until(() => tp.state.root !== old && tp.state.ink.children.length === 1 && tp.state.dirty, done);
        """)
        self.assertTrue(kept)

    def test_tablet_pen(self):
        self.open_viewer()
        hub = self.server.pen_hub
        hub.publish_status("connected", "test tablet")
        view = self.m.run("""
            tp.setTool('move');  // the pen draws whatever the mouse's tool
            until(() => tp.pen.status?.state === 'connected' && !document.getElementById('tpv-pen').hidden && (() => {
              const r = document.getElementById('tpv-stage').getBoundingClientRect();
              const m = tp.state.ref.getScreenCTM();
              return { w: r.width, h: r.height, left: r.left, top: r.top, a: m.a, d: m.d, e: m.e, f: m.f,
                       strokes: tp.state.ink.children.length, label: document.getElementById('tpv-pen').textContent };
            })(), done);
        """)
        self.assertTrue(view)
        tw, th = tablet_size(90)
        k = min(tw / view["w"], th / view["h"])  # tablet mm per screen px
        ax, ay = (tw - view["w"] * k) / 2, (th - view["h"] * k) / 2  # the unused strips
        self.assertIn(f"{round(view['w'] * k)} × {round(view['h'] * k)} mm", view["label"])

        def frame(x, y, touch=False, tool="pen"):
            hub.publish_frame({"x": x, "y": y, "p": 0.5 if touch else 0, "tool": tool, "touch": touch})

        def hover(x, y):
            """Hover at (x, y) on the tablet and wait for the cursor there: all frames before are handled."""
            frame(x, y)
            sx, sy = (x - ax) / k, (y - ay) / k
            self.assertTrue(self.m.run(f"""
                until(() => {{ const c = document.getElementById('tpv-pen-cursor');
                  return !c.hasAttribute('visibility') && Math.abs(c.getAttribute('cx') - {sx}) < 0.01
                    && Math.abs(c.getAttribute('cy') - {sy}) < 0.01; }}, done);"""))

        strokes = lambda: self.m.run("done(tp.state.ink.children.length);")
        cx, cy = tw / 2, th / 2
        hover(cx, cy)

        # A 20 x 20 mm square on the tablet arrives as a square, centred in the view.
        corners = [(cx - 10, cy - 10), (cx + 10, cy - 10), (cx + 10, cy + 10), (cx - 10, cy + 10), (cx - 10, cy - 10)]
        points = [(x0 + (x1 - x0) * i / 10, y0 + (y1 - y0) * i / 10)
                  for (x0, y0), (x1, y1) in zip(corners, corners[1:]) for i in range(10)] + [corners[-1]]
        for p in points:
            frame(*p, touch=True)
        hover(cx + 3, cy + 3)
        self.assertEqual(strokes(), view["strokes"] + 1)
        d = self.m.run("done(tp.state.ink.lastElementChild.getAttribute('d'));")
        nums = [float(v) for v in d.replace("M", " ").replace("L", " ").split()]
        xs, ys = nums[0::2], nums[1::2]
        side = 20 / k / abs(view["a"])  # model mm: tablet mm -> screen px -> model mm
        self.assertAlmostEqual(max(xs) - min(xs), side, delta=side * 0.01)
        self.assertAlmostEqual(max(ys) - min(ys), side, delta=side * 0.01)
        mid_x = (view["left"] + view["w"] / 2 - view["e"]) / view["a"]
        mid_y = (view["top"] + view["h"] / 2 - view["f"]) / view["d"]
        self.assertAlmostEqual((max(xs) + min(xs)) / 2, mid_x, delta=0.02)
        self.assertAlmostEqual((max(ys) + min(ys)) / 2, mid_y, delta=0.02)

        # The eraser end removes it; undo brings it back.
        frame(*corners[1], tool="eraser")
        frame(*corners[1], touch=True, tool="eraser")
        frame(*corners[1], tool="eraser")
        hover(cx, cy)
        self.assertEqual(strokes(), view["strokes"])
        self.assertEqual(self.m.run("tp.undo(); done(tp.state.ink.children.length);"), view["strokes"] + 1)

        # Touching down on the unused strip of the tablet draws nothing, even when moving into the view.
        if max(ax, ay) > 0.5:
            start = (cx, ay / 2) if ay > ax else (ax / 2, cy)
            frame(*start, touch=True)
            frame(cx, cy, touch=True)
            frame(cx, cy)
            hover(cx + 1, cy + 1)
            self.assertEqual(strokes(), view["strokes"] + 1)


if __name__ == "__main__":
    unittest.main()
