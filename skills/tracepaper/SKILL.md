---
name: tracepaper
description: Show CAD geometry to the user in the Tracepaper viewer, as 2D sheets (layered SVG drawings), 3D models (.glb) and slicer projects for the 3D printer (.3mf), which they sketch on with the mouse or a reMarkable pen, and turn their sketches back into real changes in the project's CAD model. Use when the user mentions Tracepaper, sheets, views, a sketch they drew or saved, *.sketch.svg files, or wants to see, review or mark up a part, assembly, section, profile, print plate or 3D result of a CAD model, for example instead of stopping at a debugger breakpoint.
---

# Tracepaper

Tracepaper is a visual aid between the user and you. The project's build writes **views** of the model into its **build folder** (for example `build/`), among its other output:

- **Sheets** (`.svg`): 2D drawings of the current geometry and the constraints worth seeing. The user sketches roughly what they want changed and presses Save. They then tell you what they drew, and you make the change in the real CAD model.
- **Models** (`.glb`): 3D views of the result, so the user can look at what the build produced without a debugger. They can slice them, and sketch over the view the same way, which saves `<model>.glb.sketch.svg`.
- **Slicer projects** (`.3mf`): what goes to the 3D printer, as OrcaSlicer or Bambu Studio saves it. The viewer shows the printer's plate and where the pieces sit on it, so the user can check the layout. They open the same file in the slicer to print.

Tracepaper never holds the model. It's a lens on whatever pipeline the project uses, for example build123d, CadQuery, FreeCAD, DXF or plain code. The user opens the build folder in the viewer, which shows it as a tree of the views and the subfolders holding them; each view opens in its own browser window and updates by itself when the build rewrites its file.

## Before anything else: read the spec

Read `SPEC.md` in this skill's folder every time you start working with sheets or models in a session. It links to the live spec in the Tracepaper repository, which is still being developed. Don't rely on memory of an older version.

The repository (the folder `SPEC.md` links into) also has worked examples:

- `examples/bracket/` holds two sheets and a model.
- `examples/cabinet/make_views.py` writes a model and a section whose nested groups have the same names in both.

## Tools

- **`tracepaper view build`** (the build folder, or one view) starts the viewer, a local web page. Given a folder, it opens the project page, which shows the folder's tree of views.
  - Run it in the background and give the user the URL it prints.
  - If a viewer already runs for the same directory (`pgrep -af "tracepaper view"`), reuse it.
  - The user connects their reMarkable 2 from a view's toolbar when they want to draw with it, and disconnects it there or on the tablet. A viewer doesn't hold the tablet otherwise, and connecting in one takes it from any other, so you never need to stop a viewer for the tablet. While connected, the tablet's e-ink screen shows the sheet the pen draws in, in greys; over a 3D view, only the frame of where the pen draws.
  - The pages reload views (sheets, models and slicer projects) when they change on disk, so you never need to restart the viewer for that.
  - What the user can do in a window, should you want to point them to it: in the tree, an eye hides or shows a node with everything inside it, also a node inside a hidden one; `Ctrl`+click on a name shows only that node, `Alt`+click hides it, as in the OCP viewer. In a 3D view: the standard directions (keys `0`, `7`, `1`, `3`), Fit (`F`), Edges (`E`), Transparent (`T`, every face half see-through, to look inside), Perspective (`O`, instead of orthographic; sketches are always drawn orthographic), and Slice, below the tree, with *Section only* for just the cut faces.
  - After Tracepaper itself was updated, a viewer that was already running has to be restarted: if its pages fail to load or show errors, restart it.
- **`tracepaper inspect [file or dir]`** reports on the newest sketch file under the given path: strokes with colour, coordinates and the element their colour was picked from, moved labels, and the nodes hidden, as well as those shown again inside a hidden one. It also renders the whole sheet (or, for a sketch over a model, the view they drew on) with the ink, a close-up of the ink, and the close-up with the drawing faded. Look at the images with your file-reading tool.
- **`tracepaper.sheet`** is a Python helper for writing sheets (`from tracepaper.sheet import Sheet, PathBuilder, HATCH`). It's optional; anything that writes SVG following the spec works.
- **`tracepaper.model`** writes models: `export(assembly, path, hidden=[...])`, see below.
- **`rsvg-convert -b white -w 2000 sheet.svg -o sheet.png`** renders a sheet so you can check it before showing it.

## The build folder

The views go into the project's build folder itself, such as `build/`, next to the build's other output (STEP and STL files, caches), and the viewer shows that folder: `tracepaper view build`. If the project has no build folder yet, make one. The user sees it as a tree, so its layout is part of what you show them.

- Only `.svg`, `.glb` and `.3mf` files show up, and only the folders holding them; everything else in the build folder is left out. So every file there with one of those endings must be a view: the build's other SVG, glTF or 3MF files, such as intermediate meshes, go somewhere else.
- Write each view somewhere the viewer skips first, then rename it into place (`os.replace`), so the viewer never reads half a file: under a name ending in `.tmp`, or in a folder whose name starts with a dot, such as `build/.slicer`, which the viewer never looks into. `tracepaper.sheet` and `tracepaper.model` do this themselves.
- Group the views in subfolders by what they're for, for example the whole assembly and its sections in `window/`, print plates in `print/`, and test pieces in `tests/`. A handful of views can stay at the top. When a flat folder has grown past about ten views, propose subfolders to the user.
- Never empty the build folder on a rebuild (`rm -rf build`, `shutil.rmtree`, a `clean` target): the user's sketch files wait next to their views until you've handled them. Write each view over its old file, and delete only views the build no longer makes.
- Keep a view's name and folder stable, since the user's open window and any sketch file are tied to it. Move or rename views only once their sketch files are handled, and tell the user, because their open windows still show the old file.

## Making sheets

1. Find where the real model lives and how to get 2D views from it: sections, projections, sketches.
2. Write a small script in the user's project that produces the sheets from the model, so they can be regenerated after every change. Put its command in `tp:view regenerate`.
3. Follow the spec:
   - model coordinates in mm, y up, each top-level layer flipped with `transform="scale(1,-1)"`, and no transforms on the groups inside;
   - stable, meaningful ids, and `tp:source` pointers back into the model;
   - dimensions and constraint symbols as annotation groups the user can drag: `linear`, `radius`, `diameter` and `angular` (an angle at a vertex, counter-clockwise from the leg through p1 to the one through p2) dimensions and `symbol`s on a `tracepaper.sheet` layer;
   - line widths and dashes in pixels on screen, not millimetres, since lines stay the same width at any zoom. Leave `stroke-width` out for the default 1 px, use 1.5–2 px for outlines and highlights, and dashes like `6 4`. `tracepaper.sheet` marks every shape with `vector-effect="non-scaling-stroke"`; SVG written by hand needs it on each element with a stroke.
     - A sheet script written before widths were pixels sets them in millimetres: values below 1, such as `0.25`, or dashes like `1.2 0.8`. Those now come out as faint hairlines and tiny dashes, so convert them to pixels (for example 0.25 → 1.5, 0.1 → 1, `1.2 0.8` → `6 4`) and regenerate.
4. Show what helps the user judge the design. Put the rest on hidden layers or leave it out, and agree on this with them. The user may look at a sheet on the reMarkable's screen, where colours become greys, so don't tell things apart by colour alone: vary line weight or dashes, or label them.
5. Structure the sheet as a tree. Every named group is a node the user can hide in their window, so group what belongs together:
   - Use sublayers for related things, such as `layer.sublayer("Overall")` for the dimensions of each part.
   - Give each part cut by a section a group of its own holding both its outline and its hatching: `part = layer.sublayer("shelf 1")`, then `part.path(..., fill=HATCH, stroke="none")` and `part.path(...)` for the outline.
   - When a sheet and a model show the same parts, give them the same names.
6. Set what starts hidden with `visible=False` on a layer or sublayer, which writes `display:none`.
7. Render the sheets and look at them before telling the user they're ready.

## Making models (3D views)

- At the end of the build, write the assembly the user wants to look at into the build folder with `tracepaper.model.export(assembly, "build/window/window.glb", hidden=[...])`. It writes a CadQuery assembly safely: to a temporary file first, then renamed, so the viewer never reads half a file.
- `hidden` lists what starts hidden when the user opens the model, as paths in the tree (`"Steel / Z profile"`) or plain names (`"Glass"` hides every part called Glass). Set it when the user asks for a default, such as "glass hidden".
- Write only what they'd look at, typically the coloured assembly, not every intermediate shape. Use separate files for separate things, for example the whole window and one corner joint.
- Name the assembly's parts and group them into subassemblies, since that becomes the tree the user hides things with. The top assembly itself isn't shown, so its children are the top level, which is all the user sees until they unfold a node. Give the parts colours, and different ones to parts that sit side by side: the tree shows each part's colour beside its name, and a slice closes cut parts in it. A part with a transparent material, such as glass, isn't closed when cut.
- Spec §2.8 has the details: millimetres, z up, faces as separate primitives, which CadQuery all does by default.

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

## When the user says they sketched something

1. Run `tracepaper inspect build` and look at all three images. It takes the newest sketch file anywhere under the folder. Its list of what was hidden tells you what the user wasn't looking at; "shown inside hidden" lists nodes they showed again inside a hidden one.
2. Work out what they want:
   - Strokes are rough: they give shape and position, not exact sizes.
   - Handwriting next to a sketch, like "2", "R5" or "45°", is a value.
   - Arrows from an existing dimension to new geometry usually mean "same as this".
   - A stroke's `tp:color-from` names the element whose colour they drew in, which usually means it belongs with or extends that element.
   - `tp:view` maps sheet coordinates into 3D.
   - A `<model>.glb.sketch.svg` (or `<project>.3mf.sketch.svg`, over a slicer project, in the slicer's coordinates) was drawn over a 3D view: its View layer is a picture of what they saw, and the strokes are in mm on the plane they looked at. `tp:view` places that plane in the model (point = origin + x·xdir + y·(normal × xdir)), but not how deep along `normal` they meant it: tell that from the picture, from what lies under the strokes, and from what they said. From a standard direction the coordinates are CAD's own (from the right: x is CAD y, y is CAD z). In a section (`slices` says `section at …`) seen straight on, the plane is the section's and the depth is exact. `hidden` and `slices` say what they had hidden or cut away, `shown` what they showed again inside a hidden node, and `transparent="true"` that every face was see-through, so the picture shows what's inside.
3. Change the real model in the project's own pipeline. Take sizes from what they wrote or said; otherwise pick sensible values, using the sketch's rough size as a first guess.
4. Regenerate all affected sheets and models. Put the new feature and its dimensions on a **Changes** layer, which the `tracepaper.sheet` helper draws in amber. Carry over any label positions they dragged (listed under "Moved labels").
5. Delete the sketch file. Its existence means "not handled yet", and the viewer clears the ink when it disappears.
6. Tell the user how you read the sketch and what you changed, including any value you chose yourself. They'll sketch again or correct you if it's off.

Once the user is happy with a change, fold the Changes layer into the normal layers on the next regeneration.

## Don't edit Tracepaper itself

The `tracepaper` command, this skill and the spec come straight from the Tracepaper repository, which the user develops in a separate session. Don't change files there from another project. If the tooling misbehaves or the spec is missing something, tell the user instead.
