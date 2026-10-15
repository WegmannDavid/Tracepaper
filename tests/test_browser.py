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
        cls.server = make_server(cls.dir / "section-A.svg", 0)
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
        cls.server.shutdown()
        cls.server.server_close()
        cls.tmp.cleanup()

    def open_viewer(self):
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


if __name__ == "__main__":
    unittest.main()
