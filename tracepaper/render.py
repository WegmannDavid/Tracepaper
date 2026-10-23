"""Draw a view of a scene as the viewer does, to check it: ``tracepaper render scene.step#view``.

Writes the sheet as SVG and renders it as PNG, with what the scene's .tp.json gives (its
annotations, its fields, which nodes start hidden), and says what is wrong with it if
anything is. Needs OCP, and rsvg-convert for the PNG.
"""
from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Optional

from . import views as scenes


def render(target: str, frame: Optional[int] = None, out: Optional[Path] = None) -> str:
    """Draw target ("dir/scene.step" or "dir/scene.step#view") and return a short report."""
    from . import drawing as D, scene as S
    name, view = scenes.split_name(target)
    path = Path(name)
    if not path.is_file() or not scenes.is_scene(path):
        raise SystemExit(f"tracepaper: {path} is not a .step file")
    try:
        meta = scenes.read_meta(path)
        framed = scenes.frames(path, meta).get("frames", [])
        if frame is not None and not 0 <= frame < len(framed):
            raise ValueError(f"{path.name} has {len(framed)} frames; there is no frame {frame}")
        entry = framed[frame] if frame is not None else None
        scene = S.read_step(entry["step"] if entry else path)
        if view is None and scene.flat() is None:
            view = "iso"
        spec = scenes.view_spec(meta, view) if view is not None else None
        if view is not None and spec is None:
            names = ", ".join(v["name"] for v in scenes.named_views(meta)) or "none"
            raise ValueError(f"no view {view!r}: its named views are {names}; or x=…, y=…, z=…, "
                             f"or {', '.join(scenes.DIRECTIONS)}")
        hidden = S.find_nodes(scene, meta.get("hidden") or [])
        sheet = D.draw(scene, spec, meta, path.name + (f"#{view}" if view else ""), hidden=hidden,
                       fields=scenes.fields(path, meta, entry))
    except ValueError as err:
        raise SystemExit(f"tracepaper: {err}") from None
    if entry:
        sheet.view["frame"] = scenes.frame_label(entry["at"])
    out = Path(out) if out else Path(tempfile.mkdtemp(prefix="tracepaper-")) / (
        f"{path.stem}{'-' + view if view else ''}{f'-{frame}' if frame is not None else ''}.png")
    out.parent.mkdir(parents=True, exist_ok=True)
    svg = out.with_suffix(".svg")
    svg.write_text(sheet.tostring(), encoding="utf-8")
    lines = [f"{sheet.view.get('title')}: {len(scene.parts())} parts, {sheet.view.get('kind')}",
             f"  {svg}"]
    if shutil.which("rsvg-convert"):
        subprocess.run(["rsvg-convert", "-b", "white", "-w", "2000", str(svg), "-o", str(out)], check=True,
                       capture_output=True, timeout=120)
        lines.append(f"  {out}")
    else:
        lines.append("  no PNG: rsvg-convert is not installed (Fedora: librsvg2-tools)")
    return "\n".join(lines)
