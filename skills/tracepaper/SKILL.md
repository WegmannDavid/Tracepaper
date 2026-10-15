---
name: tracepaper
description: Show CAD geometry to the user as Tracepaper sheets (layered SVG drawings they view, toggle and sketch on, with the mouse or a reMarkable pen) and turn their sketches back into real changes in the project's CAD model. Use when the user mentions Tracepaper, sheets, a sketch they drew or saved, *.sketch.svg files, or wants to see, review or mark up a part, section, profile or view of a CAD model.
---

# Tracepaper

Tracepaper is a visual aid between the user and you. The project's build writes **views** of the model into a folder:

- **Sheets** (`.svg`): 2D drawings of the current geometry and the constraints worth seeing. The user sketches roughly what they want changed and presses Save. They then tell you what they drew, and you make the change in the real CAD model.
- **Models** (`.glb`): 3D views of the result, so the user can look at what the build produced without a debugger. They can't be sketched on yet.

Tracepaper never holds the model. It's a lens on whatever pipeline the project uses, for example build123d, CadQuery, FreeCAD, DXF or plain code. The user opens the folder in the viewer, which lists all views; each opens in its own browser window and updates by itself when the build rewrites its file.

## Before anything else: read the spec

Read `SPEC.md` in this skill's folder every time you start working with sheets in a session. It links to the live spec in the Tracepaper repository, which is still being developed. Don't rely on memory of an older version.

## Tools

- **`tracepaper view <views folder, or one view>`** starts the viewer, a local web page. Given a folder, it opens the project page listing the views (also in subfolders, up to three levels down).
  - Run it in the background and give the user the URL it prints.
  - Add `--remarkable` to let the user draw with their reMarkable 2. It closes the tablet's app while it runs.
  - Only one viewer may use the tablet. Check `pgrep -af "tracepaper view"` before starting one with `--remarkable`. If a viewer already runs for the same directory, reuse it.
  - The page reloads sheets when they change on disk, so you never need to restart it for that.
- **`tracepaper inspect [file or dir]`** reports on the newest sketch file under the given path: strokes with colour, coordinates and the element their colour was picked from, moved labels and hidden layers. It also renders a whole-sheet image, a close-up of the ink, and the close-up with the drawing faded. Look at the images with your file-reading tool.
- **`tracepaper.sheet`** is a Python helper for writing sheets (`from tracepaper.sheet import Sheet, PathBuilder`). It's optional; anything that writes SVG following the spec works.
- **`rsvg-convert -b white -w 2000 sheet.svg -o sheet.png`** renders a sheet so you can check it before showing it.

## Making sheets

1. Find where the real model lives and how to get 2D views from it: sections, projections, sketches.
2. Write a small script in the user's project that produces the sheets from the model, so they can be regenerated after every change. Put its command in `tp:view regenerate`.
3. Follow the spec:
   - model coordinates in mm, y up, every layer flipped with `transform="scale(1,-1)"`;
   - stable, meaningful ids, and `tp:source` pointers back into the model;
   - dimensions and constraint symbols as annotation groups the user can drag.
4. Show what helps the user judge the design. Put the rest on hidden layers or leave it out, and agree on this with them. Group related things as sublayers (`layer.sublayer("Overall")` in `tracepaper.sheet`), for example the dimensions of each part. Every named group is a node in the viewer's tree, which the user can hide in their window. When a sheet and a model show the same parts, give them the same names.
5. Render the sheets and look at them before telling the user they're ready.

## Making models (3D views)

- At the end of the build, write the assembly the user wants to look at into the views folder with `tracepaper.model.export(assembly, "views/window.glb", hidden=[...])`. It writes a CadQuery assembly safely: to a temporary file first, then renamed, so the viewer never reads half a file.
- `hidden` lists what starts hidden when the user opens the model, as paths in the tree (`"Steel / Z profile"`) or plain names (`"Glass"` hides every part called Glass). Set it when the user asks for a default, such as "glass hidden".
- Write only what they'd look at, typically the coloured assembly, not every intermediate shape. Use separate files for separate things, for example the whole window and one corner joint.
- Name the assembly's parts and group them into subassemblies, since that becomes the tree the user hides things with. Give the parts colours.
- Spec §2.8 has the details: millimetres, z up, faces as separate primitives, which CadQuery all does by default.

## When the user says they sketched something

1. Run `tracepaper inspect <project or sheet folder>` and look at all three images.
2. Work out what they want:
   - Strokes are rough: they give shape and position, not exact sizes.
   - Handwriting next to a sketch, like "2", "R5" or "45°", is a value.
   - Arrows from an existing dimension to new geometry usually mean "same as this".
   - A stroke's `tp:color-from` names the element whose colour they drew in, which usually means it belongs with or extends that element.
   - `tp:view` maps sheet coordinates into 3D.
3. Change the real model in the project's own pipeline. Take sizes from what they wrote or said; otherwise pick sensible values, using the sketch's rough size as a first guess.
4. Regenerate all affected sheets. Put the new feature and its dimensions on a **Changes** layer, which the `tracepaper.sheet` helper draws in amber. Carry over any label positions they dragged (listed under "Moved labels").
5. Delete the sketch file. Its existence means "not handled yet", and the viewer clears the ink when it disappears.
6. Tell the user how you read the sketch and what you changed, including any value you chose yourself. They'll sketch again or correct you if it's off.

Once the user is happy with a change, fold the Changes layer into the normal layers on the next regeneration.

## Don't edit Tracepaper itself

The `tracepaper` command, this skill and the spec come straight from the Tracepaper repository, which the user develops in a separate session. Don't change files there from another project. If the tooling misbehaves or the spec is missing something, tell the user instead.
