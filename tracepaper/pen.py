"""A reMarkable 2 as a drawing tablet for the viewer (``tracepaper view --remarkable``).

The tablet's pen sensor reports through Linux's input system. Tracepaper reads
its raw events over ssh and passes pen positions, in millimetres on the tablet
as it is held, to the viewer, which maps the tablet onto the part of the sheet
in view. The computer's own pointer is never involved, so the mouse keeps
working as usual.

While connected, the tablet's app is stopped, so strokes don't also land in a
document open there; the e-ink keeps showing what it showed. The app starts
again when Tracepaper disconnects, or by itself SILENCE seconds after this
computer goes quiet (crash, cable pulled, WiFi lost).
"""
from __future__ import annotations

import os
import queue
import struct
import subprocess
import threading
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

HOLD = {0: "upright", 90: "landscape, power button on the right", 180: "upside down",
        270: "landscape, power button on the left"}

READY = b"TRACEPAPER-READY\n"
SILENCE = 20  # seconds without a heartbeat after which the tablet restarts its app
HEARTBEAT = 5
RETRY = 3.0  # seconds between connection attempts

# Runs on the tablet: stop the app, stream the pen, start the app again when done.
SCRIPT = f"""
dev=$(grep -A4 -i wacom /proc/bus/input/devices | grep -o 'event[0-9]*' | head -n 1)
[ -n "$dev" ] || {{ echo "no pen sensor found on the tablet" >&2; exit 1; }}
restore() {{ trap - EXIT HUP INT TERM PIPE; kill $pid 2>/dev/null; systemctl reset-failed xochitl 2>/dev/null; systemctl start xochitl; exit; }}
trap restore EXIT HUP INT TERM PIPE
systemctl stop xochitl || exit 1
printf '{READY.decode().strip()}\\n'
cat /dev/input/$dev & pid=$!
while read -t {SILENCE} line; do :; done
"""

SSH = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=4", "-o", "ServerAliveInterval=3",
       "-o", "ServerAliveCountMax=2", "-o", "LogLevel=ERROR"]


def tablet_size(rotation: int) -> tuple:
    """(width, height) in mm of the tablet held turned clockwise by rotation degrees."""
    w, h = SENSOR_Y / UNITS_PER_MM, SENSOR_X / UNITS_PER_MM  # upright
    return (h, w) if rotation in (90, 270) else (w, h)


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
    "tool" ("pen", "eraser", or None when the pen is out of range) and "touch".
    One frame per sensor report, and only when something changed.
    """

    def __init__(self, rotation: int = 90):
        self.rotation = rotation
        self.abs = {ABS_X: 0, ABS_Y: 0, ABS_PRESSURE: 0}
        self.keys = {BTN_TOOL_PEN: 0, BTN_TOOL_RUBBER: 0, BTN_TOUCH: 0}
        self.rest = b""
        self.last = None

    def feed(self, data: bytes) -> list:
        self.rest += data
        end = len(self.rest) - len(self.rest) % EVENT.size
        frames = []
        for _, _, kind, code, value in EVENT.iter_unpack(self.rest[:end]):
            if kind == EV_ABS and code in self.abs:
                self.abs[code] = value
            elif kind == EV_KEY and code in self.keys:
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
                "tool": tool, "touch": bool(self.keys[BTN_TOUCH]) and tool is not None}


class PenHub:
    """Hands pen frames and connection status to every viewer that listens."""

    def __init__(self, size_mm: tuple):
        self.size = size_mm
        self.status = {"state": "connecting", "message": "", "width": size_mm[0], "height": size_mm[1]}
        self._queues: set = set()
        self._lock = threading.Lock()

    def subscribe(self) -> queue.Queue:
        q = queue.Queue(maxsize=5000)
        with self._lock:
            self._queues.add(q)
            q.put(("status", self.status))
        return q

    def unsubscribe(self, q: queue.Queue) -> None:
        with self._lock:
            self._queues.discard(q)

    def _send(self, item) -> None:
        with self._lock:
            for q in self._queues:
                try:
                    q.put_nowait(item)
                except queue.Full:  # a viewer that stopped reading
                    pass

    def publish_status(self, state: str, message: str = "") -> None:
        self.status = {**self.status, "state": state, "message": message}
        self._send(("status", self.status))

    def publish_frame(self, frame: dict) -> None:
        self._send(("pen", frame))

    def close(self) -> None:
        self._send(("close", None))


class RemarkablePen:
    """Streams a reMarkable 2's pen into a PenHub, reconnecting when the tablet comes back."""

    def __init__(self, host: str = "remarkable", rotation: int = 90, command: Optional[list] = None):
        self.host = host
        self.rotation = rotation
        self.size_mm = tablet_size(rotation)
        self.command = command or [*SSH, host, SCRIPT]
        self._stop = threading.Event()
        self._proc: Optional[subprocess.Popen] = None
        self._lock = threading.Lock()
        self._thread: Optional[threading.Thread] = None

    def start(self, hub: PenHub) -> None:
        self._thread = threading.Thread(target=self._run, args=(hub,), name="tracepaper-pen", daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 15) -> None:
        """Disconnect; the tablet starts its app again."""
        self._stop.set()
        with self._lock:
            proc = self._proc
        if proc is not None:
            _close(proc.stdin)  # the script on the tablet sees the end of its input and cleans up
            try:
                proc.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                proc.kill()
        if self._thread is not None:
            self._thread.join(timeout=5)

    def _run(self, hub: PenHub) -> None:
        while not self._stop.is_set():
            hub.publish_status("connecting", f"connecting to {self.host}")
            message = self._session(hub)
            if self._stop.is_set():
                break
            hub.publish_status("error", message)
            self._stop.wait(RETRY)
        hub.publish_status("off", "disconnected")

    def _session(self, hub: PenHub) -> str:
        """One connection; returns why it ended."""
        try:
            proc = subprocess.Popen(self.command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                    stderr=subprocess.PIPE)
        except OSError as e:
            return f"cannot run ssh: {e}"
        with self._lock:
            self._proc = proc
        threading.Thread(target=self._heartbeat, args=(proc,), daemon=True).start()
        fd = proc.stdout.fileno()
        try:
            head = b""
            while not head.endswith(READY):
                chunk = os.read(fd, 1)
                if not chunk:
                    return _why(proc, "the tablet closed the connection")
                head += chunk
            hub.publish_status("connected", f"{self.host}, held {HOLD[self.rotation]}")
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

    def _heartbeat(self, proc: subprocess.Popen) -> None:
        while proc.poll() is None and not self._stop.wait(HEARTBEAT):
            try:
                proc.stdin.write(b"\n")
                proc.stdin.flush()
            except (OSError, ValueError):
                return


def _close(f) -> None:
    try:
        f.close()
    except OSError:
        pass


def _why(proc: subprocess.Popen, fallback: str) -> str:
    _close(proc.stdin)
    try:
        _, err = proc.communicate(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()
        return fallback
    return err.decode(errors="replace").strip() or fallback
