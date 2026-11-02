import json
import math
import shutil
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

from tracepaper import plots
from tracepaper.render import render
from tracepaper.report import inspect
from tracepaper.server import list_views, make_server, view_of_sketch
from tracepaper.sheet import INKSCAPE_NS, SVG_NS, TP_NS
from tracepaper.views import save_plot

SVG, TP, INK = f"{{{SVG_NS}}}", f"{{{TP_NS}}}", f"{{{INKSCAPE_NS}}}"
BANDS = [50, 63, 80, 100, 125, 160, 200, 250, 315, 400, 500, 630, 800, 1000, 1250, 1600, 2000, 2500, 3150, 4000, 5000]


def loss(thickness, f):
    return 20 * math.log10(thickness * f) - 20


def glazing_plot():
    """Transmission loss over frequency, as BoxWindow plots it: band values with their
    spread, each with a fine curve, three of them a family; a mark, a limit."""
    series = []
    for name, family, t in (("pane", None, 4), ("inner 6", "inner pane", 6), ("inner 8", "inner pane", 8),
                            ("inner 10", "inner pane", 10)):
        ys = [loss(t, f) for f in BANDS]
        series.append({"name": name, "kind": "values", "x": BANDS, "y": ys, "family": family,
                       "low": [y - 2 for y in ys], "high": [y + 1 for y in ys],
                       "summary": f"Rw = {round(loss(t, 500))} dB"})
        fine = [50 * 1.02 ** k for k in range(233)]
        series.append({"name": f"{name} fine", "detail-of": name, "x": fine, "y": [loss(t, f) for f in fine]})
    return {"title": "Transmission loss", "source": "window/acoustics.py", "notes": "Field incidence.",
            "x": {"label": "frequency", "unit": "Hz", "scale": "log", "ticks": BANDS},
            "y": {"label": "transmission loss", "unit": "dB", "min": 30},
            "series": series, "marks": [{"x": 2993, "label": "coincidence"}, {"x": 59, "label": "resonance", "of": "pane"}],
            "hidden": ["inner 6 fine"]}


def clip_plot():
    """Force over lift, without and with the foam, side by side on the same axes."""
    lifts = [k / 10 for k in range(31)]
    return {"title": "Clip", "x": {"label": "lift", "unit": "mm"}, "y": {"label": "force", "unit": "N", "max": 1},
            "panels": [{"title": title, "series": [{"name": f"β {b}", "x": lifts,
                                                    "y": [k * b / 55 * math.sin(1.7 * l) for l in lifts]}
                                                   for b in (55, 67)]}
                       for title, k in (("without the foam", 0.4), ("with the foam", 1.2))]}


def groups(root):
    return {g.get(f"{INK}label"): g for g in root.iter(f"{SVG}g") if g.get(f"{INK}label")}


class CheckTest(unittest.TestCase):
    def test_what_is_wrong_and_where(self):
        cases = [
            ({"series": [{"name": "a", "x": [1, 2], "y": [1]}]}, "series[0] (a): x has 2 values and y 1"),
            ({"series": [{"name": "a", "x": [1], "y": [1], "kind": "bars"}]}, "kind: 'bars', not one of"),
            ({"series": [{"name": "a", "x": [1], "y": [1], "detail-of": "b"}]}, "detail of 'b', which isn't"),
            ({"series": [{"name": "a", "x": [1], "y": ["1"]}]}, "series[0] (a).y: '1' is not a number"),
            ({"series": [{"name": "a", "x": [1], "y": [1]}], "x": {"scale": "log", "range": [0, 10]}},
             "x.range: a log axis's range must be above 0"),
            ({"series": [], "panels": []}, "not both"),
            ({"series": [{"name": "a", "x": [1], "y": [1]}, {"name": "a", "x": [1], "y": [1]}]}, "two series named 'a'"),
            ({"series": [{"name": "a", "x": [1], "y": [1]}], "marks": [{"x": 1, "y": 2}]}, "marks[0]: an object with x"),
            ({"panels": [{"series": [{"name": "a", "x": [1], "y": [1], "low": [1, 2]}]}]},
             "panels[0].series[0] (a).low: a list as long as x"),
            ({"series": [{"name": "a", "x": [1], "y": [float("nan")]}]}, "is not a number (null for none)"),
        ]
        for plot, message in cases:
            with self.subTest(message):
                with self.assertRaises(ValueError) as caught:
                    plots.check(plot)
                self.assertIn(message, str(caught.exception))

    def test_defaults(self):
        got = plots.check({"series": [{"name": "a", "x": [1, 2], "y": [3, None]}]})
        (panel,) = got["panels"]
        self.assertEqual(panel["series"][0]["kind"], "curve")
        self.assertEqual(panel["series"][0]["y"], [3.0, None])  # a gap
        self.assertEqual((panel["x"]["scale"], panel["own"]), ("linear", {"x": False, "y": False}))


class DrawTest(unittest.TestCase):
    def test_a_panel_as_a_tree_of_its_series(self):
        sheet = plots.draw(plots.check(glazing_plot()), "glazing.plot.json")
        root = sheet.to_element()
        view = root.find(f"{SVG}metadata/{TP}view")
        self.assertEqual((view.get("kind"), view.get("title"), view.get("source")),
                         ("plot", "Transmission loss", "window/acoustics.py"))
        (panel,) = view.findall(f"{TP}panel")
        scale, lo, hi, a, b = panel.get("x-axis").split()
        self.assertEqual((scale, float(lo), float(hi)), ("log", 50, 5000))  # the data's range along x
        self.assertEqual((panel.get("x-unit"), panel.get("y-label")), ("Hz", "transmission loss"))
        self.assertEqual([g.get(f"{INK}label") for g in root.findall(f"{SVG}g")],
                         ["Axes", "Marks", "pane", "inner pane", "Notes"])
        nodes = groups(root)
        family = nodes["inner pane"]
        self.assertEqual([g.get(f"{INK}label") for g in family.findall(f"{SVG}g")], ["inner 6", "inner 8", "inner 10"])
        # A detail inside its series, under it, faint; hidden as the file says.
        inner6 = nodes["inner 6"]
        self.assertIs(inner6[0], nodes["inner 6 fine"])
        self.assertEqual(nodes["inner 6 fine"].get("style"), "display:none")
        self.assertEqual(nodes["inner 8 fine"].find(f"{SVG}path").get("stroke-opacity"), "0.5")
        # The family's colours from light to dark, one hue; its members dashed alike.
        lines = [nodes[n].find(f"{SVG}path[@id='plot-{n.replace(' ', '-')}-line']") for n in ("inner 6", "inner 8", "inner 10")]
        light = [sum(int(l.get("stroke")[k:k + 2], 16) for k in (1, 3, 5)) for l in lines]
        self.assertEqual(light, sorted(light, reverse=True))
        self.assertEqual(len({l.get("stroke-dasharray") for l in lines}), 1)
        # The values, their spread and markers, the numbers kept for reading back.
        pane = nodes["pane"]
        self.assertEqual(pane.get(f"{TP}series"), "pane")
        self.assertEqual(len(pane.get(f"{TP}xs").split()), 21)
        ids = [el.get("id") for el in pane.iter() if el.get("id")]
        for part in ("plot-pane-bars", "plot-pane-line", "plot-pane-markers", "plot-pane-key-text", "plot-pane-key-summary"):
            self.assertIn(part, ids)
        # Below the limit: ringed in red. The mark that belongs to pane goes with it.
        self.assertEqual(pane.find(f".//{SVG}path[@id='plot-pane-over']").get("stroke"), plots.OVER)
        self.assertIn("resonance", [t.text for t in pane.iter(f"{SVG}text")])
        self.assertIn("coincidence", [t.text for t in nodes["Marks"].iter(f"{SVG}text")])

    def test_tick_labels_never_overlap(self):
        root = plots.draw(plots.check(glazing_plot()), "glazing.plot.json").to_element()
        labels = [t for t in root.iter(f"{SVG}text") if (t.get("id") or "").startswith("plot-xlabel")]
        self.assertTrue(10 <= len(labels) < 21)  # every second third octave
        xs = [float(t.get("transform").split("(")[1].split()[0]) for t in labels]
        widths = [len(t.text) * 0.62 * plots.FS for t in labels]
        for (x0, w0), (x1, w1) in zip(zip(xs, widths), zip(xs[1:], widths[1:])):
            self.assertGreater(x1 - x0, (w0 + w1) / 2)

    def test_panels_share_their_axes(self):
        root = plots.draw(plots.check(clip_plot()), "clip.plot.json").to_element()
        a, b = root.findall(f"{SVG}metadata/{TP}view/{TP}panel")
        self.assertEqual((a.get("name"), b.get("name")), ("without the foam", "with the foam"))
        span = lambda el, k: el.get(f"{k}-axis").split()[:3]
        self.assertEqual(span(a, "y"), span(b, "y"))  # the same range, the larger force's
        ax, bx = (float(el.get("x-axis").split()[3]) for el in (a, b))
        self.assertGreater(bx, ax + plots.W)  # side by side
        self.assertEqual(float(a.get("y-axis").split()[3]), float(b.get("y-axis").split()[3]))
        nodes = [g.get(f"{INK}label") for g in root.findall(f"{SVG}g")]
        self.assertEqual(nodes, ["without the foam", "with the foam"])
        # A curve above its limit is drawn over in red where it is.
        over = [p for p in root.iter(f"{SVG}path") if (p.get("id") or "").endswith("-over")]
        self.assertTrue(over and all(p.get("stroke") == plots.OVER for p in over))

    def test_panels_with_their_own_y_stack(self):
        plot = clip_plot()
        plot["panels"][1]["y"] = {"label": "stiffness", "unit": "N/mm"}
        root = plots.draw(plots.check(plot), "clip.plot.json").to_element()
        a, b = root.findall(f"{SVG}metadata/{TP}view/{TP}panel")
        self.assertEqual(a.get("x-axis").split()[3:], b.get("x-axis").split()[3:])  # one above the other
        self.assertNotEqual(a.get("y-axis").split()[1:3], b.get("y-axis").split()[1:3])

    def test_out_of_range_is_cut_off(self):
        plot = {"x": {"range": [0, 10]}, "y": {"range": [0, 1]},
                "series": [{"name": "a", "x": [-5, 5, 15], "y": [0.5, 0.5, 2], "kind": "values"}]}
        sheet = plots.draw(plots.check(plot), "a.plot.json")
        (panel,) = sheet.panels
        x0, x1, y0, y1 = panel["x"].a, panel["x"].b, panel["y"].a, panel["y"].b
        root = sheet.to_element()
        line = root.find(f".//{SVG}path[@id='plot-a-line']").get("d")
        for x, y in zip(*[iter(float(v) for v in line.replace("M", " ").replace("L", " ").split())] * 2):
            self.assertTrue(x0 - 1e-6 <= x <= x1 + 1e-6 and y0 - 1e-6 <= y <= y1 + 1e-6, (x, y))
        self.assertEqual(root.find(f".//{SVG}path[@id='plot-a-markers']").get("d").count("M"), 1)  # one inside


class FilesTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_save_checks_and_writes_in_place(self):
        path = save_plot(self.dir / "fem" / "glazing.plot.json", glazing_plot())
        text = path.read_text()
        self.assertIn('"ticks": [50, 63, 80,', text)  # numbers on one line
        self.assertEqual(json.loads(text), glazing_plot())
        self.assertEqual(list(path.parent.iterdir()), [path])  # no temporary file left
        with self.assertRaisesRegex(ValueError, "ends in .plot.json"):
            save_plot(self.dir / "glazing.json", glazing_plot())
        with self.assertRaisesRegex(ValueError, "x has 1 values and y 2"):
            save_plot(self.dir / "bad.plot.json", {"series": [{"name": "a", "x": [1], "y": [1, 2]}]})
        self.assertFalse((self.dir / "bad.plot.json").exists())

    def test_listed_and_served_as_a_sheet(self):
        save_plot(self.dir / "glazing.plot.json", glazing_plot())
        (self.dir / "glazing.plot.json.tmp").write_text("{")  # half written
        (views,) = list_views(self.dir)
        self.assertEqual((views.name, views.kind, views.plot), ("glazing.plot.json", "sheet", True))
        self.assertEqual(views.sketch().name, "glazing.plot.json.sketch.svg")
        self.assertEqual(view_of_sketch("glazing.plot.json.sketch.svg"), "glazing.plot.json")
        server = make_server(self.dir, 0)
        try:
            svg = server.view_file(views)
            self.assertEqual(ET.fromstring(svg).find(f"{SVG}metadata/{TP}view").get("kind"), "plot")
            (self.dir / "glazing.plot.json").write_text('{"series": 3}')
            with self.assertRaisesRegex(ValueError, "glazing.plot.json: series: a list of series"):
                server.view_file(views)
        finally:
            server.server_close()

    def test_inspect_reads_strokes_in_the_data(self):
        save_plot(self.dir / "glazing.plot.json", glazing_plot())
        sheet = plots.draw(plots.read_plot(self.dir / "glazing.plot.json"), "glazing.plot.json")
        root = sheet.to_element()
        root.set(f"{TP}sketch-of", "glazing.plot.json")
        (panel,) = sheet.panels
        X, Y = panel["x"], panel["y"]
        # A stroke along "inner 10" from 125 to 160 Hz, a little above it.
        pts = [(X(f), Y(loss(10, f) + 3)) for f in (125, 140, 160)]
        ink = ET.SubElement(root, f"{SVG}g", {"id": "tp-sketch", f"{INK}groupmode": "layer", f"{INK}label": "Sketch",
                                              "transform": "scale(1,-1)"})
        ET.SubElement(ink, f"{SVG}path", {"d": "M" + " L".join(f"{x:.4f} {y:.4f}" for x, y in pts),
                                          "stroke": "#e11d48", "fill": "none"})
        ET.ElementTree(root).write(self.dir / "glazing.plot.json.sketch.svg", encoding="unicode")
        report = inspect(self.dir, self.dir / "images")
        self.assertIn("frequency 125 Hz..160 Hz", report)
        self.assertIn(f"from (125 Hz, {plots.num(loss(10, 125) + 3, 3)} dB)", report)
        self.assertIn("nearest inner 10", report)
        self.assertNotIn("Moved labels", report)

    def test_render(self):
        save_plot(self.dir / "clip.plot.json", clip_plot())
        report = render(str(self.dir / "clip.plot.json"), out=self.dir / "out" / "clip.png")
        self.assertIn("Clip: 2 panels, 4 series", report)
        self.assertTrue((self.dir / "out" / "clip.svg").is_file())
        if shutil.which("rsvg-convert"):
            self.assertTrue((self.dir / "out" / "clip.png").is_file())


if __name__ == "__main__":
    unittest.main()
