"""A scene's views (SPEC.md §2.3): the 3D view, and the sheets drawn from it.

A scene is a STEP file. Next to it, <name>.tp.json may say what the STEP file can't:
its title and where it comes from, nodes that start hidden, annotations, and named
views: planes the AI wants drawn, as sections or projections. Any scene can also be
drawn on the fly, through a plane square to an axis or from a standard direction.

A view of a scene is named after the scene's file, with its view after a "#":
bracket.step is the 3D view (or the drawing, for a 2D scene), bracket.step#section-a a
named view, bracket.step#x=12.5 the section at x = 12.5, bracket.step#front the scene
seen from the front, hidden lines and all. Standard library only.
"""
from __future__ import annotations

import json
import math
import re
from pathlib import Path
from typing import Optional

SCENE_SUFFIXES = (".step", ".stp")
META_SUFFIX = ".tp.json"

# The standard directions: where the viewer looks from (normal, towards the viewer) and
# which way is right on the sheet (xdir), so that sheet coordinates are CAD's own: from the
# front, x is CAD x and y is CAD z; from the right, x is CAD y; from above, x and y.
DIRECTIONS = {
    "front": ((0, -1, 0), (1, 0, 0)),
    "back": ((0, 1, 0), (-1, 0, 0)),
    "right": ((1, 0, 0), (0, 1, 0)),
    "left": ((-1, 0, 0), (0, -1, 0)),
    "top": ((0, 0, 1), (1, 0, 0)),
    "bottom": ((0, 0, -1), (1, 0, 0)),
    "iso": ((1, -1, 1), (1, 1, 0)),
}
TITLES = {"front": "from the front", "back": "from the back", "right": "from the right", "left": "from the left",
          "top": "from above", "bottom": "from below", "iso": "isometric"}
# A section square to an axis is seen from that axis's side, as from the right, the front
# and above.
SECTIONS = {"x": DIRECTIONS["right"], "y": DIRECTIONS["front"], "z": DIRECTIONS["top"]}
NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")  # a named view's name, safe in a file name
NUMBER = r"-?(?:\d+\.?\d*|\.\d+)"


def is_scene(path: Path) -> bool:
    return path.suffix.lower() in SCENE_SUFFIXES


def meta_path(scene: Path) -> Path:
    return scene.with_name(scene.stem + META_SUFFIX)


def read_meta(scene: Path) -> dict:
    """The scene's <name>.tp.json, or {} if it has none. Raises ValueError if it isn't JSON."""
    path = meta_path(scene)
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return {}
    try:
        meta = json.loads(text)
    except json.JSONDecodeError as err:
        raise ValueError(f"{path.name}: {err}") from None
    if not isinstance(meta, dict):
        raise ValueError(f"{path.name}: not a JSON object")
    return meta


def _vector(v, what: str) -> tuple:
    if not (isinstance(v, (list, tuple)) and len(v) == 3 and all(isinstance(c, (int, float)) for c in v)):
        raise ValueError(f"{what} must be three numbers")
    return tuple(float(c) for c in v)


def named_views(meta: dict) -> list:
    """The views the scene's .tp.json names: [{name, title, kind, origin, normal, xdir,
    annotations}], kind "section" or "projection". Raises ValueError for a malformed one."""
    out = []
    for i, v in enumerate(meta.get("views") or []):
        name = v.get("name") if isinstance(v, dict) else None
        if not isinstance(name, str) or not NAME.match(name):
            raise ValueError(f"views[{i}]: name must be letters, digits, - _ or ., such as \"section-a\"")
        kind = v.get("kind", "section")
        if kind not in ("section", "projection"):
            raise ValueError(f"view {name}: kind must be \"section\" or \"projection\"")
        normal = _vector(v.get("normal"), f"view {name}: normal")
        xdir = _vector(v.get("xdir", _any_square(normal)), f"view {name}: xdir")
        if math.hypot(*normal) < 1e-9 or math.hypot(*_cross(normal, xdir)) < 1e-9:
            raise ValueError(f"view {name}: normal and xdir must be non-zero and not parallel")
        out.append({"name": name, "title": v.get("title") or name, "kind": kind,
                    "origin": _vector(v.get("origin", (0, 0, 0)), f"view {name}: origin"),
                    "normal": normal, "xdir": xdir, "annotations": v.get("annotations") or [],
                    "notes": notes(v.get("notes"), f"view {name}: notes")})
    return out


def notes(given, what: str = "notes") -> list:
    """Notes as lines of text: given as one text, with line breaks, or a list of lines."""
    if given is None:
        return []
    if isinstance(given, str):
        return given.split("\n")
    if isinstance(given, list) and all(isinstance(line, str) for line in given):
        return list(given)
    raise ValueError(f"{what} must be a text or a list of lines")


def limits(entry: dict, what: str) -> tuple:
    """(min, max) of a quantity or field, each a number or None: the least and the most it may
    be. limit, as the first draft of version 1 called max, is read as max."""
    lo, hi = entry.get("min"), entry.get("max", entry.get("limit"))
    for v in (lo, hi):
        if v is not None and not isinstance(v, (int, float)):
            raise ValueError(f"{what}: min and max must be numbers")
    return lo, hi


# What a frame can give for itself, in place of the top level's (frame_meta).
FRAME_KEYS = ("annotations", "views", "notes")


def frame_meta(meta: dict, frame: Optional[dict]) -> dict:
    """The scene's .tp.json as it holds for a frame: the frame's own annotations, notes and
    views in place of the top level's, a view by its name, the others as they are."""
    own = (frame or {}).get("meta") or {}
    if not own:
        return meta
    out = {**meta, **{k: v for k, v in own.items() if k != "views"}}
    if "views" in own:
        mine = {v.get("name"): v for v in own["views"] if isinstance(v, dict)}
        top = [mine.pop(v.get("name"), v) if isinstance(v, dict) else v for v in meta.get("views") or []]
        out["views"] = top + list(mine.values())
    return out


def _cross(a, b):
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])


def _any_square(n) -> tuple:
    """A direction square to n: CAD x, or y if n is along x."""
    return (1, 0, 0) if abs(n[0]) < 0.9 * math.hypot(*n) else (0, 1, 0)


def view_spec(meta: dict, view: str) -> Optional[dict]:
    """What a scene's view (the part of its name after "#") draws, as named_views gives
    them, or None if there is no such view: a named view, a section square to an axis
    ("x=12.5") or a standard direction ("front")."""
    for v in named_views(meta):
        if v["name"] == view:
            return v
    m = re.fullmatch(rf"([xyz])=({NUMBER})", view)
    if m:
        axis, at = m[1], float(m[2])
        normal, xdir = SECTIONS[axis]
        origin = tuple(at if a == axis else 0.0 for a in "xyz")
        return {"name": view, "title": f"section at {axis} = {m[2]}", "kind": "section", "origin": origin,
                "normal": normal, "xdir": xdir, "annotations": []}
    if view in DIRECTIONS:
        normal, xdir = DIRECTIONS[view]
        return {"name": view, "title": TITLES[view], "kind": "projection", "origin": (0.0, 0.0, 0.0),
                "normal": normal, "xdir": xdir, "annotations": []}
    return None


def split_name(name: str) -> tuple:
    """(the scene's name, its view or None) of a view's name, "a/b.step#x=1" -> ("a/b.step", "x=1")."""
    scene, sep, view = name.partition("#")
    return scene, (view if sep else None)


def frames(scene: Path, meta: dict) -> dict:
    """The scene's frames, from its .tp.json: {axes, quantities, frames}, or {} if it has none.

    axes: [{name, unit}], the scene's parameters, such as an insertion depth;
    quantities: [{name, unit, min, max}], values worked out for each frame, such as a force,
    with the least and the most each may be, where it has limits;
    frames: [{at: {axis: value}, step: Path, values: {quantity: number}, fields: {field: file},
    meta: what it gives in place of the top level's (frame_meta)}], each a STEP file of the
    whole scene at those values of the axes, the first shown first.
    Raises ValueError for a malformed one."""
    given = meta.get("frames")
    if not given:
        return {}
    if not isinstance(given, list):
        raise ValueError("frames must be a list")
    axes = meta.get("axes") or []
    if not isinstance(axes, list) or not all(isinstance(a, dict) and isinstance(a.get("name"), str) for a in axes):
        raise ValueError("axes must be a list of {\"name\": ..., \"unit\": ...}")
    names = [a["name"] for a in axes]
    quantities = meta.get("quantities") or []
    if not isinstance(quantities, list) or not all(isinstance(q, dict) and isinstance(q.get("name"), str)
                                                   for q in quantities):
        raise ValueError("quantities must be a list of {\"name\": ..., \"unit\": ..., \"min\": ..., \"max\": ...}")
    out = []
    for i, f in enumerate(given):
        if not isinstance(f, dict) or not isinstance(f.get("at"), dict) or not isinstance(f.get("step"), str):
            raise ValueError(f"frames[{i}] needs \"at\": {{axis: value}} and \"step\": a STEP file")
        if set(f["at"]) != set(names) or not all(isinstance(v, (int, float)) for v in f["at"].values()):
            raise ValueError(f"frames[{i}]: \"at\" must give a number for each axis: {', '.join(names)}")
        step = (scene.parent / f["step"]).resolve()
        if not step.is_relative_to(scene.parent.resolve()) or not is_scene(step):
            raise ValueError(f"frames[{i}]: step must be a .step file in the scene's folder or below it")
        values = f.get("values") or {}
        if not isinstance(values, dict) or not all(isinstance(v, (int, float)) for v in values.values()):
            raise ValueError(f"frames[{i}]: values must be numbers, {{\"force\": 3.1}}")
        own = f.get("fields") or {}
        if not isinstance(own, dict) or not all(isinstance(v, str) for v in own.values()):
            raise ValueError(f"frames[{i}]: fields must map a field's name to its .vtu file")
        given_meta = {k: f[k] for k in FRAME_KEYS if k in f}
        if not isinstance(given_meta.get("annotations", []), list) or not isinstance(given_meta.get("views", []), list):
            raise ValueError(f"frames[{i}]: annotations and views must be lists, as at the top level")
        notes(given_meta.get("notes"), f"frames[{i}]: notes")
        out.append({"at": {k: float(v) for k, v in f["at"].items()}, "step": step, "values": values,
                    "fields": own, "meta": given_meta})
    return {"axes": [{"name": a["name"], "unit": a.get("unit", "")} for a in axes],
            "quantities": [dict(zip(("name", "unit", "min", "max"),
                                    (q["name"], q.get("unit", ""), *limits(q, f"quantity {q['name']}"))))
                           for q in quantities],
            "frames": out}


def frame_label(at: dict) -> str:
    """A frame's place on its axes as text: "insertion=2.5 beta=75"."""
    return " ".join(f"{k}={v:g}" for k, v in at.items())


def fields(scene: Path, meta: dict, frame: dict = None) -> list:
    """The fields to draw on the scene's sheets, from its .tp.json: [{name, unit, min, max,
    path, value, displacement}], path the .vtu file (the frame's own, if it gives one), value
    the name of its point or cell data to show (the field's name by default), displacement
    that of a vector field to move the mesh's nodes by, or None. Fields without a file are
    left out. Raises ValueError for a malformed one."""
    given = meta.get("fields") or []
    if not isinstance(given, list):
        raise ValueError("fields must be a list")
    own = (frame or {}).get("fields") or {}
    out = []
    for i, f in enumerate(given):
        if not isinstance(f, dict) or not isinstance(f.get("name"), str):
            raise ValueError(f"fields[{i}] needs a name")
        file = own.get(f["name"], f.get("file"))
        if not file:
            continue
        path = (scene.parent / file).resolve()
        if not path.is_relative_to(scene.parent.resolve()) or path.suffix.lower() != ".vtu":
            raise ValueError(f"field {f['name']}: its file must be a .vtu file in the scene's folder or below it")
        lo, hi = limits(f, f"field {f['name']}")
        out.append({"name": f["name"], "unit": f.get("unit", ""), "min": lo, "max": hi, "path": path,
                    "value": f.get("value", f["name"]), "displacement": f.get("displacement")})
    return out


def save_scene(assembly, path, meta: Optional[dict] = None) -> Path:
    """Write a scene: assembly, a CadQuery assembly or anything with CadQuery's export(path,
    exportType), as the STEP file path, and meta, if given, as its .tp.json; each to a
    temporary name first and renamed into place, so the viewer never reads half a file.
    Makes the folder if need be. A frame's STEP file is written the same way, without meta."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    try:
        assembly.export(str(tmp), exportType="STEP")
        tmp.replace(path)
    finally:
        tmp.unlink(missing_ok=True)
    if meta is not None:
        target = meta_path(path)
        tmp = target.with_name(target.name + ".tmp")
        tmp.write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")
        tmp.replace(target)
    return path
