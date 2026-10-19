"""Slicer projects as models for the 3D view (SPEC.md §2.9): ``to_glb(path)``.

OrcaSlicer and Bambu Studio save a project as a 3MF file: the pieces' meshes where
the slicer put them, and under Metadata/ the printer's settings and each piece's name
and filament. to_glb() turns such a file into a .glb like tracepaper.model's, which the
3D view shows: the plate, the pieces on it in their filaments' colours, and the
printer's build volume, hidden at first. Coordinates stay the slicer's, in mm. A 3MF
file from anything else shows its objects, without a plate. Standard library only.
"""
from __future__ import annotations

import json
import math
import sys
import xml.etree.ElementTree as ET
import zipfile
from array import array
from pathlib import Path
from typing import Iterator, Optional, Union

from .model import glb_bytes

CORE = "{http://schemas.microsoft.com/3dmanufacturing/core/2015/02}"
PRODUCTION = "{http://schemas.microsoft.com/3dmanufacturing/production/2015/06}"
RELATIONSHIPS = "{http://schemas.openxmlformats.org/package/2006/relationships}"
START_PART = "http://schemas.microsoft.com/3dmanufacturing/2013/01/3dmodel"
UNITS = {"micron": 0.001, "millimeter": 1.0, "centimeter": 10.0, "inch": 25.4, "foot": 304.8, "meter": 1000.0}
Y_UP = [-math.sqrt(0.5), 0.0, 0.0, math.sqrt(0.5)]  # the slicer's z up to glTF's y up, as CadQuery turns it
PLATE_GAP = 0.2  # between a project's plates, of a plate's size (Bambu Studio's LOGICAL_PART_PLATE_GAP)
BED = 2.0  # mm: how thick the plate is drawn, under z = 0
EXCLUDED = 0.2  # mm: how high the part of the plate the printer keeps for itself is drawn
COLORS = {"bed": "#5b5f66", "excluded": "#c2410c", "volume": "#93c5fd", "piece": "#9ca3af"}
VOLUME_OPACITY = 0.08
FOLD = math.cos(math.radians(25))  # triangles folding sharper than the 3D view's EDGE_ANGLE are on different faces
NOT_PRINTED = {"modifier_part", "negative_part", "support_blocker", "support_enforcer"}
MESH_SUFFIXES = (".stl", ".obj", ".step", ".stp", ".3mf", ".amf")
IDENTITY = (1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0)


def to_glb(path: Union[str, Path]) -> bytes:
    """The .glb file showing the 3MF file at path. Raises ValueError if it can't be read."""
    try:
        with zipfile.ZipFile(path) as zf:
            return _convert(zf)
    except (zipfile.BadZipFile, KeyError, ET.ParseError, TypeError) as e:
        raise ValueError(f"{Path(path).name} is not a 3MF file this viewer can read: {e}") from e


def _convert(zf: zipfile.ZipFile) -> bytes:
    package = _Package(zf)
    settings = _json(zf, "Metadata/project_settings.config")
    objects, plates = _slicer_objects(zf)
    colours = settings.get("filament_colour") or []
    glb = _Glb()

    bed = _bounds(_points(settings.get("printable_area")))
    if bed:
        excluded = _bounds(_points(settings.get("bed_exclude_area")))
        height = _number(settings.get("printable_height"))
        x0, y0, x1, y1 = bed
        cols = _columns(plates)
        offsets = [((i % cols) * (x1 - x0) * (1 + PLATE_GAP), -(i // cols) * (y1 - y0) * (1 + PLATE_GAP))
                   for i in range(plates)]
        for i, (dx, dy) in enumerate(offsets):
            parts = [(_box(x0 + dx, y0 + dy, -BED, x1 + dx, y1 + dy, 0), COLORS["bed"])]
            if excluded:
                ex0, ey0, ex1, ey1 = excluded
                parts.append((_box(ex0 + dx, ey0 + dy, 0, ex1 + dx, ey1 + dy, EXCLUDED), COLORS["excluded"]))
            glb.node("Plate" if plates == 1 else f"Plate {i + 1}",
                     [f for mesh, colour in parts for f in glb.faces(*mesh, glb.material(colour))])

    names = {}
    root, _, scale = package.model(package.start)
    for item in root.iterfind(f"{CORE}build/{CORE}item"):
        oid = item.get("objectid")
        about = objects.get(oid, {})
        name = about.get("name") or package.object(package.start, oid).get("name") or f"object {oid}"
        for suffix in MESH_SUFFIXES:
            if name.lower().endswith(suffix) and len(name) > len(suffix):
                name = name[: -len(suffix)]
        names[name] = names.get(name, 0) + 1
        if names[name] > 1:
            name = f"{name} ({names[name]})"
        primitives = []
        for part_id, positions, indices in package.meshes(package.start, oid, _transform(item.get("transform"), scale)):
            part = about.get("parts", {}).get(part_id, {})
            if part.get("subtype") in NOT_PRINTED or not indices:
                continue
            extruder = int(part.get("extruder") or about.get("extruder") or 1)
            colour = colours[extruder - 1] if 0 < extruder <= len(colours) else COLORS["piece"]
            primitives += glb.faces(positions, indices, glb.material(colour))
        if primitives:
            glb.node(name, primitives)

    if bed and height:
        volumes = [_box(x0 + dx, y0 + dy, 0, x1 + dx, y1 + dy, height) for dx, dy in offsets]
        glb.node("Build volume", [f for mesh in volumes
                                  for f in glb.faces(*mesh, glb.material(COLORS["volume"], VOLUME_OPACITY))],
                 hidden=True)
    return glb.bytes()


class _Package:
    """The model parts of a 3MF file, read when first needed."""

    def __init__(self, zf: zipfile.ZipFile):
        self.zip = zf
        self.parts = {}
        self.start = "3D/3dmodel.model"
        if "_rels/.rels" in zf.namelist():
            for rel in ET.fromstring(zf.read("_rels/.rels")).iter(f"{RELATIONSHIPS}Relationship"):
                if rel.get("Type") == START_PART:
                    self.start = rel.get("Target", self.start).lstrip("/")

    def model(self, part: str) -> tuple:
        """(root element, {object id: object element}, mm per unit) of a model part."""
        if part not in self.parts:
            root = ET.fromstring(self.zip.read(part))
            objects = {o.get("id"): o for o in root.iter(f"{CORE}object")}
            self.parts[part] = (root, objects, UNITS.get(root.get("unit", "millimeter"), 1.0))
        return self.parts[part]

    def object(self, part: str, oid: str) -> ET.Element:
        found = self.model(part)[1].get(oid)
        if found is None:
            raise ValueError(f"{part} has no object {oid}")
        return found

    def meshes(self, part: str, oid: str, transform: tuple, depth: int = 0) -> Iterator[tuple]:
        """(id, positions, indices) of each mesh that object oid of part is made of, placed by
        transform: positions in mm, three to a vertex, and indices three to a triangle."""
        if depth > 32:
            raise ValueError("components nested too deep")
        obj, scale = self.object(part, oid), self.model(part)[2]
        mesh = obj.find(f"{CORE}mesh")
        if mesh is not None:
            yield (oid, *_place(mesh, scale, transform))
        for c in obj.iterfind(f"{CORE}components/{CORE}component"):
            sub = (c.get(f"{PRODUCTION}path") or "").lstrip("/") or part
            placed = _compose(_transform(c.get("transform"), scale), transform)
            yield from self.meshes(sub, c.get("objectid"), placed, depth + 1)


def _slicer_objects(zf: zipfile.ZipFile) -> tuple:
    """({object id: {"name", "extruder", "parts": {id: {"name", "subtype", "extruder"}}}}, number
    of plates) from OrcaSlicer's and Bambu Studio's Metadata/model_settings.config."""
    if "Metadata/model_settings.config" not in zf.namelist():
        return {}, 1
    root = ET.fromstring(zf.read("Metadata/model_settings.config"))
    meta = lambda el: {m.get("key"): m.get("value") for m in el.findall("metadata")}  # noqa: E731
    objects = {}
    for obj in root.findall("object"):
        parts = {p.get("id"): {**meta(p), "subtype": p.get("subtype")} for p in obj.findall("part")}
        objects[obj.get("id")] = {**meta(obj), "parts": parts}
    return objects, max(1, len(root.findall("plate")))


def _json(zf: zipfile.ZipFile, name: str) -> dict:
    if name not in zf.namelist():
        return {}
    try:
        found = json.loads(zf.read(name))
    except ValueError:
        return {}
    return found if isinstance(found, dict) else {}


def _points(values) -> list:
    """[(x, y)] of a slicer's list of points, ["0x0", "256x0", ...] or "0x0,256x0,..."."""
    if isinstance(values, str):
        values = values.split(",")
    out = []
    for p in values or []:
        try:
            x, y = (float(v) for v in str(p).split("x"))
        except ValueError:
            continue
        out.append((x, y))
    return out


def _bounds(points: list) -> Optional[tuple]:
    """(x0, y0, x1, y1) around the points, if they cover an area."""
    if not points:
        return None
    xs, ys = [p[0] for p in points], [p[1] for p in points]
    box = (min(xs), min(ys), max(xs), max(ys))
    return box if box[2] > box[0] and box[3] > box[1] else None


def _number(value) -> Optional[float]:
    try:
        return float(value[0] if isinstance(value, list) else value)
    except (TypeError, ValueError, IndexError):
        return None


def _columns(plates: int) -> int:
    """How many plates the slicer lays out side by side, before starting another row."""
    root = math.sqrt(plates)
    return int(round(root) + (1 if root > round(root) else 0))


def _transform(text: Optional[str], scale: float = 1.0) -> tuple:
    """A 3MF transform, "m00 m01 m02 m10 m11 m12 m20 m21 m22 m30 m31 m32": a point (x, y, z)
    goes to (x y z 1) times those as four rows of three. Its move is scaled to mm."""
    if not text:
        return IDENTITY
    m = tuple(float(v) for v in text.split())
    if len(m) != 12:
        raise ValueError(f"not a 3MF transform: {text!r}")
    return m[:9] + tuple(v * scale for v in m[9:])


def _compose(a: tuple, b: tuple) -> tuple:
    """Transform a, then b."""
    rows = [a[0:3], a[3:6], a[6:9], a[9:12]]
    return tuple(sum(row[k] * b[3 * k + j] for k in range(3)) + (b[9 + j] if i == 3 else 0.0)
                 for i, row in enumerate(rows) for j in range(3))


def _place(mesh: ET.Element, scale: float, t: tuple) -> tuple:
    a, b, c, d, e, f, g, h, i, x0, y0, z0 = t
    positions = array("f")
    for v in mesh.iterfind(f"{CORE}vertices/{CORE}vertex"):
        x, y, z = float(v.get("x")) * scale, float(v.get("y")) * scale, float(v.get("z")) * scale
        positions.extend((x * a + y * d + z * g + x0, x * b + y * e + z * h + y0, x * c + y * f + z * i + z0))
    indices = array("I")
    for tri in mesh.iterfind(f"{CORE}triangles/{CORE}triangle"):
        indices.extend((int(tri.get("v1")), int(tri.get("v2")), int(tri.get("v3"))))
    if indices and max(indices) >= len(positions) // 3:
        raise ValueError("a triangle refers to a vertex that isn't there")
    return positions, indices


def _box(x0: float, y0: float, z0: float, x1: float, y1: float, z1: float) -> tuple:
    """(positions, indices) of a box."""
    corners = [(x, y, z) for z in (z0, z1) for y in (y0, y1) for x in (x0, x1)]  # corner 4z + 2y + x
    sides = [(0, 1, 3, 2), (4, 5, 7, 6), (0, 1, 5, 4), (2, 3, 7, 6), (0, 2, 6, 4), (1, 3, 7, 5)]
    return (array("f", [v for corner in corners for v in corner]),
            array("I", [k for p, q, r, s in sides for k in (p, q, r, p, r, s)]))


def _faces(positions: array, indices: array) -> list:
    """The mesh split into faces, [(positions, indices)], as a CAD model is (SPEC.md §2.8):
    triangles that meet at a fold sharper than the 3D view's EDGE_ANGLE are on different
    faces. The 3D view draws the edges between faces, and those of a face where it is in
    front, so a mesh as one face would show its hidden edges through itself."""
    count = len(indices) // 3
    places = {}  # where vertices are: triangles meet where they share one, even as copies
    place = [places.setdefault((positions[k], positions[k + 1], positions[k + 2]), len(places))
             for k in range(0, len(positions), 3)]
    normals = []
    for t in range(count):
        a, b, c = (3 * indices[3 * t + k] for k in range(3))
        ax, ay, az = positions[a], positions[a + 1], positions[a + 2]
        ux, uy, uz = positions[b] - ax, positions[b + 1] - ay, positions[b + 2] - az
        vx, vy, vz = positions[c] - ax, positions[c + 1] - ay, positions[c + 2] - az
        nx, ny, nz = uy * vz - uz * vy, uz * vx - ux * vz, ux * vy - uy * vx
        length = math.sqrt(nx * nx + ny * ny + nz * nz)
        normals.append((nx / length, ny / length, nz / length) if length else None)  # None: no area, any face's

    face = list(range(count))  # union-find: a triangle's face is face[...] followed to its end

    def find(t: int) -> int:
        while face[t] != t:
            face[t] = face[face[t]]
            t = face[t]
        return t

    first, stride = {}, len(places)
    for t in range(count):
        a, b, c = (place[indices[3 * t + k]] for k in range(3))
        for p, q in ((a, b), (b, c), (c, a)):
            s = first.setdefault(p * stride + q if p < q else q * stride + p, t)
            if s == t:
                continue
            n, m = normals[s], normals[t]
            if n is None or m is None or n[0] * m[0] + n[1] * m[1] + n[2] * m[2] >= FOLD:
                face[find(s)] = find(t)

    groups = {}
    for t in range(count):
        groups.setdefault(find(t), []).append(t)
    out = []
    for triangles in groups.values():
        own, pos, idx = {}, array("f"), array("I")
        for t in triangles:
            for v in indices[3 * t: 3 * t + 3]:
                if v not in own:
                    own[v] = len(own)
                    pos.extend(positions[3 * v: 3 * v + 3])
                idx.append(own[v])
        out.append((pos, idx))
    return out


def _linear(colour: str) -> list:
    """An sRGB colour "#rrggbb" as glTF's linear red, green and blue."""
    text = colour.lstrip("#")
    try:
        rgb = [int(text[k:k + 2], 16) / 255 for k in (0, 2, 4)]
    except ValueError:
        return _linear(COLORS["piece"])
    return [v / 12.92 if v <= 0.04045 else ((v + 0.055) / 1.055) ** 2.4 for v in rgb]


class _Glb:
    """A glTF scene being put together: named nodes under a root that turns z up into y up."""

    def __init__(self):
        self.gltf = {"asset": {"version": "2.0", "generator": "tracepaper.plate"}, "scene": 0,
                     "scenes": [{"nodes": [0]}], "nodes": [{"rotation": Y_UP, "children": []}],
                     "meshes": [], "materials": [], "accessors": [], "bufferViews": []}
        self.binary = bytearray()
        self.materials = {}

    def _data(self, values: array, target: int) -> int:
        if sys.byteorder != "little":  # glTF is little-endian
            values = array(values.typecode, values)
            values.byteswap()
        self.binary += b"\0" * (-len(self.binary) % 4)
        self.gltf["bufferViews"].append({"buffer": 0, "byteOffset": len(self.binary),
                                         "byteLength": len(values) * values.itemsize, "target": target})
        self.binary += values.tobytes()
        return len(self.gltf["bufferViews"]) - 1

    def material(self, colour: str, opacity: float = 1.0) -> int:
        key = (colour.lower(), opacity)
        if key not in self.materials:
            m = {"pbrMetallicRoughness": {"baseColorFactor": [*_linear(colour), opacity]}, "doubleSided": True}
            if opacity < 1:
                m["alphaMode"] = "BLEND"
            self.gltf["materials"].append(m)
            self.materials[key] = len(self.gltf["materials"]) - 1
        return self.materials[key]

    def faces(self, positions: array, indices: array, material: int) -> list:
        """The primitives of a mesh, one for each of its faces (_faces)."""
        return [self.primitive(*face, material) for face in _faces(positions, indices)]

    def primitive(self, positions: array, indices: array, material: int) -> dict:
        """A mesh's part without normals, which the viewer then shades flat, as slicers do."""
        accessors = self.gltf["accessors"]
        accessors.append({"bufferView": self._data(positions, 34962), "componentType": 5126,
                          "count": len(positions) // 3, "type": "VEC3",
                          "min": [min(positions[k::3]) for k in range(3)],
                          "max": [max(positions[k::3]) for k in range(3)]})
        accessors.append({"bufferView": self._data(indices, 34963), "componentType": 5125,
                          "count": len(indices), "type": "SCALAR"})
        return {"attributes": {"POSITION": len(accessors) - 2}, "indices": len(accessors) - 1, "material": material}

    def node(self, name: str, primitives: list, hidden: bool = False) -> None:
        self.gltf["meshes"].append({"primitives": primitives})
        node = {"name": name, "mesh": len(self.gltf["meshes"]) - 1}
        if hidden:
            node["extras"] = {"tracepaper": {"hidden": True}}
        self.gltf["nodes"].append(node)
        self.gltf["nodes"][0]["children"].append(len(self.gltf["nodes"]) - 1)

    def bytes(self) -> bytes:
        self.gltf["buffers"] = [{"byteLength": len(self.binary)}]
        return glb_bytes(self.gltf, bytes(self.binary))
