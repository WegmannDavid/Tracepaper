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

from . import views as scenes
from .server import MODEL_SUFFIXES, SKETCH_SUFFIX, view_of_sketch
from .sheet import INKSCAPE_NS, SVG_NS, TP_NS, fmt

SVG, INK, TP = f"{{{SVG_NS}}}", f"{{{INKSCAPE_NS}}}", f"{{{TP_NS}}}"
PLACEMENT = ("offset", "text-at", "angle")
WIDTH_PX = 2000
# The images show the sheet as on a screen this many pixels wide, at 2 image pixels to
# each of its pixels, which is what line widths are in (SPEC-v0.md §2.1).
SCREEN_PX = WIDTH_PX / 2
SHAPES = {"path", "line", "polyline", "polygon", "circle", "ellipse", "rect"}


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


NOT_DRAWN = {"defs", "metadata", "pattern", "clipPath", "mask", "marker", "symbol", "style", "script"}


def _is_node(el: ET.Element) -> bool:
    return el.tag == f"{SVG}g" and (el.get(f"{INK}groupmode") == "layer" or el.get(f"{INK}label") is not None)


def _layer_tree(parent: ET.Element, above: str = "") -> list:
    """(path, group) for every node of the tree (SPEC-v0.md §2.4), as the viewer shows it:
    named groups, e.g. ("Dimensions / Overall", g); unnamed groups in between don't count."""
    out = []
    for el in parent:
        if _is_node(el):
            path = f"{above} / {_label(el)}" if above else _label(el)
            out += [(path, el), *_layer_tree(el, path)]
        elif el.tag.split("}")[-1] not in NOT_DRAWN:
            out += _layer_tree(el, above)
    return out


def _display_none(el: ET.Element) -> bool:
    return "display:none" in (el.get("style") or "").replace(" ", "") or el.get("display") == "none"


def _visibility(el: ET.Element) -> Optional[str]:
    for item in (el.get("style") or "").split(";"):
        name, _, value = item.partition(":")
        if name.strip() == "visibility":
            return value.strip()
    return el.get("visibility")


def hidden_and_shown(root: ET.Element) -> tuple:
    """([path], [path]): the nodes whose own drawing is hidden while that of the node around
    them isn't, and the nodes shown inside one whose own drawing is hidden. The viewer hides
    nodes with SVG's visibility, which a node inside can set back (SPEC.md §3); a sheet hides
    them with display="none" (§2.4)."""
    hidden, shown = [], []

    def walk(parent: ET.Element, above: str, node_hidden: bool, none: bool, visibility: str) -> None:
        for el in parent:
            if el.tag.rpartition("}")[2] in NOT_DRAWN:
                continue
            gone = none or _display_none(el)
            seen = _visibility(el) if _visibility(el) in ("visible", "hidden", "collapse") else visibility
            if _is_node(el):
                path = f"{above} / {_label(el)}" if above else _label(el)
                own = gone or seen != "visible"
                if own and not node_hidden:
                    hidden.append(path)
                if not own and node_hidden:
                    shown.append(path)
                walk(el, path, own, gone, seen)
            else:
                walk(el, above, node_hidden, gone, seen)

    walk(root, "", False, False, "visible")
    return hidden, shown


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
            out.append({"color": p.get("stroke"), "from": p.get(f"{TP}color-from"), "points": pts,
                        "frame": p.get(f"{TP}frame")})
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


def _placement(attrs: dict, kind: Optional[str]) -> dict:
    """An annotation's placement attributes with their defaults filled in, as text: attrs
    maps names (offset, text-at, angle) to values, numbers or "x y" text or [x, y]."""
    def text(v):
        if v is None:
            return None
        if isinstance(v, (list, tuple)):
            return " ".join(fmt(float(c)) for c in v)
        try:
            return " ".join(fmt(float(c)) for c in str(v).replace(",", " ").split())
        except ValueError:
            return str(v)
    defaults = {"linear": {"offset": 0, "text-at": 0.5}, "angular": {"text-at": 0.5},
                "radius": {"offset": 0, "angle": 45}, "diameter": {"offset": 0, "angle": 45},
                None: {"offset": "0 0"}}.get(kind, {})
    return {name: text(attrs.get(name, defaults.get(name))) for name in PLACEMENT
            if attrs.get(name, defaults.get(name)) is not None}


def moved_in_scene(sketch: ET.Element, meta: dict, view: Optional[str]) -> list:
    """(id, attribute, value in the .tp.json, sketch value) for annotations the person dragged
    on a view of a scene, against the annotations the scene's .tp.json gives that view."""
    if view is None:
        given = meta.get("annotations") or []
    else:
        spec = scenes.view_spec(meta, view)
        given = spec["annotations"] if spec else []
    given = {a["id"]: a for a in given if isinstance(a, dict) and "id" in a}
    out = []
    for g in sketch.iter(f"{SVG}g"):
        a = given.get(g.get("id"))
        if a is None or not (g.get(f"{TP}dim") or g.get(f"{TP}anchor")):
            continue
        kind = g.get(f"{TP}dim")
        before = _placement(a, kind)
        after = _placement({name: g.get(TP + name) for name in PLACEMENT if g.get(TP + name) is not None}, kind)
        for name in PLACEMENT:
            if name in after and before.get(name) != after[name]:
                out.append((g.get("id"), name, before.get(name), after[name]))
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
    for el in root.iter():
        if el.tag.rpartition("}")[2] in SHAPES:
            el.set("vector-effect", "non-scaling-stroke")  # lines in pixels, as the viewer draws them
    if box is not None:
        x0, y0, x1, y1 = box
        root.set("viewBox", f"{fmt(x0)} {fmt(-y1)} {fmt(x1 - x0)} {fmt(y1 - y0)}")  # model y is up
    # rsvg counts those pixels in the root's size, before scaling it to the image.
    _, _, w, h = (float(v) for v in root.get("viewBox", "0 0 100 100").replace(",", " ").split())
    root.set("width", f"{fmt(SCREEN_PX)}px")
    root.set("height", f"{fmt(SCREEN_PX * h / w)}px")
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
    # The view it is of sits next to it, under the name it gives (once a path from the project):
    # a sheet, a model, or a scene, with the view of it after "#".
    of = Path(root.get(f"{TP}sketch-of") or view_of_sketch(sketch_path.name)).name
    scene_name, scene_view = scenes.split_name(of)
    view = root.find(f"{SVG}metadata/{TP}view")
    sheet_path = sketch_path.with_name(scene_name)
    scene = scenes.is_scene(sheet_path)
    if scene:  # drawn over its 3D view, or on a sheet drawn from it
        model = view is not None and view.get("kind") == "model view"
    else:
        model = sheet_path.suffix.lower() in MODEL_SUFFIXES  # drawn over a 3D view (SPEC.md §3.1)
    sheet = ET.parse(sheet_path).getroot() if not (model or scene) and sheet_path.is_file() else None

    lines = [f"Sketch: {sketch_path}", f"  of {of}" + ("" if sheet_path.is_file() else " (missing)")
             + (f", saved {root.get(TP + 'saved')}" if root.get(TP + "saved") else "")]
    if view is not None:
        lines.append("  view: " + ", ".join(f"{k}={v}" for k, v in view.attrib.items()))
    if not model:  # a model's hidden parts are in the view's attributes
        hidden, shown = hidden_and_shown(root)
        lines.append(f"  hidden: {', '.join(hidden) or 'nothing'}")
        if shown:
            lines.append(f"  shown inside hidden: {', '.join(shown)}")

    ink = strokes(root)
    lines.append(f"\nStrokes: {len(ink)} " + (
        "(mm on the plane the view looked at, y up: model point = origin + x·xdir + y·(normal × xdir), "
        "at some depth along normal)" if model else "(model mm, y up)"))
    for i, s in enumerate(ink, 1):
        x0, y0, x1, y1 = _bounds(s["points"])
        (sx, sy), (ex, ey) = s["points"][0], s["points"][-1]
        line = (f"  {i:>3}  {s['color']}  {len(s['points']):>4} pts  x {x0:.1f}..{x1:.1f}  y {y0:.1f}..{y1:.1f}"
                f"  from ({sx:.1f}, {sy:.1f}) to ({ex:.1f}, {ey:.1f})")
        if s["from"]:
            layer = _layer_of(root, s["from"])
            line += f"  colour of {s['from']}" + (f" ({layer})" if layer else "")
        if s["frame"]:
            line += f"  drawn at {s['frame']}"
        lines.append(line)

    if not model:  # nothing to move on a model
        if scene:
            try:
                moved = moved_in_scene(root, scenes.read_meta(sheet_path), scene_view)
            except ValueError as err:
                moved = []
                lines.append(f"  (can't compare labels: {err})")
        else:
            moved = moved_labels(root, sheet)
        lines.append(f"\nMoved labels: {len(moved) or 'none'}")
        lines += [f"  {i}  {name}: {old} -> {new}" for i, name, old, new in moved]

    if not shutil.which("rsvg-convert"):
        lines.append("\nNo images: rsvg-convert is not installed (Fedora: librsvg2-tools).")
        return "\n".join(lines)
    out_dir = Path(out_dir or tempfile.mkdtemp(prefix="tracepaper-"))
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = sketch_path.name[: -len(SKETCH_SUFFIX)]
    images = [(out_dir / f"{stem}-full.png", None, False, f"the whole {'view' if model else 'sheet'} with the ink")]
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
