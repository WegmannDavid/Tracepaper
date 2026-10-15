import os
import shutil
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

from tracepaper.report import find_sketch, inspect
from tracepaper.sheet import INKSCAPE_NS, SVG_NS, TP_NS

EXAMPLE = Path(__file__).resolve().parents[1] / "examples" / "bracket" / "section-A.svg"


def make_sketch(directory: Path) -> Path:
    """A sketch of section-A as the viewer would save it: two strokes, one dimension moved."""
    shutil.copy(EXAMPLE, directory / "section-A.svg")
    root = ET.parse(EXAMPLE).getroot()
    root.set(f"{{{TP_NS}}}sketch-of", "section-A.svg")
    root.find(f".//{{{SVG_NS}}}g[@id='d-height']").set(f"{{{TP_NS}}}offset", "15")
    ink = ET.SubElement(root, f"{{{SVG_NS}}}g", {"id": "tp-sketch", f"{{{INKSCAPE_NS}}}groupmode": "layer",
                                                 f"{{{INKSCAPE_NS}}}label": "Sketch", "transform": "scale(1,-1)"})
    ET.SubElement(ink, f"{{{SVG_NS}}}path", {"d": "M40 20 L45 25 L50 22", "stroke": "#e11d48", "fill": "none"})
    ET.SubElement(ink, f"{{{SVG_NS}}}path", {"d": "M10 45 L30 45", "stroke": "#475569", "fill": "none",
                                             f"{{{TP_NS}}}color-from": "d-length"})
    path = directory / "section-A.sketch.svg"
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
        self.assertIn("hidden layers: Construction", report)
        self.assertIn("Strokes: 2", report)
        self.assertIn("#e11d48     3 pts  x 40.0..50.0  y 20.0..25.0  from (40.0, 20.0) to (50.0, 22.0)", report)
        self.assertIn("colour of d-length (Dimensions)", report)
        self.assertIn("d-height  offset: 9 -> 15", report)
        if shutil.which("rsvg-convert"):
            for name in ("section-A-full.png", "section-A-closeup.png", "section-A-ink.png"):
                self.assertTrue((out / name).stat().st_size > 1000, name)
            self.assertEqual(sorted(p.suffix for p in out.iterdir()), [".png"] * 3)  # no leftovers

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
