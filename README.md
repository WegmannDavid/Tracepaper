# Tracepaper

A visual aid for working on CAD geometry with an AI.

The AI's build writes **views** of the model into a project folder:

- **Sheets:** plain, layered SVG files showing geometry, dimensions and the constraints worth seeing. In the viewer you toggle layers, drag labels out of the way and sketch roughly what you want changed. Lines stay the same few pixels wide at any zoom. Press Save, then tell the AI what you drew. It finds `<sheet>.sketch.svg` next to the sheet, makes the change in the real CAD pipeline and rebuilds, and the viewer reloads.
- **Models:** 3D `.glb` files, such as a CadQuery assembly's export. The viewer shows them so you can look at the build's result, with no debugger needed, and you can slice them and sketch over them as on sheets.

Tracepaper doesn't care which CAD tool or constraint solver is behind a sheet. The format is described in [SPEC.md](SPEC.md).

## Viewer

Needs Python 3.9 or newer and a browser; there are no other dependencies.

```sh
python3 -m tracepaper view examples/bracket     # from this directory
```

- **The project page** lists the folder's sheets and models, including up to three folders down. Each view opens in a window of its own, so you can put several side by side.
- **Updates:** a window updates by itself when its file changes. A 3D view keeps the camera where you left it.
- **3D views:** the mouse works as in the OCP CAD viewer. Drag to rotate: near the middle it tips the model over, near the edge it turns it about the line of sight; with `Ctrl` only up/down counts, with `Alt` only sideways. Right-drag or `Shift`-drag to pan, scroll or middle-drag to zoom. Iso, Top, Front and Right (keys `0`, `7`, `1`, `3`) look from the standard directions, Fit (`F`) frames what's shown, and Edges (`E`) switches the edges off and on. **Slice**, below the tree, cuts the model with planes along X, Y and Z, as the OCP viewer does: switch a plane on, drag its slider or type where it goes in mm, and `⇄` keeps the other side. Cut parts are closed in their own colours. *Section only* leaves just those cut faces, seen straight on when one plane is on, and *Cut a corner* takes away only what lies beyond all the planes.
- **Sketching over a 3D view:** press **Sketch** (`P`) and draw with the mouse, or with the reMarkable's pen. You draw on the plane you're looking at, so while there's ink the view only pans and zooms; turning it first asks to wipe the ink. Save writes `<model>.glb.sketch.svg`, a picture of the view with your ink over it, placed in the model. In *Section only*, looking straight at the section, the ink is on the section's plane.
- **The tree panel:** both kinds of view show their named parts, a sheet's named groups and a model's assembly tree, in the same panel. It starts folded, showing only the top level; `▸` unfolds a node. Click a node's eye to hide it and everything in it, in that window only. Half an eye means something below the node is hidden. What starts hidden comes from the file.
- **After updating Tracepaper,** restart a running `tracepaper view`. Pages come fresh from disk, but the server behind them doesn't.

To get a `tracepaper` command for use in other projects:

```sh
pip install --user -e .
tracepaper view path/to/sheets                  # a directory, or a single sheet .svg
```

| | |
|---|---|
| `P` / `V` | mouse draws / mouse moves labels and pans (the default) |
| `1`–`4` | ink colour |
| `I`, then click | pick the ink colour from something on the sheet; `5` selects it again later |
| scroll | zoom |
| middle drag, `Space` + drag, one finger | pan |
| two fingers | pinch zoom |
| `F` | fit the sheet |
| `Ctrl+Z` / `Ctrl+S` | undo / save |

## Drawing with a reMarkable 2

Press **Connect reMarkable** in a view's toolbar. The tablet's pen then draws in that window, and the mouse keeps doing everything else, even at the same time. The pen's other end erases whole strokes. **Disconnect** gives the tablet back.

The tablet shows the sheet you draw on, lined up with the pen, so you can look at the tablet instead of the screen. Over a 3D view it shows only the frame of where the pen draws, and you look at the screen:

- **The picture** follows the window: it's sent again shortly after you pan, zoom, hide something or draw. A grey frame marks the part of the tablet the pen draws in.
- **While you draw,** the tablet draws the stroke itself, in black and as fast as the tablet's own app does. When you lift the pen, the viewer's version of it replaces that a moment later, in its colour as a shade of grey.
- **Disconnect on the tablet:** the button in its top right corner, tapped twice with a finger. The pen only draws.

- **Several windows.** The pen draws in the one you used last with the mouse, or the sheet you opened last; the toolbar says where it is. **Connect here** in another window moves it there. A 3D window shows where the pen is and can disconnect it.
- **Several viewers.** Only one can have the tablet. Connecting in one takes the tablet from any other Tracepaper viewer that has it, which then shows "taken by another viewer" and leaves it alone. So a viewer can keep running all day without holding the tablet.
- **Closing the windows** lets the tablet go too, ten seconds after the viewer's last window closed, and so does stopping the viewer.
- **Mapping.** The tablet covers the part of the sheet in view, so zooming in with the mouse gives finer control.
- **Aspect ratio.** The view's shape is fitted into the middle of the tablet at one scale for both axes, so a circle drawn on the tablet stays a circle. The toolbar shows how much of the tablet that is; the strips outside it do nothing.
- **Holding the tablet.** Landscape with the power button on the right is the default. Use `--rotate 270` for the power button on the left; 0 and 180 are portrait.
- **Connection.** The tablet is reached with `ssh remarkable`, or `tracepaper view --tablet-host HOST` for another host name. If the connection drops, the viewer reconnects by itself, unless another viewer has taken the tablet meanwhile.
- **The tablet's own app.** It's closed while connected, so strokes don't also land in a notebook there. The app starts again on disconnecting, or by itself 20 seconds after the connection is lost.
- **The tablet's screen** is drawn by a small program, `tracepaper/tablet/tracepaper-screen`, which the viewer copies onto the tablet (to `/home/root/.tracepaper/`) when connecting. It's built for the tablet's software version 3.23; after an update of the tablet, rebuild it if the tablet stops showing the view: install reMarkable's [SDK](https://developer.remarkable.com/links) for the new version into `~/codex-sdk/rm2/<version>` and run `tablet/build.sh`. Without the program, the pen still works, just without the picture on the tablet.

## Using Tracepaper from other projects

Link this checkout in, rather than copying it, so other projects always use the current code, spec and skill:

```sh
pip install --user --no-build-isolation -e .                          # `tracepaper` and `import tracepaper` run from here
ln -s "$PWD/skills/tracepaper" ~/.claude/skills/tracepaper            # the Claude Code skill
```

- **The skill** (`skills/tracepaper/SKILL.md`) teaches Claude in any project how to make sheets, run the viewer and act on your sketches. Its `SPEC.md` is a link to the spec here, and the skill tells Claude to read it fresh each session.
- **Updates** reach other projects as soon as the files change here:
  - spec and skill edits;
  - viewer changes, after a page reload;
  - server or tablet connection changes, after restarting that `tracepaper view`.
- **New skills** are only noticed by new Claude sessions. Once a session knows the skill, it reads the current files each time it uses it.

`tracepaper inspect [path]` is the AI's side of the loop. It reports on the newest sketch file under `path` (strokes, picked colours, moved labels) and renders a whole-sheet image plus close-ups of the ink.

## Writing sheets

Anything that writes SVG following [SPEC.md](SPEC.md) works. For Python there's a small helper, `tracepaper.sheet`, which supports layers, sublayers and named parts for the viewer's tree. Two examples use it:

- [`examples/bracket/make_sheets.py`](examples/bracket/make_sheets.py) draws a section and a top view of a bracket.
- [`examples/cabinet/make_views.py`](examples/cabinet/make_views.py) writes a cabinet as a model and a section with nested groups that have the same names in both.

Models are written by the CAD code itself. For CadQuery, use `tracepaper.model.export(assembly, "bracket.glb", hidden=["screw"])`: it writes the assembly and marks the listed parts as hidden at first. See [`examples/bracket/make_model.py`](examples/bracket/make_model.py), which needs CadQuery. The 3D view uses [three.js](https://threejs.org) 0.170.0, bundled in `tracepaper/viewer/vendor/` (MIT).

## Tests

```sh
python3 -m unittest discover tests
```

`test_sheet.py` checks that the Python writer and the viewer draw annotations identically; this needs `gjs`. `test_browser.py` runs the viewer in headless Firefox. Each is skipped if its tool is missing.
