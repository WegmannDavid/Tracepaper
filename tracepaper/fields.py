"""Fields on a scene (SPEC.md §2.11): values at the nodes of a mesh, read from VTK's .vtu files.

A field is what a FEM solver or any other calculation gives: a scalar (a strain) or a
vector (a displacement, a force) at each node of a mesh, linear across each element, or
one value for each element, the same all across it. The mesh is of triangles,
quadrilaterals or tetrahedra, of lines (a contact boundary), or of bare points (where
forces act), or of several of these. read_vtu reads one; on_plane gives what a sheet draws
of it, in the sheet's coordinates: the mesh's own triangles, lines and points where they
lie in the sheet's plane, and where a plane cuts tetrahedra, the cut, exact for a field
linear across them; and a vector field's arrows. Standard library only.
"""
from __future__ import annotations

import base64
import math
import struct
import xml.etree.ElementTree as ET
import zlib
from dataclasses import dataclass, field
from pathlib import Path

TYPES = {"Int8": "b", "UInt8": "B", "Int16": "h", "UInt16": "H", "Int32": "i", "UInt32": "I",
         "Int64": "q", "UInt64": "Q", "Float32": "f", "Float64": "d"}
# VTK cell types: their corner nodes (quadratic elements by their corners).
VERTEX, POLY_VERTEX, LINE, POLY_LINE, QUADRATIC_EDGE = 1, 2, 3, 4, 21
TRIANGLE, QUAD, TETRA, QUADRATIC_TRIANGLE, QUADRATIC_QUAD, QUADRATIC_TETRA = 5, 9, 10, 22, 23, 24
CORNERS = {LINE: 2, QUADRATIC_EDGE: 2, TRIANGLE: 3, QUAD: 4, TETRA: 4, QUADRATIC_TRIANGLE: 3, QUADRATIC_QUAD: 4,
           QUADRATIC_TETRA: 4}
IN_PLANE = 1e-6  # mm: how close a triangle's corners must be to a plane to lie in it


@dataclass
class Mesh:
    """A .vtu file's points ([(x, y, z)]), its triangles, tetrahedra, lines (node indices) and
    bare points (vertices, a node index each), its point data by name (a list of floats, or
    of tuples for vectors, of three components), and its cell data by name, as cells, the
    index of the cell each triangle, tetrahedron, line and vertex is of. A file of points
    without cells is bare points."""
    points: list
    triangles: list
    tetrahedra: list
    data: dict
    cell_data: dict = None
    triangle_cells: list = None
    tetrahedron_cells: list = None
    lines: list = field(default_factory=list)
    line_cells: list = field(default_factory=list)
    vertices: list = field(default_factory=list)
    vertex_cells: list = field(default_factory=list)


def _decode(el: ET.Element, appended: bytes, header: str, compressed: bool) -> list:
    kind = TYPES[el.get("type", "Float32")]
    fmt = el.get("format", "ascii")
    if fmt == "ascii":
        return [float(v) if kind in "fd" else int(v) for v in (el.text or "").split()]
    if fmt == "binary":
        raw = base64.b64decode("".join((el.text or "").split()))
    else:  # appended: raw bytes, or base64, after the AppendedData element's "_"
        raw = appended[int(el.get("offset", "0")):]
    size = struct.calcsize(header)
    if compressed:
        # A header of block count, block size, last block size and each block's compressed size.
        nblocks = struct.unpack_from("<" + header, raw)[0]
        sizes = struct.unpack_from(f"<{3 + nblocks}{header}", raw)
        pos, out = size * (3 + nblocks), b""
        for n in sizes[3:]:
            out += zlib.decompress(raw[pos:pos + n])
            pos += n
        body = out
    else:
        n = struct.unpack_from("<" + header, raw)[0]
        body = raw[size:size + n]
    return list(struct.unpack(f"<{len(body) // struct.calcsize(kind)}{kind}", body))


def read_vtu(path) -> Mesh:
    """The mesh and point data of a VTK XML unstructured grid (.vtu), ASCII, binary or
    appended, compressed with zlib or not. Raises ValueError for anything else."""
    try:
        root = ET.parse(path).getroot()
    except (ET.ParseError, OSError) as err:
        raise ValueError(f"{Path(path).name}: {err}") from None
    if root.get("type") != "UnstructuredGrid":
        raise ValueError(f"{Path(path).name}: not a VTK unstructured grid (.vtu)")
    header = TYPES[root.get("header_type", "UInt32")]
    compressed = root.get("compressor") == "vtkZLibDataCompressor"
    appended = b""
    app = root.find("AppendedData")
    if app is not None:
        text = (app.text or "").strip()
        appended = text[1:].encode("latin-1") if text.startswith("_") else b""
        if app.get("encoding") == "base64":
            appended = base64.b64decode(appended)
    piece = root.find("UnstructuredGrid/Piece")
    if piece is None:
        raise ValueError(f"{Path(path).name}: no Piece")

    def array(parent: str, name: str = None):
        for el in piece.findall(f"{parent}/DataArray"):
            if name is None or el.get("Name") == name:
                return el
        return None

    points_el = array("Points")
    if points_el is None:
        raise ValueError(f"{Path(path).name}: no Points")
    flat = _decode(points_el, appended, header, compressed)
    ncomp = int(points_el.get("NumberOfComponents", "3"))
    points = [tuple(flat[i:i + ncomp]) + (0.0,) * (3 - ncomp) for i in range(0, len(flat), ncomp)]
    conn = _decode(array("Cells", "connectivity"), appended, header, compressed)
    offsets = _decode(array("Cells", "offsets"), appended, header, compressed)
    types = _decode(array("Cells", "types"), appended, header, compressed)
    triangles, tetrahedra, tri_cells, tet_cells, start = [], [], [], [], 0
    lines, line_cells, vertices, vertex_cells = [], [], [], []
    for cell, (end, kind) in enumerate(zip(offsets, types)):
        nodes = conn[start:end]
        start = end
        if kind in (VERTEX, POLY_VERTEX):
            vertices += nodes
            vertex_cells += [cell] * len(nodes)
            continue
        if kind == POLY_LINE:
            lines += list(zip(nodes, nodes[1:]))
            line_cells += [cell] * (len(nodes) - 1)
            continue
        corners = CORNERS.get(kind)
        if corners is None:
            continue
        nodes = nodes[:corners]
        if kind in (LINE, QUADRATIC_EDGE):
            lines.append(tuple(nodes))
            line_cells.append(cell)
        elif kind in (TRIANGLE, QUADRATIC_TRIANGLE):
            triangles.append(tuple(nodes))
            tri_cells.append(cell)
        elif kind in (QUAD, QUADRATIC_QUAD):
            triangles += [(nodes[0], nodes[1], nodes[2]), (nodes[0], nodes[2], nodes[3])]
            tri_cells += [cell, cell]
        else:
            tetrahedra.append(tuple(nodes))
            tet_cells.append(cell)
    if not types:  # points alone: bare points
        vertices, vertex_cells = list(range(len(points))), list(range(len(points)))

    def arrays(where: str) -> dict:
        out = {}
        for el in piece.findall(f"{where}/DataArray"):
            values = _decode(el, appended, header, compressed)
            n = int(el.get("NumberOfComponents", "1"))
            pad = (0.0,) if n == 2 else ()  # a vector in the plane z = 0
            out[el.get("Name")] = values if n == 1 else [tuple(values[i:i + n]) + pad for i in range(0, len(values), n)]
        return out

    return Mesh(points, triangles, tetrahedra, arrays("PointData"), arrays("CellData"), tri_cells, tet_cells,
                lines, line_cells, vertices, vertex_cells)


def _magnitudes(values: list) -> list:
    return [sum(c * c for c in v) ** 0.5 for v in values] if values and isinstance(values[0], tuple) else values


def values_of(mesh: Mesh, value: str) -> list:
    """All the values of a field's point or cell data, vectors as their lengths."""
    if value in mesh.data:
        return _magnitudes(mesh.data[value])
    return _magnitudes((mesh.cell_data or {}).get(value) or [])


def _sub(a, b):
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def _dot(a, b):
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


@dataclass
class OnPlane:
    """What a sheet in a plane draws of a field, in the plane's coordinates, displacements
    (from the vector field that moves the mesh, in the plane too) None where there is none:
    triangles, [((x, y) corners, (a, b, c) values, displacements, cell)]; lines, [((x, y)
    ends, (a, b) values, displacements)]; dots, the bare points, [((x, y), value,
    displacement)], vectors as their lengths; and for a vector field, arrows, [((x, y), (vx,
    vy) the vector in the plane, its whole length, displacement)], at the nodes, or for one
    value for each element, at the elements' middles. areas: whether the mesh has elements
    with an area or a volume, whose arrows are a field's, rather than forces on lines or
    points."""
    vector: bool
    areas: bool
    triangles: list
    lines: list
    dots: list
    arrows: list


def on_plane(mesh: Mesh, pl, value: str, vector: str = None) -> OnPlane:
    """What a sheet in plane pl (scene.Plane) draws of the field value, moved by the vector
    field vector, if given: the mesh's triangles, lines and bare points lying in the plane,
    and the cut through its tetrahedra (OnPlane)."""
    per_cell = value not in mesh.data
    raw = (mesh.cell_data or {}).get(value) if per_cell else mesh.data[value]
    if raw is None:
        names = [*mesh.data, *(mesh.cell_data or {})]
        raise ValueError(f"the field file has no point or cell data {value!r}; it has: {', '.join(names) or 'none'}")
    is_vector = bool(raw) and isinstance(raw[0], tuple)
    if is_vector and len(raw[0]) != 3:
        raise ValueError(f"{value!r} has {len(raw[0])} components: a field is a number or a vector of 2 or 3")
    vals = _magnitudes(raw)  # a vector: its length
    disp = mesh.data.get(vector) if vector else None
    if vector and disp is None:
        raise ValueError(f"the field file has no point data {vector!r}")
    o, n, x, y = pl.origin, pl.normal, pl.xdir, pl.ydir
    dist = [_dot(_sub(p, o), n) for p in mesh.points]
    on = [abs(d) <= IN_PLANE for d in dist]

    def flat(p):
        d = _sub(p, o)
        return (_dot(d, x), _dot(d, y))

    def along(v):
        return (_dot(v, x), _dot(v, y)) if v is not None else None

    def at(nodes, cell):  # the values at an element's nodes, or its one value
        return tuple(vals[i] for i in nodes) if not per_cell else (vals[cell],) * len(nodes)

    def moved(nodes):
        return tuple(along(disp[i]) for i in nodes) if disp else None

    out = OnPlane(is_vector, bool(mesh.triangles or mesh.tetrahedra), [], [], [], [])
    nodes, middles = {}, {}  # arrows: at the nodes in the plane, or at its elements' middles
    for k, tri in enumerate(mesh.triangles):
        if all(on[i] for i in tri):
            cell = mesh.triangle_cells[k]
            out.triangles.append((tuple(flat(mesh.points[i]) for i in tri), at(tri, cell), moved(tri), cell))
            for i in tri:
                nodes.setdefault(i, None)
            middles.setdefault(("cell", cell), set()).update(tri)
    for k, line in enumerate(mesh.lines):
        if all(on[i] for i in line):
            cell = mesh.line_cells[k]
            out.lines.append((tuple(flat(mesh.points[i]) for i in line), at(line, cell), moved(line)))
            for i in line:
                nodes.setdefault(i, None)
            middles.setdefault(("cell", cell), set()).update(line)
    for k, i in enumerate(mesh.vertices):
        if on[i]:
            cell = mesh.vertex_cells[k]
            out.dots.append((flat(mesh.points[i]), at((i,), cell)[0], moved((i,))[0] if disp else None))
            nodes.setdefault(i, None)
            middles.setdefault(("vertex", k), set()).add(i)
    cuts, cut_middles = {}, []  # where the plane crosses a tetrahedron's edges; the middles of its cuts
    for k, tet in enumerate(mesh.tetrahedra):
        cell = mesh.tetrahedron_cells[k]
        cut = []
        for a, b in ((0, 1), (0, 2), (0, 3), (1, 2), (1, 3), (2, 3)):
            i, j = tet[a], tet[b]
            if (dist[i] < 0) != (dist[j] < 0):
                t = dist[i] / (dist[i] - dist[j])
                p = tuple(pi + t * (pj - pi) for pi, pj in zip(mesh.points[i], mesh.points[j]))
                v = vals[i] + t * (vals[j] - vals[i]) if not per_cell else vals[cell]
                u = tuple(di + t * (dj - di) for di, dj in zip(disp[i], disp[j])) if disp else None
                if is_vector and not per_cell:
                    cuts[(min(i, j), max(i, j))] = (p, tuple(ri + t * (rj - ri) for ri, rj in zip(raw[i], raw[j])), u)
                cut.append((flat(p), v, along(u), p))
        if len(cut) == 4:  # a quadrilateral: its corners in order round it, then two triangles
            cx, cy = sum(c[0][0] for c in cut) / 4, sum(c[0][1] for c in cut) / 4
            cut.sort(key=lambda c: math.atan2(c[0][1] - cy, c[0][0] - cx))
            parts = [cut[:3], [cut[0], cut[2], cut[3]]]
        elif len(cut) == 3:
            parts = [cut]
        else:
            continue
        for part in parts:
            out.triangles.append((tuple(c[0] for c in part), tuple(c[1] for c in part),
                                  tuple(c[2] for c in part) if disp else None, cell))
        if is_vector and per_cell:
            middle = tuple(sum(c[3][m] for c in cut) / len(cut) for m in range(3))
            u = tuple(sum(c[2][m] for c in cut) / len(cut) for m in range(2)) if disp else None
            cut_middles.append((flat(middle), along(raw[cell]), vals[cell], u))
    if not is_vector:
        return out
    if per_cell:
        for (kind, key), members in middles.items():
            cell = key if kind == "cell" else mesh.vertex_cells[key]
            middle = tuple(sum(mesh.points[i][m] for i in members) / len(members) for m in range(3))
            u = tuple(sum(along(disp[i])[m] for i in members) / len(members) for m in range(2)) if disp else None
            out.arrows.append((flat(middle), along(raw[cell]), vals[cell], u))
        out.arrows += cut_middles
    else:
        for i in nodes:
            out.arrows.append((flat(mesh.points[i]), along(raw[i]), vals[i], along(disp[i]) if disp else None))
        for p, v, u in cuts.values():
            out.arrows.append((flat(p), along(v), _dot(v, v) ** 0.5, along(u)))
    return out
