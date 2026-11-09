"""Motion (SPEC.md §2.13): sliders the person moves, and the scene's nodes turned, slid and
bent along a path by formulas of them. The browser works them out live (viewer/motion.js); this module the same,
for the exact drawings at a pose. Tracepaper never checks the result: parts may pass through
each other, or come apart, as the formulas put them. Standard library only.

A formula is arithmetic on numbers, the sliders' names and the scene's definitions:
+ - * / ^ (power), parentheses, pi, and the functions in FUNCTIONS, angles in degrees.
motion.js reads and works out the same language the same way: tests/formulas.json holds
cases both must pass.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Optional

NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_TOKEN = re.compile(r"\s*(?:((?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?)|([A-Za-z_][A-Za-z0-9_]*)|(\S))")


def _div(a: float, b: float) -> float:
    if b == 0:  # as IEEE 754 has it, and JavaScript
        return math.nan if a == 0 or math.isnan(a) else math.copysign(math.inf, a) * math.copysign(1, b)
    return a / b


def _pow(a: float, b: float) -> float:
    try:
        return math.pow(a, b)
    except OverflowError:
        return math.inf
    except (ValueError, ZeroDivisionError):
        return math.inf if a == 0 and b < 0 else math.nan


def _safe(f):
    """f, giving NaN where it has no value, as JavaScript's Math does, and infinity past the
    largest number."""
    def g(*args):
        try:
            return float(f(*args))
        except OverflowError:
            return math.inf
        except ValueError:
            return math.nan
    return g


def _ln(x: float) -> float:
    return -math.inf if x == 0 else math.log(x)


def _round(x: float) -> float:
    return math.floor(x + 0.5)  # halves up, as JavaScript's Math.round, not to even


# name: (fewest arguments, most or None, function). Angles in degrees, in and out.
FUNCTIONS = {
    "sin": (1, 1, _safe(lambda x: math.sin(math.radians(x)))),
    "cos": (1, 1, _safe(lambda x: math.cos(math.radians(x)))),
    "tan": (1, 1, _safe(lambda x: math.tan(math.radians(x)))),
    "asin": (1, 1, _safe(lambda x: math.degrees(math.asin(x)))),
    "acos": (1, 1, _safe(lambda x: math.degrees(math.acos(x)))),
    "atan": (1, 1, _safe(lambda x: math.degrees(math.atan(x)))),
    "atan2": (2, 2, _safe(lambda y, x: math.degrees(math.atan2(y, x)))),
    "sqrt": (1, 1, _safe(math.sqrt)),
    "abs": (1, 1, _safe(abs)),
    "min": (1, None, _safe(lambda *a: math.nan if any(math.isnan(v) for v in a) else min(a))),
    "max": (1, None, _safe(lambda *a: math.nan if any(math.isnan(v) for v in a) else max(a))),
    "clamp": (3, 3, _safe(lambda x, lo, hi: math.nan if math.isnan(x) else min(max(x, lo), hi))),
    "floor": (1, 1, _safe(math.floor)),
    "ceil": (1, 1, _safe(math.ceil)),
    "round": (1, 1, _safe(_round)),
    "exp": (1, 1, _safe(math.exp)),
    "ln": (1, 1, _safe(_ln)),
}
CONSTANTS = {"pi": math.pi}


# ---- Formulas -----------------------------------------------------------------------------

def parse(text) -> tuple:
    """A formula as a tree of tuples: ("n", number), ("v", name), ("neg", a), (op, a, b) for
    + - * / ^, ("f", name, [args]). A number is a formula of itself. Raises ValueError saying
    what is wrong and where, counting characters from 1."""
    if isinstance(text, bool) or not isinstance(text, (str, int, float)):
        raise ValueError("must be a formula, such as \"opening / 2\", or a number")
    if not isinstance(text, str):
        return ("n", float(text))
    tokens, pos = [], 0
    while pos < len(text):
        m = _TOKEN.match(text, pos)
        if m is None:  # only white space is left
            break
        at = m.start() + len(m.group(0)) - len(m.group(0).lstrip()) + 1
        if m.group(1):
            tokens.append(("num", float(m.group(1)), at))
        elif m.group(2):
            tokens.append(("name", m.group(2), at))
        else:
            tokens.append(("op", m.group(3), at))
        pos = m.end()
    tokens.append(("end", None, len(text) + 1))
    p = _Parser(tokens)
    tree = p.sum()
    if p.peek()[0] != "end":
        p.fail()
    return tree


class _Parser:
    def __init__(self, tokens):
        self.tokens, self.i = tokens, 0

    def peek(self):
        return self.tokens[self.i]

    def take(self):
        self.i += 1
        return self.tokens[self.i - 1]

    def fail(self, wanted: Optional[str] = None):
        kind, value, at = self.peek()
        found = "the end" if kind == "end" else f"\"{value if kind != 'num' else _num(value)}\""
        raise ValueError(f"expected {wanted} at {at}, found {found}" if wanted else f"unexpected {found} at {at}")

    def op(self, *ops) -> Optional[str]:
        kind, value, _ = self.peek()
        if kind == "op" and value in ops:
            self.take()
            return value
        return None

    def sum(self):
        a = self.product()
        while (op := self.op("+", "-")) is not None:
            a = (op, a, self.product())
        return a

    def product(self):
        a = self.unary()
        while (op := self.op("*", "/")) is not None:
            a = (op, a, self.unary())
        return a

    def unary(self):
        if (op := self.op("-", "+")) is not None:
            a = self.unary()
            return ("neg", a) if op == "-" else a
        return self.power()

    def power(self):
        a = self.atom()
        if self.op("^") is not None:
            return ("^", a, self.unary())  # from the right: 2^3^2 is 2^9
        return a

    def atom(self):
        kind, value, at = self.peek()
        if kind == "num":
            self.take()
            return ("n", value)
        if kind == "name":
            self.take()
            if self.op("(") is None:
                if value in FUNCTIONS:
                    raise ValueError(f"{value} at {at} is a function: {value}(...)")
                return ("n", CONSTANTS[value]) if value in CONSTANTS else ("v", value)
            if value not in FUNCTIONS:
                raise ValueError(f"unknown function \"{value}\" at {at}; there are {', '.join(FUNCTIONS)}")
            args = []
            if self.op(")") is None:
                args.append(self.sum())
                while self.op(",") is not None:
                    args.append(self.sum())
                if self.op(")") is None:
                    self.fail("\",\" or \")\"")
            least, most, _ = FUNCTIONS[value]
            if len(args) < least or (most is not None and len(args) > most):
                count = f"{least}" if least == most else f"at least {least}"
                raise ValueError(f"{value} at {at} takes {count} argument{'' if least == most == 1 else 's'}, "
                                 f"not {len(args)}")
            return ("f", value, args)
        if kind == "op" and value == "(":
            self.take()
            a = self.sum()
            if self.op(")") is None:
                self.fail("\")\"")
            return a
        self.fail("a number, a name or \"(\"")


def names(tree) -> set:
    """The names a formula uses."""
    if tree[0] == "v":
        return {tree[1]}
    if tree[0] == "f":
        return set().union(*(names(a) for a in tree[2]))
    return set().union(*(names(a) for a in tree[1:] if isinstance(a, tuple)))


def evaluate(tree, values: dict) -> float:
    """A formula's value for values {name: number}: a float, NaN or infinite where it has none,
    as in JavaScript."""
    kind = tree[0]
    if kind == "n":
        return tree[1]
    if kind == "v":
        return float(values[tree[1]])
    if kind == "neg":
        return -evaluate(tree[1], values)
    if kind == "f":
        return FUNCTIONS[tree[1]][2](*(evaluate(a, values) for a in tree[2]))
    a, b = evaluate(tree[1], values), evaluate(tree[2], values)
    if kind == "+":
        return a + b
    if kind == "-":
        return a - b
    if kind == "*":
        return a * b
    if kind == "/":
        return _div(a, b)
    return _pow(a, b)


def _num(v: float) -> str:
    return f"{v:.10g}"


# ---- A scene's motion ---------------------------------------------------------------------

@dataclass
class Slider:
    """A slider: a value to use in formulas, from lo to hi, at start at rest. on: (the name of
    the slider it branches off, that one's value where), for a track of several (SPEC.md
    §2.13); draw: its track's points, [(x, y)], or None for the viewer's; closed: whether its
    track's ends meet, so that it goes round."""
    name: str
    unit: str
    lo: float
    hi: float
    start: float
    on: Optional[tuple] = None
    draw: Optional[list] = None
    closed: bool = False


def _sub(a, b):
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def _dot(a, b):
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _cross(a, b):
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])


def _add(a, b, k=1.0):
    return (a[0] + k * b[0], a[1] + k * b[1], a[2] + k * b[2])


STRAIGHT = 1e-9  # radians: points of a path turning less than this are on one line


class Path:
    """A path a part follows (SPEC.md §2.13): straight lines through points, each corner
    rounded by an arc of radius round, all in one plane. Lengths along it count from its
    first point; before that and past its end it runs on straight.

    The frame at a length u is the point c there, the direction t and n = b × t, with b the
    plane's normal: towards the reader of a plane in which the path's first turn is to the
    left. place() puts a point of a part lying straight along the first line onto the path.
    viewer/bend.js is the same; tests/paths.json holds cases both must pass.

    Raises ValueError for a path that can't be: points on each other, a turn right back, a
    corner with no radius, arcs longer than the lines between them, points off one plane."""

    def __init__(self, points, round_: float = 0.0):
        pts = [tuple(float(c) for c in p) for p in points]
        self.points, self.round = pts, float(round_)
        dirs = []
        for a, b in zip(pts, pts[1:]):
            d, n = _sub(b, a), math.dist(a, b)
            if n == 0:
                raise ValueError("two of its points are the same")
            dirs.append((tuple(c / n for c in d), n))
        self.origin, self.t0 = pts[0], dirs[0][0]
        turns = [_cross(dirs[i][0], dirs[i + 1][0]) for i in range(len(dirs) - 1)]
        first = next((t for t in turns if math.hypot(*t) > STRAIGHT), None)
        if first is None:  # one straight line: any plane through it
            k = min(range(3), key=lambda i: abs(self.t0[i]))
            first = _cross(self.t0, tuple(1.0 if i == k else 0.0 for i in range(3)))
        self.b = tuple(c / math.hypot(*first) for c in first)
        self.n0 = _cross(self.b, self.t0)
        size = max(n for _, n in dirs)
        if any(abs(_dot(_sub(p, pts[0]), self.b)) > 1e-6 * size for p in pts):
            raise ValueError("its points must lie in one plane")
        # Each corner's arc: how much of the lines to either side it takes, and its angle.
        corners = []
        for i, turn in enumerate(turns):
            sine, cosine = math.hypot(*turn), _dot(dirs[i][0], dirs[i + 1][0])
            if sine <= STRAIGHT:
                if cosine < 0:
                    raise ValueError(f"it turns right back at its point {i + 2}")
                corners.append(None)
                continue
            if self.round <= 0:
                raise ValueError("a path with corners needs \"round\", the radius of their arcs, above 0")
            angle = math.atan2(sine, cosine)
            corners.append((self.round * math.tan(angle / 2), angle, 1.0 if _dot(turn, self.b) > 0 else -1.0))
        self.pieces, u, at = [], 0.0, pts[0]  # (kind, u at its start, length, ...)
        for i, (d, n) in enumerate(dirs):
            before = corners[i - 1][0] if i > 0 and corners[i - 1] else 0.0
            corner = corners[i] if i < len(corners) else None
            length = n - before - (corner[0] if corner else 0.0)
            if length < -1e-9 * size:
                raise ValueError(f"the line from its point {i + 1} to {i + 2} is too short for arcs of radius "
                                 f"{self.round:g} at its ends")
            if length > 0:
                self.pieces.append(("line", u, length, at, d))
                u, at = u + length, _add(at, d, length)
            if corner:
                _, angle, sign = corner
                centre = _add(at, _cross(self.b, d), sign * self.round)
                self.pieces.append(("arc", u, self.round * angle, centre, _sub(at, centre), d, sign))
                u += self.round * angle
                at = _add(centre, self._turned(_sub(at, centre), sign * angle))
        self.length, self.end, self.t1 = u, at, dirs[-1][0]

    def _turned(self, v, angle: float) -> tuple:
        """v, square to b, turned about it."""
        c, s, w = math.cos(angle), math.sin(angle), _cross(self.b, v)
        return (c * v[0] + s * w[0], c * v[1] + s * w[1], c * v[2] + s * w[2])

    @property
    def arcs(self) -> list:
        """Its arcs, [(u at its start, u at its end)]."""
        return [(p[1], p[1] + p[2]) for p in self.pieces if p[0] == "arc"]

    def frame(self, u: float) -> tuple:
        """(the point at length u, the direction there)."""
        if u <= 0:
            return _add(self.origin, self.t0, u), self.t0
        if u >= self.length:
            return _add(self.end, self.t1, u - self.length), self.t1
        piece = next(p for p in reversed(self.pieces) if p[1] <= u)
        if piece[0] == "line":
            _, u0, _, at, d = piece
            return _add(at, d, u - u0), d
        _, u0, _, centre, arm, d, sign = piece
        angle = sign * (u - u0) / self.round
        return _add(centre, self._turned(arm, angle)), self._turned(d, angle)

    def place(self, p, by: float) -> tuple:
        """Where a point p of the part at rest is, the part moved by mm along the path: as
        far along the path as p is along the first line, and by, and as far to its sides."""
        d = _sub(p, self.origin)
        c, t = self.frame(_dot(d, self.t0) + by)
        return _add(_add(c, _cross(self.b, t), _dot(d, self.n0)), self.b, _dot(d, self.b))

    def along(self, p) -> float:
        """How far along the first line a point at rest is."""
        return _dot(_sub(p, self.origin), self.t0)

    def aside(self, p) -> float:
        """How far a point at rest is from the first line, in the path's plane, to the left."""
        return _dot(_sub(p, self.origin), self.n0)


@dataclass
class Move:
    """A move of the nodes node names: a turn about axis through a point (degrees), a slide
    along axis (mm), or a bend along path, moved along it (mm; "follow"), by a formula,
    given as text and parsed."""
    node: str
    kind: str
    axis: Optional[tuple]
    through: Optional[tuple]
    by: object
    formula: tuple
    path: Optional[Path] = None


@dataclass
class Motion:
    """A scene's sliders, its definitions, [(name, text, formula)], each of the sliders and
    those before it, and its moves, in order."""
    sliders: list
    define: list
    moves: list

    def values(self, given: Optional[dict] = None) -> dict:
        """The sliders at given {name: value} or else where they start, and the definitions there."""
        out = {s.name: float((given or {}).get(s.name, s.start)) for s in self.sliders}
        for name, _, formula in self.define:
            out[name] = evaluate(formula, out)
        return out

    def amounts(self, given: Optional[dict] = None) -> list:
        """Each move's amount at given, or None where its formula has no value there: a
        move that isn't a number is left out."""
        values = self.values(given)
        found = [evaluate(m.formula, values) for m in self.moves]
        return [v if math.isfinite(v) else None for v in found]

    def label(self, given: Optional[dict] = None) -> str:
        """The sliders' positions as text, "opening=30 handle=0", as tp:view's pose has them."""
        values = self.values(given)
        return " ".join(f"{s.name}={_num(values[s.name])}" for s in self.sliders)

    def pose(self, text: str) -> dict:
        """The positions a label gives, "opening=30 handle=0" (or with commas): {name: value}.
        Raises ValueError for a name that isn't a slider's, or a value that isn't a number."""
        out, known = {}, [s.name for s in self.sliders]
        for item in filter(None, re.split(r"[\s,]+", text.strip())):
            name, _, value = item.partition("=")
            if name not in known:
                raise ValueError(f"no slider {name!r}; the sliders are {', '.join(known) or 'none'}")
            try:
                out[name] = float(value)
            except ValueError:
                raise ValueError(f"{name}: {value!r} is not a number") from None
            if not math.isfinite(out[name]):
                raise ValueError(f"{name}: {value!r} is not a number")
        return out

    def to_json(self, keys: Optional[list] = None) -> dict:
        """As the pages read it (motion.js): with each move's nodes' keys, if given."""
        moves = []
        for i, m in enumerate(self.moves):
            if m.kind == "turn":
                spec = {"axis": list(m.axis), "through": list(m.through), "by": m.by}
            elif m.kind == "slide":
                spec = {"along": list(m.axis), "by": m.by}
            else:
                spec = {"path": [list(p) for p in m.path.points], "round": m.path.round, "by": m.by}
            moves.append({"node": m.node, m.kind: spec, **({"keys": sorted(keys[i])} if keys else {})})
        def slider(s: Slider) -> dict:
            out = {"name": s.name, "unit": s.unit, "range": [s.lo, s.hi], "start": s.start}
            if s.on:
                out["on"] = {s.on[0]: s.on[1]}
            if s.draw:
                out["draw"] = [list(p) for p in s.draw]
            if s.closed:
                out["closed"] = True
            return out

        return {"sliders": [slider(s) for s in self.sliders],
                "define": [[name, text] for name, text, _ in self.define], "moves": moves}


def read(meta: dict) -> Optional[Motion]:
    """The motion a scene's .tp.json gives, from its sliders, define and moves, or None if it
    has none. Raises ValueError saying what is wrong."""
    given_sliders, given_define, given_moves = meta.get("sliders"), meta.get("define"), meta.get("moves")
    if not (given_sliders or given_define or given_moves):
        return None
    if not isinstance(given_sliders or [], list):
        raise ValueError("sliders must be a list of {\"name\": ..., \"range\": [lowest, highest]}")
    taken = set(FUNCTIONS) | set(CONSTANTS)
    sliders = []
    for i, s in enumerate(given_sliders or []):
        what = f"sliders[{i}]"
        name = s.get("name") if isinstance(s, dict) else None
        _name(name, what, taken)
        rng = s.get("range")
        if not (isinstance(rng, list) and len(rng) == 2 and all(_number(v) for v in rng) and rng[0] < rng[1]):
            raise ValueError(f"{what} ({name}): range must be [lowest, highest], two numbers, the lowest first")
        unit = s.get("unit", "")
        if not isinstance(unit, str):
            raise ValueError(f"{what} ({name}): unit must be text, such as \"°\" or \"mm\"")
        start = s.get("start", 0 if rng[0] <= 0 <= rng[1] else rng[0])
        if not _number(start) or not rng[0] <= start <= rng[1]:
            raise ValueError(f"{what} ({name}): start must be a number in its range, {rng[0]:g} to {rng[1]:g}")
        sliders.append(Slider(name, unit, float(rng[0]), float(rng[1]), float(start),
                              _on(s.get("on"), f"{what} ({name})", sliders),
                              _draw(s.get("draw"), f"{what} ({name})"), _closed(s.get("closed"), f"{what} ({name})")))
        taken.add(name)
    known = [s.name for s in sliders]
    if not isinstance(given_define or {}, dict):
        raise ValueError("define must be {name: formula}, such as {\"lift\": \"12 * handle / 90\"}")
    define = []
    for name, text in (given_define or {}).items():
        what = f"define.{name}"
        _name(name, what, taken)
        define.append((name, text, _formula(text, what, known)))
        known.append(name)
        taken.add(name)
    if not isinstance(given_moves or [], list):
        raise ValueError("moves must be a list of {\"node\": ..., \"turn\", \"slide\" or \"follow\": {...}}")
    moves, following = [], set()
    for i, m in enumerate(given_moves or []):
        what = f"moves[{i}]"
        if not isinstance(m, dict) or not isinstance(m.get("node"), str) or not m["node"]:
            raise ValueError(f"{what}: node must name a node of the scene, such as \"Sash\" or \"Sash / handle\"")
        kinds = [k for k in ("turn", "slide", "follow") if k in m]
        if len(kinds) != 1 or not isinstance(m[kinds[0]], dict):
            raise ValueError(f"{what} ({m['node']}): give either \"turn\": {{axis, through, by}}, "
                             f"\"slide\": {{along, by}} or \"follow\": {{path, round, by}}")
        kind, spec = kinds[0], m[kinds[0]]
        what = f"{what}.{kind}"
        axis = through = path = None
        if kind == "follow":
            if m["node"] in following:
                raise ValueError(f"{what}: {m['node']} follows a path already; a node follows one")
            following.add(m["node"])
            path = _path(spec.get("path"), spec.get("round", 0), what)
        else:
            axis = _vector(spec.get("axis" if kind == "turn" else "along"), f"{what}.{'axis' if kind == 'turn' else 'along'}")
            if math.hypot(*axis) == 0:
                raise ValueError(f"{what}: its direction must not be [0, 0, 0]")
            through = _vector(spec.get("through", [0, 0, 0]), f"{what}.through") if kind == "turn" else None
        if "by" not in spec:
            raise ValueError(f"{what}: by must say how far, a formula of the sliders, such as \"opening\"")
        moves.append(Move(m["node"], kind, axis, through, spec["by"], _formula(spec["by"], f"{what}.by", known), path))
    return Motion(sliders, define, moves)


def _path(points, round_, what: str) -> Path:
    if not (isinstance(points, list) and len(points) >= 2):
        raise ValueError(f"{what}.path must be two points or more, [[x, y, z], ...]")
    points = [_vector(p, f"{what}.path[{i}]") for i, p in enumerate(points)]
    if not _number(round_) or round_ < 0:
        raise ValueError(f"{what}.round must be the radius of the path's corners, a number from 0")
    try:
        return Path(points, round_)
    except ValueError as e:
        raise ValueError(f"{what}.path: {e}") from None


def _on(on, what: str, before: list) -> Optional[tuple]:
    """Where a slider branches off another: {name: value} -> (name, value)."""
    if on is None:
        return None
    if not (isinstance(on, dict) and len(on) == 1):
        raise ValueError(f"{what}: on must be {{slider: value}}, the slider before it it branches off and where, "
                         f"such as {{\"handle\": 90}}")
    (name, value), = on.items()
    found = next((s for s in before if s.name == name), None)
    if found is None:
        raise ValueError(f"{what}: on: {name!r} is not a slider before it; they are "
                         f"{', '.join(s.name for s in before) or 'none'}")
    if not _number(value) or not found.lo <= value <= found.hi:
        raise ValueError(f"{what}: on: {name} branches at a number in its range, {found.lo:g} to {found.hi:g}")
    return name, float(value)


def _draw(points, what: str) -> Optional[list]:
    """A slider's track as the panel draws it: [(x, y)], two points or more, not all the same."""
    if points is None:
        return None
    if not (isinstance(points, list) and len(points) >= 2
            and all(isinstance(p, list) and len(p) == 2 and all(_number(c) for c in p) for p in points)):
        raise ValueError(f"{what}: draw must be two points or more, [[x, y], ...], as many as its curve needs")
    if all(p == points[0] for p in points):
        raise ValueError(f"{what}: draw's points must not all be the same")
    return [(float(x), float(y)) for x, y in points]


def _closed(closed, what: str) -> bool:
    if closed is not None and not isinstance(closed, bool):
        raise ValueError(f"{what}: closed must be true or false")
    return bool(closed)


def _number(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def _name(name, what: str, taken: set) -> None:
    if not isinstance(name, str) or not NAME.match(name):
        raise ValueError(f"{what}: name must be letters, digits and _, not starting with a digit, "
                         f"such as \"opening\", to be used in formulas")
    if name in taken:
        raise ValueError(f"{what}: the name {name} is taken: by a function, pi, a slider or a definition")


def _vector(v, what: str) -> tuple:
    if not (isinstance(v, list) and len(v) == 3 and all(_number(c) for c in v)):
        raise ValueError(f"{what} must be three numbers")
    return tuple(float(c) for c in v)


def _formula(text, what: str, known: list) -> tuple:
    try:
        tree = parse(text)
    except ValueError as e:
        raise ValueError(f"{what}: {e}") from None
    unknown = sorted(names(tree) - set(known))
    if unknown:
        raise ValueError(f"{what}: unknown name {unknown[0]!r}; the sliders and definitions before it are "
                         f"{', '.join(known) or 'none'}")
    return tree
