---
name: tracepaper
description: Show CAD geometry to the user in the Tracepaper viewer, as scenes (exact STEP files of the model, which the viewer shows in 3D and draws as exact 2D sections and projections), with dimensions, frames (variants or motion along an axis, with per-frame values and plots) and fields (FEM results), and slicer projects for the 3D printer (.3mf); the user sketches on them with the mouse or a reMarkable pen, and you turn their sketches back into real changes in the project's CAD model. Use when the user mentions Tracepaper, scenes, sheets, views, a sketch they drew or saved, *.sketch.svg files, or wants to see, review or mark up a part, assembly, section, profile, print plate or 3D result of a CAD model, for example instead of stopping at a debugger breakpoint.
---

# Tracepaper

Tracepaper is a visual aid between the user and you. The project's build writes **views** of the model into its **build folder** (for example `build/`), among its other output:

- **Scenes** (`.step`): the model's geometry, exact, as STEP files, with a little JSON beside them (`<name>.tp.json`) for what STEP can't say: dimensions, named sections, frames, fields. The viewer shows a scene in 3D, and draws exact 2D drawings of it: the sections and projections you name, and any the user opens from the 3D view. A scene whose faces all lie in one plane, without solids, is a **2D scene**, shown as a drawing. The user sketches roughly what they want changed on any of these and presses Save. They then tell you what they drew, and you make the change in the real CAD model.
- **Slicer projects** (`.3mf`): what goes to the 3D printer, as OrcaSlicer or Bambu Studio saves it. The viewer shows the printer's plate and where the pieces sit on it, so the user can check the layout. They open the same file in the slicer to print.

Scenes hold geometry, and the parts' colours if the project gives them. Everything else about how they look (line widths, hatching, hidden lines) is the viewer's business, the same for every project, so don't try to style anything else.

Tracepaper never holds the model. It's a lens on whatever pipeline the project uses, for example build123d, CadQuery, FreeCAD or plain code. The user opens the build folder in the viewer, which shows it as a tree of the views and its subfolders; each view opens in its own browser window and updates by itself when the build rewrites its files.

Older builds write 2D sheets as layered `.svg` files and 3D models as `.glb` files (`SPEC-v0.md`). The viewer still shows them, but write scenes for anything new, and move a project's views over to scenes when you next work on them, if the user agrees.

## Before anything else: read the spec

Read `SPEC.md` in this skill's folder every time you start working with views in a session. It links to the live spec in the Tracepaper repository, which is still being developed. Don't rely on memory of an older version. `SPEC-v0.md` describes the older views, and the SVG that drawings and sketch files come as.

The repository (the folder `SPEC.md` links into) also has test scenes: `tests/geometry/make.py` writes them, with CadQuery, and their `.tp.json` files show named sections with dimensions (`bracket`), a 2D scene with dimensions, notes and a field with one value per element (`profile`), frames (`hinge`, opening its door), and frames with values and limits both ways, a strain field, dimensions and notes of each frame's own, a Bézier curve with its ends and handles as points, a contact point, and contact forces at bare points (`clip`, a snap clip pushed into its groove), a cantilever with stress on quadrilaterals, its displacement as arrows, a load along a line and reactions at an edge's nodes (`beam`), a small detail, 15 mm across, densely dimensioned (`snap`), and parts in their own colours, with see-through glass (`corner`).

## Tools

- **`tracepaper view build`** (the build folder, or one view) starts the viewer, a local web page. Given a folder, it opens the project page, which shows the folder's tree of views.
  - Run it in the background and give the user the URL it prints. It needs OCP, OpenCascade's Python binding, which CadQuery and build123d install, so run it with the Python that has them.
  - If a viewer already runs for the same directory (`pgrep -af "tracepaper view"`), reuse it.
  - The user connects their reMarkable 2 from a view's toolbar when they want to draw with it, and disconnects it there or on the tablet. A viewer doesn't hold the tablet otherwise, and connecting in one takes it from any other, so you never need to stop a viewer for the tablet. While connected, the tablet's e-ink screen shows the drawing the pen draws in, in greys; over a 3D view, only the frame of where the pen draws.
  - The pages reload views when their files change on disk, so you never need to restart the viewer for that.
  - What the user can do in a window, should you want to point them to it: in the tree, an eye hides or shows a node with everything inside it, also a node inside a hidden one; `Ctrl`+click on a name shows only that node, `Alt`+click hides it, as in the OCP viewer. In a 3D view: the standard directions (keys `0`, `7`, `1`, `3`), Fit (`F`), Edges (`E`), Transparent (`T`), Perspective (`O`; sketches are always drawn orthographic), Slice, below the tree, with *Section only*, and **Drawing** (`D`), which opens the exact drawing of what the view shows: the section through the one slice plane that is on, or the scene from the standard direction it looks from. On a drawing, **Measure** (`R`): picks snap to end points, centres, points and annotations' points, or onto lines and curves, and give the distance (Δx, Δy) and the angle at the middle of three picks, so the user can check a gap without asking you. *A−* and *A+* make the annotations' text smaller or larger (the browser remembers it per view), so a drawing whose details are small next to its extent doesn't need splitting for its labels. With frames: a slider per axis, ◀ ⏵ ▶ and the arrow keys, values and plots (against an axis or another value), and *Trail* (of everything or one part). With fields: range, *Only outside its limits*, *Isolines*, *Mesh*, *Deformation ×*, for vectors *Arrows*, *Length ×*, *Values* and *Colours*, and the value under the pointer.
  - After Tracepaper itself was updated, a viewer that was already running has to be restarted: if its pages fail to load or show errors, restart it.
- **`tracepaper render build/part.step#view`** draws a view of a scene as the viewer does, as SVG and PNG, and says what is wrong in the `.tp.json` if anything. `--frame N` draws one of its frames. Without `#view`, a 2D scene is drawn in its plane and a 3D one isometric. Look at the PNG before telling the user a view is ready.
- **`tracepaper inspect [file or dir]`** reports on the newest sketch file under the given path: strokes with colour, coordinates, the frame they were drawn at, and the element their colour was picked from; moved labels; and the nodes hidden, as well as those shown again inside a hidden one. It also renders the whole view with the ink, a close-up of the ink, and the close-up with the drawing faded. Look at the images with your file-reading tool.
- **`tracepaper.views.save_scene(assembly, path, meta)`** writes a CadQuery assembly as a scene's STEP file, and `meta` (a dict) as its `.tp.json`, each to a temporary name first and renamed into place. Use it for frames' STEP files too, without `meta`. With build123d or another pipeline, write the STEP file and the JSON the same way: under a name ending in `.tmp`, then `os.replace`.

## The build folder

The views go into the project's build folder itself, such as `build/`, next to the build's other output (STL files, caches), and the viewer shows that folder: `tracepaper view build`. If the project has no build folder yet, make one. The user sees it as a tree, so its layout is part of what you show them.

- Only `.step`, `.3mf`, `.svg` and `.glb` files show up, and the subfolders, empty ones too; everything else in the build folder is left out, as is any folder whose name starts with a dot. So every file there with one of those endings must be a view: STEP files the build writes for other uses (for printing, for other CAD programs) go into a dot folder, such as `build/.step/`, or somewhere else.
- Write each file somewhere the viewer skips first, then rename it into place (`os.replace`), so the viewer never reads half a file: under a name ending in `.tmp`, or in a dot folder. `save_scene` does this itself.
- Group the views in subfolders by what they're for, for example the whole assembly and its parts in `window/`, print plates in `print/`, and test pieces in `tests/`. A handful of views can stay at the top. When a flat folder has grown past about ten views, propose subfolders to the user.
- Never empty the build folder on a rebuild (`rm -rf build`, `shutil.rmtree`, a `clean` target): the user's sketch files wait next to their views until you've handled them. Write each view over its old file, and delete only views the build no longer makes.
- Keep a scene's name, folder and named views stable, since the user's open windows and any sketch file are tied to them. Move or rename them only once their sketch files are handled, and tell the user, because their open windows still show the old one.

## Making scenes

1. Find where the real model lives. Write a small script in the user's project that writes its scenes, so they can be rewritten after every change, or add it to the build. Put its command in the `.tp.json`'s `regenerate`.
2. Write each scene as a STEP file of what the user should look at, not every intermediate shape: an assembly with its parts **named** and **grouped** into subassemblies as the user thinks of them, since the tree is what they hide things with. The top assembly itself isn't shown, so its children are the top level. Use separate scenes for separate things, for example the whole window and one corner joint. Colours are optional: the viewer keeps those the STEP file gives (CadQuery's `color=`, build123d's `color`), a see-through one makes a part see-through in 3D, as glass (`cq.Color(r, g, b, 0.35)`), and parts without one get a colour of the viewer's palette.
   - Curves (wires, edges) in a scene are drawn as reference geometry, thin and dashed, in their part's colour: an axis, a path, an older design's outline. Points (vertices) are drawn as dots: control points, a contact point. Put different kinds in parts of their own, named for what they are (`centre line / ends`, `centre line / handles`), so the user tells them apart by colour and name.
   - For a 2D view of something that isn't a plane of the model, such as a profile or a sketch, write a 2D scene: faces in one plane, z = 0, named per part.
3. Write its `.tp.json` (spec §2.3): `title`, `source`, `pipeline`, `regenerate`; `hidden` for what starts hidden ("glass hidden"); `notes`, text drawn under every drawing, for a key to the letters in the dimensions or what changed since the last version; and `views`, the sections and projections worth naming, with their `annotations`: dimensions (`linear`, `radius`, `diameter`, `angular`: an angle at a vertex, counter-clockwise from the leg through p1 to the one through p2) and constraint symbols, in the view's own coordinates (spec §2.4). Agree with the user on which views and dimensions to show. Give annotations stable ids and `source` pointers back into the model.
4. For something that moves or varies (an insertion, an opening angle, design variants), write **frames** (spec §2.10): the whole scene as a STEP file at each value of the axes, in a dot folder such as `build/window/.clip/`, listed in `frames` with `at`, and `values` per frame for quantities worth plotting, with `min` and `max` where they have limits. A frame can give its own `annotations`, `notes` and `views`, for dimensions that follow a moving or bending part. Keep the scene's own STEP file as the first frame.
5. For calculated values over the geometry (strain, stress, temperature), write **fields** (spec §2.11): a `.vtu` file of the mesh with point data (or cell data, one value per element), per frame if it changes, and a `displacement` vector if the mesh moves. A vector `value` is drawn as arrows. For forces where parts touch, or loads and reactions on an edge, write a mesh of their own: the nodes as bare points (VTK vertex cells), or the edge as lines, with a vector at each node; those arrows end at their nodes, as forces are drawn, and up to about a hundred of them are all drawn. A number on lines is drawn as a band along them (a contact pressure).
6. Check every view with `tracepaper render` and look at the images before telling the user they're ready. The user may look at a drawing on the reMarkable's screen, in greys: the viewer's hatching and line styles keep parts apart, but say in words what isn't otherwise clear.

## When the user says they sketched something

1. Run `tracepaper inspect build` and look at all three images. It takes the newest sketch file anywhere under the folder. Its list of what was hidden tells you what the user wasn't looking at; "shown inside hidden" lists nodes they showed again inside a hidden one.
2. Work out what they want:
   - Strokes are rough: they give shape and position, not exact sizes.
   - Handwriting next to a sketch, like "2", "R5" or "45°", is a value.
   - Arrows from an existing dimension to new geometry usually mean "same as this".
   - A stroke's colour picked from the drawing (`colour of …`) names the element whose colour they drew in, which usually means it belongs with or extends that element.
   - On a drawing, stroke coordinates are the drawing's, exact: `tp:view` maps them into the model (point = origin + x·xdir + y·(normal × xdir)). The sketch file's name says which view: `part.step#section-a.sketch.svg` was drawn on the named view section-a, `part.step#x=12.5.sketch.svg` on a section the user opened themselves, `part.step.sketch.svg` on a 2D scene or over the 3D view.
   - A sketch over a 3D view (`tp:view` kind `model view`): its View layer is a picture of what they saw, and the strokes are in mm on the plane they looked at, which `tp:view` places in the model, but not how deep along `normal` they meant it: tell that from the picture, from what lies under the strokes, and from what they said. From a standard direction the coordinates are CAD's own (from the right: x is CAD y, y is CAD z). In a section (`slices` says `section at …`) seen straight on, the plane is the section's and the depth is exact. `hidden` and `slices` say what they had hidden or cut away, `shown` what they showed again inside a hidden node, and `transparent="true"` that every face was see-through.
   - With frames, `frame` in `tp:view` says which frame was shown when they saved, and each stroke which frame it was drawn at. Ink stays on screen as they step through frames, so a stroke can be meant for all of them (a wall the moving part should clear), or for the one it was drawn at; ask if it isn't clear.
3. Change the real model in the project's own pipeline. Take sizes from what they wrote or said; otherwise pick sensible values, using the sketch's rough size as a first guess.
4. Rewrite all affected scenes. Carry over any label positions they dragged ("Moved labels": write the new `offset`, `text-at` or `angle` into the `.tp.json`).
5. Delete the sketch file. Its existence means "not handled yet", and the viewer clears the ink when it disappears.
6. Tell the user how you read the sketch and what you changed, including any value you chose yourself. They'll sketch again or correct you if it's off.

## Making slicer projects (.3mf)

When the user wants to print pieces, the build lays them out and writes the slicer project; the user checks it in the viewer, then opens it in their slicer and prints from there. Tracepaper never slices or prints, and you don't press print either.

- Run the slicer's command line in a dot folder of the build folder, such as `build/.slicer`, which the viewer skips, and move the project it writes into place, for example as `build/print/<name>.3mf` (`os.replace`), so the viewer never reads half a file.
- With OrcaSlicer: `orca-slicer --load-settings "machine.json;process.json" --load-filaments "filament.json" --arrange 0 --orient 0 --export-3mf <file> <pieces.stl …>`.
  - Its command line doesn't follow a preset's `inherits`, so merge each preset with its parents yourself.
  - It leaves `result.json` and a log in its working directory, the dot folder: check `result.json` for errors.
  - `--slice 0` also slices every plate into the file, which shows Orca's warnings in `result.json` and `Metadata/slice_info.config`.
- The STL files' names become the pieces' names in the viewer's tree (without `.stl`), so name them after the pieces.
- The viewer shows the pieces in their filament's colour, the part of the plate the printer keeps for itself in orange, and the build volume, hidden at first: check tall pieces against `printable_height` yourself.
- Spec §2.9 has the details.

## Don't edit Tracepaper itself

The `tracepaper` command, this skill and the spec come straight from the Tracepaper repository, which the user develops in a separate session. Don't change files there from another project. If the tooling misbehaves or the spec is missing something, tell the user instead.
