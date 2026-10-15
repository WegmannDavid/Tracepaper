"""Local HTTP server behind the viewer: serves the viewer, lists and serves sheets,
and writes sketch files when the person presses Save (SPEC.md §3, §5)."""
from __future__ import annotations

import json
import os
import tempfile
import threading
import webbrowser
import xml.etree.ElementTree as ET
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Optional
from urllib.parse import unquote, urlsplit

SVG_NS = "http://www.w3.org/2000/svg"
SKETCH_SUFFIX = ".sketch.svg"
MAX_SKETCH_BYTES = 50 * 1024 * 1024
VIEWER_DIR = Path(__file__).resolve().parent / "viewer"
STATIC = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/viewer/app.js": ("app.js", "text/javascript; charset=utf-8"),
    "/viewer/annotations.js": ("annotations.js", "text/javascript; charset=utf-8"),
    "/viewer/style.css": ("style.css", "text/css; charset=utf-8"),
}


def sketch_path(sheet: Path) -> Path:
    return sheet.with_name(sheet.name[: -len(".svg")] + SKETCH_SUFFIX)


def list_sheets(directory: Path) -> list:
    return sorted(p for p in directory.glob("*.svg") if not p.name.endswith(SKETCH_SUFFIX) and p.is_file())


def _mtime(path: Path) -> Optional[str]:
    try:
        return str(path.stat().st_mtime_ns)
    except FileNotFoundError:
        return None


class ViewerServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address, directory: Path, initial: Optional[str]):
        super().__init__(address, Handler)
        self.directory = directory
        self.initial = initial
        port = self.server_address[1]
        self.allowed_hosts = {f"127.0.0.1:{port}", f"localhost:{port}"}

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.server_address[1]}/"


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

    def _sheet(self, name: str) -> Optional[Path]:
        # Only names from the listing, so nothing outside the directory is reachable.
        return next((p for p in list_sheets(self.server.directory) if p.name == name), None)

    def _listing(self) -> dict:
        sheets = []
        for p in list_sheets(self.server.directory):
            mtime = _mtime(p)
            if mtime is None:
                continue
            sketch = _mtime(sketch_path(p))
            sheets.append({"name": p.name, "mtime": mtime, "sketch": {"mtime": sketch} if sketch else None})
        return {"dir": str(self.server.directory), "initial": self.server.initial, "sheets": sheets}

    def do_GET(self) -> None:
        if not self._allowed():
            return
        path = urlsplit(self.path).path
        if path in STATIC:
            name, content_type = STATIC[path]
            return self._send(200, (VIEWER_DIR / name).read_bytes(), content_type)
        if path == "/api/sheets":
            return self._json(self._listing())
        for prefix, sketch in (("/api/sheet/", False), ("/api/sketch/", True)):
            if path.startswith(prefix):
                sheet = self._sheet(unquote(path[len(prefix):]))
                if sheet is None:
                    return self._error(404, "no such sheet")
                try:
                    data = (sketch_path(sheet) if sketch else sheet).read_bytes()
                except FileNotFoundError:
                    return self._error(404, "no sketch for this sheet")
                return self._send(200, data, "image/svg+xml")
        self._error(404, "not found")

    def do_PUT(self) -> None:
        if not self._allowed():
            return
        # Browsers only send custom headers cross-origin after a preflight we never approve.
        if self.headers.get("X-Tracepaper") != "1":
            return self._error(403, "missing X-Tracepaper header")
        path = urlsplit(self.path).path
        if not path.startswith("/api/sketch/"):
            return self._error(404, "not found")
        sheet = self._sheet(unquote(path[len("/api/sketch/"):]))
        if sheet is None:
            return self._error(404, "no such sheet")
        try:
            length = int(self.headers.get("Content-Length", ""))
        except ValueError:
            return self._error(411, "Content-Length required")
        if length > MAX_SKETCH_BYTES:
            return self._error(413, "sketch too large")
        body = self.rfile.read(length)
        try:
            root = ET.fromstring(body)
        except ET.ParseError as err:
            return self._error(400, f"not well-formed XML: {err}")
        if root.tag != f"{{{SVG_NS}}}svg":
            return self._error(400, "not an SVG document")
        target = sketch_path(sheet)
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


def make_server(target: Path, port: int = 0) -> ViewerServer:
    """A server for a sheet file or a directory of sheets. Port 0 picks a free port."""
    target = target.resolve()
    if target.is_dir():
        directory, initial = target, None
    elif target.is_file() and target.suffix == ".svg":
        directory, initial = target.parent, target.name
        if initial.endswith(SKETCH_SUFFIX):
            initial = initial[: -len(SKETCH_SUFFIX)] + ".svg"
    else:
        raise SystemExit(f"tracepaper: {target} is not a directory or an .svg file")
    try:
        return ViewerServer(("127.0.0.1", port), directory, initial)
    except OSError:
        if port == 0:
            raise
        return ViewerServer(("127.0.0.1", 0), directory, initial)


def serve(target: Path, port: int, open_browser: bool) -> None:
    server = make_server(target, port)
    print(f"Tracepaper: {server.directory}\n  {server.url}  (Ctrl+C to stop)", flush=True)
    if open_browser:
        threading.Thread(target=webbrowser.open, args=(server.url,), daemon=True).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
