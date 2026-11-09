"""Draw a view of a scene, or a plot, as the viewer does, or a slicer project from above, to
check it: ``tracepaper render scene.step#view``, ``tracepaper render name.plot.json`` or
``tracepaper render plate.3mf``.

Writes the sheet as SVG and renders it as PNG, with what the scene's .tp.json gives (its
annotations, its fields, which nodes start hidden), and says what is wrong with it if
anything is: for a slicer project, pieces off their plate, over the part the printer keeps
for itself or too tall. Needs OCP for scenes, and rsvg-convert for the PNG.
"""
from __future__ import annotations

import shutil
import tempfile
from pathlib import Path
from typing import Optional

from . import plate, plots
from . import views as scenes
from .report import _render


def _write(sheet, out: Path, lines: list) -> str:
    """Write sheet as out's SVG and out, a PNG, and the report's lines saying where."""
    out.parent.mkdir(parents=True, exist_ok=True)
    svg = out.with_suffix(".svg")
    lines.append(f"  {svg}")
    if shutil.which("rsvg-convert"):
        _render(sheet.tostring(), out)  # lines in pixels, as on a screen
        lines.append(f"  {out}")
    else:
        lines.append("  no PNG: rsvg-convert is not installed (Fedora: librsvg2-tools)")
    svg.write_text(sheet.tostring(), encoding="utf-8")  # after the PNG, which uses its name in passing
    return "\n".join(lines)


def render_plot(path: Path, out: Optional[Path] = None) -> str:
    """Draw the plot at path and return a short report."""
    try:
        plot = plots.read_plot(path)
    except (ValueError, OSError) as err:
        raise SystemExit(f"tracepaper: {err}") from None
    sheet = plots.draw(plot, path.name)
    stem = path.name[: -len(plots.PLOT_SUFFIX)]
    out = Path(out) if out else Path(tempfile.mkdtemp(prefix="tracepaper-")) / f"{stem}.png"
    series = sum(len(p["series"]) for p in plot["panels"])
    return _write(sheet, out, [f"{sheet.view.get('title')}: {len(plot['panels'])} panel"
                               f"{'s' if len(plot['panels']) > 1 else ''}, {series} series"])


def render_plate(path: Path, out: Optional[Path] = None) -> str:
    """Draw the slicer project at path from above and return a short report."""
    try:
        layout = plate.read(path)
    except (ValueError, OSError) as err:
        raise SystemExit(f"tracepaper: {err}") from None
    sheet = plate.draw(layout, path.name)
    out = Path(out) if out else Path(tempfile.mkdtemp(prefix="tracepaper-")) / f"{path.stem}.png"
    count = len(layout.pieces)
    lines = [f"{sheet.view.get('title')}: {count} piece{'' if count == 1 else 's'}"
             + (f" on {len(layout.plates)} plates" if len(layout.plates) > 1 else "")]
    return _write(sheet, out, lines + [f"  {line}" for line in plate.check(layout)])


def render(target: str, frame: Optional[int] = None, out: Optional[Path] = None) -> str:
    """Draw target ("dir/scene.step", "dir/scene.step#view", "dir/name.plot.json" or
    "dir/plate.3mf") and return a short report."""
    from .server import Scenes, View
    if plots.is_plot(Path(target)) or Path(target).suffix.lower() == ".3mf":
        if frame is not None:
            raise SystemExit(f"tracepaper: a {'plot' if plots.is_plot(Path(target)) else 'slicer project'} "
                             "has no frames")
        return (render_plot if plots.is_plot(Path(target)) else render_plate)(Path(target), out)
    name, view = scenes.split_name(target)
    path = Path(name)
    if not path.is_file() or not scenes.is_scene(path):
        raise SystemExit(f"tracepaper: {path} is not a .step, .plot.json or .3mf file")
    drawn = Scenes()  # as the viewer draws it: text, field colours over the frames
    try:
        meta = scenes.read_meta(path)
        framed = scenes.frames(path, meta).get("frames", [])
        if frame is not None and not 0 <= frame < len(framed):
            raise ValueError(f"{path.name} has {len(framed)} frames; there is no frame {frame}")
        if view is None and drawn.scene(View(path.name, "sheet", path)).flat() is None:
            view = "iso"
        entry = framed[frame] if frame is not None else None
        if view is not None and scenes.view_spec(scenes.frame_meta(meta, entry), view) is None:
            names = ", ".join(v["name"] for v in scenes.named_views(meta)) or "none"
            raise ValueError(f"no view {view!r}: its named views are {names}; or x=…, y=…, z=…, "
                             f"or {', '.join(scenes.DIRECTIONS)}")
        it = View(path.name + (f"#{view}" if view else ""), "sheet", path, view)
        scene, sheet = drawn.scene(it, frame), drawn.sheet(it, frame)
    except ValueError as err:
        raise SystemExit(f"tracepaper: {err}") from None
    out = Path(out) if out else Path(tempfile.mkdtemp(prefix="tracepaper-")) / (
        f"{path.stem}{'-' + view if view else ''}{f'-{frame}' if frame is not None else ''}.png")
    return _write(sheet, out, [f"{sheet.view.get('title')}: {len(scene.parts())} parts, {sheet.view.get('kind')}"])
