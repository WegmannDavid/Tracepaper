import os
import shutil
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

from tracepaper.report import find_sketch, hidden_and_shown, inspect
from tracepaper.sheet import INKSCAPE_NS, SVG_NS, TP_NS

EXAMPLE = Path(__file__).resolve().parents[1] / "examples" / "bracket" / "section-A.svg"
GEOMETRY = Path(__file__).resolve().parent / "geometry"


def make_sketch(directory: Path) -> Path:
    """A sketch of section-A as the viewer would save it: two strokes, one dimension moved."""
    shutil.copy(EXAMPLE, directory / "section-A.svg")
    root = ET.parse(EXAMPLE).getroot()
    root.set(f"{{{TP_NS}}}sketch-of", "section-A.svg")
    root.find(f".//{{{SVG_NS}}}g[@id='d-height']").set(f"{{{TP_NS}}}offset", "15")
    # A named part inside an unnamed group, hidden: a node of the tree like any layer.
    outline = root.find(f".//{{{SVG_NS}}}g[@id='layer-outline']")
    wrapper = ET.SubElement(outline, f"{{{SVG_NS}}}g")
    ET.SubElement(wrapper, f"{{{SVG_NS}}}g", {f"{{{INKSCAPE_NS}}}label": "Bolt", "style": "display:none"})
    ink = ET.SubElement(root, f"{{{SVG_NS}}}g", {"id": "tp-sketch", f"{{{INKSCAPE_NS}}}groupmode": "layer",
                                                 f"{{{INKSCAPE_NS}}}label": "Sketch", "transform": "scale(1,-1)"})
    ET.SubElement(ink, f"{{{SVG_NS}}}path", {"d": "M40 20 L45 25 L50 22", "stroke": "#e11d48", "fill": "none"})
    ET.SubElement(ink, f"{{{SVG_NS}}}path", {"d": "M10 45 L30 45", "stroke": "#475569", "fill": "none",
                                             f"{{{TP_NS}}}color-from": "d-length"})
    path = directory / "section-A.sketch.svg"
    ET.ElementTree(root).write(path, encoding="unicode")
    return path


def scene_sketch(directory: Path) -> Path:
    """A sketch on the bracket scene's section A-A, at a frame, with one label moved."""
    for name in ("bracket.step", "bracket.tp.json"):
        shutil.copy(GEOMETRY / name, directory / name)
    svg, tp, ink = f"{{{SVG_NS}}}", f"{{{TP_NS}}}", f"{{{INKSCAPE_NS}}}"
    root = ET.Element(f"{svg}svg", {"width": "100mm", "height": "80mm", "viewBox": "-20 -60 100 80",
                                    f"{tp}sketch-of": "bracket.step#section-a"})
    ET.SubElement(ET.SubElement(root, f"{svg}metadata"), f"{tp}view",
                  {"kind": "section", "scene": "bracket.step", "view": "section-a", "frame": "insertion=2.5"})
    dims = ET.SubElement(root, f"{svg}g", {f"{ink}groupmode": "layer", f"{ink}label": "Dimensions",
                                           "transform": "scale(1,-1)"})
    # Moved; as given, written out; and given with a default left out.
    ET.SubElement(dims, f"{svg}g", {"id": "d-length", f"{tp}dim": "linear", f"{tp}offset": "-22", f"{tp}text-at": "0.667"})
    ET.SubElement(dims, f"{svg}g", {"id": "d-height", f"{tp}dim": "linear", f"{tp}offset": "9", f"{tp}text-at": "0.5"})
    ET.SubElement(dims, f"{svg}g", {"id": "d-fillet", f"{tp}dim": "radius", f"{tp}angle": "225", f"{tp}offset": "-13"})
    ET.SubElement(dims, f"{svg}g", {"id": "c-square", f"{tp}anchor": "0 0", f"{tp}offset": "-4 -4"})
    sketch = ET.SubElement(root, f"{svg}g", {"id": "tp-sketch", f"{ink}groupmode": "layer", f"{ink}label": "Sketch",
                                             "transform": "scale(1,-1)"})
    ET.SubElement(sketch, f"{svg}path", {"d": "M20 20 L30 26", "stroke": "#e11d48", f"{tp}frame": "insertion=2.5"})
    path = directory / "bracket.step#section-a.sketch.svg"
    ET.ElementTree(root).write(path, encoding="unicode")
    return path


class ReportTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)

    def test_report(self):
        make_sketch(self.dir)
        out = self.dir / "images"
        report = inspect(self.dir, out)  # finds the sketch in the directory
        self.assertIn("of section-A.svg", report)
        self.assertIn("hidden: Outline / Bolt, Construction", report)  # in the file's order
        self.assertIn("Strokes: 2", report)
        self.assertIn("#e11d48     3 pts  x 40.0..50.0  y 20.0..25.0  from (40.0, 20.0) to (50.0, 22.0)", report)
        self.assertIn("colour of d-length (Dimensions)", report)
        self.assertIn("d-height  offset: 9 -> 15", report)
        if shutil.which("rsvg-convert"):
            for name in ("section-A-full.png", "section-A-closeup.png", "section-A-ink.png"):
                self.assertTrue((out / name).stat().st_size > 1000, name)
            self.assertEqual(sorted(p.suffix for p in out.iterdir()), [".png"] * 3)  # no leftovers

    def test_sketch_on_a_scene(self):
        scene_sketch(self.dir)
        report = inspect(self.dir, self.dir / "images")
        self.assertIn("  of bracket.step#section-a\n", report)
        self.assertNotIn("(missing)", report)
        self.assertIn("view=section-a, frame=insertion=2.5", report)
        self.assertIn("drawn at insertion=2.5", report)
        # Against the annotations its .tp.json gives the view.
        self.assertIn("Moved labels: 2\n  d-length  offset: -16 -> -22\n  d-length  text-at: 0.5 -> 0.667", report)

    def test_nodes_shown_inside_hidden_ones(self):
        # As the viewer saves them: a hidden layer (visibility, not display), one of its
        # sublayers shown again, the other hidden with it.
        g = lambda parent, label, **attrs: ET.SubElement(  # noqa: E731
            parent, f"{{{SVG_NS}}}g", {f"{{{INKSCAPE_NS}}}label": label, **attrs})
        root = ET.Element(f"{{{SVG_NS}}}svg")
        dims = g(root, "Dimensions", visibility="hidden")
        g(g(dims, "Overall", visibility="visible"), "Width")
        g(dims, "Details")
        g(root, "Notes", style="display:none")
        g(g(root, "Outline"), "Bolt", style="visibility: hidden")
        self.assertEqual(hidden_and_shown(root), (["Dimensions", "Notes", "Outline / Bolt"],
                                                  ["Dimensions / Overall"]))

    def test_sketch_of_a_sheet_in_a_folder(self):
        path = make_sketch(self.dir)
        root = ET.parse(path).getroot()
        root.set(f"{{{TP_NS}}}sketch-of", "parts/section-A.svg")  # as a project's viewer names it
        ET.ElementTree(root).write(path, encoding="unicode")
        self.assertIn("d-height  offset: 9 -> 15", inspect(path, self.dir / "images"))  # the sheet next to it

    def test_sketch_over_a_model(self):
        (self.dir / "frame.glb").write_bytes(b"glTF")
        svg = (f'<svg xmlns="{SVG_NS}" xmlns:inkscape="{INKSCAPE_NS}" xmlns:tp="{TP_NS}" '
               'xmlns:xlink="http://www.w3.org/1999/xlink" width="100mm" height="50mm" viewBox="-50 -25 100 50" '
               'tp:sketch-of="frame.glb"><metadata><tp:view title="frame.glb, from the right" kind="model view" '
               'origin="12 0 0" normal="1 0 0" xdir="0 1 0" area="-50 -25 50 25"/></metadata>'
               '<g id="tp-view" inkscape:groupmode="layer" inkscape:label="View" transform="scale(1,-1)">'
               '<image width="100" height="50" transform="translate(-50 25) scale(1,-1)" xlink:href="data:image/png;base64,'
               'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="/>'
               '</g><g id="tp-sketch" inkscape:groupmode="layer" inkscape:label="Sketch" transform="scale(1,-1)">'
               '<path d="M0 0 L10 5" stroke="#e11d48" fill="none"/></g></svg>')
        (self.dir / "frame.glb.sketch.svg").write_text(svg)
        report = inspect(self.dir, self.dir / "images")
        self.assertIn("  of frame.glb\n", report)  # found next to it
        self.assertIn("normal=1 0 0", report)
        self.assertIn("model point = origin + x·xdir + y·(normal × xdir)", report)
        self.assertIn("from (0.0, 0.0) to (10.0, 5.0)", report)
        self.assertNotIn("Moved labels", report)
        if shutil.which("rsvg-convert"):
            self.assertTrue((self.dir / "images" / "frame.glb-full.png").stat().st_size > 1000)

    def test_finds_the_newest_sketch_and_skips_hidden_folders(self):
        older = self.dir / "a" / "x.sketch.svg"
        newer = self.dir / "b" / "y.sketch.svg"
        hidden = self.dir / ".git" / "z.sketch.svg"
        for i, p in enumerate((older, newer, hidden)):
            p.parent.mkdir()
            p.write_text("<svg/>")
            os.utime(p, (1000 + i, 1000 + i))
        self.assertEqual(find_sketch(self.dir), newer)
        self.assertEqual(find_sketch(older), older)
        (self.dir / "empty").mkdir()
        self.assertIsNone(find_sketch(self.dir / "empty"))


if __name__ == "__main__":
    unittest.main()
