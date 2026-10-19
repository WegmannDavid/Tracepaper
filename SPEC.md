# Tracepaper format, v0 (draft)

Tracepaper is a visual aid for a person working on CAD geometry with an AI. The AI draws the current geometry and the constraints it wants to show as a **sheet**. The person opens it in the Tracepaper viewer, toggles layers, drags labels out of the way, and sketches roughly what they want changed. They save, then ask for the change in the usual way ("I sketched a chamfer on section A"), and the AI reads the **sketch file** the viewer wrote.

Tracepaper is a lens, not a model. The real geometry stays in whatever CAD pipeline the project uses (CadQuery, build123d, FreeCAD, a DXF file, plain code). A sheet may leave things out or approximate them. Mapping a sketch back into the pipeline is the AI's job. Nothing in Tracepaper solves or checks constraints.

MUST, SHOULD and MAY are used as in RFC 2119.

## 1. Files

| File | Written by | Contents |
|---|---|---|
| `<name>.svg` | the AI | a sheet (§2) |
| `<name>.sketch.svg` | the viewer, when the person presses Save | the sheet as it was on screen, plus their ink (§3) |
| `<name>.glb.sketch.svg` | the viewer, when the person presses Save on a model | a picture of the model's view, plus their ink (§3.1) |

- Both are plain SVG 1.1, plus SVG 2's `vector-effect` (§2.1), and open in any browser or in Inkscape.
- The sketch file sits next to its sheet. A sheet's file name MUST NOT end in `.sketch.svg`.
- A project also has 3D models, `.glb` files (§2.8). Sheets and models are the project's **views**; the viewer shows each in a window of its own (§5).
- **The views folder.** The views live in a folder of their own, such as `build/views/`, and in its subfolders, which group them by what they're for. Every `.svg` and `.glb` file in it is a view, so it SHOULD hold nothing else, such as STEP files or caches. The viewer shows the folder as a tree (§5).
- The build writes each view in place of the old one, and deletes only views it no longer makes. It MUST NOT empty the views folder wholesale, since sketch files wait there next to their views until the AI has handled them.
- Tracepaper attributes and elements use the namespace `urn:tracepaper:0`, by convention with the prefix `tp`. The `0` is the format version (§6).
- An existing sketch file means "not handled yet". The AI deletes it once it has acted on it. A sketch file of either kind ends in `.sketch.svg`.

## 2. Sheets

### 2.1 Root element

```xml
<svg xmlns="http://www.w3.org/2000/svg"
     xmlns:inkscape="http://www.inkscape.org/namespaces/inkscape"
     xmlns:tp="urn:tracepaper:0"
     width="99mm" height="84mm" viewBox="-22 -58 99 84"
     font-family="DejaVu Sans, Arial, sans-serif" font-size="3.5">
```

- One user unit is one millimetre: `width` and `height` MUST be in `mm` and equal the `viewBox` width and height.
- The root SHOULD set `font-family` and `font-size`. The default text height is 3.5 mm.
- Styling MUST use presentation attributes (`stroke="…"`), not CSS, so every renderer draws the sheet the same way.
- **Lines are a set width on screen, at any zoom,** as in a CAD program. `stroke-width` and the lengths in `stroke-dasharray` are in pixels on the screen (CSS pixels), not millimetres. A line without a `stroke-width` is 1 pixel wide.
- Every element with a stroke SHOULD carry `vector-effect="non-scaling-stroke"`, which tells other SVG renderers so. It isn't inherited, so it goes on each element, not on a group. The viewer draws every line this way regardless.
- Everything else is in millimetres and scales with the drawing: text, arrowheads, and the spacing of hatching.
- Sheets MUST NOT contain scripts or event-handler attributes.

### 2.2 Coordinates

Everything is drawn in model coordinates: millimetres, x to the right, **y up**. SVG's y axis points down, so every layer carries `transform="scale(1,-1)"` (§2.4). As a result:

- A sheet showing model y from `y0` to `y1` has a `viewBox` y range from `-y1` to `-y0`.
- Text has to be flipped back upright: `transform="translate(x y) scale(1,-1)"`, or `translate(x y) rotate(a) scale(1,-1)` for text at angle `a` (degrees, counter-clockwise).
- In path data, an arc's `sweep-flag` of 1 means counter-clockwise in model coordinates.

### 2.3 Metadata

```xml
<metadata>
  <tp:view title="Bracket, section A-A" source="parts/bracket.py" pipeline="build123d"
           rev="git:3f2a9c1" regenerate="python3 tools/sheets.py bracket"
           kind="section" origin="0 15 0" normal="0 -1 0" xdir="1 0 0"/>
</metadata>
```

Every attribute is optional free text unless stated otherwise.

| Attribute | Meaning |
|---|---|
| `title` | Shown to the person. |
| `source` | Where the real model lives: a path, URL or document name. |
| `pipeline` | What produced it: `build123d`, `FreeCAD`, `DXF`, `python`, … |
| `rev` | The version of the source the sheet was made from (`git:<hash>`, a timestamp, …). |
| `regenerate` | How the AI rebuilds this sheet. The viewer never runs it. |
| `kind` | `section`, `projection`, `sketch`, `detail`, … |
| `origin`, `normal`, `xdir` | Three numbers each. They place the sheet in 3D: model point = origin + x·xdir + y·(normal × xdir), with `normal` pointing toward the viewer. Omit them for purely 2D sources. |

### 2.4 Layers

```xml
<g id="layer-outline" inkscape:groupmode="layer" inkscape:label="Outline"
   transform="scale(1,-1)" stroke="#1a1a1a" stroke-width="1.5" fill="none">
```

- Layers are `<g>` elements with `inkscape:groupmode="layer"`. `inkscape:label` is the name the person sees. This is Inkscape's convention, so Inkscape shows the same layers.
- **The tree.** A sheet's nodes are its *named groups*: `<g>` elements with an `inkscape:label`. The top-level nodes are the layers. Below them, every named group is a node too, whether it's a sublayer (`inkscape:groupmode="layer"`, as Inkscape writes them) or a named part holding several drawings of it. Unnamed groups in between don't count: the named groups inside them belong to the nearest named group above. Models have the same kind of tree (§2.8), and the viewer shows both in the same panel.
- Order is drawing order: later nodes are drawn on top. The viewer lists the topmost first. Hiding a node hides everything in it.
- Every top-level layer MUST have exactly `transform="scale(1,-1)"`. Nested groups SHOULD NOT have a `transform`, so everything stays in model coordinates.
- A node that starts hidden has `style="display:none"`. The AI decides what starts visible. The person can hide or show any node in their window; that choice isn't stored.
- All drawing SHOULD be inside layers.
- The label `Sketch` and the id `tp-sketch` are reserved for the person's ink (§3). It is always a top-level layer.

Suggested layer names (others are fine):

| Layer | For |
|---|---|
| Outline | visible edges |
| Hidden | hidden edges, dashed |
| Center | centre lines, dash-dot |
| Construction | construction geometry, often hidden by default |
| Hatch | section hatching |
| Section lines | where other sheets are cut |
| Dimensions | dimensions |
| Constraints | geometric constraint symbols |
| Notes | text notes |
| Changes | what the AI just changed, highlighted, so the person can check it |

### 2.5 Geometry

Any SVG shape may be used: `path`, `line`, `circle`, `ellipse`, `polyline`, `polygon`, `text`.

- `id`: elements the person might refer to SHOULD have a stable, meaningful id (`mount-hole`, `flange-outer`) that survives regenerating the sheet.
- `tp:source`: an OPTIONAL free-text pointer back into the pipeline, in whatever form identifies the thing there: `tag:mount_hole`, `Sketch001.Edge3`, `DXF handle 2F`, `bracket.py:42`.

### 2.6 Annotations

An annotation is a dimension or a constraint symbol: a `<g>` placed directly in a layer, without a `transform`. Its `tp:` attributes say what it is and where it goes. Its children are pre-drawn graphics, which ordinary SVG viewers show.

The Tracepaper viewer shows those children too, until the person drags the annotation. It then redraws the children from the attributes (§2.7) and updates the placement attributes (`tp:offset`, `tp:text-at`, `tp:angle`). Colour, line width and font size belong on the group, or are inherited from the layer, never on the children, so a redrawn annotation keeps its look.

Attributes common to all annotations:

| Attribute | |
|---|---|
| `id` | REQUIRED, and stable, so moved labels can be carried into the next version of the sheet. |
| `tp:type` | What it expresses. Free-form; see the defaults below. |
| `tp:refs` | Ids of the elements it relates to, space-separated. MAY name a part: `hole.center`, `edge3.start`. |
| `tp:value` | The value, as a number or a parameter name. |
| `tp:label` | The text shown. |
| `tp:source` | As for geometry. |

**Linear dimension** (`tp:dim="linear"`):

| Attribute | |
|---|---|
| `tp:p1`, `tp:p2` | The measured points, `"x y"`. |
| `tp:dir` | `x` (horizontal), `y` (vertical) or `aligned` (along p1→p2, the default). |
| `tp:offset` | Signed distance of the dimension line from p1, along n (defined below). Default 0. |
| `tp:text-at` | Label position along the dimension line: 0 at the p1 end, 1 at the p2 end. Default 0.5. Values outside 0–1 put the label beyond the extension lines. |

Let u be the unit measuring direction: (1, 0) for `x`, (0, 1) for `y`, p1→p2 for `aligned`. Let n = (−u.y, u.x). The dimension line then runs from a = p1 + offset·n to b = p2 + (p1·n + offset − p2·n)·n. So a positive offset puts a horizontal dimension above p1, and a vertical one to its left.

**Radius and diameter** (`tp:dim="radius"` or `tp:dim="diameter"`):

| Attribute | |
|---|---|
| `tp:center` | `"x y"` |
| `tp:r` | Radius. |
| `tp:angle` | Where the leader meets the circle, in degrees counter-clockwise from +x. Default 45. |
| `tp:offset` | Distance from that point to the label end of the leader, measured outward from the centre. Negative values run back through the centre, which suits fillets. Default 0. |

**Constraint symbol** (any annotation group with `tp:anchor`):

| Attribute | |
|---|---|
| `tp:anchor` | The point the symbol belongs to, `"x y"`. |
| `tp:offset` | Label position relative to the anchor, `"dx dy"`. Default `"0 0"`. A leader is drawn when the label is far from the anchor. |
| `tp:label` | The symbol, e.g. `⊥`. |

Other annotations (angular dimensions, balloons, notes) MAY be drawn as plain graphics. The viewer shows them but doesn't let the person move them.

**Default vocabulary.** `tp:type` is free-form, so an AI can use any type, including a sentence. These defaults keep sheets consistent:

| `tp:type` | Shown as |
|---|---|
| `distance` | linear dimension |
| `angle` | value with a degree sign, e.g. `30°` |
| `radius` | `R5` |
| `diameter` | `⌀6` |
| `horizontal` | `H` |
| `vertical` | `V` |
| `parallel` | `∥` |
| `perpendicular` | `⊥` |
| `tangent` | `T` |
| `equal` | `=` |
| `coincident` | `●` |
| `concentric` | `◎` |
| `symmetric` | `S` |
| `midpoint` | `M` |
| `fixed` | `F` |
| `note` | free text, for constraints best said in words ("holes evenly spaced on a bolt circle") |

### 2.7 Drawing annotations

Sizes are multiples of the font size fs (3.5 mm by default).

| Part | Drawing |
|---|---|
| Arrowheads | filled triangles, 0.75 fs long and 0.3 fs wide |
| Extension lines | start 0.3 fs from the measured point and end 0.5 fs past the dimension line |
| Linear arrows | inside the extension lines if the dimension line is at least 2 fs long; otherwise outside and pointing in, with 0.6 fs of line beyond each |
| Linear label | centred at `text-at`, baseline 0.25 fs above the dimension line, rotated with it and kept readable (−90° < angle ≤ 90°) |
| Radial label | on a horizontal shoulder at the end of the leader, on the side away from the circle |
| Diameter arrows | inside the circle pointing out, or outside pointing in when the diameter is under 2 fs |
| Symbol | label centred on anchor + offset; a leader from the anchor, with a dot, when the offset is longer than 1.2 fs |

The reference implementations are `tracepaper/sheet.py` (for writing sheets) and `tracepaper/viewer/annotations.js` (in the viewer). They produce the same paths for the same attributes, which `tests/test_sheet.py` checks.

### 2.8 3D models

A model is a glTF 2.0 binary file (`.glb`) showing a part or an assembly in 3D. The person can look at it, slice it and sketch over its view (§3.1). There's no Tracepaper metadata in models yet, except which nodes start hidden.

- **What to write:** only what the person should look at, typically one coloured assembly per file, not every intermediate shape. With CadQuery: `assembly.export("views/frame.glb")`.
- **Units and orientation:** millimetres. CAD's z-up coordinates are turned into glTF's y-up by a rotation on the root node, as CadQuery (OpenCascade) writes it. Fronts face −y in CAD coordinates.
- **The tree:** a model's nodes are its named glTF nodes, which form the assembly tree. Unnamed nodes don't count, and if there is a single top node (the assembly itself), it's left out so that its parts are the top level. A node's path is its names joined with ` / `, as in `Steel / Z profile`. Colours come from the materials.
- **Hidden at first:** a node marked `"extras": {"tracepaper": {"hidden": true}}` starts hidden. `tracepaper.model.export(assembly, path, hidden=[…])` writes a CadQuery assembly with such marks, taking paths or plain names (a plain name hides every node of that name).
- **Faces:** each CAD face SHOULD be its own primitive, as OpenCascade writes it. The viewer draws each primitive's outline as the model's edges.
- **Writing:** write to a temporary file and rename it into place, so the viewer never reads half a model.

## 3. Sketch files

When the person presses Save, the viewer writes `<name>.sketch.svg` next to `<name>.svg`:

- **The sheet as it was on screen.** Layers the person hid have `style="display:none"`. Annotations they dragged carry new placement attributes and redrawn graphics. Scripts and event-handler attributes are removed.
- **Root attributes.** The root gets `tp:sketch-of="<name>.svg"` and `tp:saved="<ISO 8601 time>"`.
- **Size.** The viewer MAY enlarge `viewBox`, `width` and `height` so that ink drawn past the edge of the sheet is kept.
- **Ink layer.** The ink is the last layer:

```xml
<g id="tp-sketch" inkscape:groupmode="layer" inkscape:label="Sketch" transform="scale(1,-1)">
  <path stroke="#e11d48" stroke-width="2.2" vector-effect="non-scaling-stroke" fill="none"
        stroke-linecap="round" stroke-linejoin="round" d="M-1 37.5 L2.5 41 L3 41.5"/>
</g>
```

Each stroke is one `path` in model coordinates, using only `M` and `L` commands. Its `stroke-width` is in pixels on screen, like every line's (§2.1).

The person can also ink in a colour picked from the sheet. A stroke drawn that way carries `tp:color-from`: the id of the element the colour was picked from. That's the nearest element with an id: the shape itself, the dimension it belongs to, or its layer. It usually means the stroke belongs with that element, for example an extension of it.

Strokes are rough by design. They show the general shape of a change and where it goes. Exact sizes are negotiated afterwards. A handwritten number or note next to a sketch is just more strokes. Colours mean only what the person says they mean.

### 3.1 Sketches over models

The person can also draw over a model's 3D view (§2.8). Save then writes `<name>.glb.sketch.svg` next to `<name>.glb`. It is a sheet (§2) whose plane is the one the camera looked at:

- **Coordinates.** Millimetres on that plane, x to the right on screen and y up. `tp:view` places them in the model as in §2.3: `normal` points towards the viewer and `xdir` to the right, and `origin` is a point of the plane. A stroke point (x, y) is the model point origin + x·xdir + y·(normal × xdir), give or take a depth along `normal`, which a drawing over a 3D view doesn't say. The plane is through the middle of the view, or, when the view looks straight at a section (Section only), the section's plane, where the depth is exact. From a standard direction the coordinates are CAD's own: from the right, x is CAD y and y is CAD z.
- **`tp:view`** also says what was on screen: `kind="model view"`; `title`, the model and the direction looked from; `area`, the part of the plane in view (`"x0 y0 x1 y1"`); `hidden`, the model's tree nodes that were hidden (paths, separated by `; `); and `slices`, what the slice planes left of the model, in words, such as `kept: x ≤ 412.5` or `section at x = 412.5`.
- **The View layer** holds a picture of the view as it was, an `image` covering `area`. The Sketch layer holds the ink, as in §3.
- **The root** gets `tp:sketch-of="<name>.glb"`.

Ink belongs to the direction it was drawn from, so the viewer doesn't turn the view while there is ink on it; it only pans and zooms.

## 4. Working with an AI (informative)

**Making sheets**

1. Project the part of the model that matters into a sheet: a section, a projection, a 2D sketch. Use the pipeline's own tools, or `tracepaper.sheet` from Python. Write it into the views folder (§1), in the subfolder for what it shows.
2. Give elements stable ids and `tp:source` pointers, and fill in `tp:view`.
3. Show the constraints and dimensions that help the person judge the design. Put the rest on hidden layers or leave them out. Which ones to show is worked out in conversation.

**Acting on a sketch**

1. Find the newest `*.sketch.svg` in the views folder. One named `<name>.glb.sketch.svg` was drawn over a model (§3.1).
2. Look at it. `tracepaper inspect` does both steps: it lists the strokes, picked colours and moved labels, and renders the whole sheet, a close-up of the ink, and the close-up with the drawing faded. Without it, render with `rsvg-convert -b white -z 6 X.sketch.svg -o x.png`; for a close-up, render a copy whose root `viewBox` is set to the Sketch layer's bounds (with y negated), plus a margin.
3. Work out what the sketch is and where it goes. Stroke coordinates are model coordinates. Nearby ids and `tp:source` pointers say what the strokes touch, and `tp:view` maps them into 3D. Over a model, the picture shows what the strokes were drawn on, and `tp:view` places them in the model up to a depth along the line of sight. A stroke's `tp:color-from` names the element whose colour it was drawn in. Ink in a sheet colour can be hard to tell apart from the drawing; rendering the Sketch layer alone helps.
4. Change the real model, in its own pipeline. The sketch gives the shape and a rough size. Exact values come from what the person said or wrote, or from a sensible choice.
5. Regenerate the sheet. Put the new feature and its dimensions on a `Changes` layer. Keep any label positions the person dragged: compare the sketch file's placement attributes with the sheet's.
6. Delete the sketch file.

If the result isn't right, the person sketches again or says so, and the loop repeats.

## 5. The viewer (informative)

`tracepaper view <views folder, or one view>` serves the viewer on 127.0.0.1 and opens it in a browser. The viewer:

- shows the views folder (§1) on a project page, as a tree: its subfolders, which the person can fold, and the sheets and models in them. A folded folder says when a sketch inside it is waiting. Each view opens in a window of its own, so several can be watched side by side, and reloads when its file changes on disk. A view's window has a menu of all views, grouped by folder;
- shows models (§2.8) in 3D: rotate, pan, zoom, standard directions, and slices along the axes, or just the sections there. The camera stays where it is when the model reloads;
- shows each view's tree (§2.4, §2.8) in the same panel, folded at first so only the top level shows, where the person can unfold nodes and hide and show them in that window. Their choices last while the window is open, including when the file is rewritten, and aren't stored;
- toggles layers, lets the person drag annotations (§2.6), and provides a pen with a few colours, on sheets and over models, where the view only pans and zooms while there is ink, and turning it first asks to wipe the ink (§3.1);
- can take ink from a drawing tablet, such as a reMarkable 2, which the person connects and disconnects in a view's window; connecting takes the tablet from any other viewer that has it. The tablet covers the current view of the window the person used last with the mouse (or connected from, or opened last), fitted at one scale for both axes so drawings keep their proportions, while the mouse keeps doing everything else. A tablet with a screen shows a sheet's view, lined up with the pen; over a model it shows only the frame of where the pen draws, and the person looks at the window;
- writes the sketch file on Save (§3), and clears the ink once the AI has deleted that file;
- shows an unhandled sketch file again when reopened;
- never solves, checks or interprets anything, and never runs `regenerate`.

## 6. Versioning

The namespace carries the version: `urn:tracepaper:0`. A change that would make existing files mean something different gets a new namespace. Adding optional attributes does not.
