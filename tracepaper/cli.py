from __future__ import annotations

import argparse
from pathlib import Path

from .pen import HOLD, RemarkablePen
from .server import serve


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(prog="tracepaper", description="Tracepaper sheets and viewer.")
    commands = parser.add_subparsers(dest="command", required=True)
    view = commands.add_parser("view", help="open a project's views (sheets and 3D models) in the viewer")
    view.add_argument("path", nargs="?", default=".",
                      help="the views folder (.svg sheets, .glb models, in subfolders too), or one view to open directly")
    view.add_argument("--port", type=int, default=7310, help="port to serve on (default 7310, or a free one)")
    view.add_argument("--no-browser", action="store_true", help="don't open a browser")
    view.add_argument("--tablet-host", default="remarkable", metavar="HOST",
                      help="the ssh host of the reMarkable 2 that the pages' Connect button uses "
                           "(default: remarkable)")
    view.add_argument("--rotate", type=int, choices=sorted(HOLD), default=90,
                      help="how the tablet is held, in degrees clockwise from upright: "
                           "90 (the default) is landscape with the power button on the right, 270 on the left")
    look = commands.add_parser("inspect", help="report on a sketch file and render images of it")
    look.add_argument("path", nargs="?", default=".",
                      help="a .sketch.svg file, or a directory to take the newest one from (default: .)")
    look.add_argument("--out", type=Path, help="directory for the images (default: a new temporary one)")
    args = parser.parse_args(argv)
    if args.command == "view":
        serve(Path(args.path), args.port, not args.no_browser, RemarkablePen(args.tablet_host, args.rotate))
    elif args.command == "inspect":
        from .report import inspect
        print(inspect(Path(args.path), args.out))
