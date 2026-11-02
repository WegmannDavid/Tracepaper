"""Plots (SPEC.md §2.12): curves and values over axes, as a solver or a calculation gives
them, drawn as a sheet like a scene's drawings, so the person looks at them, hides series
and sketches on them the same way. Standard library only.

A plot is a <name>.plot.json file: its title, notes and where it comes from as a scene's
.tp.json has them, and one panel (series, marks at the top level) or several (panels),
over axes that the panels share unless one gives its own. A series says what it is, not
how it looks: a curve, values or points; the detail of another series; one of an ordered
family; with its spread. The viewer picks the colours, dashes and markers, so that series
stay apart in greys too.

The sheet keeps what the page and `tracepaper inspect` need to read values back: each
panel's axes in its tp:view (tp:panel), and each series' numbers on its group.
"""
from __future__ import annotations

import json
import math
import re
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Optional

from .sheet import SVG_NS, TP_NS, Sheet, fmt, notes as write_notes, text_width

PLOT_SUFFIX = ".plot.json"
KINDS = ("curve", "values", "points")
SCALES = ("linear", "log")
W, H = 160, 100  # mm: a panel's plotting area
FS = 3.5  # mm: the text height, ISO's, a 60th of a panel or so as on drawings
INK = "#1a1a1a"
MUTED = "#6b7280"
GRID = "#e5e7eb"
MINOR = "#f3f4f6"
ZERO = "#9ca3af"
OVER = "#b91c1c"  # outside the limits, as on drawings
# Okabe and Ito's colours, apart for colour-blind eyes too, red left to what is outside its limits.
COLORS = ["#0072b2", "#e69f00", "#009e73", "#cc79a7", "#56b4e9", "#8c6d31", "#d55e00", "#4b5563"]
# On a screen without colour, the series stay apart by their dashes (pixels) and markers.
DASHES = [None, "8 3", "2 3", "10 3 2 3", "16 4", "3 2 3 6"]
MARKERS = ["circle", "square", "triangle", "diamond", "down", "cross"]
MARK_DASH = "10 3 2 3"
LIMIT_DASH = "6 4"
TP, SVG = f"{{{TP_NS}}}", f"{{{SVG_NS}}}"


def is_plot(path: Path) -> bool:
    return Path(path).name.lower().endswith(PLOT_SUFFIX)


def read_plot(path: Path) -> dict:
    """The plot in the file at path, checked (check). Raises ValueError with what is wrong."""
    path = Path(path)
    try:
        plot = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as err:
        raise ValueError(f"{path.name}: {err}") from None
    try:
        return check(plot)
    except ValueError as err:
        raise ValueError(f"{path.name}: {err}") from None


# ---- Checking -----------------------------------------------------------------------------

def _number(v, where: str, none: bool = False):
    if v is None and none:
        return None
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
        raise ValueError(f"{where}: {v!r} is not a number" + (" (null for none)" if none else ""))
    return float(v)


def _text(v, where: str) -> Optional[str]:
    if v is None:
        return None
    if not isinstance(v, str):
        raise ValueError(f"{where}: {v!r} is not text")
    return v


def _lines(v, where: str) -> list:
    if v is None:
        return []
    if isinstance(v, str):
        return v.split("\n")
    if isinstance(v, list) and all(isinstance(s, str) for s in v):
        return list(v)
    raise ValueError(f"{where}: text, or a list of lines")


def _axis(a, where: str) -> dict:
    if a is None:
        a = {}
    if not isinstance(a, dict):
        raise ValueError(f"{where}: an object with label, unit, scale, range, ticks, min, max")
    scale = a.get("scale", "linear")
    if scale not in SCALES:
        raise ValueError(f"{where}.scale: {scale!r}, not linear or log")
    lo, hi = None, None
    if a.get("range") is not None:
        r = a["range"]
        if not (isinstance(r, list) and len(r) == 2):
            raise ValueError(f"{where}.range: [lowest, highest], either null for the data's")
        lo, hi = (_number(v, f"{where}.range", none=True) for v in r)
        if lo is not None and hi is not None and not lo < hi:
            raise ValueError(f"{where}.range: {lo:g} is not below {hi:g}")
        if scale == "log" and any(v is not None and v <= 0 for v in (lo, hi)):
            raise ValueError(f"{where}.range: a log axis's range must be above 0")
    ticks = None
    if a.get("ticks") is not None:
        if not isinstance(a["ticks"], list):
            raise ValueError(f"{where}.ticks: a list of values, or of [value, label]")
        ticks = []
        for t in a["ticks"]:
            if isinstance(t, list) and len(t) == 2:
                ticks.append((_number(t[0], f"{where}.ticks"), str(_text(t[1], f"{where}.ticks"))))
            else:
                v = _number(t, f"{where}.ticks")
                ticks.append((v, num(v)))
        if scale == "log" and any(v <= 0 for v, _ in ticks):
            raise ValueError(f"{where}.ticks: a log axis's ticks must be above 0")
    return {"label": _text(a.get("label"), f"{where}.label"), "unit": _text(a.get("unit"), f"{where}.unit"),
            "scale": scale, "lo": lo, "hi": hi, "ticks": ticks,
            "min": _number(a.get("min"), f"{where}.min", none=True),
            "max": _number(a.get("max"), f"{where}.max", none=True)}


def _series(s, where: str) -> dict:
    if not isinstance(s, dict):
        raise ValueError(f"{where}: an object with name, x and y")
    name = _text(s.get("name"), f"{where}.name")
    if not name:
        raise ValueError(f"{where}: no name")
    where = f"{where} ({name})"
    xs, ys = s.get("x"), s.get("y")
    if not isinstance(xs, list) or not isinstance(ys, list):
        raise ValueError(f"{where}: x and y are lists of numbers")
    if len(xs) != len(ys):
        raise ValueError(f"{where}: x has {len(xs)} values and y {len(ys)}")
    out = {"name": name, "x": [_number(v, f"{where}.x") for v in xs],
           "y": [_number(v, f"{where}.y", none=True) for v in ys]}
    kind = s.get("kind", "curve")
    if kind not in KINDS:
        raise ValueError(f"{where}.kind: {kind!r}, not one of {', '.join(KINDS)}")
    out["kind"] = kind
    for key in ("low", "high"):
        v = s.get(key)
        if v is not None:
            if not isinstance(v, list) or len(v) != len(xs):
                raise ValueError(f"{where}.{key}: a list as long as x, null where there is none")
            v = [_number(c, f"{where}.{key}", none=True) for c in v]
        out[key] = v
    out["detail_of"] = _text(s.get("detail-of"), f"{where}.detail-of")
    out["family"] = _text(s.get("family"), f"{where}.family")
    out["summary"] = _text(s.get("summary"), f"{where}.summary")
    if out["detail_of"] and out["family"]:
        raise ValueError(f"{where}: a detail goes with its series, which may be in a family, not in one itself")
    return out


def _mark(m, where: str, names: set) -> dict:
    if not isinstance(m, dict) or ("x" in m) == ("y" in m):
        raise ValueError(f"{where}: an object with x (a line across) or y (a line along), and a label")
    axis = "x" if "x" in m else "y"
    of = _text(m.get("of"), f"{where}.of")
    if of is not None and of not in names:
        raise ValueError(f"{where}.of: no series {of!r} in this panel")
    return {"axis": axis, "at": _number(m[axis], f"{where}.{axis}"), "label": _text(m.get("label"), f"{where}.label"),
            "of": of}


def _panel(p: dict, at: str, x: dict, y: dict) -> dict:
    """A panel; at is where it is in the file, "panels[1]." or "" for the top level's."""
    where = at.rstrip(".") or "plot"
    series = p.get("series")
    if not isinstance(series, list) or not series:
        raise ValueError(f"{at}series: a list of series, at least one")
    out = [_series(s, f"{at}series[{i}]") for i, s in enumerate(series)]
    names = [s["name"] for s in out]
    for n in names:
        if names.count(n) > 1:
            raise ValueError(f"{where}: two series named {n!r}")
    families = {s["family"] for s in out if s["family"]}
    for f in families & set(names):
        raise ValueError(f"{where}: {f!r} names a series and a family")
    for s in out:
        d = s["detail_of"]
        if d is not None:
            main = next((m for m in out if m["name"] == d), None)
            if main is None:
                raise ValueError(f"{where}: {s['name']!r} is the detail of {d!r}, which isn't in this panel")
            if main["detail_of"]:
                raise ValueError(f"{where}: {s['name']!r} is the detail of {d!r}, itself a detail")
    marks = p.get("marks") or []
    if not isinstance(marks, list):
        raise ValueError(f"{at}marks: a list")
    own = {k: k in p for k in ("x", "y")}
    return {"title": _text(p.get("title"), f"{at}title"), "series": out,
            "marks": [_mark(m, f"{at}marks[{i}]", set(names)) for i, m in enumerate(marks)],
            "x": _axis(p["x"], f"{at}x") if own["x"] else x,
            "y": _axis(p["y"], f"{at}y") if own["y"] else y, "own": own}


def check(plot) -> dict:
    """A plot as read from its file (SPEC.md §2.12), checked and with its defaults filled in:
    {title, source, pipeline, rev, regenerate, notes, hidden, panels: [{title, x, y, series,
    marks, own}]}. Raises ValueError saying what is wrong and where."""
    if not isinstance(plot, dict):
        raise ValueError("not a JSON object")
    x, y = _axis(plot.get("x"), "x"), _axis(plot.get("y"), "y")
    if "panels" in plot:
        if "series" in plot or "marks" in plot:
            raise ValueError("series and marks go in panels, or at the top level for one panel, not both")
        panels = plot["panels"]
        if not isinstance(panels, list) or not panels:
            raise ValueError("panels: a list of panels, at least one")
        for i, p in enumerate(panels):
            if not isinstance(p, dict):
                raise ValueError(f"panels[{i}]: an object with series")
        panels = [_panel(p, f"panels[{i}].", x, y) for i, p in enumerate(panels)]
    else:
        panels = [_panel({"series": plot.get("series"), "marks": plot.get("marks")}, "", x, y)]
    hidden = plot.get("hidden") or []
    if not (isinstance(hidden, list) and all(isinstance(h, str) for h in hidden)):
        raise ValueError("hidden: a list of names or paths")
    out = {k: _text(plot.get(k), k) for k in ("title", "source", "pipeline", "rev", "regenerate")}
    return {**out, "notes": _lines(plot.get("notes"), "notes"), "hidden": hidden, "panels": panels}


# ---- Axes ---------------------------------------------------------------------------------

def num(v: float, digits: int = 4) -> str:
    """A value as tick labels and readouts show it: up to 4 significant digits, a real minus."""
    if v == 0:
        return "0"
    s = f"{v:.{digits}g}"
    if "e" in s:
        m, e = s.split("e")
        s = f"{m}·10^{int(e)}"
    return s.replace("-", "−")


def _nice_step(span: float, n: int = 5) -> float:
    raw = span / n
    k = 10 ** math.floor(math.log10(raw))
    return next(m * k for m in (1, 2, 2.5, 5, 10) if m * k >= raw * (1 - 1e-9))


class Axis:
    """An axis as drawn: its data range lo..hi, linear or log, onto a..b mm of the sheet."""

    def __init__(self, spec: dict, lo: float, hi: float, a: float, b: float):
        self.spec, self.lo, self.hi, self.a, self.b = spec, lo, hi, a, b
        self.log = spec["scale"] == "log"

    def __call__(self, v: float) -> float:
        if self.log:
            t = (math.log10(v) - math.log10(self.lo)) / (math.log10(self.hi) - math.log10(self.lo))
        else:
            t = (v - self.lo) / (self.hi - self.lo)
        return self.a + t * (self.b - self.a)

    def drawable(self, v) -> bool:
        return v is not None and (v > 0 or not self.log)

    def ticks(self) -> list:
        """[(value, label or None)]: labelled ticks, and a log axis's unlabelled ones between."""
        lo, hi = self.lo, self.hi
        tol = 1e-9 * (hi - lo)
        within = lambda v: lo - tol <= v <= hi + tol
        if self.spec["ticks"] is not None:
            return [(v, label) for v, label in self.spec["ticks"] if within(v)]
        if not self.log:
            step = _nice_step(hi - lo)
            first = math.ceil(lo / step - 1e-9)
            return [(round(k * step, 12), num(round(k * step, 12))) for k in range(first, math.floor(hi / step + 1e-9) + 1)]
        decades = range(math.floor(math.log10(lo)), math.ceil(math.log10(hi)) + 1)
        for labelled in ((1,), (1, 2, 5), tuple(range(1, 10))):
            major = [m * 10 ** d for d in decades for m in labelled if within(m * 10 ** d)]
            if len(major) >= 3:
                break
        minor = [m * 10 ** d for d in decades for m in range(2, 10) if within(m * 10 ** d) and m * 10 ** d not in major]
        return [(v, num(v)) for v in major] + [(v, None) for v in minor]

    def text(self, v: float, digits: int = 4) -> str:
        unit = self.spec["unit"]
        return f"{num(v, digits)} {unit}" if unit else num(v, digits)


def _extent(panels: list, axis: str, shown=lambda s: True) -> tuple:
    """The lowest and highest of what the panels draw along an axis: the series shown (all
    of a panel's, if none of them is), with their spreads and marks, and the loose marks
    and limits; on a log axis what is above 0."""
    spec = panels[0][axis]
    vals = []
    for p in panels:
        series = [s for s in p["series"] if shown(s)] or p["series"]
        names = {s["name"] for s in series}
        for s in series:
            vals += [v for v in s[axis] if v is not None]
            if axis == "y":
                vals += [v for key in ("low", "high") for v in (s[key] or []) if v is not None]
        vals += [m["at"] for m in p["marks"] if m["axis"] == axis and (m["of"] is None or m["of"] in names)]
    vals += [v for v in (spec["min"], spec["max"]) if v is not None]
    if spec["scale"] == "log":
        vals = [v for v in vals if v > 0]
    return (min(vals), max(vals)) if vals else (None, None)


def _range(spec: dict, lo: Optional[float], hi: Optional[float], axis: str) -> tuple:
    """An axis's range: as given, else the data's; along y with room to spare, linear ones
    out to their ticks."""
    log = spec["scale"] == "log"
    lo = spec["lo"] if spec["lo"] is not None else lo
    hi = spec["hi"] if spec["hi"] is not None else hi
    if lo is None or hi is None:
        lo, hi = (1.0, 10.0) if log else (0.0, 1.0)
    if not lo < hi:
        lo, hi = (lo / 2, hi * 2) if log else (lo - (abs(lo) or 1), hi + (abs(hi) or 1))
    if axis == "y" and log:
        pad = 0.05 * (math.log10(hi) - math.log10(lo))
        lo = lo if spec["lo"] is not None else 10 ** (math.log10(lo) - pad)
        hi = hi if spec["hi"] is not None else 10 ** (math.log10(hi) + pad)
    elif axis == "y":
        for _ in range(3):  # out to the ticks of the range it comes to
            step = _nice_step(hi - lo)
            lo = lo if spec["lo"] is not None else math.floor(lo / step + 1e-9) * step
            hi = hi if spec["hi"] is not None else math.ceil(hi / step - 1e-9) * step
    return lo, hi


def _spaced(ticks: list, at, size) -> list:
    """The ticks with labels that fit: every one, or every second, third ... from the first,
    the others' labels left out. at(value) is a tick's place in mm, size(label) how much room
    its label takes along the axis."""
    labelled = [k for k, (_, label) in enumerate(ticks) if label]
    for every in range(1, len(labelled) + 1):
        kept = labelled[::every]
        if all(abs(at(ticks[b][0]) - at(ticks[a][0])) >= (size(ticks[a][1]) + size(ticks[b][1])) / 2 + 0.5 * FS
               for a, b in zip(kept, kept[1:])):
            break
    keep = set(kept)
    return [(v, label if k in keep else None, bool(label)) for k, (v, label) in enumerate(ticks)]


# ---- Drawing ------------------------------------------------------------------------------

def _clip_segment(p, q, box):
    """The part of the segment p-q inside box (x0, y0, x1, y1), or None (Liang and Barsky)."""
    (x0, y0, x1, y1), (px, py), (qx, qy) = box, p, q
    dx, dy, t0, t1 = qx - px, qy - py, 0.0, 1.0
    for d, e in ((-dx, px - x0), (dx, x1 - px), (-dy, py - y0), (dy, y1 - py)):
        if d == 0:
            if e < 0:
                return None
        else:
            t = e / d
            if d < 0:
                t0 = max(t0, t)
            else:
                t1 = min(t1, t)
    return ((px + t0 * dx, py + t0 * dy), (px + t1 * dx, py + t1 * dy)) if t0 <= t1 else None


def _clipped_runs(runs: list, box) -> list:
    """Polylines cut to box: the runs of them inside it."""
    out = []
    for run in runs:
        current = []
        for p, q in zip(run, run[1:]):
            seg = _clip_segment(p, q, box)
            if seg is None:
                if len(current) > 1:
                    out.append(current)
                current = []
                continue
            if current and math.dist(current[-1], seg[0]) < 1e-9:
                current.append(seg[1])
            else:
                if len(current) > 1:
                    out.append(current)
                current = [seg[0], seg[1]]
        if len(current) > 1:
            out.append(current)
        elif len(run) == 1 and _inside(run[0], box):
            out.append(run)
    return out


def _clip_polygon(points: list, box) -> list:
    """A polygon cut to box (Sutherland and Hodgman)."""
    x0, y0, x1, y1 = box
    for inside, cut in (
            (lambda p: p[0] >= x0, lambda p, q: (x0, p[1] + (q[1] - p[1]) * (x0 - p[0]) / (q[0] - p[0]))),
            (lambda p: p[0] <= x1, lambda p, q: (x1, p[1] + (q[1] - p[1]) * (x1 - p[0]) / (q[0] - p[0]))),
            (lambda p: p[1] >= y0, lambda p, q: (p[0] + (q[0] - p[0]) * (y0 - p[1]) / (q[1] - p[1]), y0)),
            (lambda p: p[1] <= y1, lambda p, q: (p[0] + (q[0] - p[0]) * (y1 - p[1]) / (q[1] - p[1]), y1))):
        if not points:
            break
        out = []
        for i, q in enumerate(points):
            p = points[i - 1]
            if inside(q):
                if not inside(p):
                    out.append(cut(p, q))
                out.append(q)
            elif inside(p):
                out.append(cut(p, q))
        points = out
    return points


def _inside(p, box) -> bool:
    return box[0] - 1e-9 <= p[0] <= box[2] + 1e-9 and box[1] - 1e-9 <= p[1] <= box[3] + 1e-9


def _d(runs: list) -> str:
    return " ".join("M" + " L".join(f"{fmt(x)} {fmt(y)}" for x, y in run) for run in runs)


def _marker(shape: str, c, r: float) -> str:
    x, y = c
    if shape == "circle":
        return f"M{fmt(x - r)} {fmt(y)} A{fmt(r)} {fmt(r)} 0 1 0 {fmt(x + r)} {fmt(y)} A{fmt(r)} {fmt(r)} 0 1 0 {fmt(x - r)} {fmt(y)} Z"
    if shape == "square":
        s = 0.85 * r
        return f"M{fmt(x - s)} {fmt(y - s)} H{fmt(x + s)} V{fmt(y + s)} H{fmt(x - s)} Z"
    if shape in ("triangle", "down"):
        k = 1 if shape == "triangle" else -1
        return f"M{fmt(x)} {fmt(y + k * 1.15 * r)} L{fmt(x + r)} {fmt(y - k * 0.6 * r)} L{fmt(x - r)} {fmt(y - k * 0.6 * r)} Z"
    if shape == "diamond":
        return f"M{fmt(x)} {fmt(y + 1.2 * r)} L{fmt(x + r)} {fmt(y)} L{fmt(x)} {fmt(y - 1.2 * r)} L{fmt(x - r)} {fmt(y)} Z"
    s = 0.8 * r  # cross
    return f"M{fmt(x - s)} {fmt(y - s)} L{fmt(x + s)} {fmt(y + s)} M{fmt(x - s)} {fmt(y + s)} L{fmt(x + s)} {fmt(y - s)}"


def _mix(color: str, toward: str, t: float) -> str:
    a = [int(color[i:i + 2], 16) for i in (1, 3, 5)]
    b = [int(toward[i:i + 2], 16) for i in (1, 3, 5)]
    return "#" + "".join(f"{round(u + (v - u) * t):02x}" for u, v in zip(a, b))


def _shades(color: str, n: int) -> list:
    """A family's colours: its hue from light to dark, in the order of its members."""
    if n == 1:
        return [color]
    return [_mix(color, "#ffffff", 0.45 * (1 - 2 * k / (n - 1))) if k < (n - 1) / 2
            else _mix(color, "#000000", 0.35 * (2 * k / (n - 1) - 1)) for k in range(n)]


def _looks(panels: list) -> dict:
    """Each series' looks by name, the same in every panel: (colour, dashes, marker). A family
    has one colour, its members' from light to dark; a detail its series'."""
    order, members = [], {}
    for p in panels:
        for s in p["series"]:
            if s["detail_of"]:
                continue
            key = ("family", s["family"]) if s["family"] else ("series", s["name"])
            if key not in order:
                order.append(key)
            if s["family"]:
                members.setdefault(s["family"], [])
                if s["name"] not in members[s["family"]]:
                    members[s["family"]].append(s["name"])
    looks = {}
    for i, (what, name) in enumerate(order):
        color, dash, marker = COLORS[i % len(COLORS)], DASHES[i % len(DASHES)], MARKERS[i % len(MARKERS)]
        if what == "family":
            for shade, member in zip(_shades(color, len(members[name])), members[name]):
                looks[member] = (shade, dash, marker)
        else:
            looks[name] = (color, dash, marker)
    return looks


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-") or "x"


class PlotSheet(Sheet):
    """A sheet with the plot's panels in its tp:view: what the page and inspect read values
    back with."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.panels: list = []  # {name, x: Axis, y: Axis}

    def to_element(self) -> ET.Element:
        root = super().to_element()
        view = root.find(f"{SVG}metadata/{TP}view")
        for p in self.panels:
            attrs = {"name": p["name"]}
            for k in ("x", "y"):
                ax = p[k]
                attrs[f"{k}-axis"] = f"{ax.spec['scale']} {fmt(ax.lo, 9)} {fmt(ax.hi, 9)} {fmt(ax.a)} {fmt(ax.b)}"
                for key in ("label", "unit"):
                    if ax.spec[key]:
                        attrs[f"{k}-{key}"] = ax.spec[key]
            ET.SubElement(view, f"{TP}panel", attrs)
        return root


def draw(plot: dict, name: str) -> PlotSheet:
    """The sheet of a plot, checked (check), named name in the build folder."""
    fs = FS
    title = plot["title"] or Path(name).name[: -len(PLOT_SUFFIX)]
    sheet = PlotSheet(title, source=plot["source"], pipeline=plot["pipeline"], rev=plot["rev"],
                      regenerate=plot["regenerate"], kind="plot", font_size=fs, margin=2 * fs)
    panels = plot["panels"]
    looks = _looks(panels)
    hidden = plot["hidden"]
    ids = set()

    def uid(text: str) -> str:
        base, k = _slug(text), 1
        while (base if k == 1 else f"{base}-{k}") in ids:
            k += 1
        out = base if k == 1 else f"{base}-{k}"
        ids.add(out)
        return out

    def shown(path: list) -> bool:
        return not any(h == " / ".join(path) or h == path[-1] for h in hidden)

    # Each series' node in the tree: the panel's (if there are several), its family's or the
    # series' it is the detail of, its own.
    many = len(panels) > 1
    names = [p["title"] or (f"panel {i + 1}" if many else "") for i, p in enumerate(panels)]
    paths = {}
    for name, p in zip(names, panels):
        prefix = [name] if many else []
        for s in p["series"]:
            if not s["detail_of"]:
                paths[id(s)] = prefix + ([s["family"]] if s["family"] else []) + [s["name"]]
        for s in p["series"]:
            if s["detail_of"]:
                main = next(m for m in p["series"] if m["name"] == s["detail_of"])
                paths[id(s)] = paths[id(main)] + [s["name"]]
    at_first = lambda s: all(shown(paths[id(s)][:k]) for k in range(1, len(paths[id(s)]) + 1))

    # The ranges: shared axes over every panel sharing them, fitted to the series shown at
    # first; one shown later in the tree may run past the panel's edge.
    ranges = []
    for p in panels:
        r = {}
        for k in ("x", "y"):
            sharing = [q for q in panels if not q["own"][k]] if not p["own"][k] else [p]
            r[k] = _range(p[k], *_extent(sharing, k, at_first), k)
        ranges.append(r)

    # Each panel's place: side by side, or one above the other where they share x alone.
    stacked = len(panels) > 1 and not any(p["own"]["x"] for p in panels) and any(p["own"]["y"] for p in panels)
    blocks = []
    for p, r in zip(panels, ranges):
        y_ticks = Axis(p["y"], *r["y"], 0, H).ticks()
        label_w = max([text_width(t, fs) for _, t in y_ticks if t] or [0])
        titles = {k: " ".join(t for t in (p[k]["label"], f"({p[k]['unit']})" if p[k]["unit"] else None) if t)
                  for k in ("x", "y")}
        left = 0.7 * fs + label_w + (1.6 * fs if titles["y"] else 0)
        legend = (3.2 * fs if titles["x"] else 1.6 * fs) + 1.8 * fs  # the legend's first line under the panel
        entries = [s for s in p["series"] if not s["detail_of"]]
        below = legend + 1.5 * fs * max(len(entries) - 1, 0) + 0.6 * fs
        above = 2.0 * fs if p["title"] else 0.8 * fs
        blocks.append({"left": left, "label_w": label_w, "titles": titles, "legend": legend, "below": below,
                       "above": above})
    places, x, y = [], 0.0, 0.0
    if stacked:
        left = max(b["left"] for b in blocks)
        for b in blocks:
            y -= b["above"] + H
            places.append((left, y))
            y -= b["below"] + fs
    else:
        for b in blocks:
            x += b["left"]
            places.append((x, -H))
            x += W + 2 * fs

    for i, (p, r, (px, py)) in enumerate(zip(panels, ranges, places)):
        pname = names[i]
        top = sheet.layer(pname, style={"stroke": INK, "fill": "none"}) if many else None
        prefix = [pname] if many else []

        def layer(name: str, style: dict, parent=None, visible: bool = True):
            if parent is not None:
                return parent.sublayer(name, style=style, visible=visible)
            if top is not None:
                return top.sublayer(name, style=style, visible=visible)
            return sheet.layer(name, style=style, visible=visible)

        X, Y = Axis(p["x"], *r["x"], px, px + W), Axis(p["y"], *r["y"], py, py + H)
        sheet.panels.append({"name": pname, "x": X, "y": Y})
        box = (px, py, px + W, py + H)
        block = blocks[i]
        sheet._grow([(px - block["left"], py - block["below"]), (px + W + fs, py + H + block["above"])])
        pid = uid(pname or "plot")

        # The axes: grid, frame, ticks and their labels, the axes' titles, the panel's.
        axes = layer("Axes", {"stroke": INK, "fill": "none"})
        for v, label, major in _spaced(X.ticks(), X, lambda t: text_width(t, fs)):
            mx = X(v)
            axes.line(uid(f"{pid}-xgrid"), (mx, py), (mx, py + H), stroke=GRID if major else MINOR)
            if major:
                axes.line(uid(f"{pid}-xtick"), (mx, py), (mx, py - 0.4 * fs))
            if label:
                axes.text(uid(f"{pid}-xlabel"), (mx, py - 0.6 * fs - fs), label, anchor="middle", fill=INK)
        for v, label, major in _spaced(Y.ticks(), Y, lambda t: fs):
            my = Y(v)
            axes.line(uid(f"{pid}-ygrid"), (px, my), (px + W, my), stroke=GRID if major else MINOR)
            if major:
                axes.line(uid(f"{pid}-ytick"), (px - 0.4 * fs, my), (px, my))
            if label:
                axes.text(uid(f"{pid}-ylabel"), (px - 0.7 * fs, my - 0.35 * fs), label, anchor="end", fill=INK)
        if not Y.log and Y.lo < 0 < Y.hi:
            axes.line(uid(f"{pid}-zero"), (px, Y(0)), (px + W, Y(0)), stroke=ZERO)
        axes.path(uid(f"{pid}-frame"), f"M{fmt(px)} {fmt(py)} H{fmt(px + W)} V{fmt(py + H)} H{fmt(px)} Z")
        x_title, y_title = block["titles"]["x"], block["titles"]["y"]
        if x_title:
            axes.text(uid(f"{pid}-xtitle"), (px + W / 2, py - 3.2 * fs), x_title, anchor="middle", fill=INK)
        if y_title:
            axes.text(uid(f"{pid}-ytitle"), (px - 0.7 * fs - block["label_w"] - 0.6 * fs, py + H / 2), y_title,
                      angle=90, anchor="middle", fill=INK)
        if p["title"]:
            axes.text(uid(f"{pid}-title"), (px + W / 2, py + H + 0.8 * fs), p["title"], anchor="middle",
                      fill=INK, **{"font-weight": "bold"})

        # The limits, dashed, and the marks that belong to no series.
        def mark_lines(parent, marks: list, color: str):
            for m in marks:
                ax = X if m["axis"] == "x" else Y
                if not ax.drawable(m["at"]) or not ax.lo <= m["at"] <= ax.hi:
                    continue
                at = ax(m["at"])
                dash = LIMIT_DASH if m.get("limit") else MARK_DASH
                if m["axis"] == "x":
                    parent.line(uid(f"{pid}-mark"), (at, py), (at, py + H), stroke=color, **{"stroke-dasharray": dash})
                    if m["label"]:
                        parent.text(uid(f"{pid}-mark-label"), (at - 0.3 * fs, py + H - 0.5 * fs), m["label"],
                                    angle=90, anchor="end", fill=color)
                else:
                    parent.line(uid(f"{pid}-mark"), (px, at), (px + W, at), stroke=color, **{"stroke-dasharray": dash})
                    if m["label"]:
                        parent.text(uid(f"{pid}-mark-label"), (px + W - 0.5 * fs, at + 0.3 * fs), m["label"],
                                    anchor="end", fill=color)

        limits = [{"axis": k, "at": p[k][lim], "label": f"{lim} {num(p[k][lim])}", "limit": True}
                  for k in ("x", "y") for lim in ("min", "max") if p[k][lim] is not None]
        loose = [m for m in p["marks"] if m["of"] is None]
        if limits or loose:
            marks = layer("Marks", {"stroke": MUTED, "fill": "none"})
            mark_lines(marks, limits, OVER)
            mark_lines(marks, loose, MUTED)

        # The series, in the tree as the file has them: families, each series and its details.
        entries = [s for s in p["series"] if not s["detail_of"]]
        legend_y = {s["name"]: py - block["legend"] - 1.5 * fs * k for k, s in enumerate(entries)}
        summary_x = px + 3.2 * fs + max(text_width(s["name"], fs) for s in entries) + 1.5 * fs  # a column of them
        families = {}
        groups = {}
        y_lo, y_hi = p["y"]["min"], p["y"]["max"]

        def draw_series(s, g, color, dash, marker, detail: bool):
            g.element.set(f"{TP}series", s["name"])
            g.element.set(f"{TP}panel", str(i))
            g.element.set(f"{TP}kind", s["kind"])
            g.element.set(f"{TP}xs", " ".join(fmt(v, 9) for v in s["x"]))
            g.element.set(f"{TP}ys", " ".join("-" if v is None else fmt(v, 9) for v in s["y"]))
            for key in ("low", "high"):
                if s[key]:
                    g.element.set(f"{TP}{key}", " ".join("-" if v is None else fmt(v, 9) for v in s[key]))
            sid = uid(f"{pid}-{s['name']}")
            pts = [(X(a), Y(b)) if X.drawable(a) and Y.drawable(b) else None for a, b in zip(s["x"], s["y"])]
            runs, run = [], []
            for q in pts:
                if q is None:
                    if run:
                        runs.append(run)
                    run = []
                else:
                    run.append(q)
            if run:
                runs.append(run)
            # Its spread: bars at values and points, a band along a curve.
            if s["low"] or s["high"]:
                lows = s["low"] or s["y"]
                highs = s["high"] or s["y"]
                if s["kind"] == "curve":
                    band, bands = [], []
                    for a, l, h in zip(s["x"], lows, highs):
                        if X.drawable(a) and Y.drawable(l) and Y.drawable(h):
                            band.append((X(a), Y(l), Y(h)))
                        elif band:
                            bands.append(band)
                            band = []
                    if band:
                        bands.append(band)
                    for b in bands:
                        poly = _clip_polygon([(u, l) for u, l, _ in b] + [(u, h) for u, _, h in reversed(b)], box)
                        if len(poly) > 2:
                            g.path(uid(f"{sid}-band"), "M" + " L".join(f"{fmt(u)} {fmt(v)}" for u, v in poly) + " Z",
                                   stroke="none", fill=color, **{"fill-opacity": "0.18"})
                else:
                    bars, cap = [], 0.3 * fs
                    for a, l, h in zip(s["x"], lows, highs):
                        if not (X.drawable(a) and Y.drawable(l) and Y.drawable(h)):
                            continue
                        u = X(a)
                        bars += [[(u, Y(l)), (u, Y(h))], [(u - cap, Y(l)), (u + cap, Y(l))],
                                 [(u - cap, Y(h)), (u + cap, Y(h))]]
                    bars = _clipped_runs(bars, box)
                    if bars:
                        g.path(uid(f"{sid}-bars"), _d(bars), stroke=color)
            inside = _clipped_runs(runs, box)
            if s["kind"] != "points" and inside:
                width = "1" if detail else ("1.75" if s["kind"] == "curve" else "1.25")
                attrs = {"stroke": color, "stroke-width": width, "stroke-linejoin": "round"}
                if detail:
                    attrs["stroke-opacity"] = "0.5"
                elif dash:
                    attrs["stroke-dasharray"] = dash
                g.path(uid(f"{sid}-line"), _d(inside), **attrs)
            if s["kind"] != "curve" and not detail:
                r_mark = 0.3 * fs
                dots = [q for q in pts if q is not None and _inside(q, box)]
                if dots:
                    d = " ".join(_marker(marker, q, r_mark) for q in dots)
                    if marker == "cross":
                        g.path(uid(f"{sid}-markers"), d, stroke=color, **{"stroke-width": "1.5"})
                    else:
                        g.path(uid(f"{sid}-markers"), d, stroke="none", fill=color)
            # What is outside the limits along y, in red: values and points ringed, curves over.
            if not detail and (y_lo is not None or y_hi is not None):
                lo_mm = Y(y_lo) if y_lo is not None and Y.drawable(y_lo) else -math.inf
                hi_mm = Y(y_hi) if y_hi is not None and Y.drawable(y_hi) else math.inf
                if s["kind"] == "curve":
                    over = []
                    for band_box in ((box[0], box[1], box[2], min(lo_mm, box[3])),
                                     (box[0], max(hi_mm, box[1]), box[2], box[3])):
                        if band_box[1] < band_box[3]:
                            over += _clipped_runs(inside, band_box)
                    over = [o for o in over if math.dist(o[0], o[-1]) > 1e-6 or len(o) > 2]
                    if over:
                        g.path(uid(f"{sid}-over"), _d(over), stroke=OVER, **{"stroke-width": "2"})
                else:
                    rings = [q for q, v in zip(pts, s["y"]) if q is not None and _inside(q, box)
                             and ((y_lo is not None and v < y_lo) or (y_hi is not None and v > y_hi))]
                    if rings:
                        g.path(uid(f"{sid}-over"), " ".join(_marker("circle", q, 0.55 * fs) for q in rings),
                               stroke=OVER, fill="none", **{"stroke-width": "1.5"})
            own_marks = [m for m in p["marks"] if m["of"] == s["name"]]
            if own_marks:
                mark_lines(g, own_marks, color)
            # Its line in the legend, under the panel: hidden with it.
            if not detail:
                ly = legend_y[s["name"]]
                sample = [(px, ly + 0.35 * fs), (px + 2.4 * fs, ly + 0.35 * fs)]
                if s["kind"] != "points":
                    attrs = {"stroke": color, "stroke-width": "1.75" if s["kind"] == "curve" else "1.25"}
                    if dash:
                        attrs["stroke-dasharray"] = dash
                    g.path(uid(f"{sid}-key"), _d([sample]), **attrs)
                if s["kind"] != "curve":
                    mid = ((sample[0][0] + sample[1][0]) / 2, sample[0][1])
                    if marker == "cross":
                        g.path(uid(f"{sid}-key-marker"), _marker(marker, mid, 0.3 * fs), stroke=color,
                               **{"stroke-width": "1.5"})
                    else:
                        g.path(uid(f"{sid}-key-marker"), _marker(marker, mid, 0.3 * fs), stroke="none", fill=color)
                g.text(uid(f"{sid}-key-text"), (sample[1][0] + 0.8 * fs, ly), s["name"], fill=INK)
                if s["summary"]:
                    g.text(uid(f"{sid}-key-summary"), (summary_x, ly), s["summary"], fill=INK)

        for s in p["series"]:
            if s["detail_of"]:
                continue
            parent = None
            path = prefix
            if s["family"]:
                if s["family"] not in families:
                    families[s["family"]] = layer(s["family"], {"stroke": "none", "fill": "none"},
                                                  visible=shown(prefix + [s["family"]]))
                parent = families[s["family"]]
                path = prefix + [s["family"]]
            color, dash, marker = looks[s["name"]]
            g = layer(s["name"], {"stroke": color, "fill": "none"}, parent, visible=shown(path + [s["name"]]))
            groups[s["name"]] = (g, path + [s["name"]])
            draw_series(s, g, color, dash, marker, detail=False)
        for s in p["series"]:
            if s["detail_of"]:
                main, path = groups[s["detail_of"]]
                color, dash, marker = looks[s["detail_of"]]
                g = layer(s["name"], {"stroke": color, "fill": "none"}, main, visible=shown(path + [s["name"]]))
                draw_series(s, g, color, None, None, detail=True)
                # under its series' own line
                main.element.remove(g.element)
                main.element.insert(0, g.element)

    if plot["notes"]:  # broken to the plot's width
        write_notes(sheet.layer("Notes", style={"stroke": "none", "fill": INK}), plot["notes"])
    return sheet


# ---- Reading values back ------------------------------------------------------------------

class Panel:
    """A panel as a sheet or sketch file gives it (tp:panel): data to and from mm."""

    def __init__(self, el: ET.Element):
        self.name = el.get("name") or ""
        self.axes = {}
        for k in ("x", "y"):
            scale, lo, hi, a, b = el.get(f"{k}-axis").split()
            spec = {"scale": scale, "unit": el.get(f"{k}-unit"), "label": el.get(f"{k}-label"), "ticks": None}
            self.axes[k] = Axis(spec, float(lo), float(hi), float(a), float(b))

    def value(self, k: str, mm: float) -> float:
        ax = self.axes[k]
        t = (mm - ax.a) / (ax.b - ax.a)
        if ax.log:
            return 10 ** (math.log10(ax.lo) + t * (math.log10(ax.hi) - math.log10(ax.lo)))
        return ax.lo + t * (ax.hi - ax.lo)

    def box(self) -> tuple:
        return self.axes["x"].a, self.axes["y"].a, self.axes["x"].b, self.axes["y"].b

    def distance(self, p) -> float:
        x0, y0, x1, y1 = self.box()
        return math.hypot(max(x0 - p[0], 0, p[0] - x1), max(y0 - p[1], 0, p[1] - y1))


def panels_of(root: ET.Element) -> list:
    return [Panel(el) for el in root.iter(f"{TP}panel")]


def _hidden(el: ET.Element, parents: dict) -> bool:
    for n in (el, *_ancestors(el, parents)):
        if "display:none" in (n.get("style") or "").replace(" ", "") or n.get("display") == "none":
            return True
    return False


def _ancestors(el, parents):
    while el in parents:
        el = parents[el]
        yield el


def _numbers(text: Optional[str]) -> list:
    return [None if t == "-" else float(t) for t in (text or "").split()]


def stroke_report(root: ET.Element, points: list) -> str:
    """Where a stroke lies in a plot's sketch file, in the data's terms: its panel, its
    ranges along the axes, where it starts and ends, and the series shown nearest it."""
    panels = panels_of(root)
    if not panels or not points:
        return ""
    panel_no = min(range(len(panels)), key=lambda k: sum(panels[k].distance(p) for p in points))
    panel = panels[panel_no]
    xs = [panel.value("x", p[0]) for p in points]
    ys = [panel.value("y", p[1]) for p in points]
    X, Y = panel.axes["x"], panel.axes["y"]
    name = lambda ax, k: ax.spec["label"] or k
    x, y = (lambda v: X.text(v, 3)), (lambda v: Y.text(v, 3))  # a stroke is rough
    out = (f"{name(X, 'x')} {x(min(xs))}..{x(max(xs))}, {name(Y, 'y')} {y(min(ys))}..{y(max(ys))},"
           f" from ({x(xs[0])}, {y(ys[0])}) to ({x(xs[-1])}, {y(ys[-1])})")
    if panel.name:
        out = f"panel {panel.name!r}: " + out
    parents = {c: p for p in root.iter() for c in p}
    best = None
    for g in root.iter(f"{SVG}g"):
        if g.get(f"{TP}series") is None or g.get(f"{TP}panel") != str(panel_no) or _hidden(g, parents):
            continue
        line = [(X(a), Y(b)) for a, b in zip(_numbers(g.get(f"{TP}xs")), _numbers(g.get(f"{TP}ys")))
                if X.drawable(a) and Y.drawable(b)]
        if not line:
            continue
        d = sum(min(math.dist(p, q) for q in line) for p in points) / len(points)
        if best is None or d < best[0]:
            best = (d, g.get(f"{TP}series"))
    if best is not None:
        out += f", nearest {best[1]} ({best[0]:.1f} mm away on average)"
    return out


def save(path, plot: dict) -> Path:
    """Write plot as path, a <name>.plot.json, after checking it: to a temporary name first,
    renamed into place, so the viewer never reads half a file. Lists of numbers stay on one
    line. Makes the folder if need be. Raises ValueError if the plot is wrong."""
    path = Path(path)
    if not is_plot(path):
        raise ValueError(f"{path.name}: a plot's file name ends in {PLOT_SUFFIX}")
    check(plot)
    text = json.dumps(plot, indent=2, ensure_ascii=False)
    text = re.sub(r"\[([^\[\]{}\"]*)\]", lambda m: "[" + ", ".join(v.strip() for v in m.group(1).split(",")
                                                                    if v.strip()) + "]", text)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text + "\n", encoding="utf-8")
    tmp.replace(path)
    return path
