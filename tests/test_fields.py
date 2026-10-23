"""Fields from .vtu files, and where a sheet's plane meets them."""
import base64
import struct
import tempfile
import unittest
import zlib
from pathlib import Path

from tracepaper import fields as F


class Plane:  # as scene.Plane, without OpenCascade
    def __init__(self, origin, normal, xdir, ydir):
        self.origin, self.normal, self.xdir, self.ydir = origin, normal, xdir, ydir


XY = Plane((0, 0, 0), (0, 0, 1), (1, 0, 0), (0, 1, 0))


def binary(fmt: str, values, compressed: bool) -> str:
    body = struct.pack(f"<{len(values)}{fmt}", *values)
    if compressed:
        packed = zlib.compress(body)
        return base64.b64encode(struct.pack("<4I", 1, len(body), len(body), len(packed)) + packed).decode()
    return base64.b64encode(struct.pack("<I", len(body)) + body).decode()


def vtu(points, cells, types, data, encoding="ascii", cell_data=None) -> str:
    """A .vtu file: points [(x, y, z)], cells [[node...]] of VTK types, point data {name: values},
    cell data likewise."""
    compressed = encoding == "zlib"

    def array(kind, fmt, values, extra=""):
        if encoding == "ascii":
            return f'<DataArray type="{kind}"{extra} format="ascii">{" ".join(map(str, values))}</DataArray>'
        return f'<DataArray type="{kind}"{extra} format="binary">{binary(fmt, values, compressed)}</DataArray>'

    offsets, n = [], 0
    for c in cells:
        n += len(c)
        offsets.append(n)
    def arrays(given):
        return "".join(
            array("Float64", "d", [c for v in vals for c in (v if isinstance(v, tuple) else (v,))],
                  f' Name="{name}"' + (' NumberOfComponents="3"' if isinstance(vals[0], tuple) else ""))
            for name, vals in given.items())
    return (f'<VTKFile type="UnstructuredGrid" version="1.0" byte_order="LittleEndian" header_type="UInt32"'
            f'{" compressor=\"vtkZLibDataCompressor\"" if compressed else ""}><UnstructuredGrid>'
            f'<Piece NumberOfPoints="{len(points)}" NumberOfCells="{len(cells)}">'
            f'<Points>{array("Float32", "f", [c for p in points for c in p], " NumberOfComponents=\"3\"")}</Points>'
            f'<Cells>{array("Int64", "q", [i for c in cells for i in c], " Name=\"connectivity\"")}'
            f'{array("Int64", "q", offsets, " Name=\"offsets\"")}{array("UInt8", "B", types, " Name=\"types\"")}</Cells>'
            f'<PointData>{arrays(data)}</PointData><CellData>{arrays(cell_data or {})}</CellData>'
            '</Piece></UnstructuredGrid></VTKFile>')


def linear(p):  # a field linear in space: exact on any linear element
    return p[0] + 2 * p[1] + 3 * p[2]


class FieldsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def write(self, text: str) -> Path:
        path = self.dir / "f.vtu"
        path.write_text(text)
        return path

    def test_encodings(self):
        points = [(0, 0, 0), (2, 0, 0), (2, 1, 0), (0, 1, 0)]
        for encoding in ("ascii", "binary", "zlib"):
            with self.subTest(encoding=encoding):
                mesh = F.read_vtu(self.write(vtu(points, [[0, 1, 2, 3]], [F.QUAD],
                                                 {"t": [1.0, 2.0, 3.0, 4.0], "u": [(1.0, 0.0, 0.0)] * 4}, encoding)))
                self.assertEqual(mesh.points[2], (2, 1, 0))
                self.assertEqual(mesh.triangles, [(0, 1, 2), (0, 2, 3)])  # a quad in two
                self.assertEqual(mesh.data["t"], [1, 2, 3, 4])
                self.assertEqual(mesh.data["u"][3], (1, 0, 0))

    def test_triangles_in_the_plane(self):
        points = [(0, 0, 0), (2, 0, 0), (0, 1, 0), (0, 0, 5)]
        mesh = F.read_vtu(self.write(vtu(points, [[0, 1, 2], [0, 1, 3]], [F.TRIANGLE, F.TRIANGLE],
                                         {"t": [1.0, 2.0, 3.0, 4.0], "u": [(0.0, 1.0, 9.0)] * 4})))
        (tri,) = F.triangles_on(mesh, XY, "t", "u")  # the other one stands up out of it
        self.assertEqual(tri, (((0, 0), (2, 0), (0, 1)), (1, 2, 3), ((0, 1), (0, 1), (0, 1))))
        with self.assertRaisesRegex(ValueError, "no point or cell data 'v'; it has: t, u"):
            F.triangles_on(mesh, XY, "v")

    def test_values_per_element(self):
        # A quad (two triangles) and a triangle in the plane, a tetrahedron through it.
        points = [(0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 0), (2, 0, 0), (0, 0, -1), (0, 0, 1), (3, 3, 0)]
        mesh = F.read_vtu(self.write(vtu(points, [[0, 1, 2, 3], [1, 4, 2], [7, 4, 5, 6]], [F.QUAD, F.TRIANGLE, F.TETRA],
                                         {}, "zlib", cell_data={"s": [5.0, 7.0, 9.0]})))
        tris = F.triangles_on(mesh, XY, "s")
        values = sorted(v for _, v, _ in tris)
        self.assertEqual(values[:3], [(5, 5, 5), (5, 5, 5), (7, 7, 7)])
        self.assertTrue(all(v == (9, 9, 9) for v in values[3:]) and len(values) > 3)  # the cut, one value
        self.assertEqual(F.values_of(mesh, "s"), [5, 7, 9])

    def test_cut_through_tetrahedra_is_exact(self):
        points = [(0, 0, 0), (1, 0, 0), (0, 1, 0), (0, 0, 1), (1, 1, 1)]
        tets = [[0, 1, 2, 3], [1, 2, 3, 4]]
        mesh = F.read_vtu(self.write(vtu(points, tets, [F.TETRA, F.TETRA], {"t": [linear(p) for p in points]})))
        cut = Plane((0, 0, 0.4), (0, 0, 1), (1, 0, 0), (0, 1, 0))
        tris = F.triangles_on(mesh, cut, "t")
        self.assertGreaterEqual(len(tris), 2)
        for pts, values, _ in tris:
            for (x, y), v in zip(pts, values):
                self.assertAlmostEqual(v, linear((x, y, 0.4)))


if __name__ == "__main__":
    unittest.main()
