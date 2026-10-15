import json
import math
import shutil
import subprocess
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

from tracepaper import sheet as S

VIEWER_JS = Path(__file__).resolve().parents[1] / "tracepaper" / "viewer" / "annotations.js"

LINEAR = [
    dict(p1=[0, 0], p2=[60, 0], dir="x", offset=-16, textAt=0.5, label="60"),
    dict(p1=[0, 0], p2=[0, 40], dir="y", offset=9, textAt=0.5, label="40"),
    dict(p1=[60, 0], p2=[60, 5], dir="y", offset=-7, textAt=0.5, label="5"),
    dict(p1=[10, 3], p2=[2, 9], dir="aligned", offset=4, textAt=0.5, label="10"),
    dict(p1=[40, 15], p2=[0, 30], dir="x", offset=8, textAt=1.4, label="40"),
    dict(p1=[0, 0], p2=[0, -20], dir="y", offset=-3, textAt=-0.2, label="20"),
    dict(p1=[5, 5], p2=[5, 5], dir="aligned", offset=2, textAt=0.5, label="0"),
]
RADIAL = [
    dict(kind="radius", center=[8, 8], r=3, angle=225, offset=-13, label="R3"),
    dict(kind="radius", center=[0, 0], r=10, angle=30, offset=6, label="R10"),
    dict(kind="radius", center=[0, 0], r=10, angle=90, offset=0, label="R10"),
    dict(kind="diameter", center=[40, 15], r=3, angle=50, offset=7, label="⌀6"),
    dict(kind="diameter", center=[0, 0], r=12, angle=135, offset=4, label="⌀24"),
    dict(kind="diameter", center=[0, 0], r=12, angle=270, offset=-30, label="⌀24"),
]
SYMBOL = [
    dict(anchor=[0, 0], offset=[-4, -4], label="⊥"),
    dict(anchor=[5, 30], offset=[3, 0], label="="),
]
FMT = [0, -0.0, 60, 2.625, -15.125, 1 / 3, 1e-7, -1e-7, 123456.78915, 0.00005, -2.5]


def python_geometry(ann: dict, fs: float) -> dict:
    if "p1" in ann:
        g = S.linear_geometry(ann["p1"], ann["p2"], ann["offset"], ann["label"], fs, ann["dir"], ann["textAt"])
    elif "anchor" in ann:
        g = S.symbol_geometry(ann["anchor"], ann["offset"], ann["label"], fs)
    else:
        g = S.radial_geometry(ann["center"], ann["r"], ann["angle"], ann["offset"], ann["label"], fs,
                              ann["kind"] == "diameter")
    return {"lines": g["lines"], "arrows": g["arrows"], "dots": g["dots"], "texts": g["texts"]}


def flatten(x):
    if isinstance(x, (list, tuple)):
        return [v for item in x for v in flatten(item)]
    return [x]


class GeometryMatchesViewer(unittest.TestCase):
    """sheet.py and viewer/annotations.js must draw annotations identically."""

    @classmethod
    def setUpClass(cls):
        if not shutil.which("gjs"):
            raise unittest.SkipTest("gjs not installed")
        cases = [dict(a, kind="linear") for a in LINEAR] + RADIAL + [dict(a, kind="symbol") for a in SYMBOL]
        cls.cases = cases
        script = f"""
import * as A from '{VIEWER_JS.as_uri()}';
const cases = {json.dumps(cases)};
print(JSON.stringify({{
  geometry: cases.map((c) => {{
    const g = A.geometry(c, 3.5);
    return {{ lines: g.lines, arrows: g.arrows.map((a) => [a.tip, a.dir]), dots: g.dots,
             texts: g.texts.map((t) => [t.at, t.angle, t.label]) }};
  }}),
  fmt: {json.dumps(FMT)}.map((v) => A.fmt(v)),
}}));
"""
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "harness.js"
            path.write_text(script)
            out = subprocess.run(["gjs", "-m", str(path)], capture_output=True, text=True, check=True).stdout
        cls.js = json.loads(out)

    def test_geometry(self):
        self.assertEqual(len(self.js["geometry"]), len(self.cases))
        for case, js in zip(self.cases, self.js["geometry"]):
            with self.subTest(case=case):
                py = python_geometry(case, 3.5)
                for key in ("lines", "arrows", "dots", "texts"):
                    a, b = flatten(py[key]), flatten(js[key])
                    self.assertEqual(len(a), len(b), key)
                    for x, y in zip(a, b):
                        if isinstance(x, str):
                            self.assertEqual(x, y)
                        else:
                            self.assertTrue(math.isclose(x, y, abs_tol=1e-9), f"{key}: {x} != {y}")

    def test_fmt(self):
        self.assertEqual([S.fmt(v) for v in FMT], self.js["fmt"])


class SheetOutput(unittest.TestCase):
    def test_layers_coordinates_and_annotations(self):
        sheet = S.Sheet("Plate", source="plate.py", kind="sketch", margin=5)
        outline = sheet.layer("Outline")
        outline.polyline("plate", [(0, 0), (40, 0), (40, 20), (0, 20)], closed=True, source="tag:plate")
        sheet.layer("Construction", visible=False).line("axis", (0, 10), (40, 10))
        sheet.layer("Dimensions").linear("d-w", (0, 0), (40, 0), "40", dir="x", offset=-8)
        root = ET.fromstring(sheet.tostring().split("\n", 1)[1])
        ns = {"svg": S.SVG_NS}
        ink = f"{{{S.INKSCAPE_NS}}}"

        layers = root.findall("svg:g", ns)
        self.assertEqual([g.get(ink + "label") for g in layers], ["Outline", "Construction", "Dimensions"])
        self.assertTrue(all(g.get("transform") == "scale(1,-1)" for g in layers))
        self.assertEqual(layers[1].get("style"), "display:none")

        # Model y runs from -8 (dimension line) to 20, so viewBox y covers -(20 + 5) .. -(-8 - ...).
        x0, y0, w, h = map(float, root.get("viewBox").split())
        self.assertEqual(root.get("width"), f"{S.fmt(w)}mm")
        self.assertLessEqual(y0, -25)
        self.assertGreaterEqual(y0 + h, 8 + 1.75 + 5)

        dim = layers[2].find("svg:g", ns)
        self.assertEqual(dim.get(f"{{{S.TP_NS}}}dim"), "linear")
        self.assertEqual(dim.get(f"{{{S.TP_NS}}}offset"), "-8")
        self.assertEqual(dim.find("svg:text", ns).text, "40")
        self.assertEqual(layers[0].find("svg:path", ns).get(f"{{{S.TP_NS}}}source"), "tag:plate")


if __name__ == "__main__":
    unittest.main()
