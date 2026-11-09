# Tracepaper format, v1 (draft)

Tracepaper is a visual aid for a person working on CAD geometry with an AI. The AI's build writes **scenes**: the geometry as exact solids, regions and curves in STEP files, with a little JSON beside them for what STEP can't say. The person looks at them in the Tracepaper viewer, in 3D and as exact 2D drawings (sections and projections, through any plane), toggles parts, drags dimension labels out of the way, and sketches roughly what they want changed. They save, then ask for the change in the usual way ("I sketched a chamfer on section A"), and the AI reads the **sketch file** the viewer wrote.

Tracepaper is a lens, not a model. The real geometry stays in whatever CAD pipeline the project uses (CadQuery, build123d, FreeCAD, plain code). A scene is a snapshot of it, and how it looks is the viewer's business: files say what things are, and the parts' colours, never how thick or how drawn. Mapping a sketch back into the pipeline is the AI's job. Nothing in Tracepaper solves or checks constraints.

MUST, SHOULD and MAY are used as in RFC 2119.

## 1. Files

| File | Written by | Contents |
|---|---|---|
| `<name>.step` | the AI's build | a scene (§2) |
| `<name>.tp.json` | the AI's build | what the scene's STEP file can't say: annotations, named views, frames, fields (§2.3) |
| `.vtu`, `.step` in a dot folder | the AI's build | a scene's fields (§2.11) and frames (§2.10) |
| `<name>.plot.json` | the AI's build | a plot: curves and values over axes, drawn as a sheet (§2.12) |
| `<view>.sketch.svg` | the viewer, when the person presses Save | the view as it was on screen, plus their ink (§3) |
| `<name>.3mf` | the AI's build, with the slicer's command line | a slicer project, shown in 3D (§2.9) |

- **The build folder.** The views live in the folder the build writes its output to, such as `build/`, among that output (STL files, caches), in subfolders that group them by what they're for. The viewer shows the folder as a tree of its views and subfolders, empty ones too (§5); other files are left out, as are folders whose names start with a dot. Every `.step`, `.plot.json`, `.3mf`, `.svg` and `.glb` file there is taken for a view, so the build's other files MUST NOT end in those; STEP files for other uses go in a dot folder or elsewhere.
- The build writes each file under a name the viewer skips (ending in `.tmp`, or in a dot folder) and renames it into place, so the viewer never reads half of it. It writes each view in place of the old one, and deletes only views it no longer makes. It MUST NOT empty the build folder wholesale, since sketch files wait there next to their views until the AI has handled them.
- An existing sketch file means "not handled yet". The AI deletes it once it has acted on it.
- Older builds write 2D sheets as `.svg` files and 3D models as `.glb` files, in the format of [SPEC-v0.md](SPEC-v0.md). The viewer still shows them (§6).

## 2. Scenes

### 2.1 The STEP file

A scene is a STEP file (ISO 10303-21, AP203, AP214 or AP242, `.step` or `.stp`) as CAD programs write them: exact boundary representations, faces on planes, cylinders, cones, spheres, tori and B-spline surfaces, bounded by exact curves.

- **Units and orientation:** millimetres, z up. Fronts face −y.
- **The tree:** the file's assemblies are the scene's groups, and its shapes its parts, under the names the file gives them. Instances of one shape keep names of their own (`screw 1`, `screw 2`). A single top assembly is left out, so that its parts are the top level. A node's path is its names joined with ` / `, as in `Screws / screw 1`. Names SHOULD be unique among siblings; the viewer adds ` (2)` to tell repeated ones apart.
- **What parts are:** a part's shape can hold
  - **solids**, which sections cut into regions;
  - **faces without a volume**: the regions of a 2D scene (§2.2), and otherwise surfaces, which sections cut into lines;
  - **curves**: reference geometry, such as an axis, a path or an older design's outline, drawn thin and dashed;
  - **points** (vertices on no edge): reference geometry too, such as a spline's control points or a contact point, drawn as dots. A part of points only, beside the curve they belong to, keeps them apart: its ends in one part and its handles in another, say.
- **Colours, no other looks.** A part has the colour the file gives it: its instance's, its shape's, the commonest of its solids' or faces', or else the nearest group's. A colour that isn't opaque makes the part see-through in 3D, as glass. Colours are optional, and the viewer chooses one for a part without (§2.5). Layers and styles in the file are ignored.
- With CadQuery: `assembly.export("build/part.step")`, parts named with `name=`. With build123d: `export_step(assembly, "build/part.step")`, parts named with `label`.

### 2.2 2D scenes

A scene without solids whose faces all lie in one plane is a **2D scene**: a set of regions, such as profiles, sketches or a cross-section the AI made itself. The viewer shows it as a drawing of that plane, seen from the side its normal points to (+z for a scene in z = 0), with x along CAD x (or y, if the plane is square to x). For a scene in the plane z = 0, sheet coordinates are CAD's x and y.

Regions MAY touch, and MAY lie inside another's hole. Curves in a 2D scene are drawn as reference geometry.

### 2.3 The scene's `.tp.json`

Next to `<name>.step`, `<name>.tp.json` MAY say what the STEP file can't. Every key is optional.

```json
{
  "title": "Bracket",
  "source": "parts/bracket.py",
  "pipeline": "CadQuery",
  "rev": "git:3f2a9c1",
  "regenerate": "python3 tools/views.py bracket",
  "hidden": ["screw", "Frame / door"],
  "annotations": [],
  "views": [
    {"name": "section-a", "title": "section A-A", "kind": "section",
     "origin": [0, 15, 0], "normal": [0, -1, 0], "xdir": [1, 0, 0],
     "annotations": [
       {"id": "d-length", "dim": "linear", "p1": [0, 0], "p2": [60, 0], "dir": "x", "offset": -16, "value": 60}
     ]}
  ]
}
```

| Key | Meaning |
|---|---|
| `title`, `source`, `pipeline`, `rev`, `regenerate` | Free text, as in SPEC-v0.md §2.3: shown to the person, where the model lives, what made it, which version, and how the AI rebuilds this scene (the viewer never runs it). |
| `hidden` | Nodes that start hidden when a window opens: paths (`"Frame / door"`) or plain names (`"screw"` hides every node of that name). |
| `annotations` | A 2D scene's annotations (§2.6), in its plane's coordinates. |
| `notes` | Text for the person: a key to the letters in the dimensions, what changed since the last version. One text with line breaks, or a list of lines. Every drawing of the scene shows it, under the drawing. |
| `views` | Named views (§2.4), each with its own `annotations` and `notes`. |
| `sliders`, `define`, `moves` | The scene's moving parts: sliders the person moves, and formulas of them that turn and slide nodes (§2.13). |
| `axes`, `quantities`, `frames` | The scene's frames (§2.10), for parts that change shape. |
| `fields` | Fields on the scene (§2.11). |

A malformed `.tp.json` makes the scene's views show what is wrong instead of a drawing; `tracepaper render` says the same (§4).

### 2.4 Views

A scene has **views**, each a tab of its own in the viewer, named after the scene's file with the view after `#`:

| View | Shows |
|---|---|
| `bracket.step` | the scene in 3D (§2.8), or, for a 2D scene, its drawing |
| `bracket.step#section-a` | a named view from `views` in the `.tp.json` |
| `bracket.step#x=12.5`, `#y=…`, `#z=…` | the section through the plane square to that axis at that coordinate, seen from +x (as from the right), −y (from the front) or +z (from above) |
| `bracket.step#front`, `#back`, `#left`, `#right`, `#top`, `#bottom`, `#iso` | the scene seen from that direction, with hidden lines |

The project page lists the scene and its named views; the person opens the others from the 3D view (§5). A named view's `name` MUST be letters, digits, `-`, `_` and `.`, starting with a letter or digit, and MUST NOT be a standard direction's (`front`, `back`, `left`, `right`, `top`, `bottom`, `iso`), which `#top` would mean too; the viewer and `tracepaper render` report such a view as an error.

A named view is a plane:

| Key | Meaning |
|---|---|
| `kind` | `section` (the default): what the plane cuts; or `projection`: everything, seen along the normal, with hidden lines. |
| `origin`, `normal`, `xdir` | Three numbers each. A point of the drawing (x, y) is the model point origin + x·xdir + y·(normal × xdir), with `normal` pointing towards the viewer and `xdir` to the right. `origin` defaults to [0, 0, 0], `xdir` to CAD x (or y, if the normal is along x). |
| `title` | Shown to the person. |
| `annotations` | Annotations on the drawing (§2.6), in its coordinates. |
| `notes` | Notes for this drawing, after the scene's. |

### 2.5 How views are drawn (informative)

The viewer draws every scene the same way; a scene can't change how.

- **Exact.** Sections and projections are worked out on the exact geometry (OpenCascade, in the viewer's server) and drawn with SVG's lines, circular and elliptical arcs and Bézier curves: lines, arcs, ellipses and polynomial splines come out exactly, other curves (hyperbolas, rational splines) to within 0.1 µm. Projections use OpenCascade's exact hidden-line removal, which can take seconds for large or freeform scenes. On some freeform faces, such as lofts through sections of lines and arcs, it goes wrong: outlines running far outside the part, or no answer at all. Then, and when it takes longer than 30 s, the viewer draws the projection from a fine mesh of the faces instead, to within 3·10⁻⁵ of the scene's size (0.02 mm for a scene 700 mm across). Each view is worked out once for each version of its files.
- **Colours.** Each part has the colour the file gives it (§2.1), or else one of a fixed palette, given out in the order of the tree to the parts without; the same in 3D and in drawings. Sections and 2D scenes tint their regions with it, so dark colours still leave the hatching and outlines readable.
- **Sections** draw what the plane cuts from each solid as regions, tinted in the part's colour, outlined, and hatched at 45° to each region's main outlines, as ISO 128-3 has it, not to the sheet's edges, so that a slanted wall's hatching doesn't run along it and make it look thicker; one way or the other and closer or wider, part by part, so that parts side by side stay apart in greys. A region whose outlines have no main direction, such as a ring's, is hatched at 45° to the sheet. Surfaces cut become lines. Where OpenCascade's Boolean leaves part of a solid's section out, as it can on freeform faces for a plane along a knot line, the section's edges are drawn as well, so that the part doesn't vanish.
- **2D scenes** draw their regions tinted and outlined.
- **Projections** draw each part's visible edges, its smooth edges (where faces meet tangentially) lighter, and its hidden edges dashed, on a layer of their own, *Hidden lines*, hidden at first.
- **Curves** are thin and dashed, in their part's colour, so that one part's curves (a centre line) stay apart from another's (an older outline). **Points** are dots a few pixels across at any zoom, in their part's colour, named in a tooltip.
- **Fields** (§2.11) go under the parts, which are then only outlined, so that the outlines, curves and points show over them.
- **Notes** are a block of text under the drawing, on the *Notes* layer, a line given broken between words where it is wider than the drawing (but not into lines of fewer than 60 characters).
- **The tree** of a drawing is the scene's, leaving out parts the view doesn't show, with the *Dimensions*, *Constraints*, *Fields* and *Hidden lines* layers beside it.
- **Text and arrowheads** are sized to the drawing: about a 60th of its larger side, in steps of the ISO text heights (2.5, 3.5, 5, 7 mm ..., halved on below 2.5 mm: 1.75, 1.25, 0.875 ...). So they are as big on screen with the whole drawing in view, whether it is a detail 15 mm across or a building, and the notes too. Sections' hatching is spaced to match. A frame's drawing has the scene's own text height, so it stays the same over the frames. The person can make the annotations' text, and their arrowheads with it, a few steps smaller or larger in the drawing's window (§5), for a drawing whose details are much smaller than its extent; the notes, the fields and the hatching keep the drawing's.

A drawing reaches the browser, and the sketch file, as an SVG sheet in the format of SPEC-v0.md §2, with `tp:view`'s `scene`, `view` and `frame` saying what it is of.

### 2.6 Annotations

An annotation is a dimension or a constraint symbol on a drawing: a JSON object, in the drawing's coordinates, with points as `[x, y]`. The person can drag its label (§5); the sketch file says where to (§3).

Keys common to all annotations:

| Key | |
|---|---|
| `id` | REQUIRED, and stable, so moved labels can be carried into the next version of the scene. |
| `type` | What it expresses. Free-form; see the defaults below. |
| `refs` | Names of the nodes or ids it relates to, space-separated. |
| `value` | The value, as a number or a parameter name. |
| `label` | The text shown. Dimensions default to their measured value. |
| `source` | A free-text pointer back into the pipeline: `HOLE_D`, `Sketch001.Constraint7`, `bracket.py:42`. |

**Linear dimension** (`"dim": "linear"`):

| Key | |
|---|---|
| `p1`, `p2` | The measured points. |
| `dir` | `x` (horizontal), `y` (vertical) or `aligned` (along p1→p2, the default). |
| `offset` | Signed distance of the dimension line from p1, along n (defined below). Default 0. |
| `text-at` | Label position along the dimension line: 0 at the p1 end, 1 at the p2 end. Default 0.5. Values outside 0–1 put the label beyond the extension lines. |

Let u be the unit measuring direction: (1, 0) for `x`, (0, 1) for `y`, p1→p2 for `aligned`. Let n = (−u.y, u.x). The dimension line then runs from a = p1 + offset·n to b = p2 + (p1·n + offset − p2·n)·n. So a positive offset puts a horizontal dimension above p1, and a vertical one to its left.

**Radius and diameter** (`"dim": "radius"` or `"dim": "diameter"`):

| Key | |
|---|---|
| `center` | The centre. |
| `r` | Radius. |
| `angle` | Where the leader meets the circle, in degrees counter-clockwise from +x. Default 45. |
| `offset` | Distance from that point to the label end of the leader, measured outward from the centre. Negative values run back through the centre, which suits fillets. Default 0. |

**Angular dimension** (`"dim": "angular"`):

| Key | |
|---|---|
| `center` | The vertex. |
| `p1`, `p2` | The measured points, one on each leg. The angle runs counter-clockwise from the leg through p1 to the leg through p2, so swapping them measures the rest of the turn. |
| `offset` | Radius of the dimension arc, round the vertex. Default: the distance to p1. |
| `text-at` | Label position along the arc: 0 at the p1 leg, 1 at the p2 leg. Default 0.5. Values outside 0–1 put the label beyond the legs. |

**Constraint symbol** (any annotation with `anchor` and no `dim`):

| Key | |
|---|---|
| `anchor` | The point the symbol belongs to. |
| `offset` | Label position relative to the anchor, `[dx, dy]`. Default `[0, 0]`. A leader is drawn when the label is far from the anchor. |
| `label` | The symbol, e.g. `⊥`. |

**Default vocabulary.** `type` is free-form, so an AI can use any type, including a sentence. These defaults keep drawings consistent:

| `type` | Shown as |
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

As SPEC-v0.md §2.7: arrowheads, extension lines, labels and leaders, in multiples of the text height. `tracepaper/sheet.py` draws them in the server and `tracepaper/viewer/annotations.js` redraws them when the person drags one, the same way.

### 2.8 The 3D view

The viewer shows a scene in 3D as the server tessellates it (to within 0.05 mm and 0.3 rad): each part in its colour (§2.5), see-through as far as the file makes it, with the edges between its faces. Slices don't close a part that is see-through. The person can turn, pan and zoom it, slice it, see through it, and open the exact drawing of what it shows (§5). Curves, points and fields aren't shown in 3D yet.

### 2.9 Slicer projects

A slicer project is a 3MF file (`.3mf`) as OrcaSlicer or Bambu Studio saves it: what goes to the 3D printer. The viewer shows it in 3D, read-only: the printer's plate and the pieces where the slicer put them, so the person can check the layout before they open the file in the slicer and print from there. Tracepaper never slices and never prints.

- **What the viewer reads:** the build items and their meshes (3MF core, with the production extension's components in other files), and from `Metadata/`:
  - `project_settings.config`: `printable_area`, `bed_exclude_area`, `printable_height` and `filament_colour`;
  - `model_settings.config`: each object's name and filament, which of its parts are modifiers, and how many plates there are.
- **The tree:** `Plate` (or `Plate 1`, `Plate 2`, …, laid out side by side as the slicer lays them out), then each piece under its name without a mesh file's extension (`sash_tenon_01.stl` shows as `sash_tenon_01`; a repeated name gets ` (2)`), then `Build volume`, hidden at first. Modifiers, negative parts and support blockers and enforcers aren't shown.
- **What it looks like:** pieces in their filament's colour, shaded flat as slicers show meshes, with edges where the surface folds by more than 25°. The plate is drawn 2 mm thick below z = 0, and the part of it the printer keeps for itself (`bed_exclude_area`) in orange.
- **Coordinates** are the slicer's: millimetres, z up, the plate's origin as in `printable_area`. Fronts face −y.
- **Checking it without a browser:** `tracepaper render <name>.3mf` draws it from above (each plate, the part the printer keeps for itself, and what of each piece faces up, in its filament's colour, outlined and named) and lists each piece's size, height and plate, saying which are off their plate's printable area, over the part the printer keeps for itself, or taller than `printable_height`.
- **Sketching** over it works as over a scene's 3D view, and Save writes `<name>.3mf.sketch.svg` (§3.1).

### 2.10 Frames

A scene MAY come in **frames**: the whole scene at several values of one or more **axes**, such as an insertion depth, an angle, or a design variant. Each frame is a STEP file of its own, with the whole scene in it (a bent part, not just a moved one). Parts that only move, turning and sliding as they are, are better given as sliders and moves (§2.13): the person moves them live, on any number of sliders, without a file for each position. The person steps through them, plays them, and sees per-frame values plotted along an axis.

```json
{
  "axes": [{"name": "insertion", "unit": "mm"}],
  "quantities": [{"name": "force", "unit": "N"}, {"name": "strain", "unit": "%", "max": 3},
                 {"name": "pull-out force", "unit": "N", "min": 15}],
  "frames": [
    {"at": {"insertion": 0}, "step": ".clip/insertion=0.step", "values": {"force": 0, "strain": 0, "pull-out force": 0}},
    {"at": {"insertion": 0.5}, "step": ".clip/insertion=0.5.step", "values": {"force": 1.2, "strain": 0.4, "pull-out force": 0},
     "fields": {"strain": ".clip/insertion=0.5.vtu"},
     "annotations": [{"id": "d-depth", "dim": "linear", "p1": [9, 24], "p2": [9, 23.5], "dir": "y", "offset": -3}],
     "notes": "s = 0.5 mm: not touching yet"}
  ]
}
```

| Key | Meaning |
|---|---|
| `axes` | The scene's parameters: `name` and `unit`. |
| `quantities` | Values worked out for each frame: `name`, `unit`, and `min` and `max`, the least and the most it may be, where it has limits. (`limit`, as the first draft called `max`, is read as `max`.) |
| `frames` | The frames, the first shown first. `at` gives a number for each axis; `step` is the frame's STEP file, relative to the scene's folder and inside it; `values` the frame's quantities; `fields` each field's `.vtu` file for this frame (§2.11). A frame MAY also give `annotations`, `notes` and `views` as the top level does (§2.3): they hold for that frame in place of the top level's, a view in place of the one of the same name. So a dimension can follow a part that moves or bends, and a section can move. |

- The frames' STEP files SHOULD be in a dot folder, such as `.clip/`, so the viewer doesn't list them as views of their own. The scene's own `<name>.step` is what other programs see, and SHOULD be the first frame.
- With two axes, the frames form a grid, and the person moves along each axis with its own slider.
- The viewer (informative) shows a slider for each axis, with steps and Play; each frame's values, and a plot of each quantity against the first axis (or another axis, or another quantity, such as force against deflection) with the frame shown marked and its limits dashed; frames where a quantity is outside its limits are marked on the slider and in red. *Trail* shows every frame along the first axis faintly behind the one shown, of everything or of one part: what it sweeps through. Ink stays as it is from frame to frame, and so do moved labels, carried by their ids.
- Every view of the scene has the frames: its 3D view and its drawings.

### 2.11 Fields

A scene MAY carry **fields**: values at the nodes of a mesh, such as the strain a FEM solver found, linear across each element. A value is a number or a vector: a displacement, or the forces between two parts at the nodes where they touch.

```json
{
  "fields": [{"name": "strain", "unit": "%", "max": 3, "file": "clip.vtu", "value": "strain", "displacement": "u"}]
}
```

| Key | Meaning |
|---|---|
| `name` | The field's name, shown to the person. |
| `unit`, `min`, `max` | Its unit, and the least and the most it may be, where it has limits. |
| `file` | Its `.vtu` file, relative to the scene's folder and inside it. A frame can give one of its own (§2.10). |
| `value` | The name of the point data to show, or of the cell data: one value for each element, the same all across it. Default: `name`. A number, or a vector of 2 or 3 components. |
| `displacement` | The name of a vector point data that moves the mesh's nodes from where the file has them, or none. |

- **The file** is a VTK XML unstructured grid (`.vtu`), as meshio, FEniCS, ParaView and most FEM tools write it: ASCII, binary or appended, compressed with zlib or not. Its cells are triangles, quadrilaterals or tetrahedra, lines (such as the edge where two parts touch) or vertices (bare points, such as where forces act); quadratic ones by their corners; points without cells are bare points. All in the scene's coordinates.
- **What it is, not how it looks:** a field of forces on a boundary is a mesh of lines or bare points with a vector at each node; a field over a part is a mesh of its areas or volume. The viewer draws them accordingly (below); a field has no keys about looks.
- **On a drawing:** the mesh's triangles, lines and bare points that lie in the drawing's plane, and where the plane cuts tetrahedra, the cut, which is exact for a field linear across them.
- **The viewer (informative)** draws each field on its own layer under *Fields*:
  - **Numbers** in a colour map that gets lighter from its lowest value to its highest (viridis, which reads in greys too), the same range over all frames: areas filled, lines as bands, bare points as dots.
  - **Vectors** as arrows, at their nodes (or, one for each element, at its middle), as long as they are big, on one scale over all frames: the longest a tenth of the drawing or so. Over areas and volumes an arrow starts at its node, as a field's are drawn; on lines and bare points it ends there, as forces on a body are. Zero ones aren't drawn, and where there are more than about a hundred, they are thinned out evenly. Arrows outside the field's limits are red. Their lengths' colours are there too, hidden at first.
  - Fields with areas go under the parts, which are then only outlined; fields of lines and points alone go over them.
  - The person can change the range, the arrows' length, show only what is outside the limits, add isolines, the mesh's edges (quadrilaterals as such) and the arrows' values, scale the displacement (0 shows the mesh where the file has it, 1 moved as far as the field says, more to exaggerate), and read the value under the pointer, or an arrow's near it. Fields aren't shown in 3D yet.

### 2.12 Plots

Beside its scenes, the build MAY write **plots**: curves and values over axes, as a solver or a calculation gives them, such as a glazing's sound transmission loss over frequency or a clip's force over lift. A plot is a file `<name>.plot.json`, written as views are (§1), and shown as a drawing: the person hides series in the tree, reads values under the pointer and sketches on it, as on a scene's drawings. Like a scene, it says what its data is, not how it looks.

```json
{
  "title": "Transmission loss",
  "source": "window/acoustics.py",
  "regenerate": "python -m window.acoustics plot",
  "notes": "Field incidence.",
  "x": {"label": "frequency", "unit": "Hz", "scale": "log", "ticks": [50, 63, 80, 100, 125]},
  "y": {"label": "transmission loss", "unit": "dB", "min": 30},
  "series": [
    {"name": "8 mm", "kind": "values", "family": "inner pane", "x": [50, 63, 80, 100, 125], "y": [31, 24, 42, 43, 44],
     "low": [29, 20, 40, 41, 43], "high": [33, 27, 44, 45, 46], "summary": "Rw (C; Ctr) = 41 (−1; −4) dB"},
    {"name": "8 mm every frequency", "detail-of": "8 mm", "x": [44.7, 45.1, 45.6], "y": [31.6, 32.0, 32.4]}
  ],
  "marks": [{"x": 59, "label": "resonance", "of": "8 mm"}]
}
```

| Key | Meaning |
|---|---|
| `title`, `source`, `pipeline`, `rev`, `regenerate`, `notes`, `hidden` | As a scene's `.tp.json` has them (§2.3). `hidden` names series, families or panels, or paths through them (`"8 mm / 8 mm every frequency"`). |
| `x`, `y` | The axes (below), shared by every panel that doesn't give its own. |
| `series`, `marks` | One panel's series and marks; or `panels`, a list of panels, each with its own `series` and `marks`, a `title`, and `x` or `y` if it doesn't share the plot's. |

An **axis** has a `label` and a `unit`; a `scale`, `linear` (the default) or `log`; a `range`, `[lowest, highest]`, either of them `null` for the data's, that is the series shown when the plot opens (not those `hidden` at first, which run past the panel's edge when shown), their spreads and marks, and the limits; `ticks`, the values to mark, each a number or `[value, "label"]` (such as the nominal third octaves 50, 63, 80 ... at their exact values), else the viewer's own; and `min` and `max`, the least and the most a value may be, as a quantity's (§2.10).

A **series** has a `name`, unique in its panel, and `x` and `y`, lists of numbers as long as each other, `null` in `y` for a gap. What it is:

| Key | Meaning |
|---|---|
| `kind` | `curve` (the default): a function at as many points as it takes, drawn as a line; `values`: a function at these points only, such as third-octave band values, drawn as markers joined by a thin line; `points`: values that don't follow one another, such as measurements, markers alone. |
| `detail-of` | The name of a series in the same panel that this one is the detail of, such as its values at every frequency beside the band values: drawn faint and thin in that series' colour, and in the tree inside it. |
| `family` | A name the series shares with others in an order, such as an inner pane 6, 8 and 10 mm thick: drawn in one hue from light to dark, in the order they come, and in the tree as a node holding them. |
| `low`, `high` | Lists as long as `x`: the spread at each point, the lowest and the highest value (not distances from `y`), `null` where there is none; drawn as error bars on values and points, a band along a curve. |
| `summary` | Text for the legend beside its name, such as its single-number rating. |

A **mark** is a line across a panel at `x`, or along it at `y`, with a `label`, and `of`, the series it belongs to, if any: drawn in its colour and hidden with it.

- **Panels** sit side by side, or one above the other where they share `x` and not `y`. Notes are drawn under them as on drawings (§2.5), broken to the plot's width. A shared axis has the same range in every panel sharing it.
- **The viewer (informative)** gives each series its colour (Okabe and Ito's, which colour-blind eyes tell apart), its dashes and its markers, the same in every panel by name, so that series stay apart on a screen without colour too; and each a line in the legend under its panel, with its `summary`. Limits are dashed and red; values beyond them are ringed in red, and a curve beyond them drawn over in red. A series' tree node holds its details and its legend line, so hiding it hides those too. The pointer reads the axes' values, and the value of the nearest point of a series shown, with its spread.
- **The sheet** a plot is drawn as is a drawing as in §2.5, with `tp:view kind="plot"`. Its `tp:view` holds a `tp:panel` for each panel: its `name`, and `x-axis` and `y-axis`, each `"<scale> <lowest> <highest> <from> <to>"`, the axis's range and where it lies on the sheet in mm, with `x-label`, `x-unit`, `y-label` and `y-unit`. Each series' group has `tp:series` (its name), `tp:panel` (its panel's index), `tp:kind`, and its numbers in `tp:xs`, `tp:ys`, `tp:low` and `tp:high`, separated by spaces, `-` for none. So a sketch file over a plot (§3), `<name>.plot.json.sketch.svg`, gives the strokes in the data's terms, and `tracepaper inspect` says so: the panel, the values along each axis, and the series nearest them.

### 2.13 Moving parts

A scene's parts MAY move: the `.tp.json` gives **sliders**, which the person moves, and **moves**, which turn and slide nodes of the tree by **formulas** of the sliders. The STEP file has the parts at rest, where every move is 0. The viewer works the formulas out as the slider moves and places the parts; it never checks the result. Parts go where the formulas put them, through each other or apart: making the motion right, such as working out a linkage, is the AI's.

```json
{
  "sliders": [{"name": "opening", "unit": "°", "range": [0, 90]},
              {"name": "handle", "unit": "°", "range": [0, 180], "start": 0}],
  "define": {"lift": "12 * min(handle, 90) / 90"},
  "moves": [
    {"node": "Sash", "turn": {"axis": [0, 0, 1], "through": [2, 55, 0], "by": "opening"}},
    {"node": "Sash / handle", "turn": {"axis": [0, -1, 0], "through": [570, 0, 395], "by": "handle"}},
    {"node": "Sash / bolt", "slide": {"along": [0, 0, 1], "by": "lift"}}
  ]
}
```

| Key | Meaning |
|---|---|
| `sliders` | Each with a `name` to use in formulas (letters, digits and `_`, not starting with a digit), its `range`, `[lowest, highest]`, a `unit` to show, and `start`, where it starts: 0 if that is in its range, else its lowest. A slider MAY be part of a track (below): `on`, `draw` and `closed`. |
| `define` | Names for formulas, `{name: formula}`, each of the sliders and the names before it: for a value several moves use. |
| `moves` | In order. Each moves `node`, a path or a plain name as in `hidden` (§2.3), with every node inside it. A `turn` is by `by` degrees about `axis` through the point `through` ([0, 0, 0] if left out), counter-clockwise looking against the axis; a `slide` is by `by` mm along `along`. Axes and points are where they are at rest, in the STEP file's coordinates. |

- **Nested moves.** A node inside a moved one moves with it: its own moves come first, then those of the nodes around it. So a handle turning on a sash is given about its spindle at rest, and goes along as the sash opens. A node with several moves makes them one after another.
- **Formulas** are numbers, the sliders' and definitions' names, `+ - * /`, `^` (power, from the right), parentheses, `pi`, and the functions `sin cos tan asin acos atan atan2(y, x) sqrt abs min max clamp(x, lo, hi) floor ceil round exp ln`. Angles are degrees, in and out: `asin(0.5)` is 30. `round` rounds halves up. A formula MAY be just a number. A move whose formula has no value at a position (`sqrt` of a negative number, a division by 0) is left out there.
- **Frames and moves** go together: the moves move the parts of whichever frame is shown.

**Tracks.** Sliders can form a track: one control whose knob runs along lines, each a slider's, and passes from one line to another only where they join. That is how a mechanism's positions hang together. A tilt-and-turn window's sash swings open only with its handle sideways and tilts only with it up, and the handle turns only while the sash is shut:

```json
"sliders": [
  {"name": "handle", "unit": "°", "range": [0, 180], "draw": [[0, -1], [0.17, -0.98], ..., [0, 1]]},
  {"name": "tilt", "unit": "°", "range": [0, 12], "on": {"handle": 180}, "draw": [[0, 0], [0, 0.8]]},
  {"name": "opening", "unit": "°", "range": [0, 90], "on": {"handle": 90}, "draw": [[0, 0], [1.2, 0]]}
]
```

| Key | Meaning |
|---|---|
| `on` | `{slider: value}`: this slider branches off that one, which comes before it, at that value. The knob passes onto it there only, and while it is on it, that one stays there; the other sliders of the track stay where they start. A branch can have branches of its own. |
| `draw` | The slider's line in the control, as points `[[x, y], ...]` in units of its own, y up, as many as its curve needs: its value runs along it by length, from its lowest at the first point to its highest at the last. A branch's line is placed so that its point at its `start` lies where it joins: so its points are its own shape, and it can run out to both sides of the joint. Without `draw`, a slider is a straight line, and a branch one square to where it joins. |
| `closed` | `true` joins its line's ends: the knob goes round, as for a part that turns all the way. |

Lines, trees of branches and loops cover what a mechanism's positions need; the AI that writes the moves draws the track to look like what it moves, such as an arc for a handle that turns. Sliders that no `on`, `draw` or `closed` joins into a track stay plain sliders. The sliders' values are what formulas use, as for plain ones; the viewer keeps them to the track, but a pose given to `tracepaper render` needn't be on it.

The viewer (informative):

- shows a slider for each in the panel of every view of the scene, or a track, drawn as its lines and the knob on them, which the person drags along them; each with the value to type in, which on a track puts the knob there, through the joints on the way. Double-clicking one puts it, or a track's knob, back where it starts. All the open windows of a scene share them: moving one in the 3D view moves the parts in its drawings too, and a window opening takes the positions of one already open;
- moves the parts in the 3D view live, as the slider moves;
- in a section, cuts the moving parts live from the 3D view's triangles, to within their 0.05 mm, in place of their exact cut, and moves a 2D scene's parts as they are; a projection's moving parts fade. Once the sliders rest (let go, or still for a moment), the exact drawing at their positions replaces it. Annotations stay where the view gives them;
- writes the sliders' positions into a sketch file's `tp:view` as `pose`, such as `opening=30 handle=0`, and opens a sketch with the sliders where they were.

`tracepaper render scene.step#view --pose "opening=30 handle=90"` draws a view with the sliders there (the others where they start), and says what is wrong with them.

## 3. Sketch files

When the person presses Save on a view, the viewer writes `<view>.sketch.svg` next to the scene: `bracket.step#section-a.sketch.svg` on a named view, `bracket.step#x=12.5.sketch.svg` on a section opened from the 3D view, `profile.step.sketch.svg` on a 2D scene, and `bracket.step.sketch.svg` over a scene's 3D view (§3.1).

- **The drawing as it was on screen**, as an SVG sheet (SPEC-v0.md §2): nodes the person hid have `visibility="hidden"`, which the nodes inside them inherit, and a node they showed again inside a hidden one has `visibility="visible"`. Annotations they dragged carry new placement attributes (`tp:offset`, `tp:text-at`, `tp:angle`) and redrawn graphics. Fields are as the person showed them.
- **What it is of.** The root gets `tp:sketch-of="<view>"` and `tp:saved="<ISO 8601 time>"`. `tp:view` in its metadata gives the drawing's plane (`origin`, `normal`, `xdir`: a point (x, y) is origin + x·xdir + y·(normal × xdir)), its `kind`, the `scene` and `view`, the `frame` shown, as `insertion=2.5` (axes separated by spaces), and for a scene with sliders, their `pose`, as `opening=30 handle=0` (§2.13).
- **Size.** The viewer MAY enlarge `viewBox`, `width` and `height` so that ink drawn past the edge of the drawing is kept.
- **Ink layer.** The ink is the last layer:

```xml
<g id="tp-sketch" inkscape:groupmode="layer" inkscape:label="Sketch" transform="scale(1,-1)">
  <path stroke="#e11d48" stroke-width="2.2" vector-effect="non-scaling-stroke" fill="none"
        stroke-linecap="round" stroke-linejoin="round" d="M-1 37.5 L2.5 41 L3 41.5"
        tp:frame="insertion=2.5"/>
</g>
```

Each stroke is one `path` in the drawing's coordinates, using only `M` and `L` commands. Its `stroke-width` is in pixels on screen. `tp:frame` says which frame was shown when it was drawn, for a scene with frames.

The person can also ink in a colour picked from the drawing. A stroke drawn that way carries `tp:color-from`: the id of the element the colour was picked from. That's the nearest element with an id: the shape itself (`<part>-cut-1`, `<part>-edges`), the dimension it belongs to, or its layer. It usually means the stroke belongs with that element, for example an extension of it.

Strokes are rough by design. They show the general shape of a change and where it goes. Exact sizes are negotiated afterwards. A handwritten number or note next to a sketch is just more strokes. Colours mean only what the person says they mean.

### 3.1 Sketches over 3D views

The person can also draw over a scene's 3D view, or a slicer project's. Save then writes `<name>.step.sketch.svg` (or `<name>.3mf.sketch.svg`). It is a sheet whose plane is the one the camera looked at:

- **Coordinates.** Millimetres on that plane, x to the right on screen and y up. `tp:view` places them in the model as in §3: a stroke point (x, y) is the model point origin + x·xdir + y·(normal × xdir), give or take a depth along `normal`, which a drawing over a 3D view doesn't say. The plane is through the middle of the view, or, when the view looks straight at a section (Section only), the section's plane, where the depth is exact. From a standard direction the coordinates are CAD's own: from the right, x is CAD y and y is CAD z.
- **`tp:view`** also says what was on screen: `kind="model view"`; `title`, the scene and the direction looked from; `pose`, where its sliders were (§2.13); `area`, the part of the plane in view (`"x0 y0 x1 y1"`); `hidden`, the tree's nodes that were hidden (paths, separated by `; `), not counting those inside them; `shown`, nodes shown again inside a hidden one; `transparent="true"` for the transparent view; `slices`, what the slice planes left of the model, in words, such as `kept: x ≤ 412.5` or `section at x = 412.5`; and `frame`.
- **The View layer** holds a picture of the view as it was, an `image` covering `area`. The Sketch layer holds the ink, as in §3.
- **The root** gets `tp:sketch-of="<name>.step"` (or `"<name>.3mf"`).

Ink belongs to the direction it was drawn from, so the viewer doesn't turn the view while there is ink on it; it only pans and zooms. Turning it wipes the ink, once the person agrees, and leaves the sketch file waiting for the AI; since a sketch file holds one direction's ink, Save then asks before it replaces that file with ink from another. The view is orthographic while the person sketches. For exact coordinates under the ink, the person can sketch on a drawing instead (§2.4).

## 4. Working with an AI (informative)

**Making scenes**

1. Write the part of the model that matters as a STEP file into the build folder (§1), with the parts named and grouped as the person thinks of them. For a 2D view of something the model doesn't hold as faces, such as a profile, write a 2D scene (§2.2).
2. Write the `.tp.json` beside it: where it comes from, what starts hidden, the sections and projections worth naming, and their dimensions; for parts that move, sliders and moves (§2.13). Which ones to show is worked out in conversation.
3. Check each view with `tracepaper render build/part.step#section-a`, which draws it as the viewer does (SVG and PNG) and says what is wrong in the `.tp.json`, if anything, and each slicer project with `tracepaper render build/print/plate.3mf` (§2.9). Look at the PNG.

**Acting on a sketch**

1. Run `tracepaper inspect build`: it finds the newest `*.sketch.svg`, lists the strokes (with the frame each was drawn on), picked colours and moved labels (against the `.tp.json`), and renders the whole view, a close-up of the ink, and the close-up with the drawing faded.
2. Work out what the sketch is and where it goes. Stroke coordinates are the drawing's; `tp:view` maps them into the model. Nearby part names and ids say what the strokes touch. Over a 3D view, the picture shows what the strokes were drawn on, and `tp:view` places them up to a depth along the line of sight.
3. Change the real model, in its own pipeline. The sketch gives the shape and a rough size. Exact values come from what the person said or wrote, or from a sensible choice.
4. Rebuild the scene. Keep any label positions the person dragged: write their new `offset`, `text-at` or `angle` into the `.tp.json`.
5. Delete the sketch file.

If the result isn't right, the person sketches again or says so, and the loop repeats.

## 5. The viewer (informative)

`tracepaper view <build folder, or one view>` serves the viewer on 127.0.0.1 and opens it in a browser. It needs OpenCascade's Python binding, OCP (which CadQuery and build123d install), for scenes. The viewer:

- shows the build folder (§1) on a project page, as a tree of its views: its subfolders, empty ones too, which the person can fold, and the scenes, their named views, the plots, and the other views in them. A folded folder says when a sketch inside it is waiting. Each view opens in a tab of its own, which the person can drag out into a window to watch several side by side, and reloads when its files change on disk;
- shows scenes in 3D, orthographic or in perspective: rotate, pan, zoom, standard directions, slices along the axes, or just the sections there, and a transparent view. *Drawing* opens the exact drawing of what the 3D view shows, in a tab of its own: the section through the slice plane that is on, or the scene from the standard direction it looks from;
- shows each view's tree in a panel beside it, where the person can hide and show nodes in that window, as in the OCP viewer: a node's eye shows or hides it with everything inside it, also a node inside a hidden one; `Ctrl`+click on a name shows only that node, `Alt`+click hides it. The person's choices last while the window is open, including when the files are rewritten, and aren't stored;
- lets the person drag annotations' labels, and provides a pen with a few colours, on drawings and over 3D views, where the view only pans and zooms while there is ink;
- measures on drawings: the person picks points, which snap to vertices (the ends of lines, arcs and curves), the centres of circles and arcs, points and the points annotations measure from, or else to the nearest point on a line or curve, and reads the distance between two, with Δx and Δy, and the angle at the middle one of three. The measurement is drawn over the drawing, not saved;
- makes the annotations' text smaller or larger on drawings, through the ISO heights (*A−*, *A+*); the browser remembers it for each view, and a sketch file saved meanwhile has the annotations at that size;
- moves a scene's parts by its sliders (§2.13), live in 3D and in sections, the same in every window of the scene;
- steps through a scene's frames (§2.10) and shows its fields (§2.11), in the panel;
- draws plots (§2.12), and reads their values under the pointer;
- can take ink from a drawing tablet, such as a reMarkable 2, which the person connects and disconnects in a view's window; connecting takes the tablet from any other viewer that has it. The tablet covers the current view of the window the person used last with the mouse, fitted at one scale for both axes. A tablet with a screen shows a drawing, lined up with the pen; over a 3D view it shows only the frame of where the pen draws;
- writes the sketch file on Save (§3), and clears the ink once the AI has deleted that file;
- never solves, checks or interprets anything, and never runs `regenerate`.

## 6. Versioning, and older views

The `.tp.json` format is version 1. A change that would make existing files mean something different will add a `"format"` key with a new version; adding optional keys does not. SVG sheets and sketch files keep the namespace `urn:tracepaper:0` (SPEC-v0.md §6).

Version 0 had the AI draw 2D sheets itself, as layered SVG files with styles of its own, and write 3D models as `.glb` files: [SPEC-v0.md](SPEC-v0.md). The viewer still shows them, and sketches on them work as that spec says, but new builds SHOULD write scenes.
