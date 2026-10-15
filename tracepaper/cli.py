from __future__ import annotations

import argparse
from pathlib import Path

from .server import serve


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(prog="tracepaper", description="Tracepaper sheets and viewer.")
    commands = parser.add_subparsers(dest="command", required=True)
    view = commands.add_parser("view", help="open sheets in the viewer")
    view.add_argument("path", nargs="?", default=".", help="a sheet (.svg) or a directory of sheets")
    view.add_argument("--port", type=int, default=7310, help="port to serve on (default 7310, or a free one)")
    view.add_argument("--no-browser", action="store_true", help="don't open a browser")
    args = parser.parse_args(argv)
    if args.command == "view":
        serve(Path(args.path), args.port, not args.no_browser)
