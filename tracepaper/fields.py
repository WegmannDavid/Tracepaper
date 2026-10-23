"""Fields on a scene (SPEC.md §2.11): values at the nodes of a mesh, read from VTK's .vtu files.

A field is what a FEM solver or any other calculation gives: a scalar (a strain) or a
vector (a displacement) at each node of a mesh of triangles or tetrahedra, linear across
each element, or one value for each element, the same all across it. read_vtu reads one; triangles_on gives the triangles a sheet draws, with
the values at their corners, in the sheet's coordinates: the mesh's own triangles where
they lie in the sheet's plane, and where a plane cuts tetrahedra, the cut, exact for a
field linear across them. Standard library only.
"""
from __future__ import annotations

import base64
import math
import struct
import xml.etree.ElementTree as ET
import zlib
from dataclasses import dataclass
from pathlib import Path

TYPES = {"Int8": "b", "UInt8": "B", "Int16": "h", "UInt16": "H", "Int32": "i", "UInt32": "I",
         "Int64": "q", "UInt64": "Q", "Float32": "f", "Float64": "d"}
# VTK cell types: their corner nodes (quadratic elements by their corners).
TRIANGLE, QUAD, TETRA, QUADRATIC_TRIANGLE, QUADRATIC_TETRA = 5, 9, 10, 22, 24
CORNERS = {TRIANGLE: 3, QUAD: 4, TETRA: 4, QUADRATIC_TRIANGLE: 3, QUADRATIC_TETRA: 4}
IN_PLANE = 1e-6  # mm: how close a triangle's corners must be to a plane to lie in it


@dataclass
class Mesh:
    """A .vtu file's points ([(x, y, z)]), its triangles and tetrahedra (corner indices), its
    point data by name (a list of floats, or of tuples for vectors), and its cell data by name,
    as cells, the index of the cell each triangle and tetrahedron is of."""
    points: list
    triangles: list
    tetrahedra: list
    data: dict
    cell_data: dict = None
    triangle_cells: list = None
    tetrahedron_cells: list = None


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
    for cell, (end, kind) in enumerate(zip(offsets, types)):
        nodes = conn[start:end]
        start = end
        corners = CORNERS.get(kind)
        if corners is None:
            continue
        nodes = nodes[:corners]
        if kind in (TRIANGLE, QUADRATIC_TRIANGLE):
            triangles.append(tuple(nodes))
            tri_cells.append(cell)
        elif kind == QUAD:
            triangles += [(nodes[0], nodes[1], nodes[2]), (nodes[0], nodes[2], nodes[3])]
            tri_cells += [cell, cell]
        else:
            tetrahedra.append(tuple(nodes))
            tet_cells.append(cell)

    def arrays(where: str) -> dict:
        out = {}
        for el in piece.findall(f"{where}/DataArray"):
            values = _decode(el, appended, header, compressed)
            n = int(el.get("NumberOfComponents", "1"))
            out[el.get("Name")] = values if n == 1 else [tuple(values[i:i + n]) for i in range(0, len(values), n)]
        return out

    return Mesh(points, triangles, tetrahedra, arrays("PointData"), arrays("CellData"), tri_cells, tet_cells)


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


def triangles_on(mesh: Mesh, pl, value: str, vector: str = None) -> list:
    """The triangles a sheet in plane pl (scene.Plane) draws of the field value: [((x, y)
    corners, (a, b, c) values, (u, v) displacements or None)] in the plane's coordinates,
    displacements from the vector field of that name, in the plane too. The mesh's
    triangles lying in the plane, and the cut through its tetrahedra."""
    cells = (mesh.cell_data or {}).get(value) if value not in mesh.data else None
    if value not in mesh.data and cells is None:
        names = [*mesh.data, *(mesh.cell_data or {})]
        raise ValueError(f"the field file has no point or cell data {value!r}; it has: {', '.join(names) or 'none'}")
    vals = _magnitudes(mesh.data[value]) if cells is None else None  # a vector: its length
    cells = _magnitudes(cells) if cells is not None else None
    disp = mesh.data.get(vector) if vector else None
    if vector and disp is None:
        raise ValueError(f"the field file has no point data {vector!r}")
    o, n, x, y = pl.origin, pl.normal, pl.xdir, pl.ydir
    dist = [_dot(_sub(p, o), n) for p in mesh.points]

    def flat(p):
        d = _sub(p, o)
        return (_dot(d, x), _dot(d, y))

    def along(v):
        return (_dot(v, x), _dot(v, y)) if v is not None else None

    out = []
    for k, tri in enumerate(mesh.triangles):
        if all(abs(dist[i]) <= IN_PLANE for i in tri):
            v = tuple(vals[i] for i in tri) if cells is None else (cells[mesh.triangle_cells[k]],) * 3
            out.append((tuple(flat(mesh.points[i]) for i in tri), v,
                        tuple(along(disp[i]) for i in tri) if disp else None))
    for k, tet in enumerate(mesh.tetrahedra):
        cut = []
        for a, b in ((0, 1), (0, 2), (0, 3), (1, 2), (1, 3), (2, 3)):
            i, j = tet[a], tet[b]
            if (dist[i] < 0) != (dist[j] < 0):
                t = dist[i] / (dist[i] - dist[j])
                p = tuple(pi + t * (pj - pi) for pi, pj in zip(mesh.points[i], mesh.points[j]))
                v = vals[i] + t * (vals[j] - vals[i]) if cells is None else cells[mesh.tetrahedron_cells[k]]
                u = tuple(di + t * (dj - di) for di, dj in zip(disp[i], disp[j])) if disp else None
                cut.append((flat(p), v, along(u)))
        if len(cut) == 4:  # a quadrilateral: its corners in order round it, then two triangles
            cx, cy = sum(c[0][0] for c in cut) / 4, sum(c[0][1] for c in cut) / 4
            cut.sort(key=lambda c: math.atan2(c[0][1] - cy, c[0][0] - cx))
            parts = [cut[:3], [cut[0], cut[2], cut[3]]]
        elif len(cut) == 3:
            parts = [cut]
        else:
            continue
        for part in parts:
            out.append((tuple(c[0] for c in part), tuple(c[1] for c in part),
                        tuple(c[2] for c in part) if disp else None))
    return out
