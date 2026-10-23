"""Write Tracepaper sheets (SPEC.md §2). Standard library only.

    from tracepaper.sheet import PathBuilder, Sheet

    sheet = Sheet("Plate", source="parts/plate.py", pipeline="build123d")
    outline = sheet.layer("Outline")
    outline.path("plate", PathBuilder().move(0, 0).line(60, 0).line(60, 30).line(0, 30).close())
    dims = sheet.layer("Dimensions")
    dims.linear("d-length", (0, 0), (60, 0), "60", dir="x", offset=-10)
    dims.angular("d-corner", (0, 0), (60, 0), (0, 30), offset=8)  # 90°, counter-clockwise from x
    sheet.save("plate.svg")

Coordinates are model coordinates: millimetres, y up. The annotation drawing mirrors
tracepaper/viewer/annotations.js; keep the two in sync.
"""
from __future__ import annotations

import math
import re
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Iterable, Optional, Sequence, Union

SVG_NS = "http://www.w3.org/2000/svg"
INKSCAPE_NS = "http://www.inkscape.org/namespaces/inkscape"
TP_NS = "urn:tracepaper:0"

ET.register_namespace("", SVG_NS)
ET.register_namespace("inkscape", INKSCAPE_NS)
ET.register_namespace("tp", TP_NS)

DEFAULT_FONT = "DejaVu Sans, Arial, sans-serif"
HATCH_ID = "tp-hatch"
HATCH = f"url(#{HATCH_ID})"  # fill=HATCH hatches a shape, e.g. a part's cut face inside its own group

Point = Sequence[float]

# Sizes as multiples of the font size.
K = dict(arrowLen=0.75, arrowHalf=0.15, extGap=0.3, extOver=0.5, textGap=0.25, charW=0.62,
         tail=0.6, insideMin=2.0, leaderMin=1.2, leaderStop=0.7, dotR=0.1, vCenter=0.35)

DEG = math.pi / 180
EPS = 1e-6

# Line widths and dashes are in pixels on screen, at any zoom (SPEC.md §2.1). Lines
# without a width are 1 pixel wide.
STYLES = {
    "geometry": {"stroke": "#1a1a1a", "stroke-width": "1.5", "fill": "none",
                 "stroke-linecap": "round", "stroke-linejoin": "round"},
    "thin": {"stroke": "#1a1a1a", "fill": "none"},
    "hidden": {"stroke": "#1a1a1a", "fill": "none", "stroke-dasharray": "6 4"},
    "center": {"stroke": "#6b7280", "fill": "none", "stroke-dasharray": "16 4 3 4"},
    "section": {"stroke": "#1a1a1a", "stroke-width": "1.5", "fill": "none",
                "stroke-dasharray": "20 4 4 4"},
    "construction": {"stroke": "#9ca3af", "fill": "none", "stroke-dasharray": "4 3"},
    "hatch": {"stroke": "none", "fill": f"url(#{HATCH_ID})"},
    "dimensions": {"stroke": "#475569", "fill": "#475569"},
    "constraints": {"stroke": "#7c3aed", "fill": "#7c3aed"},
}
THIN = "1"  # the default width, which annotations' lines keep on any layer
# Marks a line's width and dashes as pixels on screen for any SVG renderer. It isn't
# inherited, so every shape carries it.
SCREEN_WIDTH = {"vector-effect": "non-scaling-stroke"}

# Style used when Sheet.layer() is called with one of the suggested names (SPEC.md §2.4).
LAYER_STYLES = {
    "Outline": "geometry", "Hidden": "hidden", "Center": "center", "Construction": "construction",
    "Hatch": "hatch", "Dimensions": "dimensions", "Constraints": "constraints", "Notes": "dimensions",
    "Section lines": "section",
}


def fmt(x: float, digits: int = 4) -> str:
    k = 10 ** digits
    v = math.floor(x * k + 0.5) / k
    return str(int(v)) if v == int(v) else repr(v)


def _svg(tag: str) -> str:
    return f"{{{SVG_NS}}}{tag}"


def _tp(name: str) -> str:
    return f"{{{TP_NS}}}{name}"


def _ink(name: str) -> str:
    return f"{{{INKSCAPE_NS}}}{name}"


def _pt(p: Point) -> str:
    return f"{fmt(p[0])} {fmt(p[1])}"


def _value(v) -> Optional[str]:
    if v is None:
        return None
    return fmt(v) if isinstance(v, (int, float)) else str(v)


def _attrs(extra: dict) -> dict:
    return {k.replace("_", "-"): _value(v) for k, v in extra.items() if v is not None}


def _add(p, q):
    return (p[0] + q[0], p[1] + q[1])


def _sub(p, q):
    return (p[0] - q[0], p[1] - q[1])


def _mul(p, k):
    return (p[0] * k, p[1] * k)


def _dot(p, q):
    return p[0] * q[0] + p[1] * q[1]


def _len(p):
    return math.hypot(p[0], p[1])


def _unit(p):
    n = _len(p)
    return _mul(p, 1 / n) if n > 1e-12 else None


def text_width(label: str, fs: float) -> float:
    return len(label) * K["charW"] * fs


def _empty() -> dict:
    return {"lines": [], "arcs": [], "arrows": [], "dots": [], "texts": [], "box": None}


def _readable(v) -> float:
    """The angle of text along v in degrees, turned half round if need be to read from the left."""
    ang = math.atan2(v[1], v[0]) / DEG
    if ang > 90 + EPS:
        return ang - 180
    if ang <= -90 + EPS:
        return ang + 180
    return ang


def linear_geometry(p1: Point, p2: Point, offset: float, label: str, fs: float,
                    dir: str = "aligned", text_at: float = 0.5) -> dict:
    if dir == "x":
        u = (1.0, 0.0)
    elif dir == "y":
        u = (0.0, 1.0)
    else:
        u = _unit(_sub(p2, p1)) or (1.0, 0.0)
    n = (-u[1], u[0])
    level = _dot(p1, n) + offset
    a = _add(p1, _mul(n, level - _dot(p1, n)))
    b = _add(p2, _mul(n, level - _dot(p2, n)))
    g = _empty()
    for f, e in ((p1, a), (p2, b)):
        d = _sub(e, f)
        dl = _len(d)
        if dl > K["extGap"] * fs:
            k = _mul(d, 1 / dl)
            g["lines"].append((_add(f, _mul(k, K["extGap"] * fs)), _add(e, _mul(k, K["extOver"] * fs))))
    span = _len(_sub(b, a))
    v = _mul(_sub(b, a), 1 / span) if span > 1e-9 else u
    s0, s1 = 0.0, span
    if span >= K["insideMin"] * fs:
        g["arrows"] += [(a, _mul(v, -1)), (b, v)]
    else:
        t = (K["arrowLen"] + K["tail"]) * fs
        s0, s1 = -t, span + t
        g["arrows"] += [(a, v), (b, _mul(v, -1))]
    tw = text_width(label, fs)
    s_text = text_at * span
    hw = tw / 2 + K["textGap"] * fs
    if s_text < 0 or s_text > span:
        s0, s1 = min(s0, s_text - hw), max(s1, s_text + hw)
    g["lines"].append((_add(a, _mul(v, s0)), _add(a, _mul(v, s1))))
    ang = _readable(v)
    up = (-math.sin(ang * DEG), math.cos(ang * DEG))
    base = _add(_add(a, _mul(v, s_text)), _mul(up, K["textGap"] * fs))
    g["texts"].append((base, ang, label))
    g["box"] = (_add(base, _mul(up, K["vCenter"] * fs)), tw / 2, 0.5 * fs)
    return g


def legs(center: Point, p1: Point, p2: Point) -> tuple:
    """Where the legs of an angle at center through p1 and p2 point: the first one's direction,
    and how far counter-clockwise the second one is from it, both in radians."""
    t1 = math.atan2(p1[1] - center[1], p1[0] - center[0])
    sweep = math.atan2(p2[1] - center[1], p2[0] - center[0]) - t1
    if sweep < 0:
        sweep += 2 * math.pi
    return t1, sweep


def angular_geometry(center: Point, p1: Point, p2: Point, offset: float, label: str, fs: float,
                     text_at: float = 0.5) -> dict:
    """Like linear_geometry, but along an arc of radius offset round the vertex."""
    c, r = center, offset
    t1, sweep = legs(c, p1, p2)

    def on(t):
        return _add(c, (r * math.cos(t), r * math.sin(t)))

    g = _empty()
    # The legs run from the vertex to p1 and p2; where the arc is further out, a leg goes on
    # to it as an extension line.
    for p, t in ((p1, t1), (p2, t1 + sweep)):
        d = _len(_sub(p, c))
        if r > d + K["extGap"] * fs:
            k = (math.cos(t), math.sin(t))
            g["lines"].append((_add(c, _mul(k, d + K["extGap"] * fs)), _add(c, _mul(k, r + K["extOver"] * fs))))
    length = r * sweep
    # An arrowhead from its tip at angle t, its base on the arc at angle t + side * head.
    head = 2 * math.asin(min(1, K["arrowLen"] * fs / (2 * r)))

    def arrow(t, side):
        tip = on(t)
        return tip, _unit(_sub(tip, on(t + side * head))) or (1.0, 0.0)

    s0, s1 = 0.0, length
    if length >= K["insideMin"] * fs:
        g["arrows"] += [arrow(t1, 1), arrow(t1 + sweep, -1)]
    else:
        t = (K["arrowLen"] + K["tail"]) * fs
        s0, s1 = -t, length + t
        g["arrows"] += [arrow(t1, -1), arrow(t1 + sweep, 1)]
    tw = text_width(label, fs)
    s_text = text_at * length
    hw = tw / 2 + K["textGap"] * fs
    if s_text < 0 or s_text > length:
        s0, s1 = min(s0, s_text - hw), max(s1, s_text + hw)
    g["arcs"].append((tuple(c), r, (t1 + s0 / r) / DEG, (t1 + s1 / r) / DEG))
    t_text = t1 + s_text / r
    ang = _readable((-math.sin(t_text), math.cos(t_text)))
    up = (-math.sin(ang * DEG), math.cos(ang * DEG))
    base = _add(on(t_text), _mul(up, K["textGap"] * fs))
    g["texts"].append((base, ang, label))
    g["box"] = (_add(base, _mul(up, K["vCenter"] * fs)), tw / 2, 0.5 * fs)
    return g


def radial_geometry(center: Point, r: float, angle: float, offset: float, label: str, fs: float,
                    diameter: bool = False) -> dict:
    d = (math.cos(angle * DEG), math.sin(angle * DEG))
    q = _add(center, _mul(d, r))
    end = _add(center, _mul(d, r + offset))
    g = _empty()
    if diameter and 2 * r >= K["insideMin"] * fs:
        lo, hi = min(-r, r + offset), max(r, r + offset)
        g["arrows"] += [(q, d), (_sub(center, _mul(d, r)), _mul(d, -1))]
    elif diameter:
        # Small hole: arrows outside, pointing in.
        t = (K["arrowLen"] + K["tail"]) * fs
        lo, hi = min(-r - t, r + offset), max(r + t, r + offset)
        g["arrows"] += [(q, _mul(d, -1)), (_sub(center, _mul(d, r)), d)]
    else:
        lo, hi = min(r, r + offset), max(r, r + offset)
        g["arrows"].append((q, _mul(d, -1) if offset >= 0 else d))
    g["lines"].append((_add(center, _mul(d, lo)), _add(center, _mul(d, hi))))
    side = d if (r + offset if diameter else offset) >= 0 else _mul(d, -1)
    sx = 1 if side[0] >= -1e-9 else -1
    tw = text_width(label, fs)
    gap = K["textGap"] * fs
    g["lines"].append((end, _add(end, (sx * (tw + 2 * gap), 0))))
    base = _add(end, (sx * (tw / 2 + gap), gap))
    g["texts"].append((base, 0, label))
    g["box"] = (_add(base, (0, K["vCenter"] * fs)), tw / 2, 0.5 * fs)
    return g


def symbol_geometry(anchor: Point, offset: Point, label: str, fs: float) -> dict:
    at = _add(anchor, offset)
    dl = _len(offset)
    g = _empty()
    if dl > K["leaderMin"] * fs:
        k = _mul(offset, 1 / dl)
        g["lines"].append((tuple(anchor), _sub(at, _mul(k, K["leaderStop"] * fs))))
        g["dots"].append(tuple(anchor))
    g["texts"].append(((at[0], at[1] - K["vCenter"] * fs), 0, label))
    g["box"] = (at, text_width(label, fs) / 2, 0.5 * fs)
    return g


def _arc_end(c, r, a):
    return _add(c, (r * math.cos(a * DEG), r * math.sin(a * DEG)))


def _arc_points(c, r, a0, a1) -> list:
    """The arc's ends, and where it is furthest left, right, up or down."""
    quarters = range(math.ceil(a0 / 90), math.floor(a1 / 90) + 1)
    return [_arc_end(c, r, a) for a in (a0, a1, *(90 * q for q in quarters))]


def _geometry_points(g: dict) -> list:
    pts = [p for line in g["lines"] for p in line] + [tip for tip, _ in g["arrows"]] + g["dots"]
    pts += [p for arc in g["arcs"] for p in _arc_points(*arc)]
    center, hw, hh = g["box"]
    reach = max(hw, hh)  # the label may be rotated
    pts += [_add(center, (-reach, -reach)), _add(center, (reach, reach))]
    return pts


def _arrow_d(tip, direction, fs) -> str:
    base = _sub(tip, _mul(direction, K["arrowLen"] * fs))
    side = _mul((-direction[1], direction[0]), K["arrowHalf"] * fs)
    return f"M{_pt(tip)} L{_pt(_add(base, side))} L{_pt(_sub(base, side))} Z"


def text_transform(at: Point, angle: float = 0) -> str:
    rotate = f" rotate({fmt(angle)})" if abs(angle) > 1e-9 else ""
    return f"translate({_pt(at)}){rotate} scale(1,-1)"


def _arc_d(c, r, a0, a1) -> str:
    """An arc's path, counter-clockwise in model coordinates."""
    return (f"M{_pt(_arc_end(c, r, a0))} A{fmt(r)} {fmt(r)} 0 {int(a1 - a0 > 180)} 1 "
            f"{_pt(_arc_end(c, r, a1))}")


def _render(group: ET.Element, g: dict, fs: float) -> None:
    if g["lines"] or g["arcs"]:
        d = [f"M{_pt(a)} L{_pt(b)}" for a, b in g["lines"]] + [_arc_d(*arc) for arc in g["arcs"]]
        ET.SubElement(group, _svg("path"), {"d": " ".join(d), "fill": "none", **SCREEN_WIDTH})
    if g["arrows"]:
        ET.SubElement(group, _svg("path"),
                      {"d": " ".join(_arrow_d(t, d, fs) for t, d in g["arrows"]), "stroke": "none"})
    for c in g["dots"]:
        ET.SubElement(group, _svg("circle"),
                      {"cx": fmt(c[0]), "cy": fmt(c[1]), "r": fmt(K["dotR"] * fs), "stroke": "none"})
    for at, angle, label in g["texts"]:
        t = ET.SubElement(group, _svg("text"),
                          {"transform": text_transform(at, angle), "text-anchor": "middle", "stroke": "none"})
        t.text = label


class PathBuilder:
    """Path data in model coordinates (mm, y up)."""

    def __init__(self) -> None:
        self._parts: list = []
        self.points: list = []

    def move(self, x: float, y: float) -> "PathBuilder":
        self._parts.append(f"M{fmt(x)} {fmt(y)}")
        self.points.append((x, y))
        return self

    def line(self, x: float, y: float) -> "PathBuilder":
        self._parts.append(f"L{fmt(x)} {fmt(y)}")
        self.points.append((x, y))
        return self

    def arc(self, x: float, y: float, r: float, *, ccw: bool = True, large: bool = False) -> "PathBuilder":
        """Circular arc of radius r to (x, y), counter-clockwise unless ccw=False."""
        self._parts.append(f"A{fmt(r)} {fmt(r)} 0 {int(large)} {int(ccw)} {fmt(x)} {fmt(y)}")
        self.points.append((x, y))
        return self

    def close(self) -> "PathBuilder":
        self._parts.append("Z")
        return self

    @property
    def d(self) -> str:
        return " ".join(self._parts)


class Layer:
    def __init__(self, sheet: "Sheet", name: str, style: Union[str, dict], visible: bool, extra: dict,
                 parent: Optional["Layer"] = None):
        self.sheet = sheet
        self.name = name
        preset = dict(STYLES[style]) if isinstance(style, str) else dict(style)
        preset.update(_attrs(extra))
        if preset.get("fill") == f"url(#{HATCH_ID})":
            sheet.uses_hatch = True
        # What the layer draws with, set here or inherited from the layer above.
        stroke = preset.get("stroke") or (parent.color if parent else "#1a1a1a")
        self.color = "#1a1a1a" if stroke == "none" else stroke
        self._fill_attr = preset.get("fill", parent._fill_attr if parent else "")
        self._width = preset.get("stroke-width", parent._width if parent else None)
        # Text and annotation arrows need a solid fill even when the layer's shapes have none,
        # and annotations stay thin on layers with heavy lines, such as Outline.
        self._fill = None if self._fill_attr.startswith("#") else self.color
        self._thin = None if self._width in (None, THIN) else THIN
        slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or "layer"
        attrs = {"id": f"{parent.element.get('id')}-{slug}" if parent else f"layer-{slug}",
                 _ink("groupmode"): "layer", _ink("label"): name}
        if parent is None:
            attrs["transform"] = "scale(1,-1)"  # only top-level layers flip: SPEC.md §2.4
            self.element = ET.Element(_svg("g"), {**attrs, **preset})
        else:
            self.element = ET.SubElement(parent.element, _svg("g"), {**attrs, **preset})
        if not visible:
            self.element.set("style", "display:none")

    def sublayer(self, name: str, *, style: Union[str, dict, None] = None, visible: bool = True,
                 **attrs) -> "Layer":
        """A layer inside this one, shown as a branch of the viewer's layer tree.

        Without a style it draws like this layer, unless its name is one of the
        suggested layer names (LAYER_STYLES).
        """
        style = style or LAYER_STYLES.get(name) or {}
        return Layer(self.sheet, name, style, visible, attrs, parent=self)

    def _shape(self, tag: str, id: str, attrs: dict, source: Optional[str], extra: dict,
               points: Iterable[Point]) -> ET.Element:
        el = ET.SubElement(self.element, _svg(tag), {"id": id, **attrs, **({} if tag == "text" else SCREEN_WIDTH)})
        if source:
            el.set(_tp("source"), source)
        el.attrib.update(_attrs(extra))
        if el.get("fill") == HATCH:
            self.sheet.uses_hatch = True
        self.sheet._grow(points)
        return el

    def line(self, id: str, p1: Point, p2: Point, *, source: Optional[str] = None, **attrs) -> ET.Element:
        return self._shape("line", id, {"x1": fmt(p1[0]), "y1": fmt(p1[1]), "x2": fmt(p2[0]), "y2": fmt(p2[1])},
                           source, attrs, [p1, p2])

    def path(self, id: str, d: Union[PathBuilder, str], *, source: Optional[str] = None, **attrs) -> ET.Element:
        """A path; pass a PathBuilder so the sheet can size itself to it."""
        if isinstance(d, PathBuilder):
            return self._shape("path", id, {"d": d.d}, source, attrs, d.points)
        return self._shape("path", id, {"d": d}, source, attrs, [])

    def polyline(self, id: str, points: Sequence[Point], *, closed: bool = False,
                 source: Optional[str] = None, **attrs) -> ET.Element:
        b = PathBuilder().move(*points[0])
        for p in points[1:]:
            b.line(*p)
        if closed:
            b.close()
        return self.path(id, b, source=source, **attrs)

    def circle(self, id: str, center: Point, r: float, *, source: Optional[str] = None, **attrs) -> ET.Element:
        cx, cy = center
        return self._shape("circle", id, {"cx": fmt(cx), "cy": fmt(cy), "r": fmt(r)}, source, attrs,
                           [(cx - r, cy - r), (cx + r, cy + r)])

    def text(self, id: str, at: Point, text: str, *, angle: float = 0, anchor: str = "start",
             source: Optional[str] = None, **attrs) -> ET.Element:
        """A note that can't be dragged. `at` is the start of the baseline."""
        fs = self.sheet.font_size
        base = {"transform": text_transform(at, angle), "text-anchor": anchor, "stroke": "none"}
        if self._fill:
            base["fill"] = self._fill
        el = self._shape("text", id, base, source, attrs,
                         [at, _add(at, (text_width(text, fs), fs))])
        el.text = text
        return el

    def _annotation(self, id: str, tp_attrs: dict, g: dict) -> ET.Element:
        el = ET.SubElement(self.element, _svg("g"), {"id": id})
        for k, v in tp_attrs.items():
            if v is not None:
                el.set(_tp(k), v)
        if self._fill:
            el.set("fill", self._fill)
        if self._thin:
            el.set("stroke-width", self._thin)
        _render(el, g, self.sheet.font_size)
        self.sheet._grow(_geometry_points(g))
        return el

    def linear(self, id: str, p1: Point, p2: Point, label: str, *, offset: float = 10,
               dir: str = "aligned", text_at: float = 0.5, type: str = "distance", refs=None, value=None,
               source: Optional[str] = None) -> ET.Element:
        """Linear dimension between p1 and p2; dir is "x", "y" or "aligned"."""
        g = linear_geometry(p1, p2, offset, label, self.sheet.font_size, dir, text_at)
        return self._annotation(id, {
            "type": type, "dim": "linear", "p1": _pt(p1), "p2": _pt(p2),
            "dir": None if dir == "aligned" else dir, "offset": fmt(offset),
            "text-at": None if text_at == 0.5 else fmt(text_at), "label": label,
            "refs": _refs(refs), "value": _value(value), "source": source}, g)

    def angular(self, id: str, center: Point, p1: Point, p2: Point, label: Optional[str] = None, *,
                offset: Optional[float] = None, text_at: float = 0.5, type: str = "angle", refs=None, value=None,
                source: Optional[str] = None) -> ET.Element:
        """Angular dimension at the vertex center, counter-clockwise from its leg through p1 to
        its leg through p2. offset is the arc's radius: by default, it runs through p1."""
        sweep = legs(center, p1, p2)[1] / DEG
        offset = _len(_sub(p1, center)) if offset is None else offset
        label = label or f"{fmt(sweep, 1)}°"
        g = angular_geometry(center, p1, p2, offset, label, self.sheet.font_size, text_at)
        return self._annotation(id, {
            "type": type, "dim": "angular", "center": _pt(center), "p1": _pt(p1), "p2": _pt(p2),
            "offset": fmt(offset), "text-at": None if text_at == 0.5 else fmt(text_at), "label": label,
            "refs": _refs(refs), "value": _value(sweep if value is None else value), "source": source}, g)

    def radius(self, id: str, center: Point, r: float, label: Optional[str] = None, *, angle: float = 45,
               offset: float = 5, type: str = "radius", refs=None, value=None,
               source: Optional[str] = None) -> ET.Element:
        return self._radial("radius", id, center, r, label or f"R{fmt(r)}", angle, offset, type, refs,
                            r if value is None else value, source)

    def diameter(self, id: str, center: Point, r: float, label: Optional[str] = None, *, angle: float = 45,
                 offset: float = 5, type: str = "diameter", refs=None, value=None,
                 source: Optional[str] = None) -> ET.Element:
        return self._radial("diameter", id, center, r, label or f"⌀{fmt(2 * r)}", angle, offset, type,
                            refs, 2 * r if value is None else value, source)

    def _radial(self, kind, id, center, r, label, angle, offset, type, refs, value, source) -> ET.Element:
        g = radial_geometry(center, r, angle, offset, label, self.sheet.font_size, kind == "diameter")
        return self._annotation(id, {
            "type": type, "dim": kind, "center": _pt(center), "r": fmt(r), "angle": fmt(angle),
            "offset": fmt(offset), "label": label, "refs": _refs(refs), "value": _value(value),
            "source": source}, g)

    def symbol(self, id: str, anchor: Point, label: str, *, offset: Point = (0, 0), type: Optional[str] = None,
               refs=None, value=None, source: Optional[str] = None) -> ET.Element:
        """Constraint symbol (e.g. "⊥") drawn at anchor + offset."""
        g = symbol_geometry(anchor, offset, label, self.sheet.font_size)
        return self._annotation(id, {
            "type": type, "anchor": _pt(anchor), "offset": _pt(offset) if any(offset) else None,
            "label": label, "refs": _refs(refs), "value": _value(value), "source": source}, g)


def _refs(refs) -> Optional[str]:
    if refs is None or isinstance(refs, str):
        return refs
    return " ".join(refs)


def _vec(v) -> Optional[str]:
    return None if v is None else " ".join(fmt(x) for x in v)


class Sheet:
    """A sheet: layers of geometry and annotations in model coordinates (mm, y up)."""

    def __init__(self, title: Optional[str] = None, *, source: Optional[str] = None,
                 pipeline: Optional[str] = None, rev: Optional[str] = None, regenerate: Optional[str] = None,
                 kind: Optional[str] = None, origin: Optional[Point] = None, normal: Optional[Point] = None,
                 xdir: Optional[Point] = None, font_size: float = 3.5, font_family: str = DEFAULT_FONT,
                 margin: float = 8, bounds: Optional[Sequence[float]] = None):
        self.view = {"title": title, "source": source, "pipeline": pipeline, "rev": rev,
                     "regenerate": regenerate, "kind": kind, "origin": _vec(origin), "normal": _vec(normal),
                     "xdir": _vec(xdir)}
        self.font_size = font_size
        self.font_family = font_family
        self.margin = margin
        self.bounds = bounds
        self.layers: list = []  # the top-level layers
        self.uses_hatch = False
        self._lo = [math.inf, math.inf]
        self._hi = [-math.inf, -math.inf]

    def layer(self, name: str, *, style: Union[str, dict, None] = None, visible: bool = True, **attrs) -> Layer:
        """Add a layer. style is a key of STYLES or a dict of presentation attributes."""
        layer = Layer(self, name, style or LAYER_STYLES.get(name, "geometry"), visible, attrs)
        self.layers.append(layer)
        return layer

    def _grow(self, points: Iterable[Point]) -> None:
        for x, y in points:
            self._lo = [min(self._lo[0], x), min(self._lo[1], y)]
            self._hi = [max(self._hi[0], x), max(self._hi[1], y)]

    def _box(self):
        if self.bounds:
            return tuple(self.bounds)
        if self._lo[0] > self._hi[0]:
            return (0, 0, 100, 100)
        m = self.margin
        return (math.floor(self._lo[0] - m), math.floor(self._lo[1] - m),
                math.ceil(self._hi[0] + m), math.ceil(self._hi[1] + m))

    def to_element(self) -> ET.Element:
        x0, y0, x1, y1 = self._box()
        w, h = x1 - x0, y1 - y0
        root = ET.Element(_svg("svg"), {
            "width": f"{fmt(w)}mm", "height": f"{fmt(h)}mm",
            "viewBox": f"{fmt(x0)} {fmt(-y1)} {fmt(w)} {fmt(h)}",
            "font-family": self.font_family, "font-size": fmt(self.font_size)})
        meta = ET.SubElement(root, _svg("metadata"))
        ET.SubElement(meta, _tp("view"), {k: v for k, v in self.view.items() if v is not None})
        if self.uses_hatch:
            defs = ET.SubElement(root, _svg("defs"))
            pattern = ET.SubElement(defs, _svg("pattern"), {
                "id": HATCH_ID, "patternUnits": "userSpaceOnUse", "width": "2", "height": "2",
                "patternTransform": "rotate(45)"})
            ET.SubElement(pattern, _svg("path"), {"d": "M1 0 V2", "stroke": "#1a1a1a", "stroke-width": "0.75",
                                                  **SCREEN_WIDTH})  # 2 mm apart, but thin at any zoom
        for layer in self.layers:
            root.append(layer.element)
        return root

    def tostring(self) -> str:
        root = self.to_element()
        ET.indent(root, space="  ")
        return '<?xml version="1.0" encoding="UTF-8"?>\n' + ET.tostring(root, encoding="unicode") + "\n"

    def save(self, path: Union[str, Path]) -> Path:
        """Write the sheet to path, making its folder if need be."""
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(self.tostring(), encoding="utf-8")
        tmp.replace(path)  # the viewer may be reading the old file
        return path
