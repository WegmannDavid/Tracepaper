"""A reMarkable 2 as a drawing tablet for the viewer, connected from its pages.

The tablet's pen sensor reports through Linux's input system. Tracepaper reads
its raw events over ssh and passes pen positions, in millimetres on the tablet
as it is held, to the viewer, which maps the tablet onto the part of the sheet
in view. The computer's own pointer is never involved, so the mouse keeps
working as usual.

While connected, the tablet's app is stopped, so strokes don't also land in a
document open there; the e-ink keeps showing what it showed. The app starts
again when Tracepaper disconnects, or by itself SILENCE seconds after this
computer goes quiet (crash, cable pulled, WiFi lost).

While connected, the tablet also shows the view that the pen draws in: the
viewer sends a picture of it whenever it changes (show), and a small program on
the tablet (tablet/main.cpp, copied there when connecting) puts it on the e-ink
where the pen's coordinates are, so the two line up.

Only one viewer can have the tablet. Connecting takes it over from any other
Tracepaper viewer that has it, on this computer or another; that one hears it
was taken and doesn't try to get it back.
"""
from __future__ import annotations

import hashlib
import os
import queue
import shlex
import struct
import subprocess
import threading
from pathlib import Path
from typing import Optional

EV_SYN, EV_KEY, EV_ABS = 0x00, 0x01, 0x03
SYN_REPORT = 0
ABS_X, ABS_Y, ABS_PRESSURE = 0x00, 0x01, 0x18
BTN_TOOL_PEN, BTN_TOOL_RUBBER, BTN_TOUCH = 0x140, 0x141, 0x14A
EVENT = struct.Struct("<IIHHi")  # struct input_event on the tablet's 32-bit ARM

# The reMarkable 2's pen sensor, in its units (100 per mm). Held upright, its
# x runs up the screen and its y from left to right.
SENSOR_X, SENSOR_Y = 20967, 15725
UNITS_PER_MM = 100
MAX_PRESSURE = 4095
# Its screen, upright, in pixels. The pen sensor covers it exactly.
SCREEN_W, SCREEN_H = 1404, 1872

HOLD = {0: "upright", 90: "landscape, power button on the right", 180: "upside down",
        270: "landscape, power button on the left"}

READY = b"TRACEPAPER-READY\n"
SILENCE = 20  # seconds without a heartbeat after which the tablet restarts its app (session.sh)
HEARTBEAT = 5
RETRY = 3.0  # seconds between connection attempts
# The session's exit statuses: another viewer took the tablet over / has it; the person
# disconnected on the tablet.
TAKEN, BUSY, LEFT = 75, 76, 77
TABLET = Path(__file__).resolve().parent / "tablet"
SCRIPT = (TABLET / "session.sh").read_text()  # what runs on the tablet while connected
SCREEN = TABLET / "tracepaper-screen"  # shows the view there; built by tablet/build.sh
REMOTE_SCREEN = "/home/root/.tracepaper/tracepaper-screen"


def session(**settings) -> str:
    """The session script with its settings (see session.sh), as ssh runs it on the tablet."""
    defaults = {"program": REMOTE_SCREEN, "silence": SILENCE, "status_taken": TAKEN, "status_busy": BUSY,
                "status_left": LEFT}
    settings = {**settings, **{k: v for k, v in defaults.items() if k not in settings}}
    return " ".join(f"{k}={shlex.quote(str(v))}" for k, v in settings.items()) + "\n" + SCRIPT


# Copies tracepaper-screen onto the tablet, from its input, unless it is there already.
DEPLOY = (f'f={REMOTE_SCREEN}; [ "$(sha256sum < $f 2>/dev/null)" = "{{sha}}  -" ] && exit 0; '
          'mkdir -p "${{f%/*}}" && cat > $f.new && chmod +x $f.new && mv $f.new $f')

SSH = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=4", "-o", "ServerAliveInterval=3",
       "-o", "ServerAliveCountMax=2", "-o", "LogLevel=ERROR"]


def tablet_size(rotation: int) -> tuple:
    """(width, height) in mm of the tablet held turned clockwise by rotation degrees."""
    w, h = SENSOR_Y / UNITS_PER_MM, SENSOR_X / UNITS_PER_MM  # upright
    return (h, w) if rotation in (90, 270) else (w, h)


def screen_size(rotation: int) -> tuple:
    """(width, height) in pixels of the tablet's screen as it is held."""
    return (SCREEN_H, SCREEN_W) if rotation in (90, 270) else (SCREEN_W, SCREEN_H)


def orient(sx: int, sy: int, rotation: int) -> tuple:
    """Sensor position as (x right, y down) in mm on the tablet as held."""
    if rotation == 90:  # top edge on the right
        x, y = sx, sy
    elif rotation == 180:
        x, y = SENSOR_Y - sy, sx
    elif rotation == 270:  # top edge on the left
        x, y = SENSOR_X - sx, SENSOR_Y - sy
    else:
        x, y = sy, SENSOR_X - sx
    return x / UNITS_PER_MM, y / UNITS_PER_MM


class Decoder:
    """Turns the pen sensor's raw input events into frames of pen state.

    A frame is {"x", "y"} in mm on the tablet as held, "p" pressure from 0 to 1,
    "tool" ("pen", "eraser", or None when the pen is out of range), "touch", and
    "done": when the pen touched down for the last stroke that has ended, in
    microseconds of the input events' clock (0 before the first), which tells the
    tablet's screen which strokes a picture has (tablet/main.cpp).
    One frame per sensor report, and only when something changed.
    """

    def __init__(self, rotation: int = 90):
        self.rotation = rotation
        self.abs = {ABS_X: 0, ABS_Y: 0, ABS_PRESSURE: 0}
        self.keys = {BTN_TOOL_PEN: 0, BTN_TOOL_RUBBER: 0, BTN_TOUCH: 0}
        self.rest = b""
        self.last = None
        self.down_at = self.done = 0

    def feed(self, data: bytes) -> list:
        self.rest += data
        end = len(self.rest) - len(self.rest) % EVENT.size
        frames = []
        for sec, usec, kind, code, value in EVENT.iter_unpack(self.rest[:end]):
            if kind == EV_ABS and code in self.abs:
                self.abs[code] = value
            elif kind == EV_KEY and code in self.keys:
                if code == BTN_TOUCH and value and not self.keys[BTN_TOUCH]:
                    self.down_at = sec * 1_000_000 + usec
                elif code == BTN_TOUCH and not value and self.keys[BTN_TOUCH]:
                    self.done = self.down_at
                self.keys[code] = value
            elif kind == EV_SYN and code == SYN_REPORT:
                frame = self._frame()
                if frame != self.last:
                    frames.append(frame)
                    self.last = frame
        self.rest = self.rest[end:]
        return frames

    def _frame(self) -> dict:
        tool = "eraser" if self.keys[BTN_TOOL_RUBBER] else "pen" if self.keys[BTN_TOOL_PEN] else None
        x, y = orient(self.abs[ABS_X], self.abs[ABS_Y], self.rotation)
        return {"x": round(x, 2), "y": round(y, 2), "p": round(self.abs[ABS_PRESSURE] / MAX_PRESSURE, 3),
                "tool": tool, "touch": bool(self.keys[BTN_TOUCH]) and tool is not None, "done": self.done}


class PenHub:
    """Hands the connection status to every open view, and pen frames to the active one.

    The active view is the one the person last used with the mouse (set_active),
    or the view that can draw that they opened last. When it closes, the newest
    other view that can draw takes over.
    """

    def __init__(self, size_mm: tuple, pixels: Optional[tuple] = None):
        self.size = size_mm
        # pixels: of the tablet's screen as held; screen: whether it shows the view right now.
        self.status = {"state": "off", "message": "", "width": size_mm[0], "height": size_mm[1],
                       "pixels": pixels, "screen": False, "active": None}
        self._clients: dict = {}  # client id -> (queue, whether it draws), oldest first
        self._lock = threading.Lock()

    def subscribe(self, client: Optional[str] = None, draws: bool = True) -> queue.Queue:
        client = client or os.urandom(8).hex()
        q = queue.Queue(maxsize=5000)
        with self._lock:
            old = self._clients.pop(client, None)
            if old is not None:  # the same view reconnecting: end its old stream
                _put(old[0], ("close", None))
            self._clients[client] = (q, draws)
            before = self.status["active"]
            if draws or before not in self._clients:
                self._activate(client if draws else self._fallback())
            if self.status["active"] == before:  # otherwise _activate has sent everyone the status
                q.put(("status", self.status))
        return q

    def unsubscribe(self, q: queue.Queue) -> None:
        with self._lock:
            client = next((c for c, (cq, _) in self._clients.items() if cq is q), None)
            if client is None:
                return
            del self._clients[client]
            if self.status["active"] == client:
                self._activate(self._fallback())

    def has_clients(self) -> bool:
        with self._lock:
            return bool(self._clients)

    def set_active(self, client: str) -> None:
        with self._lock:
            self._activate(client)

    def _fallback(self) -> Optional[str]:
        drawing = [c for c, (_, draws) in self._clients.items() if draws]
        return drawing[-1] if drawing else None

    def _activate(self, client: Optional[str]) -> None:
        if self.status["active"] != client:
            self.status = {**self.status, "active": client}
            self._broadcast(("status", self.status))

    def _broadcast(self, item) -> None:
        for q, _ in self._clients.values():
            _put(q, item)

    def publish_status(self, state: str, message: str = "", screen: bool = False) -> None:
        with self._lock:
            self.status = {**self.status, "state": state, "message": message, "screen": screen}
            self._broadcast(("status", self.status))

    def publish_frame(self, frame: dict) -> None:
        with self._lock:
            active = self._clients.get(self.status["active"])
            if active is not None and active[1]:  # a 3D view, say, takes the pen but doesn't draw
                _put(active[0], ("pen", frame))

    def close(self) -> None:
        with self._lock:
            self._broadcast(("close", None))


def _put(q: queue.Queue, item) -> None:
    try:
        q.put_nowait(item)
    except queue.Full:  # a view that stopped reading
        pass


class RemarkablePen:
    """Streams a reMarkable 2's pen into a PenHub between connect and disconnect,
    reconnecting by itself when the connection drops."""

    def __init__(self, host: str = "remarkable", rotation: int = 90, command: Optional[list] = None,
                 screen: Optional[Path] = SCREEN):
        self.host = host
        self.rotation = rotation
        self.size_mm = tablet_size(rotation)
        self.pixels = screen_size(rotation)
        self.command = command or [*SSH, host]  # runs a script on the tablet, given as last argument
        self.screen = screen if screen is not None and screen.is_file() else None  # the program to show the view
        self.owner = os.urandom(8).hex()  # tells this viewer's sessions on the tablet from other viewers'
        self._lock = threading.Lock()
        self._write = threading.Lock()  # one message at a time into the session: heartbeats, pictures
        self._running = False  # a connection is up or being tried
        self._connected = False
        self._showing = False  # the tablet shows the view
        self._take = False  # the next attempt takes the tablet over from another viewer
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._proc: Optional[subprocess.Popen] = None
        self._thread: Optional[threading.Thread] = None

    @property
    def running(self) -> bool:
        return self._running

    def connect(self, hub: PenHub) -> None:
        """Connect, taking the tablet over from any other viewer that has it."""
        with self._lock:
            if self._running and not self._stop.is_set():
                if not self._connected:  # waiting to try again: try now
                    self._take = True
                    self._wake.set()
                return
            self._running, self._take = True, True
            self._stop, self._wake = threading.Event(), threading.Event()
            self._thread = threading.Thread(target=self._run, args=(hub, self._stop, self._wake),
                                            name="tracepaper-pen", daemon=True)
            self._thread.start()

    def disconnect(self, timeout: float = 15) -> None:
        """Disconnect; the tablet starts its app again."""
        with self._lock:
            self._stop.set()
            self._wake.set()
            proc, thread = self._proc, self._thread
        if proc is not None:
            _close(proc.stdin)  # the script on the tablet sees the end of its input and cleans up
            try:
                proc.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                proc.kill()
        if thread is not None:
            thread.join(timeout=5)

    def show(self, png: bytes, done: int = 0) -> bool:
        """Show a picture of the whole screen, as the tablet is held, on the tablet, with
        the strokes up to the one that touched down at done (see Decoder) drawn in it.
        False if it isn't connected or can't show pictures."""
        with self._lock:
            proc = self._proc if self._connected and self._showing else None
        if proc is None:
            return False
        with self._write:
            try:
                proc.stdin.write(b"view %d %d\n" % (len(png), done) + png)
                proc.stdin.flush()
            except (OSError, ValueError):
                return False
        return True

    def _deploy(self) -> bool:
        """Put the screen program onto the tablet unless it is there already; whether it is."""
        if self.screen is None:
            return False
        data = self.screen.read_bytes()
        try:
            done = subprocess.run([*self.command, DEPLOY.format(sha=hashlib.sha256(data).hexdigest())],
                                  input=data, capture_output=True, timeout=60)
        except (OSError, subprocess.TimeoutExpired):
            return False
        return done.returncode == 0

    def _run(self, hub: PenHub, stop: threading.Event, wake: threading.Event) -> None:
        message = ""
        while not stop.is_set():
            with self._lock:
                take, self._take = self._take, False
            hub.publish_status("connecting", f"connecting to {self.host}")
            message, again = self._session(hub, stop, take)
            if stop.is_set() or not again:
                break
            hub.publish_status("error", message)
            wake.wait(RETRY)
            wake.clear()
        with self._lock:  # before a new connect() can announce itself
            self._running = False
            hub.publish_status("off", "" if stop.is_set() else message)

    def _session(self, hub: PenHub, stop: threading.Event, take: bool) -> tuple:
        """One connection; returns why it ended and whether to try again."""
        showing = self._deploy()
        script = session(owner=self.owner, take=int(take), screen=int(showing), turn=self.rotation)
        try:
            proc = subprocess.Popen([*self.command, script],
                                    stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        except OSError as e:
            return f"cannot run ssh: {e}", False
        with self._lock:
            self._proc = proc
        threading.Thread(target=self._heartbeat, args=(proc, stop), daemon=True).start()
        fd = proc.stdout.fileno()
        try:
            if stop.is_set():  # disconnected before the process was known
                return "", False
            head = b""
            while not head.endswith(READY):
                chunk = os.read(fd, 1)
                if not chunk:
                    return _why(proc, "the tablet closed the connection")
                head += chunk
            with self._lock:
                self._connected, self._showing = True, showing
            hub.publish_status("connected", f"{self.host}, held {HOLD[self.rotation]}", screen=showing)
            decoder = Decoder(self.rotation)
            while chunk := os.read(fd, 4096):
                for frame in decoder.feed(chunk):
                    hub.publish_frame(frame)
            hub.publish_frame({**(decoder.last or {"x": 0, "y": 0, "p": 0}), "tool": None, "touch": False})
            return _why(proc, "lost the connection to the tablet")
        finally:
            _close(proc.stdin)
            try:
                proc.wait(timeout=15)
            except subprocess.TimeoutExpired:
                proc.kill()
            with self._lock:
                self._proc = None
                self._connected = self._showing = False

    def _heartbeat(self, proc: subprocess.Popen, stop: threading.Event) -> None:
        while proc.poll() is None and not stop.wait(HEARTBEAT):
            try:
                with self._write:
                    proc.stdin.write(b"\n")
                    proc.stdin.flush()
            except (OSError, ValueError):
                return


def _close(f) -> None:
    try:
        f.close()
    except OSError:
        pass


def _why(proc: subprocess.Popen, fallback: str) -> tuple:
    """Why the session ended, and whether to try again: not when another viewer has the tablet,
    or the person disconnected on the tablet."""
    _close(proc.stdin)
    try:
        _, err = proc.communicate(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()
        return fallback, True
    return err.decode(errors="replace").strip() or fallback, proc.returncode not in (TAKEN, BUSY, LEFT)
