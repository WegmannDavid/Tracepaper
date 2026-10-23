from __future__ import annotations

import argparse
from pathlib import Path

from .pen import HOLD, RemarkablePen
from .server import serve


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(prog="tracepaper", description="Tracepaper sheets and viewer.")
    commands = parser.add_subparsers(dest="command", required=True)
    view = commands.add_parser("view", help="open a project's views (scenes, drawings of them, slicer projects) "
                                            "in the viewer")
    view.add_argument("path", nargs="?", default=".",
                      help="the build folder, whose .step scenes, .3mf slicer projects, and older .svg sheets and .glb "
                           "models it shows (in subfolders too), or one view to open directly")
    view.add_argument("--port", type=int, default=7310, help="port to serve on (default 7310, or a free one)")
    view.add_argument("--no-browser", action="store_true", help="don't open a browser")
    view.add_argument("--tablet-host", default="remarkable", metavar="HOST",
                      help="the ssh host of the reMarkable 2 that the pages' Connect button uses "
                           "(default: remarkable)")
    view.add_argument("--rotate", type=int, choices=sorted(HOLD), default=90,
                      help="how the tablet is held, in degrees clockwise from upright: "
                           "90 (the default) is landscape with the power button on the right, 270 on the left")
    render = commands.add_parser(
        "render", help="draw a view of a scene (a .step file) as the viewer does, to check it: an SVG and a PNG")
    render.add_argument("view", help="a scene, scene.step, with one of its views after # if wanted: "
                                     "scene.step#section-a, scene.step#x=12.5, scene.step#front; "
                                     "a 3D scene without one is drawn isometric")
    render.add_argument("--frame", type=int, help="draw this frame of the scene's frames (0 is the first)")
    render.add_argument("--out", type=Path, help="the PNG to write (default: next to the scene, in a "
                                                 "temporary folder); the SVG goes next to it")
    look = commands.add_parser("inspect", help="report on a sketch file and render images of it")
    look.add_argument("path", nargs="?", default=".",
                      help="a .sketch.svg file, or a directory to take the newest one from (default: .)")
    look.add_argument("--out", type=Path, help="directory for the images (default: a new temporary one)")
    args = parser.parse_args(argv)
    if args.command == "view":
        serve(Path(args.path), args.port, not args.no_browser, RemarkablePen(args.tablet_host, args.rotate))
    elif args.command == "render":
        from .render import render
        print(render(args.view, args.frame, args.out))
    elif args.command == "inspect":
        from .report import inspect
        print(inspect(Path(args.path), args.out))
