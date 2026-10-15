"""Local HTTP server behind the viewer.

It lists a project's views (2D sheets, .svg, and 3D models, .glb) and serves
them with the pages that show them, writes sketch files when the person presses
Save (SPEC.md §3, §5), and connects a tablet (pen.py) when a page asks, passing
its pen input to the view the person last used.
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
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Optional
from urllib.parse import parse_qs, quote, unquote, urlsplit

from .pen import PenHub

SVG_NS = "http://www.w3.org/2000/svg"
SKETCH_SUFFIX = ".sketch.svg"
KINDS = {".svg": "sheet", ".glb": "model"}
SKIP_DIRS = {"node_modules", "__pycache__"}
MAX_DEPTH = 3  # how many folders below the project folder are searched for views
MAX_SKETCH_BYTES = 50 * 1024 * 1024
MAX_VIEW_BYTES = 8 * 1024 * 1024  # a picture of the view for the tablet's screen
KEEPALIVE = 5  # seconds between keep-alives on the pen stream, which notice closed windows
LAST_WINDOW = 10  # seconds after the last window closed that the tablet is let go
VIEWER_DIR = Path(__file__).resolve().parent / "viewer"
PAGES = {"sheet": "index.html", "model": "model.html"}
CONTENT_TYPES = {".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8",
                 ".css": "text/css; charset=utf-8"}
VIEW_TYPES = {"sheet": "image/svg+xml", "model": "model/gltf-binary"}


def sketch_path(sheet: Path) -> Path:
    return sheet.with_name(sheet.name[: -len(".svg")] + SKETCH_SUFFIX)


def list_views(directory: Path) -> list:
    """(name, path, kind) of the sheets and models in directory and the folders below it.

    name is the path relative to directory, with "/" between folders.
    """
    found = []
    for root, dirs, files in os.walk(directory):
        rel = Path(root).relative_to(directory)
        depth = len(rel.parts)
        dirs[:] = [d for d in dirs if not d.startswith(".") and d not in SKIP_DIRS] if depth < MAX_DEPTH else []
        for f in files:
            kind = KINDS.get(Path(f).suffix.lower())
            if kind and not f.startswith(".") and not f.endswith(SKETCH_SUFFIX):
                found.append(((rel / f).as_posix(), Path(root) / f, kind))
    return sorted(found)


def view_url(name: str) -> str:
    return "/view/" + quote(name)


def _mtime(path: Path) -> Optional[str]:
    try:
        return str(path.stat().st_mtime_ns)
    except FileNotFoundError:
        return None


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

    def _view(self, name: str) -> Optional[tuple]:
        """(path, kind) of a listed view. Only listed names, so nothing outside the project is reachable."""
        return next(((p, kind) for n, p, kind in list_views(self.server.directory) if n == name), None)

    def _listing(self) -> dict:
        views = []
        for name, path, kind in list_views(self.server.directory):
            mtime = _mtime(path)
            if mtime is None:
                continue
            sketch = _mtime(sketch_path(path)) if kind == "sheet" else None
            views.append({"name": name, "kind": kind, "mtime": mtime,
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
            return self._static(PAGES[view[1]]) if view else self._error(404, "no such view")
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
        for prefix, sketch in (("/api/file/", False), ("/api/sketch/", True)):
            if path.startswith(prefix):
                view = self._view(unquote(path[len(prefix):]))
                if view is None or (sketch and view[1] != "sheet"):
                    return self._error(404, "no such view")
                try:
                    data = (sketch_path(view[0]) if sketch else view[0]).read_bytes()
                except FileNotFoundError:
                    return self._error(404, "no sketch for this sheet")
                return self._send(200, data, VIEW_TYPES["sheet" if sketch else view[1]])
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
        if view is None or view[1] != "sheet":
            return self._error(404, "no such sheet")
        body = self._body(MAX_SKETCH_BYTES)
        if body is None:
            return
        try:
            root = ET.fromstring(body)
        except ET.ParseError as err:
            return self._error(400, f"not well-formed XML: {err}")
        if root.tag != f"{{{SVG_NS}}}svg":
            return self._error(400, "not an SVG document")
        target = sketch_path(view[0])
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
    elif target.is_file() and target.suffix.lower() in KINDS:
        directory, initial = target.parent, target.name
        if initial.endswith(SKETCH_SUFFIX):
            initial = initial[: -len(SKETCH_SUFFIX)] + ".svg"
    else:
        raise SystemExit(f"tracepaper: {target} is not a directory, an .svg or a .glb file")
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
