# Tracepaper

A visual aid for working on CAD geometry with an AI.

The AI's build writes **views** of the model into its build folder, such as `build/`, among its other output:

- **Scenes:** the model's geometry, exact, as STEP files: solids, regions and curves in a tree of named parts, in the colours your CAD gives them, and nothing else about how they look. Beside each, a small `.tp.json` holds what STEP can't: dimensions (lengths, radii, diameters and angles), named sections and projections, frames and fields. The viewer shows a scene in 3D, and draws exact 2D drawings of it: sections through any plane, cut with OpenCascade, and projections with hidden lines. Circles stay circles and arcs come out as true arcs. A scene of regions in one plane is a 2D scene, shown as a drawing.
  - **Frames:** a scene can come at several values of one or more axes, an insertion depth or an opening angle, each a STEP file of its own: step through them, play them, and see per-frame values plotted along the axis, with their limits.
  - **Fields:** FEM results as `.vtu` files, over areas, along lines or at bare points: numbers as a colour map with a legend, vectors as arrows (displacements, contact forces), only what's over a limit, isolines, the mesh, the deformation scaled up, and the value under the pointer.
  - On any of these, you hide parts, drag labels out of the way and sketch roughly what you want changed. Press Save, then tell the AI what you drew. It finds the sketch file next to the scene, makes the change in the real CAD pipeline and rebuilds, and the viewer reloads.
- **Slicer projects:** `.3mf` files as OrcaSlicer or Bambu Studio save them. The viewer shows them in 3D: the printer's plate and where the pieces sit on it, in their filaments' colours, so you can check the layout. To print, open the same file in the slicer.
- **Older views:** 2D sheets the AI drew itself as layered SVG files, and 3D models as `.glb` files ([SPEC-v0.md](SPEC-v0.md)), are still shown.

Tracepaper doesn't care which CAD tool or constraint solver is behind a scene. The format is described in [SPEC.md](SPEC.md).

## Viewer

Needs Python 3.9 or newer and a browser, and for scenes OpenCascade's Python binding, OCP, which CadQuery and build123d install (or `pip install cadquery-ocp`).

```sh
python3 -m tracepaper view tests/geometry     # the test scenes, from this directory
```

- **The project page** shows the build folder as a tree: its scenes and their named views, slicer projects and older views, and the subfolders holding them. Click a folder to fold it; the browser remembers which are folded. A folded folder says when a sketch inside it waits for the AI. Each view opens in a window of its own, so you can put several side by side, and a view's window has a menu of all views, grouped by folder.
- **The build folder** holds the views among the build's other output: every `.step`, `.3mf`, `.svg` and `.glb` in it shows up, and nothing else, nor folders whose names start with a dot. The build arranges the views in subfolders as it likes, writes each view over its old file, and deletes views it no longer makes, but never empties the folder wholesale: your saved sketches wait next to their views until the AI has handled them.
- **Updates:** a window updates by itself when its files change. A 3D view keeps the camera where you left it.
- **Drawings:** a scene's named views open from the project page, and **Drawing** (`D`) in its 3D view opens the exact drawing of what you're looking at: the section through the one slice plane that's on, or the scene from the standard direction you look from, with its hidden lines on a layer of their own. Parts keep the same colours as in 3D; cut faces are hatched, each part at its own angle and spacing.
- **Frames:** below the tree, a slider for each axis, with ◀ ⏵ ▶ (the arrow keys step too), the frame's values, frames outside a limit marked red, and a plot of each value against the first axis, or another axis or value (force against deflection). *Trail* shows every frame faintly behind the one shown, of everything or of one part. Your ink stays as you step through them, and so do moved labels; dimensions and notes can change from frame to frame.
- **Fields:** below that, each field's colour map, whose ends you can type in, *Only outside its limits*, *Isolines*, *Mesh*, and *Deformation ×* where the field moves the mesh. A vector field has *Arrows*, *Length ×*, *Values* (each arrow's, written beside it) and *Colours* (its lengths' colour map). Pointing at the field shows its value there, or an arrow's near it.
- **Measuring:** **Measure** (`R`) on a drawing: click points, which snap to end points (a square), centres (a circle), points and dimensions' points, or onto a line or curve (a cross). Two give the distance, with Δx and Δy, a third the angle at the middle one, on the drawing and beside the coordinates. Right-click or `Esc` starts again.
- **Text size:** **A−** and **A+** on a drawing make the dimensions' and symbols' text smaller or larger, through the ISO heights, with their arrowheads; the notes keep theirs. For a drawing that spans much more than its details, such as a section through a wall with a window profile in it. The browser remembers it for each view.
- **3D views:** the mouse works as in the OCP CAD viewer. Drag to rotate: near the middle it tips the model over, near the edge it turns it about the line of sight; with `Ctrl` only up/down counts, with `Alt` only sideways. Right-drag or `Shift`-drag to pan, scroll or middle-drag to zoom. Iso, Top, Front and Right (keys `0`, `7`, `1`, `3`) look from the standard directions, Fit (`F`) frames what's shown, Edges (`E`) switches the edges off and on, Transparent (`T`) makes every face half see-through, as in the OCP viewer, to see what's inside, and Perspective (`O`) draws the model as a camera would see it instead of orthographic, from the same direction and at the same size where it turns. Sketching switches back to orthographic. **Slice**, below the tree, cuts the model with planes along X, Y and Z, as the OCP viewer does: switch a plane on, drag its slider or type where it goes in mm, and `⇄` keeps the other side. Cut parts are closed in their own colours. *Section only* leaves just those cut faces, seen straight on when one plane is on, and *Cut a corner* takes away only what lies beyond all the planes.
- **Sketching over a 3D view:** press **Sketch** (`P`) and draw with the mouse, or with the reMarkable's pen. You draw on the plane you're looking at, so while there's ink the view only pans and zooms; turning it first asks to wipe the ink. Save writes `<name>.step.sketch.svg`, a picture of the view with your ink over it, placed in the model. In *Section only*, looking straight at the section, the ink is on the section's plane. For exact coordinates under your ink, sketch on a drawing instead.
- **Slicer projects** open in the 3D view, where the tree has the plate, each piece by name, and the printer's build volume, hidden at first: show it to check that tall pieces fit. The orange corner of the plate is the part the printer keeps for itself.
- **The tree panel:** every view shows its named parts in the same panel: a scene's assembly tree, in 3D and in its drawings, an older sheet's named groups. It starts folded, showing only the top level; `▸` unfolds a node. Hiding works as in the OCP viewer, in that window only:
  - A node's eye hides or shows it with everything inside it. Inside a hidden node, a node can be shown again on its own; half an eye means only part of what's inside shows.
  - `Ctrl`+click on a name shows only that node; once more shows everything again. `Alt`+click hides it.
  - A part drawn in one colour has a dot of it before its name. What starts hidden comes from the file.
- **After updating Tracepaper,** restart a running `tracepaper view`. Pages come fresh from disk, but the server behind them doesn't.

To get a `tracepaper` command for use in other projects:

```sh
pip install --user -e .
tracepaper view build                           # the build folder, or a single view
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
- **Setting up a computer for it,** once. The USB cable makes a small network of its own, where the tablet is always `10.11.99.1`, so it needs no Wi-Fi or internet. Add to `~/.ssh/config`:

  ```
  Host remarkable
      HostName 10.11.99.1
      User root
      UserKnownHostsFile /dev/null
      StrictHostKeyChecking no
  ```

  Then, with the tablet plugged in and awake, run `ssh-copy-id remarkable` (after `ssh-keygen -t ed25519` if the computer has no key yet). It asks once for the tablet's password, shown under Settings → Help → Copyrights and licenses. The viewer logs in without asking, so it needs the key. `ssh remarkable echo ok` should then print `ok` without asking for anything. Several computers can be set up this way; one at a time has the tablet.
- **The tablet's own app.** It's closed while connected, so strokes don't also land in a notebook there. The app starts again on disconnecting, or by itself 20 seconds after the connection is lost.
- **The tablet's screen** is drawn by a small program, `tracepaper/tablet/tracepaper-screen`, which the viewer copies onto the tablet (to `/home/root/.tracepaper/`) when connecting. It's built for the tablet's software version 3.23; after an update of the tablet, rebuild it if the tablet stops showing the view: install reMarkable's [SDK](https://developer.remarkable.com/links) for the new version into `~/codex-sdk/rm2/<version>` and run `tablet/build.sh`. Without the program, the pen still works, just without the picture on the tablet.

## Using Tracepaper from other projects

Link this checkout in, rather than copying it, so other projects always use the current code, spec and skill:

```sh
pip install --user --no-build-isolation -e .                          # `tracepaper` and `import tracepaper` run from here
ln -s "$PWD/skills/tracepaper" ~/.claude/skills/tracepaper            # the Claude Code skill
```

- **The skill** (`skills/tracepaper/SKILL.md`) teaches Claude in any project how to write scenes, run the viewer and act on your sketches. Its `SPEC.md` is a link to the spec here, and the skill tells Claude to read it fresh each session.
- **Updates** reach other projects as soon as the files change here:
  - spec and skill edits;
  - viewer changes, after a page reload;
  - server or tablet connection changes, after restarting that `tracepaper view`.
- **New skills** are only noticed by new Claude sessions. Once a session knows the skill, it reads the current files each time it uses it.

`tracepaper render scene.step#view` and `tracepaper inspect [path]` are the AI's side of the loop. `render` draws a view as the viewer does, as SVG and PNG, so the AI can look at it first, and says what's wrong in its `.tp.json`. `inspect` reports on the newest sketch file under `path` (strokes, picked colours, moved labels, frames) and renders the whole view plus close-ups of the ink.

## Writing scenes

Any CAD program's STEP export works: CadQuery's `assembly.export("build/part.step")`, build123d's `export_step`, FreeCAD's. Name the parts, since the names are the tree you hide things with. Colours are kept, see-through ones too (glass), and the viewer picks one for parts without. `tracepaper.views.save_scene(assembly, path, meta)` writes a CadQuery assembly and its `.tp.json` without the viewer ever reading half a file.

[`tests/geometry/make.py`](tests/geometry/make.py) writes the test scenes, with CadQuery: one solid on each kind of surface; a bracket with a named section and its dimensions; freeform surfaces and a curve; a hinge three levels deep whose frames open its door; a 2D profile with dimensions, notes and a stress field with one value per element; a snap clip pushed into its groove, with its force, strain and pull-out force per frame and limits both ways, the strain in its arm as a field, a key as notes, dimensions of each frame's own, its arm's centre line as a Bézier curve with its ends and handles as points, the contact point, and the bump's push on the hook as forces where they touch; the same clip 0.35 times as big, a detail 15 mm across with a dozen dimensions; 64 copies of one part; a cantilever with its stress, its displacement as arrows, the load along its top as a band and the support's reactions as forces; and a window's corner in the colours CAD gives it, with see-through glass and a seal left without one.

## Writing older sheets

Anything that writes SVG following [SPEC-v0.md](SPEC-v0.md) works. For Python there's a small helper, `tracepaper.sheet`, which supports layers, sublayers and named parts for the viewer's tree; the viewer's drawings are made with it too. Two examples use it:

- [`examples/bracket/make_sheets.py`](examples/bracket/make_sheets.py) draws a section and a top view of a bracket.
- [`examples/cabinet/make_views.py`](examples/cabinet/make_views.py) writes a cabinet as a model and a section with nested groups that have the same names in both.

Slicer projects come from the slicer's own command line, for OrcaSlicer `orca-slicer --load-settings "machine.json;process.json" --load-filaments filament.json --export-3mf plate.3mf pieces.stl …`. The viewer reads them itself (`tracepaper/plate.py`), and never slices or prints.

Models are written by the CAD code itself. For CadQuery, use `tracepaper.model.export(assembly, "bracket.glb", hidden=["screw"])`: it writes the assembly and marks the listed parts as hidden at first. See [`examples/bracket/make_model.py`](examples/bracket/make_model.py), which needs CadQuery. The 3D view uses [three.js](https://threejs.org) 0.170.0, bundled in `tracepaper/viewer/vendor/` (MIT).

## Tests

```sh
python3 -m unittest discover tests
```

`test_sheet.py` checks that the Python writer and the viewer draw annotations identically; this needs `gjs`. `test_scene.py` checks sections, projections and drawings on the scenes in `tests/geometry`; this needs OCP. `test_browser.py` runs the viewer in headless Firefox. Each is skipped if its tool is missing.
