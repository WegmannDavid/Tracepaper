"""Read a sketch file the way the AI needs to: ``tracepaper inspect [FILE or DIR]``.

Prints what the person did (their strokes, with colour, where the colour was
picked from and where they lie; labels they moved; layers they hid) and renders
images to look at: the whole sheet, a close-up of the ink, and the same close-up
with the drawing faded, for ink in a colour of the sheet.
"""
from __future__ import annotations

import math
import shutil
import subprocess
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Optional

from .server import SKETCH_SUFFIX
from .sheet import INKSCAPE_NS, SVG_NS, TP_NS, fmt

SVG, INK, TP = f"{{{SVG_NS}}}", f"{{{INKSCAPE_NS}}}", f"{{{TP_NS}}}"
PLACEMENT = ("offset", "text-at", "angle")
WIDTH_PX = 2000


def find_sketch(path: Path) -> Optional[Path]:
    """path if it is a sketch file, else the newest sketch file under it."""
    if path.is_file():
        return path
    found = [p for p in path.rglob(f"*{SKETCH_SUFFIX}")
             if not any(part.startswith(".") for part in p.relative_to(path).parts)]
    return max(found, key=lambda p: p.stat().st_mtime, default=None)


def _layers(root: ET.Element) -> list:
    return [g for g in root.findall(f"{SVG}g") if g.get(f"{INK}groupmode") == "layer"]


def _label(g: ET.Element) -> str:
    return g.get(f"{INK}label") or g.get("id") or "layer"


def _layer_tree(parent: ET.Element, above: str = "") -> list:
    """(path, layer) for every layer and sublayer, e.g. ("Dimensions / Overall", g)."""
    out = []
    for g in _layers(parent):
        path = f"{above} / {_label(g)}" if above else _label(g)
        out += [(path, g), *_layer_tree(g, path)]
    return out


def _hidden(g: ET.Element) -> bool:
    return "display:none" in (g.get("style") or "").replace(" ", "") or g.get("display") == "none"


def _ink_layer(root: ET.Element) -> Optional[ET.Element]:
    return next((g for g in _layers(root) if g.get("id") == "tp-sketch" or _label(g) == "Sketch"), None)


def _points(d: str) -> list:
    nums = [float(v) for v in d.replace("M", " ").replace("L", " ").replace(",", " ").split()]
    return list(zip(nums[0::2], nums[1::2]))


def strokes(root: ET.Element) -> list:
    ink = _ink_layer(root)
    out = []
    for p in ink.findall(f"{SVG}path") if ink is not None else []:
        pts = _points(p.get("d", ""))
        if pts:
            out.append({"color": p.get("stroke"), "from": p.get(f"{TP}color-from"), "points": pts})
    return out


def moved_labels(sketch: ET.Element, sheet: Optional[ET.Element]) -> list:
    """(id, attribute, sheet value, sketch value) for annotations the person dragged."""
    if sheet is None:
        return []
    out = []
    for g in sketch.iter(f"{SVG}g"):
        if g.get("id") and (g.get(f"{TP}dim") or g.get(f"{TP}anchor")):
            before = sheet.find(f".//{SVG}g[@id='{g.get('id')}']")
            for name in PLACEMENT:
                if before is not None and g.get(TP + name) != before.get(TP + name):
                    out.append((g.get("id"), name, before.get(TP + name), g.get(TP + name)))
    return out


def _layer_of(root: ET.Element, element_id: str) -> Optional[str]:
    """The path of the innermost layer holding the element (or being it)."""
    found = None
    for path, g in _layer_tree(root):
        if g.get("id") == element_id or g.find(f".//*[@id='{element_id}']") is not None:
            found = path
    return found


def _bounds(points: list) -> tuple:
    xs, ys = [p[0] for p in points], [p[1] for p in points]
    return min(xs), min(ys), max(xs), max(ys)


def _render(source: str, png: Path, box: Optional[tuple] = None, fade: bool = False) -> None:
    """Render the sketch's SVG text to png; box = (x0, y0, x1, y1) in model mm shows only that."""
    root = ET.fromstring(source)
    if box is not None:
        x0, y0, x1, y1 = box
        root.set("viewBox", f"{fmt(x0)} {fmt(-y1)} {fmt(x1 - x0)} {fmt(y1 - y0)}")  # model y is up
        root.set("width", f"{fmt(x1 - x0)}mm")
        root.set("height", f"{fmt(y1 - y0)}mm")
    if fade:
        for g in _layers(root):
            if g is not _ink_layer(root):
                g.set("opacity", "0.25")
    svg = png.with_suffix(".svg")
    svg.write_text(ET.tostring(root, encoding="unicode"), encoding="utf-8")
    try:
        subprocess.run(["rsvg-convert", "-b", "white", "-w", str(WIDTH_PX), str(svg), "-o", str(png)],
                       check=True, capture_output=True, timeout=120)
    finally:
        svg.unlink()


def inspect(path: Path, out_dir: Optional[Path] = None) -> str:
    """A report on the sketch at path (or the newest under it), with images written to out_dir."""
    sketch_path = find_sketch(path)
    if sketch_path is None:
        raise SystemExit(f"tracepaper: no {SKETCH_SUFFIX} file under {path}")
    source = sketch_path.read_text(encoding="utf-8")
    root = ET.fromstring(source)
    sheet_path = sketch_path.with_name(root.get(f"{TP}sketch-of") or
                                       sketch_path.name[: -len(SKETCH_SUFFIX)] + ".svg")
    sheet = ET.parse(sheet_path).getroot() if sheet_path.is_file() else None
    view = root.find(f"{SVG}metadata/{TP}view")

    lines = [f"Sketch: {sketch_path}", f"  of {sheet_path.name}" + ("" if sheet is not None else " (missing)")
             + (f", saved {root.get(TP + 'saved')}" if root.get(TP + "saved") else "")]
    if view is not None:
        lines.append("  view: " + ", ".join(f"{k}={v}" for k, v in view.attrib.items()))
    hidden = [path for path, g in _layer_tree(root) if _hidden(g)]
    lines.append(f"  hidden layers: {', '.join(hidden) or 'none'}")

    ink = strokes(root)
    lines.append(f"\nStrokes: {len(ink)} (model mm, y up)")
    for i, s in enumerate(ink, 1):
        x0, y0, x1, y1 = _bounds(s["points"])
        (sx, sy), (ex, ey) = s["points"][0], s["points"][-1]
        line = (f"  {i:>3}  {s['color']}  {len(s['points']):>4} pts  x {x0:.1f}..{x1:.1f}  y {y0:.1f}..{y1:.1f}"
                f"  from ({sx:.1f}, {sy:.1f}) to ({ex:.1f}, {ey:.1f})")
        if s["from"]:
            layer = _layer_of(root, s["from"])
            line += f"  colour of {s['from']}" + (f" ({layer})" if layer else "")
        lines.append(line)

    moved = moved_labels(root, sheet)
    lines.append(f"\nMoved labels: {len(moved) or 'none'}")
    lines += [f"  {i}  {name}: {old} -> {new}" for i, name, old, new in moved]

    if not shutil.which("rsvg-convert"):
        lines.append("\nNo images: rsvg-convert is not installed (Fedora: librsvg2-tools).")
        return "\n".join(lines)
    out_dir = Path(out_dir or tempfile.mkdtemp(prefix="tracepaper-"))
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = sketch_path.name[: -len(SKETCH_SUFFIX)]
    images = [(out_dir / f"{stem}-full.png", None, False, "the whole sheet with the ink")]
    if ink:
        x0, y0, x1, y1 = _bounds([p for s in ink for p in s["points"]])
        margin = max(5.0, 0.25 * max(x1 - x0, y1 - y0))
        box = (math.floor(x0 - margin), math.floor(y0 - margin), math.ceil(x1 + margin), math.ceil(y1 + margin))
        images += [(out_dir / f"{stem}-closeup.png", box, False, "close-up of the ink"),
                   (out_dir / f"{stem}-ink.png", box, True, "the same, drawing faded")]
    lines.append("\nImages:")
    for png, box, fade, what in images:
        _render(source, png, box, fade)
        lines.append(f"  {png}  {what}")
    return "\n".join(lines)
