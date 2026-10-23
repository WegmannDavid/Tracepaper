"""Sheets drawn from scenes (SPEC.md §2.5): exact drawings in the viewer's own look.

A scene holds geometry only; how it looks is decided here, the same for every scene:

- Each part gets a colour of PALETTE, in the order of the tree, and the 3D view uses
  the same ones.
- A section draws what the plane cuts from each solid as regions, tinted in the part's
  colour and hatched, at an angle and spacing of its own so that parts side by side
  stay apart in greys, and outlined. Surfaces cut become lines in the part's colour.
- Fields go under the parts, which are then only outlined, so that the outlines, curves
  and points show over them.
- A 2D scene draws its regions tinted and outlined.
- A projection draws each part's visible edges, its smooth ones (where faces meet
  tangentially) lighter, and its hidden edges dashed, on a layer of their own,
  "Hidden lines", hidden at first.
- Curves are reference geometry: thin and dashed, in the part's colour. Points are too: dots
  a few pixels across at any zoom, in the part's colour, named in a tooltip.
- Annotations go on the Dimensions and Constraints layers, and the scene's and the view's
  notes in a block under the drawing, on the Notes layer.

The tree of the sheet is the scene's: a layer for each top node, a sublayer for each
node below, leaving out parts the view doesn't show. Text and arrowheads are sized to
the drawing, which can be a few millimetres across or a few metres.
"""
from __future__ import annotations

import math
import re
import xml.etree.ElementTree as ET
from typing import Optional

from . import fields as F
from . import scene as S
from .sheet import SCREEN_WIDTH, TP_NS, Sheet, fmt
from . import views as V
from .views import view_spec

PALETTE = ["#7aa6d6", "#e0a060", "#8cc07a", "#d98a8a", "#ab90d4", "#d4bd5c", "#6fbfb9", "#c995b5",
           "#9aa5b1", "#b8a07e"]
INK = "#1a1a1a"
PART_STYLE = {"stroke": INK, "stroke-width": "1.5", "fill": "none", "stroke-linecap": "round",
              "stroke-linejoin": "round"}
SMOOTH = {"stroke": "#7b8490", "stroke-width": "1"}
CURVE = {"stroke": "#475569", "stroke-width": "1", "stroke-dasharray": "6 4", "fill": "none"}
HIDDEN = {"stroke": "#6b7280", "stroke-width": "1", "stroke-dasharray": "5 3", "fill": "none"}
DOT_PX = 7  # a point's dot, across, on screen
TINT = 0.55  # of white mixed into a part's colour for its regions
# Fields' colour map: viridis, which gets lighter from start to end, so it reads in greys too.
VIRIDIS = ["#440154", "#472d7b", "#3b528b", "#2c728e", "#21918c", "#28ae80", "#5ec962", "#addc30", "#fde725"]


def field_color(t: float) -> str:
    """The colour map's colour at t, from 0 to 1."""
    t = min(1.0, max(0.0, t)) * (len(VIRIDIS) - 1)
    i = min(int(t), len(VIRIDIS) - 2)
    a, b = rgb(VIRIDIS[i]), rgb(VIRIDIS[i + 1])
    return "#" + "".join(f"{round(255 * (x + (t - i) * (y - x))):02x}" for x, y in zip(a, b))


def colors(scene: S.Scene) -> dict:
    """Each part's colour, by key: PALETTE in the order of the tree."""
    return {p.key: PALETTE[i % len(PALETTE)] for i, p in enumerate(scene.parts())}


def rgb(color: str) -> tuple:
    """"#rrggbb" as (r, g, b) from 0 to 1, for the 3D view's materials."""
    return tuple(int(color[i:i + 2], 16) / 255 for i in (1, 3, 5))


def _tint(color: str, white: float = TINT) -> str:
    return "#" + "".join(f"{round(c * 255 * (1 - white) + 255 * white):02x}" for c in rgb(color))


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-") or "part"


def font_size(size: float) -> float:
    """Text height for a drawing size mm across: 3.5 mm, or more for big drawings, so that
    labels stay readable when the whole drawing is in view."""
    return max(3.5, round(size / 60 * 2) / 2)


def draw(scene: S.Scene, spec: Optional[dict], meta: dict, name: str, hidden=(), fields=(), ranges=None) -> Sheet:
    """The sheet of a view of a scene: spec as views.view_spec gives it, or None for a 2D
    scene's own plane. meta is the scene's .tp.json, name the view's name, hidden the keys
    of the nodes that start hidden, fields as views.fields gives them and ranges each
    field's (lowest, highest) value, for its colours."""
    if spec is None and scene.flat() is None:  # faces, but not in one plane: seen from above
        spec = {**view_spec({}, "top"), "annotations": meta.get("annotations") or []}
    if spec is None:
        pl = scene.flat()
        kind, annotations, title = "2D", meta.get("annotations") or [], None
        notes = V.notes(meta.get("notes"))
    else:
        pl = S.plane(spec["origin"], spec["normal"], spec["xdir"])
        kind, annotations, title = spec["kind"], spec.get("annotations") or [], spec["title"]
        notes = V.notes(meta.get("notes")) + spec.get("notes", [])
    if spec is None:
        items = S.flat(scene, pl)
    elif kind == "section":
        items = S.cut(scene, pl)
    else:
        items = S.project(scene, pl)
    shapes = [s for it in items for s in _shapes(it)]
    box = S.bounds(shapes)
    size = max(box[2] - box[0], box[3] - box[1]) if box else 100
    fs = font_size(size)
    scene_title = meta.get("title") or name.partition("#")[0].rsplit("/", 1)[-1]
    sheet = Sheet(f"{scene_title}, {title}" if title else scene_title, source=meta.get("source"),
                  pipeline=meta.get("pipeline"), regenerate=meta.get("regenerate"),
                  kind=kind, origin=pl.origin, normal=pl.normal, xdir=pl.xdir, font_size=fs, margin=3 * fs)
    sheet.view["scene"] = name.partition("#")[0].rsplit("/", 1)[-1]
    if spec is not None:
        sheet.view["view"] = spec["name"]
    if box:
        sheet._grow([(box[0], box[1]), (box[2], box[3])])

    # Fields on this view, worked out first: where there are any, they go under the parts, whose
    # regions are then only outlined, so that the outlines, curves and points show over them.
    shown_fields = [(f, F.triangles_on(F.read_vtu(f["path"]), pl, f["value"], f.get("displacement"))) for f in fields]
    shown_fields = [(f, tris) for f, tris in shown_fields if tris]
    palette = colors(scene)
    hatch = fs * 0.6
    order = {p.key: i for i, p in enumerate(scene.parts())}
    drawn = {it.node.key: it for it in items}
    hidden_root = {}

    def shown(node) -> bool:
        return node.key in drawn or any(shown(c) for c in node.children)

    def hidden_layer(node):  # the node's counterpart under Hidden lines
        if node.key in hidden_root:
            return hidden_root[node.key]
        above, _, _ = node.key.rpartition(S.SEP)
        if not above:
            if "" not in hidden_root:
                hidden_root[""] = _role(sheet.layer("Hidden lines", style=HIDDEN, visible=False), "hidden-lines")
            layer = hidden_root[""].sublayer(node.name, visible=node.key not in hidden)
        else:
            parent = next(n for n in scene.nodes() if n.key == above)
            layer = hidden_layer(parent).sublayer(node.name, visible=node.key not in hidden)
        hidden_root[node.key] = layer
        return layer

    def part(layer, it):
        key, slug = it.node.key, _slug(it.node.key)
        color = palette[key]
        k = order[key]
        if isinstance(it, (S.Cut, S.Flat)):
            fill = sheet.hatch(45 if k % 2 == 0 else -45, hatch * (1 if k // 2 % 2 == 0 else 1.6))
            for i, face in enumerate(it.regions, 1):
                d = S.region_d(face)
                if shown_fields:  # the field shows what's inside
                    layer.path(f"{slug}-{'cut-' if isinstance(it, S.Cut) else ''}{i}", d, source=key)
                elif isinstance(it, S.Cut):
                    layer.path(f"{slug}-tint-{i}", d, stroke="none", fill=_tint(color), fill_rule="evenodd")
                    layer.path(f"{slug}-cut-{i}", d, fill=fill, fill_rule="evenodd", source=key)
                else:
                    layer.path(f"{slug}-{i}", d, fill=_tint(color), fill_rule="evenodd", source=key)
            if it.curves:  # a surface cut, in the part's colour; a 2D scene's curves, dashed too
                style = {"stroke": color} if isinstance(it, S.Cut) else {**CURVE, "stroke": color}
                layer.path(f"{slug}-curves", S.curves_d(it.curves), source=key, **_kw(style))
        elif not S.has_faces(it.node.shape):  # a projection of curves: reference geometry
            if it.visible or it.hidden:
                layer.path(f"{slug}-curves", S.curves_d(it.visible + it.hidden), source=key, **_kw({**CURVE, "stroke": color}))
        else:  # a projection
            if it.visible:
                layer.path(f"{slug}-edges", S.curves_d(it.visible), source=key)
            if it.smooth:
                layer.path(f"{slug}-smooth", S.curves_d(it.smooth), source=key, **_kw(SMOOTH))
            if it.hidden:
                hidden_layer(it.node).path(f"{slug}-hidden", S.curves_d(it.hidden), source=key)
        if it.points:  # dots: a line of no length, drawn with round ends
            dots = layer.path(f"{slug}-points", " ".join(f"M{fmt(x)} {fmt(y)} L{fmt(x)} {fmt(y)}" for x, y in it.points),
                              source=key, stroke=color, stroke_width=str(DOT_PX), stroke_linecap="round", fill="none")
            ET.SubElement(dots, "{http://www.w3.org/2000/svg}title").text = it.node.name
            sheet._grow(it.points)

    def add(node, parent):
        if not shown(node):
            return
        visible = node.key not in hidden
        layer = (sheet.layer(node.name, style=PART_STYLE, visible=visible) if parent is None
                 else parent.sublayer(node.name, visible=visible))
        if node.key in drawn:
            part(layer, drawn[node.key])
        for child in node.children:
            add(child, layer)

    for root in scene.roots:
        add(root, None)
    if hidden_root.get(""):  # drawn last would cover the parts: keep it under them
        sheet.layers.remove(hidden_root[""])
        sheet.layers.insert(0, hidden_root[""])
    _fields(sheet, shown_fields, ranges or {})
    if not items:
        notes = ["The plane misses the scene."] + notes
    _annotate(sheet, annotations)
    _notes(sheet, notes)
    return sheet


def _notes(sheet: Sheet, lines: list) -> None:
    """Notes as a block of text under the drawing, its lines as given."""
    if not lines:
        return
    layer = _role(sheet.layer("Notes"), "annotations")
    fs = sheet.font_size
    x0, y0 = (sheet._lo if sheet._lo[0] <= sheet._hi[0] else (0, 0))
    for i, line in enumerate(lines, 1):
        if line.strip():
            layer.text(f"note-{i}", (x0, y0 - 1.5 * fs - 1.4 * fs * i), line)


def _fields(sheet: Sheet, fields, ranges: dict) -> None:
    """Each field on a layer of its own under Fields, under the parts: fields as [(field, its
    triangles in the sheet's plane)], each triangle coloured by the mean of its corners' values
    and carrying them (tp:v), to be recoloured, cut at a level or traced along isolines by the
    page, and where the field moves the mesh, each corner's displacement (tp:u) and place
    before it (tp:p), drawn moved by it."""
    if not fields:
        return
    layer = _role(sheet.layer("Fields", style={"stroke": "none", "fill": "none"}), "fields")
    sheet.layers.remove(layer)  # under the parts, over the hidden lines
    under = 1 if sheet.layers and sheet.layers[0].element.get(f"{{{TP_NS}}}role") == "hidden-lines" else 0
    sheet.layers.insert(under, layer)
    for f, tris in fields:
        values = [v for _, vals, _ in tris for v in vals]
        lo, hi = ranges.get(f["name"]) or ((min(values), max(values)) if values else (0, 1))
        sub = layer.sublayer(f["name"])
        for k, v in (("field", f["name"]), ("unit", f.get("unit") or None), ("range", f"{fmt(lo)} {fmt(hi)}"),
                     ("min", None if f.get("min") is None else fmt(float(f["min"]))),
                     ("max", None if f.get("max") is None else fmt(float(f["max"])))):
            if v is not None:
                sub.element.set(f"{{{TP_NS}}}{k}", v)
        span = (hi - lo) or 1
        for pts, vals, disp in tris:
            moved = [(x + d[0], y + d[1]) for (x, y), d in zip(pts, disp)] if disp else list(pts)
            color = field_color((sum(vals) / 3 - lo) / span)
            attrs = {"d": "M" + " L".join(f"{fmt(x)} {fmt(y)}" for x, y in moved) + " Z", "fill": color,
                     "stroke": color, "stroke-width": "0.6", **SCREEN_WIDTH,  # no seams between triangles
                     f"{{{TP_NS}}}v": " ".join(fmt(v) for v in vals)}
            if disp:
                attrs[f"{{{TP_NS}}}u"] = " ".join(f"{fmt(d[0])} {fmt(d[1])}" for d in disp)
                attrs[f"{{{TP_NS}}}p"] = " ".join(f"{fmt(x)} {fmt(y)}" for x, y in pts)
            ET.SubElement(sub.element, f"{{http://www.w3.org/2000/svg}}path", attrs)
            sheet._grow(moved)


def _role(layer, role: str):
    """Mark a layer that holds no parts (tp:role), which a page's trail of frames leaves out."""
    layer.element.set(f"{{{TP_NS}}}role", role)
    return layer


def _kw(style: dict) -> dict:
    return {k.replace("-", "_"): v for k, v in style.items()}


def _shapes(it) -> list:
    if isinstance(it, S.Projection):
        return it.visible + it.smooth + it.hidden
    return it.regions + it.curves


def _pt(p, what: str) -> tuple:
    if not (isinstance(p, (list, tuple)) and len(p) == 2 and all(isinstance(c, (int, float)) for c in p)):
        raise ValueError(f"{what} must be two numbers, [x, y]")
    return float(p[0]), float(p[1])


def _annotate(sheet: Sheet, annotations: list) -> None:
    """The annotations of a view, as SPEC.md §2.6 has them, with points as [x, y]."""
    dims = constraints = None
    for i, a in enumerate(annotations):
        if not isinstance(a, dict) or not isinstance(a.get("id"), str):
            raise ValueError(f"annotations[{i}]: needs an id")
        aid = a["id"]
        common = dict(refs=a.get("refs"), value=a.get("value"), source=a.get("source"))
        kind = a.get("dim")
        if kind is None and "anchor" in a:
            constraints = constraints or _role(sheet.layer("Constraints"), "annotations")
            off = a.get("offset", [0, 0])
            constraints.symbol(aid, _pt(a["anchor"], f"{aid}: anchor"), str(a.get("label", "")),
                               offset=_pt(off, f"{aid}: offset"), type=a.get("type"), **common)
            continue
        dims = dims or _role(sheet.layer("Dimensions"), "annotations")
        if kind == "linear":
            p1, p2 = _pt(a.get("p1"), f"{aid}: p1"), _pt(a.get("p2"), f"{aid}: p2")
            d = a.get("dir", "aligned")
            length = abs(p2[0] - p1[0]) if d == "x" else abs(p2[1] - p1[1]) if d == "y" else math.dist(p1, p2)
            dims.linear(aid, p1, p2, str(a.get("label", fmt(length, 2))), offset=float(a.get("offset", 0)), dir=d,
                        text_at=float(a.get("text-at", 0.5)), type=a.get("type", "distance"), **common)
        elif kind == "angular":
            dims.angular(aid, _pt(a.get("center"), f"{aid}: center"), _pt(a.get("p1"), f"{aid}: p1"),
                         _pt(a.get("p2"), f"{aid}: p2"), a.get("label"), offset=a.get("offset"),
                         text_at=float(a.get("text-at", 0.5)), type=a.get("type", "angle"), **common)
        elif kind in ("radius", "diameter"):
            method = dims.radius if kind == "radius" else dims.diameter
            method(aid, _pt(a.get("center"), f"{aid}: center"), float(a["r"]), a.get("label"),
                   angle=float(a.get("angle", 45)), offset=float(a.get("offset", 0)), type=a.get("type", kind),
                   **common)
        else:
            raise ValueError(f"{aid}: dim must be linear, angular, radius or diameter, or give an anchor")


def svg(sheet: Sheet) -> bytes:
    return sheet.tostring().encode("utf-8")


__all__ = ["PALETTE", "colors", "draw", "rgb", "svg", "SCREEN_WIDTH"]
