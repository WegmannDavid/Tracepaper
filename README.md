# Tracepaper

A visual aid for working on CAD geometry with an AI.

The AI draws **sheets**: plain, layered SVG files showing geometry, dimensions and the constraints worth seeing. You open them in the viewer, toggle layers, drag labels out of the way and sketch roughly what you want changed. Press Save, then tell the AI what you drew. It finds `<sheet>.sketch.svg` next to the sheet, makes the change in the real CAD pipeline and regenerates the sheet, and the viewer reloads.

Tracepaper doesn't care which CAD tool or constraint solver is behind a sheet. The format is described in [SPEC.md](SPEC.md).

## Viewer

Needs Python 3.9 or newer and a browser; there are no other dependencies.

```sh
python3 -m tracepaper view examples/bracket     # from this directory
```

To get a `tracepaper` command for use in other projects:

```sh
pip install --user -e .
tracepaper view path/to/sheets                  # a directory, or a single sheet .svg
```

| | |
|---|---|
| `P` / `V` | pen / move labels |
| `1`–`4` | ink colour |
| scroll | zoom |
| middle drag, `Space` + drag, one finger | pan |
| two fingers | pinch zoom |
| `F` | fit the sheet |
| `Ctrl+Z` / `Ctrl+S` | undo / save |

## Drawing with a reMarkable 2

```sh
tracepaper view examples/bracket --remarkable
```

The tablet's pen then draws in the viewer, and the mouse keeps doing everything else, even at the same time. The pen's other end erases whole strokes.

- **Mapping.** The tablet covers the part of the sheet in view, so zooming in with the mouse gives finer control.
- **Aspect ratio.** The view's shape is fitted into the middle of the tablet at one scale for both axes, so a circle drawn on the tablet stays a circle. The toolbar shows how much of the tablet that is; the strips outside it do nothing.
- **Holding the tablet.** Landscape with the power button on the right is the default. Use `--rotate 270` for the power button on the left; 0 and 180 are portrait.
- **Connection.** The tablet is reached with `ssh remarkable`, or `--remarkable HOST` for another host name.
- **The tablet's own app.** It's closed while Tracepaper runs, so strokes don't also land in a notebook there, and the e-ink keeps showing what it showed. The app starts again when Tracepaper stops, or by itself 20 seconds after the connection is lost.

## Writing sheets

Anything that writes SVG following [SPEC.md](SPEC.md) works. For Python there's a small helper, `tracepaper.sheet`; [`examples/bracket/make_sheets.py`](examples/bracket/make_sheets.py) uses it to draw a section and a top view of an L-bracket.

## Tests

```sh
python3 -m unittest discover tests
```

`test_sheet.py` checks that the Python writer and the viewer draw annotations identically; this needs `gjs`. `test_browser.py` runs the viewer in headless Firefox. Each is skipped if its tool is missing.
