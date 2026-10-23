"""Local HTTP server behind the viewer.

It lists a project's views (scenes, .step, with the sheets drawn from them; and 2D
sheets, .svg, and 3D models, .glb) and serves them with the pages that show them,
writes sketch files when the person presses Save (SPEC.md §3, §5), and connects a
tablet (pen.py) when a page asks, passing its pen input to the view the person last
used. Scenes need OpenCascade's Python binding (OCP); everything else, only Python.
"""
from __future__ import annotations

import json
import os
import queue
import signal
import tempfile
import threading
import uuid
import webbrowser
import xml.etree.ElementTree as ET
from collections import OrderedDict
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Optional
from urllib.parse import parse_qs, quote, unquote, urlsplit

from . import plate
from . import views as scenes
from .pen import PenHub

SVG_NS = "http://www.w3.org/2000/svg"
SKETCH_SUFFIX = ".sketch.svg"
# A slicer's project (.3mf) is shown as a model: its plate and pieces (plate.py). A scene
# (.step) is shown as a model, or as a sheet if it is 2D, and its views as sheets.
KINDS = {".svg": "sheet", ".glb": "model", ".3mf": "model", ".step": "scene", ".stp": "scene"}
MODEL_SUFFIXES = {suffix for suffix, kind in KINDS.items() if kind != "sheet"}
SKIP_DIRS = {"node_modules", "__pycache__", "venv"}
MAX_SKETCH_BYTES = 50 * 1024 * 1024
MAX_VIEW_BYTES = 8 * 1024 * 1024  # a picture of the view for the tablet's screen
MODELS_KEPT = 4  # slicer projects kept converted, for windows reloading them
SCENES_KEPT = 4  # scenes kept read, and
DRAWN_KEPT = 32  # their views kept drawn
KEEPALIVE = 5  # seconds between keep-alives on the pen stream, which notice closed windows
LAST_WINDOW = 10  # seconds after the last window closed that the tablet is let go
VIEWER_DIR = Path(__file__).resolve().parent / "viewer"
PAGES = {"sheet": "index.html", "model": "model.html"}
CONTENT_TYPES = {".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8",
                 ".css": "text/css; charset=utf-8"}
VIEW_TYPES = {"sheet": "image/svg+xml", "model": "model/gltf-binary"}


@dataclass
class View:
    """A view the project page lists. name is its path from the build folder, with "/"
    between folders, and for a scene's sheets the view after "#" ("a/b.step#front"); kind
    is the page that shows it, "sheet" or "model"; path is its file, the scene's for its
    views; view is the scene's view, or None."""
    name: str
    kind: str
    path: Path
    view: Optional[str] = None

    @property
    def scene(self) -> bool:
        return scenes.is_scene(self.path)

    def files(self) -> list:
        """The files it is drawn from: a scene's views change when its .tp.json or its frames do."""
        if not self.scene:
            return [self.path]
        try:
            framed = scenes.frames(self.path, scenes.read_meta(self.path)).get("frames", [])
        except ValueError:
            framed = []
        return [self.path, scenes.meta_path(self.path), *(f["step"] for f in framed)]

    def mtime(self) -> Optional[str]:
        """When it last changed, or None if its file is gone."""
        times = [_mtime(f) for f in self.files()]
        if times[0] is None:
            return None
        return str(max(int(t) for t in times if t is not None))

    def sketch(self) -> Path:
        return sketch_path(self.path if self.view is None else self.path.with_name(f"{self.path.name}#{self.view}"))


def sketch_path(view: Path) -> Path:
    """Where a view's sketch file goes: name.sketch.svg for a sheet name.svg, and
    name.glb.sketch.svg for a model name.glb, name.3mf.sketch.svg, name.step.sketch.svg or
    name.step#front.sketch.svg (SPEC.md §3)."""
    stem = view.name[: -len(".svg")] if view.suffix.lower() == ".svg" else view.name
    return view.with_name(stem + SKETCH_SUFFIX)


def view_of_sketch(name: str) -> str:
    """The view a sketch file's name is for: the inverse of sketch_path."""
    stem = name[: -len(SKETCH_SUFFIX)]
    return stem if "#" in stem or Path(stem).suffix.lower() in MODEL_SUFFIXES else stem + ".svg"


_solid_scans: dict = {}  # path -> (mtime, size, has solids)


def has_solids(path: Path) -> bool:
    """Whether a STEP file holds any solid, else it is a 2D scene: a quick look at its text,
    kept until the file changes."""
    stat = path.stat()
    cached = _solid_scans.get(path)
    if cached and cached[:2] == (stat.st_mtime_ns, stat.st_size):
        return cached[2]
    data = path.read_bytes().upper()
    found = any(word in data for word in (b"MANIFOLD_SOLID_BREP", b"BREP_WITH_VOIDS", b"FACETED_BREP"))
    _solid_scans[path] = (stat.st_mtime_ns, stat.st_size, found)
    return found


def _scene_views(rel: Path, path: Path, sketches: set) -> list:
    """The views of the scene at path: itself (in 3D, or the drawing of a 2D scene), the
    views its .tp.json names, and views drawn on the fly that have a sketch waiting."""
    name = rel.as_posix()
    try:
        kind = "model" if has_solids(path) else "sheet"
    except OSError:
        return []
    out = [View(name, kind, path)]
    try:
        meta = scenes.read_meta(path)
        named = [v["name"] for v in scenes.named_views(meta)]
    except ValueError:
        meta, named = {}, []  # opening the scene says what is wrong
    out += [View(f"{name}#{v}", "sheet", path, v) for v in named]
    for sketch in sketches:
        if sketch.startswith(path.name + "#"):
            v = sketch[len(path.name) + 1: -len(SKETCH_SUFFIX)]
            if v not in named and scenes.view_spec(meta, v) is not None:
                out.append(View(f"{name}#{v}", "sheet", path, v))
    return out


def list_views(directory: Path) -> list:
    """The views in directory and all the folders below it, which the project page shows as
    a tree: [View], in order of their names."""
    found = []
    for root, dirs, files in os.walk(directory):
        rel = Path(root).relative_to(directory)
        dirs[:] = [d for d in dirs if not d.startswith(".") and d not in SKIP_DIRS]
        sketches = {f for f in files if f.endswith(SKETCH_SUFFIX)}
        for f in files:
            kind = KINDS.get(Path(f).suffix.lower())
            if not kind or f.startswith(".") or f.endswith(SKETCH_SUFFIX):
                continue
            if kind == "scene":
                found += _scene_views(rel / f, Path(root) / f, sketches)
            else:
                found.append(View((rel / f).as_posix(), kind, Path(root) / f))
    return sorted(found, key=lambda v: v.name)


def view_url(name: str) -> str:
    return "/view/" + quote(name)


def _mtime(path: Path) -> Optional[str]:
    try:
        return str(path.stat().st_mtime_ns)
    except FileNotFoundError:
        return None


class Scenes:
    """Scenes read from their STEP files, and the views drawn from them, kept while their
    files stay the same. OpenCascade's work is done one at a time."""

    def __init__(self):
        self._lock = threading.Lock()
        self._read = OrderedDict()  # path -> ((mtime, size), Scene)
        self._drawn = OrderedDict()  # (name, mtime) -> bytes

    def _scene(self, path: Path):
        from . import scene as S  # OCP: only once there is a scene to show
        stat = path.stat()
        stamp = (stat.st_mtime_ns, stat.st_size)
        cached = self._read.get(path)
        if cached and cached[0] == stamp:
            self._read.move_to_end(path)
            return cached[1]
        sc = S.read_step(path)
        self._read[path] = (stamp, sc)
        while len(self._read) > SCENES_KEPT:
            self._read.popitem(last=False)
        return sc

    def output(self, view: View, frame: Optional[int] = None) -> bytes:
        """The view's file for its page: a .glb for the 3D view, a sheet's SVG otherwise; of
        one of the scene's frames, if given. Raises ValueError with what is wrong."""
        key = (view.name, view.mtime(), frame)
        with self._lock:
            if key in self._drawn:
                self._drawn.move_to_end(key)
                return self._drawn[key]
            try:
                from . import drawing as D, scene as S
            except ImportError:
                raise ValueError("showing STEP files needs OpenCascade's Python binding, OCP: "
                                 "pip install cadquery-ocp (CadQuery and build123d install it)") from None
            meta = scenes.read_meta(view.path)
            path, at, entry = view.path, None, None
            framed = scenes.frames(view.path, meta).get("frames", [])
            if frame is not None:
                if not 0 <= frame < len(framed):
                    raise ValueError(f"{view.path.name} has no frame {frame}")
                entry = framed[frame]
                path, at = entry["step"], entry["at"]
            sc = self._scene(path)
            hidden = S.find_nodes(sc, meta.get("hidden") or [])
            if view.kind == "model":
                colors = {k: D.rgb(c) for k, c in D.colors(sc).items()}
                data = S.glb(sc, colors, hidden)
            else:
                spec = None
                if view.view is not None:
                    spec = scenes.view_spec(meta, view.view)
                    if spec is None:
                        raise ValueError(f"{view.path.name} has no view {view.view!r}")
                fields = scenes.fields(view.path, meta, entry)
                sheet = D.draw(sc, spec, meta, view.name, hidden=hidden, fields=fields,
                               ranges=self._ranges(view, meta, framed))
                if at is not None:
                    sheet.view["frame"] = scenes.frame_label(at)
                data = sheet.tostring().encode("utf-8")
            self._drawn[key] = data
            while len(self._drawn) > DRAWN_KEPT:
                self._drawn.popitem(last=False)
            return data

    def _ranges(self, view: View, meta: dict, framed: list) -> dict:
        """Each field's lowest and highest value over all the scene's frames, so that its colours
        mean the same in each: {name: (lo, hi)}."""
        from . import fields as F
        key = ("ranges", view.path, view.mtime())
        if key in self._drawn:
            return self._drawn[key]
        out = {}
        for entry in [None, *framed]:
            for f in scenes.fields(view.path, meta, entry):
                try:
                    mesh = F.read_vtu(f["path"])
                except ValueError:
                    continue
                values = mesh.data.get(f["value"]) or []
                values = [sum(c * c for c in v) ** 0.5 if isinstance(v, tuple) else v for v in values]
                if values:
                    lo, hi = out.get(f["name"], (min(values), max(values)))
                    out[f["name"]] = (min(lo, *values), max(hi, *values))
        self._drawn[key] = out
        return out


class ViewerServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address, directory: Path, initial: Optional[str], pen=None):
        super().__init__(address, Handler)
        self.directory = directory
        self.initial = initial
        self.pen = pen  # a tablet the pages can connect, such as pen.RemarkablePen, or None
        self.pen_hub = PenHub(pen.size_mm, getattr(pen, "pixels", None)) if pen is not None else None
        port = self.server_address[1]
        self.allowed_hosts = {f"127.0.0.1:{port}", f"localhost:{port}"}
        self._models = {}  # path -> (mtime, size, .glb): slicer projects already turned into models
        self._models_lock = threading.Lock()
        self.scenes = Scenes()

    def view_file(self, view: View, frame: Optional[int] = None) -> bytes:
        """A view's file as its page reads it. A slicer project becomes a model, a .glb
        (plate.py), once for each version of it, and a scene its 3D view or a sheet drawn from
        it, of one of its frames if given. Raises ValueError if that fails."""
        if view.scene:
            return self.scenes.output(view, frame)
        path = view.path
        if path.suffix.lower() != ".3mf":
            return path.read_bytes()
        stat = path.stat()
        with self._models_lock:
            cached = self._models.get(path)
            if cached and cached[:2] == (stat.st_mtime_ns, stat.st_size):
                return cached[2]
        data = plate.to_glb(path)
        with self._models_lock:
            self._models[path] = (stat.st_mtime_ns, stat.st_size, data)
            while len(self._models) > MODELS_KEPT:
                self._models.pop(next(iter(self._models)))
        return data

    def window_closed(self) -> None:
        """A page's pen stream ended: let the tablet go if no window comes back soon."""
        def check():
            if not self.pen_hub.has_clients():
                self.pen.disconnect()
        if not self.pen_hub.has_clients():
            timer = threading.Timer(LAST_WINDOW, check)
            timer.daemon = True
            timer.start()

    @property
    def url(self) -> str:
        """The page to open first: the view asked for, or the project's list of views."""
        base = f"http://127.0.0.1:{self.server_address[1]}"
        return base + (view_url(self.initial) if self.initial else "/")


class Handler(BaseHTTPRequestHandler):
    server: ViewerServer
    protocol_version = "HTTP/1.1"

    def log_message(self, format, *args):  # keep the terminal quiet
        pass

    def _send(self, status: int, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, obj, status: int = 200) -> None:
        self._send(status, json.dumps(obj).encode(), "application/json")

    def _error(self, status: int, message: str) -> None:
        self._json({"error": message}, status)

    def _allowed(self) -> bool:
        # Refuse requests addressed to another host name (DNS rebinding).
        if self.headers.get("Host") in self.server.allowed_hosts:
            return True
        self._error(403, "unexpected Host header")
        return False

    def _from_page(self) -> bool:
        # Browsers only send custom headers cross-origin after a preflight we never approve.
        if self.headers.get("X-Tracepaper") == "1":
            return True
        self._error(403, "missing X-Tracepaper header")
        return False

    def _view(self, name: str) -> Optional[View]:
        """A listed view, or a view of a listed scene drawn on the fly ("a.step#x=10"). Only
        those, so nothing outside the project is reachable."""
        listed = list_views(self.server.directory)
        found = next((v for v in listed if v.name == name), None)
        scene, view = scenes.split_name(name)
        if found is None and view is not None:
            base = next((v for v in listed if v.name == scene and v.view is None and v.scene), None)
            try:
                if base is not None and scenes.view_spec(scenes.read_meta(base.path), view) is not None:
                    found = View(name, "sheet", base.path, view)
            except ValueError:
                pass
        return found

    def _listing(self) -> dict:
        views = []
        for v in list_views(self.server.directory):
            mtime = v.mtime()
            if mtime is None:
                continue
            sketch = _mtime(v.sketch())
            views.append({"name": v.name, "kind": v.kind, "mtime": mtime,
                          "sketch": {"mtime": sketch} if sketch else None})
        return {"dir": str(self.server.directory), "initial": self.server.initial, "views": views}

    def _static(self, rel: str) -> None:
        path = (VIEWER_DIR / rel).resolve()
        if not path.is_relative_to(VIEWER_DIR) or path.suffix not in CONTENT_TYPES or not path.is_file():
            return self._error(404, "not found")
        self._send(200, path.read_bytes(), CONTENT_TYPES[path.suffix])

    def do_GET(self) -> None:
        if not self._allowed():
            return
        url = urlsplit(self.path)
        path = url.path
        if path == "/":
            return self._static("project.html")
        if path.startswith("/view/"):
            view = self._view(unquote(path[len("/view/"):]))
            return self._static(PAGES[view.kind]) if view else self._error(404, "no such view")
        if path.startswith("/viewer/"):
            return self._static(path[len("/viewer/"):])
        if path == "/api/views":
            return self._json(self._listing())
        if path == "/api/pen":
            if self.server.pen_hub is None:
                return self._error(404, "no pen")
            query = parse_qs(url.query)
            client = query.get("client", [""])[0][:64] or uuid.uuid4().hex
            return self._stream_pen(self.server.pen_hub, client, query.get("draws", ["1"])[0] == "1")
        if path.startswith("/api/frames/"):
            view = self._view(unquote(path[len("/api/frames/"):]))
            if view is None:
                return self._error(404, "no such view")
            if not view.scene:
                return self._json({})
            try:
                framed = scenes.frames(view.path, scenes.read_meta(view.path))
            except ValueError as e:
                return self._error(422, str(e))
            if framed:
                framed["frames"] = [{"at": f["at"], "values": f["values"]} for f in framed["frames"]]
            return self._json(framed)
        for prefix, sketch in (("/api/file/", False), ("/api/sketch/", True)):
            if path.startswith(prefix):
                view = self._view(unquote(path[len(prefix):]))
                if view is None:
                    return self._error(404, "no such view")
                frame = parse_qs(url.query).get("frame", [None])[0]
                try:
                    frame = None if frame is None else int(frame)
                    data = view.sketch().read_bytes() if sketch else self.server.view_file(view, frame)
                except FileNotFoundError:
                    return self._error(404, "no sketch for this view" if sketch else "the view's file is gone")
                except ValueError as e:
                    return self._error(422, str(e))
                return self._send(200, data, VIEW_TYPES["sheet" if sketch else view.kind])
        self._error(404, "not found")

    def _stream_pen(self, hub: PenHub, client: str, draws: bool) -> None:
        """Server-sent events: "status" when the tablet connects or drops or another view
        becomes active, "pen" per pen frame (only to the active view)."""
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.close_connection = True
        q = hub.subscribe(client, draws)
        try:
            while True:
                try:
                    items = [q.get(timeout=KEEPALIVE)]
                except queue.Empty:
                    self.wfile.write(b": keep-alive\n\n")  # notices viewers that went away
                    self.wfile.flush()
                    continue
                while not q.empty() and len(items) < 64:
                    items.append(q.get_nowait())
                if any(kind == "close" for kind, _ in items):
                    return
                self.wfile.write("".join(f"event: {kind}\ndata: {json.dumps(data)}\n\n"
                                         for kind, data in items).encode())
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            pass
        finally:
            hub.unsubscribe(q)
            self.server.window_closed()

    def _body(self, limit: int) -> Optional[bytes]:
        try:
            length = int(self.headers.get("Content-Length", ""))
        except ValueError:
            self._error(411, "Content-Length required")
            return None
        if length > limit:
            self._error(413, "too large")
            return None
        return self.rfile.read(length)

    def do_POST(self) -> None:
        """/api/pen/connect connects the tablet and makes the asking view the one it draws in,
        taking the tablet over from any other viewer; /api/pen/active only does the latter, and
        /api/pen/disconnect lets the tablet go. Each takes {"client": id} and returns the status.
        /api/pen/view?client=id&done=time takes a PNG of the view the pen draws in, for the
        tablet's screen, with the strokes up to the one whose pen touched down at time in it."""
        if not self._allowed() or not self._from_page():
            return
        url = urlsplit(self.path)
        action = url.path.removeprefix("/api/pen/")
        hub, pen = self.server.pen_hub, self.server.pen
        if hub is None or action not in ("connect", "active", "disconnect", "view"):
            return self._error(404, "not found")
        if action == "view":
            body = self._body(MAX_VIEW_BYTES)
            if body is None:
                return
            query = parse_qs(url.query)
            if query.get("client", [""])[0] != hub.status["active"]:
                return self._error(409, "the pen draws in another window")
            try:
                done = int(query.get("done", ["0"])[0])
            except ValueError:
                return self._error(400, "done must be a number")
            return self._json({"shown": pen.show(body, done)})
        body = self._body(1024)
        if body is None:
            return
        try:
            client = str(json.loads(body)["client"])[:64]
        except (ValueError, KeyError, TypeError):
            return self._error(400, "expected {\"client\": id}")
        if action == "disconnect":
            pen.disconnect()
        else:
            hub.set_active(client)
            if action == "connect":
                pen.connect(hub)
        self._json(hub.status)

    def do_PUT(self) -> None:
        if not self._allowed() or not self._from_page():
            return
        path = urlsplit(self.path).path
        if not path.startswith("/api/sketch/"):
            return self._error(404, "not found")
        view = self._view(unquote(path[len("/api/sketch/"):]))
        if view is None:
            return self._error(404, "no such view")
        body = self._body(MAX_SKETCH_BYTES)
        if body is None:
            return
        try:
            root = ET.fromstring(body)
        except ET.ParseError as err:
            return self._error(400, f"not well-formed XML: {err}")
        if root.tag != f"{{{SVG_NS}}}svg":
            return self._error(400, "not an SVG document")
        target = view.sketch()
        fd, tmp = tempfile.mkstemp(dir=target.parent, prefix=".tracepaper-", suffix=".tmp")
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(body)
            os.replace(tmp, target)
        except BaseException:
            os.unlink(tmp)
            raise
        print(f"saved {target}", flush=True)
        self._json({"mtime": _mtime(target)})


def make_server(target: Path, port: int = 0, pen=None) -> ViewerServer:
    """A server for a project folder, or for the folder of one view (which it then opens).

    Port 0 picks a free port. pen is a tablet (see pen.RemarkablePen) that the pages can
    connect and whose input then goes to the viewer.
    """
    target = target.resolve()
    if target.is_dir():
        directory, initial = target, None
    elif target.is_file() and (target.suffix.lower() in KINDS or target.name.endswith(SKETCH_SUFFIX)):
        directory, initial = target.parent, target.name
        if initial.endswith(SKETCH_SUFFIX):
            initial = view_of_sketch(initial)
    else:
        raise SystemExit(f"tracepaper: {target} is not a directory, a .step, .svg, .glb or .3mf file")
    try:
        return ViewerServer(("127.0.0.1", port), directory, initial, pen)
    except OSError:
        if port == 0:
            raise
        return ViewerServer(("127.0.0.1", 0), directory, initial, pen)


def serve(target: Path, port: int, open_browser: bool, pen=None) -> None:
    server = make_server(target, port, pen)
    print(f"Tracepaper: {server.directory}\n  {server.url}  (Ctrl+C to stop)", flush=True)
    if open_browser:
        threading.Thread(target=webbrowser.open, args=(server.url,), daemon=True).start()
    signal.signal(signal.SIGTERM, signal.default_int_handler)  # stop the same way when killed
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        if pen is not None:
            if pen.running:
                print("Starting the tablet's app again…", flush=True)
            pen.disconnect()
            server.pen_hub.close()
        server.server_close()
