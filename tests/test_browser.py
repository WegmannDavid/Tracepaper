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

from tracepaper.model import mark_hidden
from tracepaper.pen import tablet_size
from tracepaper.server import make_server
from tracepaper.sheet import PathBuilder, Sheet

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
        for f in [*EXAMPLE.glob("*.svg"), *EXAMPLE.glob("*.glb")]:
            if not f.name.endswith(".sketch.svg"):
                shutil.copy(f, cls.dir)
        tree = Sheet("Layer tree")
        tree.layer("Outline").path("box", PathBuilder().move(0, 0).line(40, 0).line(40, 20).line(0, 20).close())
        dims = tree.layer("Dimensions")
        dims.sublayer("Overall").linear("d-w", (0, 0), (40, 0), "40", dir="x", offset=-8)
        dims.sublayer("Details", visible=False).linear("d-h", (40, 0), (40, 20), "20", dir="y", offset=-8)
        root = tree.to_element()
        outline = root.find(f"{SVG}g[@id='layer-outline']")
        wrapper = ET.SubElement(outline, f"{SVG}g")  # an unnamed group: not a node, its named one is
        bolt = ET.SubElement(wrapper, f"{SVG}g", {f"{INK}label": "Bolt"})
        ET.SubElement(bolt, f"{SVG}circle", {"id": "bolt", "cx": "20", "cy": "10", "r": "3"})
        ET.ElementTree(root).write(cls.dir / "tree.svg", encoding="unicode")
        # The bracket again, with its screw hidden at first.
        shutil.copy(cls.dir / "bracket.glb", cls.dir / "hidden-screw.glb")
        mark_hidden(cls.dir / "hidden-screw.glb", ["screw"])
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

    def open_viewer(self, view=None):
        """Open the server's first page, or the given view, and wait until it shows."""
        # Leave without the "unsaved changes" prompt.
        self.m.call("WebDriver:ExecuteScript", {"script": "if (window.tracepaper) tracepaper.state.dirty = false;",
                                                "args": []})
        base = f"http://127.0.0.1:{self.server.server_address[1]}"
        self.m.call("WebDriver:Navigate", {"url": f"{base}/view/{view}" if view else self.server.url})
        if view and view.endswith(".glb"):
            self.assertTrue(self.m.run("until(() => !!window.tracepaper3d?.state.model, done);"))
        else:
            self.assertTrue(self.m.run(f"until(() => window.tracepaper && tp.state.root && tp.state.name"
                                       f"{f' === {json.dumps(view)}' if view else ''}, done);"))

    def wait_for(self, condition, timeout=8):
        deadline = time.monotonic() + timeout
        while not condition():
            if time.monotonic() > deadline:
                self.fail("timed out")
            time.sleep(0.05)

    def sketch(self) -> ET.Element:
        return ET.parse(self.dir / "section-A.sketch.svg").getroot()

    def test_colour_picker(self):
        self.open_viewer()
        got = self.m.run("""
            const pick = (p) => {
              tp.setTool('pick');
              fire('pointerdown', p, 'mouse');
              fire('pointerup', p, 'mouse');
              return { color: tp.state.color, from: tp.state.colorFrom, tool: tp.state.tool };
            };
            const g = document.getElementById('d-length');
            const c = tp.A.geometry(tp.A.read(g), tp.fontSizeOf(g)).box.center;
            const dim = pick(screen(g.parentNode, c[0], c[1]));  // a dimension, by its label
            stroke([[30, 50], [35, 52]]);
            const s = tp.state.ink.lastElementChild;
            const ink = { stroke: s.getAttribute('stroke'), from: s.getAttributeNS(tp.A.TP_NS, 'color-from') };
            const e = screen(tp.state.ref, 20, 0);
            const edge = pick({ x: e.x, y: e.y + 3 });  // a thin outline edge, clicked beside it
            tp.setTool('move');
            tp.setColor(1);
            const toolAfterColour = tp.state.tool;
            stroke([[30, 55], [35, 57]]);
            done({ dim, ink, edge, swatch: !document.querySelector('.tpv-picked').hidden, toolAfterColour,
                   plain: tp.state.ink.lastElementChild.hasAttributeNS(tp.A.TP_NS, 'color-from') });
        """)
        # Picking returns the mouse to its tool, Move by default; the tablet's pen draws anyway.
        self.assertEqual(got["dim"], {"color": "#475569", "from": {"id": "d-length", "layer": "Dimensions"},
                                      "tool": "move"})
        self.assertEqual(got["ink"], {"stroke": "#475569", "from": "d-length"})
        self.assertEqual(got["edge"]["color"], "#1a1a1a")
        self.assertEqual(got["edge"]["from"], {"id": "cut-upright", "layer": "Outline"})
        self.assertTrue(got["swatch"])
        self.assertEqual(got["toolAfterColour"], "move")  # choosing a colour doesn't switch the mouse to Pen
        self.assertFalse(got["plain"])  # a fixed colour names no source

    def test_layer_tree(self):
        self.open_viewer("tree.svg")
        got = self.m.run("""
            const rows = () => [...document.querySelectorAll('#tpv-layers .tpv-layer')].map((r) => [
              r.querySelector('.tpv-node-name').textContent, r.querySelector('.tpv-eye').dataset.visible === 'true',
              r.classList.contains('tpv-under-hidden')]);
            const before = rows();
            const states = () => Object.fromEntries([...document.querySelectorAll('#tpv-layers .tpv-layer')].map((r) => [
              r.querySelector('.tpv-node-name').textContent, r.querySelector('.tpv-eye').dataset.state]));
            const partly = states();
            tp.setLayerVisible('Dimensions', false);
            const parentOff = rows();
            const hiddenByParent = document.getElementById('d-w').getClientRects().length === 0;
            tp.setLayerVisible('Dimensions', true);
            tp.setLayerVisible('Dimensions / Details', true);
            const detailsOn = rows();
            const allShown = states();
            document.querySelector('.tpv-twisty:not([disabled])').click();  // fold Dimensions
            done({ before, partly, parentOff, hiddenByParent, detailsOn, allShown, folded: rows() });
        """)
        self.assertEqual(got["before"], [["Sketch", True, False], ["Dimensions", True, False],
                                         ["Details", False, False], ["Overall", True, False],
                                         ["Outline", True, False], ["Bolt", True, False]])
        self.assertEqual(got["parentOff"][1:4], [["Dimensions", False, False], ["Details", False, True],
                                                 ["Overall", True, True]])
        self.assertTrue(got["hiddenByParent"])  # a sublayer's content disappears with its parent
        self.assertEqual(got["detailsOn"][2], ["Details", True, False])
        # A shown node with something hidden below it gets the half-crossed eye.
        self.assertEqual((got["partly"]["Dimensions"], got["partly"]["Details"], got["partly"]["Outline"]),
                         ("partial", "hidden", "visible"))
        self.assertEqual(got["allShown"]["Dimensions"], "visible")
        self.assertEqual([r[0] for r in got["folded"]], ["Sketch", "Dimensions", "Outline", "Bolt"])

    def test_model_tree(self):
        self.open_viewer("hidden-screw.glb")
        rows = """
            const t = window.tracepaper3d;
            const rows = () => [...document.querySelectorAll('#tpv-layers .tpv-layer')].map((r) => [
              r.querySelector('.tpv-node-name').textContent, r.querySelector('.tpv-eye').dataset.visible === 'true']);
            const shown = () => Object.fromEntries(t.flatten(t.state.tree).map((n) => [n.key, n.obj.visible]));
            const click = (name) => [...document.querySelectorAll('#tpv-layers .tpv-layer')]
              .find((r) => r.querySelector('.tpv-node-name').textContent === name).querySelector('.tpv-eye').click();
        """
        first = self.m.run(rows + "done({ rows: rows(), shown: shown() });")
        self.assertEqual(first, {"rows": [["body", True], ["screw", False]],  # the file hides the screw
                                 "shown": {"body": True, "screw": False}})
        clicked = self.m.run(rows + "click('screw'); click('body'); done({ rows: rows(), shown: shown() });")
        self.assertEqual(clicked, {"rows": [["body", False], ["screw", True]], "shown": {"body": False, "screw": True}})

        # The build writes the model again: this window's choices stay.
        model = self.dir / "hidden-screw.glb"
        model.write_bytes(model.read_bytes())
        self.assertTrue(self.m.run("until(() => window.tracepaper3d.state.loads === 2, done);"))
        self.assertEqual(self.m.run(rows + "done(shown());"), {"body": False, "screw": True})

    def test_model_view(self):
        self.open_viewer("bracket.glb")
        look = """
            const t = window.tracepaper3d;
            let meshes = 0, edges = 0;
            const names = [];
            t.state.model.traverse((o) => { if (o.isMesh) meshes++; if (o.isLineSegments) edges++;
                                             if (o.name) names.push(o.name); });
            const dir = t.camera.position.clone().sub(t.controls.target).normalize();
        """
        first = self.m.run(look + "done({ meshes, edges, names, dir: dir.toArray(), loads: t.state.loads });")
        self.assertGreater(first["meshes"], 6)
        self.assertEqual(first["edges"], first["meshes"])  # every face outlined
        self.assertTrue({"bracket", "body", "screw"} <= set(first["names"]))
        for got in first["dir"]:  # isometric: CAD (1, -1, 1) is (1, 1, 1) in the y-up scene
            self.assertAlmostEqual(got, 3 ** -0.5, places=6)
        if os.environ.get("TRACEPAPER_SCREENSHOT"):
            png = self.m.call("WebDriver:TakeScreenshot", {"full": False, "hash": False})["value"]
            Path(os.environ["TRACEPAPER_SCREENSHOT"]).with_suffix(".3d.png").write_bytes(base64.b64decode(png))

        # Scrolling with the mouse off centre zooms about the middle: the rotation centre stays.
        zoomed = self.m.run("""
            const t = window.tracepaper3d, canvas = document.getElementById('tpv-canvas');
            const r = canvas.getBoundingClientRect(), target = t.controls.target.toArray();
            for (let i = 0; i < 3; i++) canvas.dispatchEvent(new WheelEvent('wheel', { deltaY: -100, bubbles: true,
              cancelable: true, clientX: r.left + r.width * 0.85, clientY: r.top + r.height * 0.2 }));
            until(() => t.camera.zoom > 1.1 && { target, after: t.controls.target.toArray(), zoom: t.camera.zoom }, done);
        """)
        self.assertTrue(zoomed)
        self.assertEqual(zoomed["after"], zoomed["target"])

        # Dragging up and down turns the model about a horizontal axis, like a trackball:
        # "up" on screen tilts, which a turntable never does.
        tilted = self.m.run("""
            const t = window.tracepaper3d, canvas = document.getElementById('tpv-canvas');
            canvas.setPointerCapture = () => {};  // synthetic pointers can't be captured
            const r = canvas.getBoundingClientRect(), x = r.left + r.width / 2, y = r.top + r.height / 2;
            const drag = (type, dy) => canvas.dispatchEvent(new PointerEvent(type, { bubbles: true, pointerId: 1,
              pointerType: 'mouse', isPrimary: true, button: 0, buttons: type === 'pointerup' ? 0 : 1,
              clientX: x, clientY: y + dy }));
            const up0 = t.camera.up.toArray();
            drag('pointerdown', 0); drag('pointermove', 60); drag('pointermove', 120); drag('pointerup', 120);
            until(() => Math.abs(t.camera.up.y - 1) > 1e-3 && { up0, up: t.camera.up.toArray() }, done);  // a turntable never tilts it
        """)
        self.assertTrue(tilted)
        self.assertEqual(tilted["up0"], [0, 1, 0])  # the iso view starts upright
        self.m.run("window.tracepaper3d.frame(window.tracepaper3d.LOOKS.iso); done(true);")

        # The build writes the model again: it reloads and keeps the camera.
        before = self.m.run("const t = window.tracepaper3d; t.frame(t.LOOKS.top); done(t.camera.position.toArray());")
        model = self.dir / "bracket.glb"
        model.write_bytes(model.read_bytes())
        self.assertTrue(self.m.run("until(() => window.tracepaper3d.state.loads === 2, done);"))
        after = self.m.run("done(window.tracepaper3d.camera.position.toArray());")
        self.assertEqual(after, before)

    def test_pen_follows_last_used_window(self):
        hub = self.server.pen_hub
        hub.publish_status("connected", "test tablet")
        self.open_viewer()
        main = self.m.call("WebDriver:GetWindowHandle")["value"]
        client = {"a": self.m.run("done(tp.CLIENT);")}
        other = self.m.call("WebDriver:NewWindow", {"type": "window"})["handle"]
        window = {"a": main, "b": other}
        try:
            self.m.call("WebDriver:SwitchToWindow", {"handle": other})
            self.open_viewer("top.svg")
            client["b"] = self.m.run("done(tp.CLIENT);")
            self.wait_for(lambda: hub.status["active"] == client["b"])  # the window opened last

            def use(name):  # click into a window's sheet with the mouse
                self.m.call("WebDriver:SwitchToWindow", {"handle": window[name]})
                self.m.run("const p = screen(tp.state.ref, 0, 0); fire('pointerdown', p, 'mouse');"
                           "fire('pointerup', p, 'mouse'); done(true);")
                self.wait_for(lambda: hub.status["active"] == client[name])

            def strokes():
                counts = {}
                for name in ("a", "b"):
                    self.m.call("WebDriver:SwitchToWindow", {"handle": window[name]})
                    counts[name] = self.m.run("done(tp.state.ink.children.length);")
                return counts

            def draw_and_wait(name):
                before = strokes()
                w, h = tablet_size(90)
                for x in range(10):
                    hub.publish_frame({"x": w / 2 + x, "y": h / 2, "p": 0.5, "tool": "pen", "touch": True})
                hub.publish_frame({"x": w / 2 + 10, "y": h / 2, "p": 0, "tool": "pen", "touch": False})
                self.m.call("WebDriver:SwitchToWindow", {"handle": window[name]})
                self.assertTrue(self.m.run(f"until(() => !tp.pen.down && tp.state.ink.children.length === "
                                           f"{before[name] + 1}, done);"))
                return before, strokes()

            use("a")
            before, after = draw_and_wait("a")
            self.assertEqual(after, {"a": before["a"] + 1, "b": before["b"]})
            self.m.call("WebDriver:SwitchToWindow", {"handle": window["b"]})
            self.assertEqual(self.m.run("done(document.getElementById('tpv-pen').textContent);"),
                             "reMarkable · in another window")
            use("b")
            before, after = draw_and_wait("b")
            self.assertEqual(after, {"a": before["a"], "b": before["b"] + 1})
        finally:
            self.m.call("WebDriver:SwitchToWindow", {"handle": other})
            self.m.call("WebDriver:CloseWindow")
            self.m.call("WebDriver:SwitchToWindow", {"handle": main})

    def test_mouse_starts_in_move(self):
        self.open_viewer()
        self.assertEqual(self.m.run("done([tp.state.tool, document.querySelector('[data-tool=move]')"
                                    ".getAttribute('aria-pressed')]);"), ["move", "true"])

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
