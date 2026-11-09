// Tracepaper viewer: shows a sheet, toggles layers, lets the person drag dimension and
// constraint labels and sketch on top, with the mouse or a tablet's pen.
// Save writes <sheet>.sketch.svg (SPEC.md §3).
import * as A from './annotations.js';
import { INK_COLORS, Ink, PenInput, coalesced, pxPerUnit, swatch, toLocal } from './ink.js';
import { FieldView } from './fieldview.js';
import { FramePanel } from './frames.js';
import { cutPath, loadParts } from './livecut.js';
import { bendable, bent } from './bend.js';
import { Measure } from './measure.js';
import { Motion } from './motion.js';
import { plotOf, readout as plotReadout } from './plot.js';
import { $, CLIENT, fetchOk, listViews, panelDivider, renderViewsMenu, toaster, viewUrl } from './page.js';
import * as Pen from './pen.js';
import { renderView } from './screen.js';
import { SliderPanel } from './sliders.js';
import {
  SVG_NS, allNodes, buildTree, colourAt, ensureInkLayer, fontSizeOf as fontSizeIn, growViewBox, inkBounds,
  hideByVisibility, inkLayerOf, isHidden, isNode, labelOf, parseSvg, pathOf, setHidden,
} from './sheetdoc.js';
import { renderTree } from './tree.js';

const REF_ID = 'tpv-ref';
const HIT_PX = 8;         // how close the pointer must be to grab a label
const CLICK_PX = 4;       // a press that moves less is a click, not a drag
const MIN_ZOOM = 0.05;
const MAX_ZOOM = 400;
const POLL_MS = 1000;
const TEXT_KEY = 'tpv-text:'; // + the view's name: how many steps smaller or larger this browser shows its dimensions' text
const TEXT_STEPS = [-6, 4];   // each a factor √2: from an eighth of the drawing's own to four times

const ui = {
  bar: $('tpv-bar'), stage: $('tpv-stage'), camera: $('tpv-camera'), paper: $('tpv-paper'),
  host: $('tpv-host'), sheet: $('tpv-sheet'), layers: $('tpv-layers'), status: $('tpv-status'),
  coords: $('tpv-coords'), toast: $('tpv-toast'), empty: $('tpv-empty'), save: $('tpv-save'),
  undo: $('tpv-undo'), clear: $('tpv-clear'), fit: $('tpv-fit'), swatches: $('tpv-swatches'),
  pen: $('tpv-pen'), penButton: $('tpv-pen-button'), penCursor: $('tpv-pen-cursor'), pickBox: $('tpv-pick-box'),
  frames: $('tpv-frames'), framesHead: $('tpv-frames-head'), fields: $('tpv-fields'), fieldsHead: $('tpv-fields-head'),
  textSmaller: $('tpv-text-smaller'), textLarger: $('tpv-text-larger'), textSize: $('tpv-text'),
  motion: $('tpv-motion'), motionHead: $('tpv-motion-head'),
};

const state = {
  views: [], name: null, sheetMtime: null, sketchMtime: null, expanded: new Set(),
  root: null, ref: null, ink: null, annotations: [],
  dirty: false, undo: [], moved: new Map(), layerVis: new Map(),
  // The mouse moves labels and pans unless asked to draw; the tablet's pen always draws.
  tool: 'move', prevTool: 'move', color: INK_COLORS[0][1], cam: { s: 1, x: 0, y: 0 },
  colorFrom: null, // {id, layer} when the ink colour was picked from the sheet
  picked: null,    // the last picked colour: {color, from}
  gen: 0, busy: false, offline: false,
  textStep: 0, // the dimensions' text, in steps from the drawing's own (TEXT_KEY)
  // A scene's frames (frames.js): each one's drawing at a pose of its sliders, as fetched,
  // for the view's mtime, by "frame|pose".
  frameTexts: new Map(), framesFor: null, redrawWanted: false,
  // Its sliders (sliders.js): the pose the drawing shown is exact for, its label or null; the
  // timer that fetches the exact one once they rest; the 3D view's parts, for live sections.
  motionFor: null, sheetPose: null, poseTimer: null, cut: { for: null, parts: null },
};

let gesture = null;
let hover = null;
let spaceHeld = false;
const touches = new Map();
let resolveReady;
const ready = new Promise((resolve) => { resolveReady = resolve; });

const api = (kind, name) => `/api/${kind}/${encodeURIComponent(name)}`;
const viewInUrl = () => (location.pathname.startsWith('/view/') ? decodeURIComponent(location.pathname.slice(6)) : null);
const sketchName = (name) => `${name.replace(/\.svg$/i, '')}.sketch.svg`;
const clamp = (v, lo, hi) => Math.min(hi, Math.max(lo, v));
const toast = toaster(ui.toast);

// ---- Sheet document ---------------------------------------------------------

const shown = (el) => !isHidden(el);

function mount(root) {
  hideByVisibility(root); // so that a node inside a hidden one can be shown
  measure.stale();
  if (!root.getAttribute('width') || !root.getAttribute('height')) {
    const vb = root.viewBox.baseVal;
    if (vb?.width && vb?.height) {
      root.setAttribute('width', `${vb.width}mm`);
      root.setAttribute('height', `${vb.height}mm`);
    }
  }
  // Model coordinates for pointer events, even when every layer is hidden. Not saved.
  const ref = document.createElementNS(SVG_NS, 'g');
  ref.setAttribute('id', REF_ID);
  ref.setAttribute('transform', 'scale(1,-1)');
  root.insertBefore(ref, root.firstChild);
  ui.host.replaceChildren(root);
  state.root = root;
  state.ref = ref;
  state.ink = ensureInkLayer(root);
  state.annotations = [...root.querySelectorAll('g')].filter(A.isAnnotation);
  state.plot = plotOf(root); // a plot's axes and series, for its values under the pointer
  ui.paper.setAttribute('width', root.width.baseVal.value);
  ui.paper.setAttribute('height', root.height.baseVal.value);
  ui.empty.hidden = true;
  hover = null;
}

const fontSizeOf = (el) => fontSizeIn(el, state.root);
const findAnnotation = (id) => (id ? state.annotations.find((g) => g.id === id) ?? null : null);

// Put a label where it was moved to (a snapshot's placement), redrawn for what it measures
// now: in another frame, or a sheet rewritten since, that may have moved.
function placeAs(g, snap) {
  for (const [n, v] of Object.entries(snap.attrs)) {
    if (v === null) g.removeAttributeNS(A.TP_NS, n);
    else g.setAttributeNS(A.TP_NS, `tp:${n}`, v);
  }
  const ann = A.read(g);
  if (ann) A.update(g, ann, fontSizeOf(g));
  else A.restore(g, snap);
}

// ---- The dimensions' text -----------------------------------------------------------------

// ISO 3098 text heights, a factor √2 apart: 2.5 mm at k = 0, 3.5 at 1, 5 at 2, 1.75 at -1 ...
const isoHeight = (k) => (k % 2 === 0 ? 2.5 : 3.5) * 2 ** Math.floor(k / 2);

// The text height of the dimensions and symbols: the drawing's own, or the person's steps
// smaller or larger. The notes keep the drawing's, as do the fields and the hatching.
function textHeight() {
  const own = fontSizeIn(state.root, state.root);
  return state.textStep ? isoHeight(Math.round(2 * Math.log2(own / 2.5)) + state.textStep) : own;
}

function rememberedTextStep(name) {
  try { return clamp(Math.round(Number(localStorage.getItem(TEXT_KEY + name))) || 0, ...TEXT_STEPS); } catch { return 0; }
}

// Draw the annotations at the text height: those it changes again, where they are placed now.
function applyText() {
  for (const g of state.annotations) {
    const was = fontSizeOf(g);
    if (state.textStep) g.setAttribute('font-size', A.fmt(textHeight()));
    else g.removeAttribute('font-size');
    const fs = fontSizeOf(g), ann = fs !== was && A.read(g);
    if (ann) A.render(g, A.geometry(ann, fs), fs);
  }
  const own = fontSizeIn(state.root, state.root), none = !state.annotations.length; // a plot has none
  ui.textSize.textContent = none ? '' : `${A.fmt(textHeight(), 3)} mm`;
  ui.textSize.title = none ? '' : `Text height of the dimensions and symbols; the drawing's own: ${A.fmt(own, 3)} mm`;
  ui.textSmaller.disabled = none || state.textStep <= TEXT_STEPS[0];
  ui.textLarger.disabled = none || state.textStep >= TEXT_STEPS[1];
}

function stepText(by) {
  if (!state.annotations.length) return;
  state.textStep = clamp(state.textStep + by, ...TEXT_STEPS);
  try {
    if (state.textStep) localStorage.setItem(TEXT_KEY + state.name, String(state.textStep));
    else localStorage.removeItem(TEXT_KEY + state.name);
  } catch { /* not remembered */ }
  applyText();
  measure.render();
}

// ---- A scene's frames (SPEC.md §2.10) ----------------------------------------------------

const fieldView = new FieldView({ container: ui.fields, head: ui.fieldsHead });

// Measuring (measure.js): its readout follows the pointer's coordinates.
const measure = new Measure({
  stage: ui.stage, host: ui.host, ref: () => state.ref, root: () => state.root,
  fontSize: () => textHeight(), changed: (text) => { state.measured = text; },
});

const frames = new FramePanel({
  container: ui.frames, onFrame: () => showFrame(), onTrail: () => drawTrail(), nodes: () => partKeys(),
});

// The tree's nodes that are parts of the scene, or groups of them: not the annotations, the
// fields, the hidden lines or the ink.
const partKeys = () => (state.root ? allNodes(state.root).filter((g) => {
  for (let n = g; n && n !== state.root; n = n.parentNode) if (n.getAttributeNS?.(A.TP_NS, 'role')) return false;
  return g !== state.ink;
}).map((g) => pathOf(g, state.root)) : []);

// The drawing of a frame of the view (k, or null for a scene without frames), at a pose of its
// sliders (their label, or null): fetched once for each version of the view.
const TEXTS_KEPT = 64;
async function frameText(name, k, pose = posed()) {
  const key = `${k}|${pose}`;
  if (!state.frameTexts.has(key)) {
    const query = new URLSearchParams();
    if (k !== null) query.set('frame', k);
    if (pose) query.set('pose', pose);
    const text = (await fetchOk(`${api('file', name)}?${query}`)).text();
    state.frameTexts.set(key, text);
    text.catch(() => state.frameTexts.delete(key));
    while (state.frameTexts.size > TEXTS_KEPT) state.frameTexts.delete(state.frameTexts.keys().next().value);
  }
  return state.frameTexts.get(key);
}

// The view's frames, when it opens or its files change: then every frame's drawing, one
// after another, so that stepping through them is quick. start: {at}, the frame to show.
async function loadFrames(name, mtime, start = null) {
  if (state.framesFor === `${name} ${mtime}`) return;
  state.framesFor = `${name} ${mtime}`;
  state.frameTexts = new Map();
  let info = {};
  if (/\.(step|stp)(#|$)/i.test(name)) {
    try { info = await (await fetchOk(api('frames', name))).json(); } catch (err) { toast(err.message, true); }
  }
  const k = start && info.frames ? info.frames.findIndex((f) => Object.entries(start.at).every(([a, v]) => f.at[a] === v)) : -1;
  frames.set(info, k >= 0 ? k : null);
  ui.framesHead.hidden = !frames.on;
  if (!frames.on) return;
  (async () => {
    for (let k = 0; k < info.frames.length && state.framesFor === `${name} ${mtime}`; k++) {
      try { await frameText(name, k); } catch { /* fetched again when shown */ }
    }
  })();
}

// Show the frame the panel is at, keeping the ink, the moved labels and what is hidden.
function showFrame() {
  if (frames.index >= 0) redraw();
}

// Draw the view again for the frame and the sliders where they are, keeping the ink, the moved
// labels and what is hidden. Resolves once it shows them, however often it is asked meanwhile.
let redrawing = null;
function redraw() {
  if (redrawing) { state.redrawWanted = true; return redrawing; }
  redrawing = (async () => {
    try {
      do {
        state.redrawWanted = false;
        while (state.busy) await new Promise((r) => setTimeout(r, 30)); // a sheet opening otherwise
        if (state.name && !(frames.on && frames.index < 0)) await openSheet(state.name, { reload: true, keep: true });
      } while (state.redrawWanted);
    } finally {
      redrawing = null;
    }
  })();
  return redrawing;
}

// ---- A scene's sliders (SPEC.md §2.13) ---------------------------------------------------
// Moving one moves the scene's parts. In a section, the moving ones are cut live from the 3D
// view's triangles (livecut.js), in place of their exact cut; a 2D scene's are moved as they
// are; a projection's fade. The exact drawing at the sliders' positions follows from the
// server once they rest: let go, or held still for POSE_REST_MS.

const POSE_REST_MS = 300;
const sceneOf = (name) => name.split('#')[0];
const isSceneView = (name) => /\.(step|stp)(#|$)/i.test(name ?? '');
const posed = () => (sliders.on ? sliders.label : null); // the pose the drawing should be exact for

const sliders = new SliderPanel({ container: ui.motion, head: ui.motionHead, scene: null,
                                  onPose: (_, settled) => onPose(settled) });

async function loadMotion(name, mtime) {
  if (state.motionFor === `${name} ${mtime}`) return;
  state.motionFor = `${name} ${mtime}`;
  sliders.scene = isSceneView(name) ? sceneOf(name) : null;
  let info = {};
  if (isSceneView(name)) {
    try { info = await (await fetchOk(api('motion', name))).json(); } catch (err) { toast(err.message, true); }
  }
  sliders.set(info);
}

function onPose(settled) {
  if (!state.root) return;
  liveDraw();
  clearTimeout(state.poseTimer);
  state.poseTimer = setTimeout(redraw, settled ? 0 : POSE_REST_MS);
}

// The drawing's plane, from its tp:view: unit vectors, xdir square to the normal.
function planeOf(view) {
  const vec = (a) => (view.getAttribute(a) ?? '').trim().split(/[\s,]+/).map(Number);
  const unit = (v) => { const n = Math.hypot(...v); return v.map((c) => c / n); };
  const origin = vec('origin'), normal = unit(vec('normal')), x = vec('xdir');
  const along = x[0] * normal[0] + x[1] * normal[1] + x[2] * normal[2];
  return { origin, normal, xdir: unit(x.map((c, i) => c - along * normal[i])) };
}

// The 3D view's parts of the scene the drawing is of, at its frame (livecut.js), or null.
function cutParts() {
  const scene = sceneOf(state.name), k = frames.on ? Math.max(0, frames.index) : null;
  const key = `${scene} ${state.sheetMtime} ${k}`;
  if (state.cut.for !== key) {
    const url = `${api('file', scene)}${k === null ? '' : `?frame=${k}`}`;
    state.cut = { for: key, parts: loadParts(url).catch((err) => { toast(`Can't cut live: ${err.message}`, true); return null; }) };
  }
  return state.cut.parts;
}

// Undo what liveDraw did to the drawing.
function clearLive(root) {
  for (const el of root.querySelectorAll('.tpv-live')) el.remove();
  for (const el of root.querySelectorAll('.tpv-live-off, .tpv-stale')) el.classList.remove('tpv-live-off', 'tpv-stale');
  for (const g of root.querySelectorAll('[data-rest-transform]')) {
    const rest = g.getAttribute('data-rest-transform');
    if (rest) g.setAttribute('transform', rest);
    else g.removeAttribute('transform');
    g.removeAttribute('data-rest-transform');
  }
}

const mix = (rgb, white) => `#${rgb.map((c) => Math.round(c * 255 * (1 - white) + 255 * white).toString(16).padStart(2, '0')).join('')}`;
const TINT = 0.55; // of white in a part's colour, as drawing.py tints its regions

// Draw the moving parts where the sliders put them, until the exact drawing comes.
async function liveDraw() {
  const root = state.root;
  if (!root || !sliders.on) return;
  clearLive(root);
  const label = sliders.label;
  if (label === state.sheetPose) return;
  const view = root.getElementsByTagNameNS(A.TP_NS, 'view')[0];
  if (!view) return;
  const kind = view.getAttribute('kind'), own = sliders.motion.own(sliders.values), along = sliders.motion.along(sliders.values);
  const groups = new Map(allNodes(root).map((g) => [pathOf(g, root), g]));
  const drawing = (g) => [...g.children].filter((c) => !isNode(c) && !c.classList.contains('tpv-live')); // its own
  if (kind === '2D') { // in the plane: moved as they are, each node by its own moves, inside those around it
    const { origin: o, normal, xdir: x } = planeOf(view);
    const y = [normal[1] * x[2] - normal[2] * x[1], normal[2] * x[0] - normal[0] * x[2], normal[0] * x[1] - normal[1] * x[0]];
    const dot = (a, b) => a[0] * b[0] + a[1] * b[1] + a[2] * b[2];
    // What is bent along a path can't be moved as it is: it fades until the exact drawing comes.
    for (const [key, g] of groups) if (sliders.motion.following(key)) g.classList.add('tpv-stale');
    for (const [key, m] of own) {
      const g = groups.get(key);
      if (!g) continue;
      const R = (v) => [0, 1, 2].map((r) => m[4 * r] * v[0] + m[4 * r + 1] * v[1] + m[4 * r + 2] * v[2]);
      const shift = R(o).map((c, i) => c + m[4 * i + 3] - o[i]), rx = R(x), ry = R(y);
      const affine = [dot(x, rx), dot(y, rx), dot(x, ry), dot(y, ry), dot(x, shift), dot(y, shift)].map((v) => A.fmt(v, 6));
      g.setAttribute('data-rest-transform', g.getAttribute('transform') ?? '');
      g.setAttribute('transform', `${g.getAttribute('transform') ?? ''} matrix(${affine.join(' ')})`.trim());
    }
    return;
  }
  const moving = [...groups.keys()].filter((key) => sliders.motion.moved(key));
  if (kind !== 'section') { // a projection: exact once the sliders rest
    for (const key of moving) groups.get(key).classList.add('tpv-stale');
    return;
  }
  const parts = await cutParts();
  if (state.root !== root || !parts || sliders.label !== label) return; // a newer one draws
  const plane = planeOf(view);
  let layer = null;
  for (const [key, part] of parts) {
    if (!sliders.motion.moved(key)) continue;
    const g = groups.get(key);
    if (g) for (const c of drawing(g)) c.classList.add('tpv-live-off');
    const d = cutPath(shaped(part, key, along), Motion.world(own, key), plane);
    if (!d || (!g && state.layerVis.get(key) === false)) continue;
    if (!g && !layer) layer = liveLayer(root);
    const cut = document.createElementNS(SVG_NS, 'g');
    cut.setAttribute('class', 'tpv-live');
    cut.setAttribute('data-live', key);
    const fill = g?.querySelector(':scope > path[id*="-cut-"]')?.getAttribute('fill') ?? liveHatch(root);
    for (const attrs of [{ d, stroke: 'none', fill: mix(part.color, TINT), 'fill-rule': 'evenodd' },
                         { d, fill, 'fill-rule': 'evenodd', 'vector-effect': 'non-scaling-stroke' }]) {
      const path = document.createElementNS(SVG_NS, 'path');
      for (const [k, v] of Object.entries(attrs)) path.setAttribute(k, v);
      cut.append(path);
    }
    (g ?? layer).append(cut);
  }
}

// A part of the 3D view as it is at the sliders' positions before it moves: bent along the
// path it follows (bend.js), if any.
function shaped(part, key, along) {
  const follow = sliders.motion.following(key), by = follow ? along.get(follow.move) : null;
  if (by === null || by === undefined) return part;
  if (part.bend?.path !== follow.path) part.bend = bendable(part, follow.path);
  return bent(part.bend, by);
}

// Where live sections of parts the exact drawing has no group for go: a layer of the
// viewer's own under the annotations, which the tree doesn't list.
function liveLayer(root) {
  let layer = root.querySelector(':scope > g.tpv-live');
  if (!layer) {
    layer = document.createElementNS(SVG_NS, 'g');
    layer.setAttribute('class', 'tpv-live');
    for (const [k, v] of Object.entries({ transform: 'scale(1,-1)', stroke: '#1a1a1a', 'stroke-width': '1.5',
                                          fill: 'none', 'stroke-linejoin': 'round' })) layer.setAttribute(k, v);
    const above = [...root.children].find((c) => c.getAttributeNS?.(A.TP_NS, 'role') || c === state.ink);
    root.insertBefore(layer, above ?? null);
  }
  return layer;
}

// A hatching for live sections, as the drawing's own: at 45°, as far apart as its text sizes it.
function liveHatch(root) {
  if (!root.querySelector('#tpv-live-hatch')) {
    const s = A.fmt(fontSizeIn(root, root) * 0.6);
    const defs = root.querySelector(':scope > defs') ?? root.insertBefore(document.createElementNS(SVG_NS, 'defs'), root.firstChild);
    defs.insertAdjacentHTML('beforeend', `<pattern xmlns="${SVG_NS}" id="tpv-live-hatch" patternUnits="userSpaceOnUse" `
      + `width="${s}" height="${s}" patternTransform="rotate(45)"><path d="M${A.fmt(s / 2)} 0 V${s}" stroke="#1a1a1a" `
      + 'stroke-width="0.75" vector-effect="non-scaling-stroke"/></pattern>');
  }
  return 'url(#tpv-live-hatch)';
}

// Every frame along the first axis, the others where they are, faint behind this one: what
// the parts sweep through, or one of them (frames.trail, its key). Only the parts; it follows
// what the tree hides. It is drawn under the sheet, in a drawing of its own, so it is never saved.
async function drawTrail() {
  ui.host.querySelector('.tpv-trail')?.remove();
  if (frames.trail === null || !frames.on || !state.root) return;
  const only = frames.trail;
  const name = state.name, here = frames.index;
  const others = frames.info.axes.slice(1), now = frames.frame;
  const trail = document.createElementNS(SVG_NS, 'svg');
  trail.setAttribute('class', 'tpv-trail');
  for (const a of ['viewBox', 'width', 'height']) trail.setAttribute(a, state.root.getAttribute(a));
  for (const [k, f] of frames.info.frames.entries()) {
    if (k === here || !others.every((a) => f.at[a.name] === now?.at[a.name])) continue;
    let root;
    try { root = parseSvg(await frameText(name, k)); } catch { continue; }
    if (state.name !== name || frames.trail !== only) return;
    for (const layer of [...root.children]) {
      const role = layer.getAttributeNS(A.TP_NS, 'role');
      if (layer.localName !== 'g' || role || labelOf(layer) === 'Sketch') layer.remove();
    }
    hideByVisibility(root);
    for (const g of allNodes(root)) {
      const visible = state.layerVis.get(pathOf(g, root));
      if (visible !== undefined) setHidden(g, !visible);
    }
    if (only) { // that node alone, as Ctrl+click in the tree shows it
      const node = allNodes(root).find((g) => pathOf(g, root) === only);
      for (const layer of [...root.children]) if (layer.localName === 'g') setHidden(layer, true);
      if (node) setHidden(node, false);
    }
    trail.append(...[...root.children].filter((c) => c.localName === 'g'));
  }
  if (state.name === name && frames.trail === only) {
    ui.host.querySelector('.tpv-trail')?.remove();
    ui.host.insertBefore(trail, state.root);
  }
}

// A view in the listing, or one of a scene's views drawn on the fly ("a.step#x=10"), which
// changes when the scene does.
function viewInfo(name) {
  const listed = state.views.find((v) => v.name === name);
  if (listed || !name.includes('#')) return listed;
  const scene = state.views.find((v) => v.name === name.split('#')[0]);
  return scene && { name, kind: 'sheet', mtime: scene.mtime, sketch: null };
}

// keep: going to another frame, which keeps the ink and label moves as they are.
async function openSheet(name, { reload = false, keep = false } = {}) {
  const info = viewInfo(name);
  if (info?.kind !== 'sheet') return;
  state.gen++;
  state.busy = true;
  try {
    // On reload with unsaved work, keep the ink and label moves in memory rather than
    // re-reading them from an older sketch file.
    const carry = reload && (state.dirty || keep)
      ? { strokes: [...state.ink.children], moved: state.moved, undo: state.undo, dirty: state.dirty }
      : null;
    const sketchMtime = info.sketch?.mtime ?? null;
    let sketch = null;
    if (sketchMtime && !carry) sketch = await fetchOk(api('sketch', name)).then((r) => r.text(), () => null);
    const fresh = sketch && BigInt(sketchMtime) >= BigInt(info.mtime); // still what was on screen
    if (!keep) await loadFrames(name, info.mtime, fresh ? sketchFrame(sketch) : null);
    await loadMotion(name, info.mtime);
    const drawnAt = fresh ? sketchPose(sketch) : null; // the sliders where the person sketched
    if (drawnAt) sliders.adopt(drawnAt);
    const pose = posed(), k = frames.on ? Math.max(0, frames.index) : null;
    let base = k !== null || pose ? await frameText(name, k, pose) : await (await fetchOk(api('file', name))).text();
    let inkFrom = null;
    if (fresh) base = sketch;
    else if (sketch) inkFrom = sketch; // the sheet changed since: show it, with the unhandled ink on top
    const root = parseSvg(base);
    const was = reload && state.root ? state.root.viewBox.baseVal : null;
    const wasAt = was && { x: was.x, y: was.y };
    mount(root);
    if (wasAt) { // the same model point where it was on screen, though the drawing's extent changed
      const vb = root.viewBox.baseVal, k = state.cam.s * root.width.baseVal.value / vb.width;
      state.cam = { ...state.cam, x: state.cam.x + (vb.x - wasAt.x) * k, y: state.cam.y + (vb.y - wasAt.y) * k };
      applyCamera();
    }
    if (inkFrom) {
      try {
        const old = inkLayerOf(parseSvg(inkFrom));
        if (old) state.ink.append(...old.children);
      } catch { /* unreadable sketch: just show the sheet */ }
    }
    if (carry) {
      state.ink.append(...carry.strokes);
      for (const [id, snap] of carry.moved) {
        const g = findAnnotation(id);
        if (g) placeAs(g, snap);
      }
      Object.assign(state, { moved: carry.moved, undo: carry.undo, dirty: carry.dirty || !keep });
    } else {
      Object.assign(state, { moved: new Map(), undo: [], dirty: false });
    }
    state.textStep = rememberedTextStep(name);
    applyText();
    for (const g of allNodes(root)) {
      const visible = state.layerVis.get(pathOf(g, root));
      if (visible !== undefined) setHidden(g, !visible);
    }
    Object.assign(state, { name, sheetMtime: info.mtime, sketchMtime, sheetPose: pose });
    document.title = `${name} · Tracepaper`;
    if (viewInUrl() !== name) history.replaceState(null, '', viewUrl(name));
    fieldView.attach(root);
    renderLayers();
    renderViews();
    if (!reload) fit();
    updateStatus();
    drawTrail();
    if (posed() !== pose) liveDraw(); // the sliders moved on meanwhile
    resolveReady();
  } catch (err) {
    toast(`Could not open ${name}: ${err.message}`, true);
    if (!state.root) { // nothing shown yet: say why there
      ui.empty.textContent = `Could not open ${name}: ${err.message.replace(/^\d+ /, '')}`;
      ui.empty.hidden = false;
    }
  } finally {
    state.busy = false;
    state.gen++;
  }
}

// Where a sketch file's scene had its sliders, {name: value}, or null.
function sketchPose(text) {
  const label = /<tp:view\b[^>]*\spose="([^"]*)"/.exec(text)?.[1];
  if (!label) return null;
  return Object.fromEntries(label.split(' ').map((kv) => kv.split('=')).map(([k, v]) => [k, Number(v)]));
}

// The frame a sketch file was saved at: its index in the panel's frames, or null.
function sketchFrame(text) {
  const label = /<tp:view\b[^>]*\sframe="([^"]*)"/.exec(text)?.[1];
  if (!label) return null;
  const at = Object.fromEntries(label.split(' ').map((kv) => kv.split('=')).map(([k, v]) => [k, Number(v)]));
  return { at };
}

function showEmpty(message) {
  ui.host.replaceChildren();
  ui.paper.setAttribute('width', 0);
  ui.paper.setAttribute('height', 0);
  Object.assign(state, { root: null, ref: null, ink: null, annotations: [], name: null });
  ui.empty.textContent = message;
  ui.empty.hidden = false;
  applyText();
  renderLayers();
  updateStatus();
}

// ---- Panels and status ------------------------------------------------------

// The tree panel, topmost first as drawn.
function renderLayers() {
  ui.layers.replaceChildren();
  if (!state.root) return;
  renderTree(ui.layers, buildTree(state.root), {
    expanded: state.expanded, topFirst: true,
    onChange: (changes) => {
      for (const [node, hidden] of changes) setLayerVisible(node.key, !hidden, false);
      renderLayers();
    },
    mark: (node) => (node.el === state.ink ? 'tpv-ink' : ''),
  });
}

// Show or hide a node with everything inside it. key is its path in the tree (pathOf); a
// top-level layer's path is its name.
function setLayerVisible(key, visible, render = true) {
  const g = state.root && allNodes(state.root).find((n) => pathOf(n, state.root) === key);
  if (!g) return;
  setHidden(g, !visible);
  for (const k of [...state.layerVis.keys()]) if (k.startsWith(`${key} / `)) state.layerVis.delete(k); // they follow it
  state.layerVis.set(key, visible);
  measure.stale(); // what is hidden doesn't snap
  if (hover && !shown(hover)) setHover(null);
  if (render) renderLayers();
}

const renderViews = () => renderViewsMenu(ui.sheet, state.views, state.name);

function updateStatus() {
  let text = '', cls = '';
  if (state.offline) { text = 'Viewer server stopped'; cls = 'dirty'; }
  else if (state.dirty) { text = 'Unsaved changes'; cls = 'dirty'; }
  else if (state.sketchMtime && state.name) { text = `Saved ${sketchName(state.name)}, ready for the AI`; cls = 'pending'; }
  ui.status.textContent = ui.status.title = text; // the title: for when it is cut short
  ui.status.className = `tpv-status ${cls}`;
  ui.undo.disabled = state.undo.length === 0;
  ui.clear.disabled = !state.ink?.firstElementChild;
  ui.save.disabled = !state.root;
}

function markDirty() {
  state.dirty = true;
  updateStatus();
}

// ---- Camera -----------------------------------------------------------------

function applyCamera() {
  const { s, x, y } = state.cam;
  ui.camera.setAttribute('transform', `translate(${x} ${y}) scale(${s})`);
}

function fit() {
  if (!state.root) return;
  const r = ui.stage.getBoundingClientRect();
  const w = state.root.width.baseVal.value, h = state.root.height.baseVal.value;
  if (!(w > 0 && h > 0)) return;
  const pad = 32;
  const s = clamp(Math.min((r.width - 2 * pad) / w, (r.height - 2 * pad) / h), MIN_ZOOM, MAX_ZOOM);
  state.cam = { s, x: (r.width - w * s) / 2, y: (r.height - h * s) / 2 };
  applyCamera();
}

function zoomAt(px, py, factor) {
  const c = state.cam;
  const s = clamp(c.s * factor, MIN_ZOOM, MAX_ZOOM), f = s / c.s;
  state.cam = { s, x: px - (px - c.x) * f, y: py - (py - c.y) * f };
  applyCamera();
}

function stagePoint(e) {
  const r = ui.stage.getBoundingClientRect();
  return { x: e.clientX - r.left, y: e.clientY - r.top };
}

// ---- Pointer input ----------------------------------------------------------

function hitTest(clientX, clientY) {
  let best = null;
  for (const el of state.annotations) {
    if (!shown(el)) continue;
    const ann = A.read(el);
    const layer = el.parentNode;
    const p = ann && toLocal(layer, clientX, clientY);
    if (!p) continue;
    const fs = fontSizeOf(el);
    const d = A.distance(ann, fs, p) * pxPerUnit(layer);
    if (d <= HIT_PX && (!best || d < best.d)) best = { el, ann, fs, layer, d };
  }
  return best;
}

function setHover(el) {
  if (hover === el) return;
  if (hover) {
    hover.classList.remove('tpv-hover');
    if (!hover.getAttribute('class')) hover.removeAttribute('class');
  }
  hover = el;
  hover?.classList.add('tpv-hover');
  updateCursor();
}

function updateCursor() {
  const t = gesture?.type;
  ui.stage.style.cursor = t === 'pan' || t === 'pinch' || t === 'drag' ? 'grabbing'
    : spaceHeld ? 'grab'
    : state.tool === 'pen' || state.tool === 'pick' || state.tool === 'measure' ? 'crosshair'
    : hover ? 'grab' : 'default';
}

function capture(e) {
  try { ui.stage.setPointerCapture(e.pointerId); } catch { /* not an active pointer */ }
}

const startPan = (e) => ({ type: 'pan', id: e.pointerId, last: stagePoint(e), from: stagePoint(e) });

function startPinch() {
  const [a, b] = [...touches.values()];
  return {
    type: 'pinch', d0: Math.hypot(b.x - a.x, b.y - a.y) || 1,
    m0: { x: (a.x + b.x) / 2, y: (a.y + b.y) / 2 }, cam0: { ...state.cam },
  };
}

function movePinch() {
  const [a, b] = [...touches.values()];
  if (!b) return;
  const g = gesture, m = { x: (a.x + b.x) / 2, y: (a.y + b.y) / 2 };
  const s = clamp(g.cam0.s * Math.hypot(b.x - a.x, b.y - a.y) / g.d0, MIN_ZOOM, MAX_ZOOM);
  const wx = (g.m0.x - g.cam0.x) / g.cam0.s, wy = (g.m0.y - g.cam0.y) / g.cam0.s;
  state.cam = { s, x: m.x - wx * s, y: m.y - wy * s };
  applyCamera();
}

// Strokes and erasing take points in client coordinates, from the mouse or a tablet (ink.js).

const ink = new Ink({ layer: () => state.ink, ref: () => state.ref, undo: () => state.undo, changed: markDirty });

function beginStroke(clientX, clientY) {
  if (isHidden(state.ink)) setLayerVisible(labelOf(state.ink), true);
  const s = ink.begin(clientX, clientY, state.color, state.colorFrom?.id);
  if (frames.label) s.path.setAttributeNS(A.TP_NS, 'tp:frame', frames.label); // the frame it was drawn on
  return s;
}

function startDrag(hit, e) {
  const inv = hit.layer.getScreenCTM().inverse();
  setHover(hit.el);
  return {
    type: 'drag', id: e.pointerId, el: hit.el, ann: hit.ann, fs: hit.fs, inv,
    p0: new DOMPoint(e.clientX, e.clientY).matrixTransform(inv), before: A.snapshot(hit.el),
  };
}

function moveDrag(g, e) {
  const p = new DOMPoint(e.clientX, e.clientY).matrixTransform(g.inv);
  A.update(g.el, A.dragged(g.ann, [p.x - g.p0.x, p.y - g.p0.y], g.fs), g.fs);
}

function endGesture() {
  const g = gesture;
  gesture = null;
  if (g?.type === 'stroke') ink.commit(g);
  else if (g?.type === 'erase') ink.finishErase(g.removed);
  else if (g?.type === 'pan' && g.clears && Math.hypot(g.last.x - g.from.x, g.last.y - g.from.y) < CLICK_PX) {
    measure.clear();
  } else if (g?.type === 'drag') {
    const after = A.snapshot(g.el);
    if (!A.samePlacement(after, g.before)) {
      state.undo.push({ type: 'annot', id: g.el.id, el: g.el, before: g.before });
      if (g.el.id) state.moved.set(g.el.id, after);
      markDirty();
    }
  }
  updateCursor();
}

function onPointerDown(e) {
  if (!state.root) return;
  if (e.pointerType === 'touch') {
    // Fingers pan and pinch; only a pen or mouse draws.
    touches.set(e.pointerId, stagePoint(e));
    capture(e);
    if (touches.size === 2) { endGesture(); gesture = startPinch(); }
    else if (touches.size === 1 && !gesture) gesture = startPan(e);
    e.preventDefault();
    return;
  }
  if (gesture) return;
  if (e.pointerType === 'pen' && (e.button === 5 || e.buttons & 32)) {
    // A pen's eraser end erases, whatever the tool.
    gesture = { type: 'erase', id: e.pointerId, removed: [] };
    ink.eraseAt(e.clientX, e.clientY, gesture.removed);
  } else if (e.button === 1 || e.button === 2 || (e.button === 0 && (spaceHeld || e.shiftKey))) {
    gesture = startPan(e);
    // In Measure, a right click that doesn't drag starts the measurement again.
    gesture.clears = e.button === 2 && state.tool === 'measure';
  } else if (e.button !== 0) return;
  else if (state.tool === 'measure') {
    measure.pick(e.clientX, e.clientY);
    ui.coords.textContent = state.measured;
    e.preventDefault();
    return;
  } else if (state.tool === 'pick') {
    pickColor(e.clientX, e.clientY);
    e.preventDefault();
    return;
  } else if (state.tool === 'pen') gesture = { type: 'stroke', id: e.pointerId, ...beginStroke(e.clientX, e.clientY) };
  else {
    const hit = hitTest(e.clientX, e.clientY);
    gesture = hit ? startDrag(hit, e) : startPan(e);
  }
  capture(e);
  e.preventDefault();
  updateCursor();
}

function onPointerMove(e) {
  if (!state.root) return;
  const p = toLocal(state.ref, e.clientX, e.clientY);
  const inPlot = p && state.plot && plotReadout(state.plot, p, pxPerUnit(state.ref), shown);
  if (p) ui.coords.textContent = inPlot || `x ${p[0].toFixed(2)}   y ${p[1].toFixed(2)} mm`;
  const value = fieldView.on && fieldView.valueAt(e.clientX, e.clientY);
  if (p && value) ui.coords.textContent += `   ${value}`;
  if (e.pointerType === 'touch' && touches.has(e.pointerId)) touches.set(e.pointerId, stagePoint(e));
  if (!gesture) {
    if (e.pointerType === 'touch' || spaceHeld) return;
    if (state.tool === 'move') setHover(hitTest(e.clientX, e.clientY)?.el ?? null);
    else if (state.tool === 'pick') previewPick(e.clientX, e.clientY);
    else if (state.tool === 'measure') {
      measure.move(e.clientX, e.clientY);
      if (state.measured) ui.coords.textContent += `   ${state.measured}`;
    }
    return;
  }
  if (gesture.type === 'pinch') { movePinch(); return; }
  if (e.pointerId !== gesture.id) return;
  if (gesture.type === 'pan') {
    const q = stagePoint(e);
    state.cam.x += q.x - gesture.last.x;
    state.cam.y += q.y - gesture.last.y;
    gesture.last = q;
    applyCamera();
  } else if (gesture.type === 'stroke') ink.extend(gesture, coalesced(e));
  else if (gesture.type === 'erase') for (const [x, y] of coalesced(e)) ink.eraseAt(x, y, gesture.removed);
  else if (gesture.type === 'drag') moveDrag(gesture, e);
}

function onPointerUp(e) {
  if (e.pointerType === 'touch') touches.delete(e.pointerId);
  if (!gesture) return;
  if (gesture.type === 'pinch') {
    if (touches.size < 2) { gesture = null; updateCursor(); }
    return;
  }
  if (e.pointerId === gesture.id) endGesture();
}

function onWheel(e) {
  e.preventDefault();
  if (!state.root) return;
  const dy = e.deltaY * (e.deltaMode === 1 ? 16 : e.deltaMode === 2 ? 400 : 1);
  const p = stagePoint(e);
  zoomAt(p.x, p.y, Math.exp(-dy * 0.0015));
}

// ---- Commands ---------------------------------------------------------------

function setTool(tool) {
  if (tool === 'pick' && state.tool !== 'pick') state.prevTool = state.tool;
  state.tool = tool;
  for (const b of document.querySelectorAll('[data-tool]')) b.setAttribute('aria-pressed', String(b.dataset.tool === tool));
  if (tool !== 'move') setHover(null);
  if (tool !== 'pick') showPickBox(null);
  if (tool !== 'measure') measure.clear();
  updateCursor();
}

function selectInk(color, from) {
  Object.assign(state, { color, colorFrom: from });
  for (const b of ui.swatches.querySelectorAll('.tpv-swatch')) {
    b.setAttribute('aria-pressed', String(from ? b === ui.picked : b.dataset.color === color && b !== ui.picked));
  }
}

function setColor(i) {
  const c = INK_COLORS[i]?.[1];
  if (c) selectInk(c, null);
}

function usePicked() {
  if (state.picked) selectInk(state.picked.color, state.picked.from);
}

// ---- Colour picker: ink in the colour of something on the sheet ----------------

const describe = (from) => [from.id, from.layer && `(${from.layer})`].filter(Boolean).join(' ');

function showPickBox(el) {
  const box = ui.pickBox;
  if (!el) { box.setAttribute('visibility', 'hidden'); return; }
  const r = el.getBoundingClientRect(), s = ui.stage.getBoundingClientRect(), pad = 3;
  box.setAttribute('x', r.left - s.left - pad);
  box.setAttribute('y', r.top - s.top - pad);
  box.setAttribute('width', r.width + 2 * pad);
  box.setAttribute('height', r.height + 2 * pad);
  box.removeAttribute('visibility');
}

function previewPick(clientX, clientY) {
  const hit = colourAt(state.root, clientX, clientY);
  showPickBox(hit?.el ?? null);
  if (hit) ui.coords.textContent += `   ${hit.color}  ${describe(hit.from)}`;
}

function pickColor(clientX, clientY) {
  const hit = colourAt(state.root, clientX, clientY);
  if (!hit) {
    toast('Nothing to pick a colour from here');
    return;
  }
  state.picked = { color: hit.color, from: hit.from };
  ui.picked.dataset.color = hit.color;
  ui.picked.style.setProperty('--c', hit.color);
  ui.picked.title = `${hit.color} from ${describe(hit.from) || 'the sheet'} (5)`;
  ui.picked.hidden = false;
  selectInk(hit.color, hit.from);
  setTool(state.prevTool);
  toast(`Ink: ${hit.color} from ${describe(hit.from) || 'the sheet'}`);
}

function undo() {
  const u = state.undo.pop();
  if (!u) return;
  if (ink.undoStep(u)) return;
  if (u.type === 'annot') {
    const g = findAnnotation(u.id) ?? u.el;
    A.restore(g, u.before);
    if (u.id) state.moved.set(u.id, u.before);
  }
  markDirty();
}

const clearInk = () => ink.clear();

// A copy of the sheet as drawn, without what the viewer adds to it.
function sheetCopy() {
  const copy = state.root.cloneNode(true);
  copy.querySelector(`#${REF_ID}`)?.remove();
  for (const n of copy.querySelectorAll('.tpv-hover')) {
    n.classList.remove('tpv-hover');
    if (!n.getAttribute('class')) n.removeAttribute('class');
  }
  return copy;
}

async function save() {
  if (!state.root || state.busy) return;
  if (posed() !== state.sheetPose) { // the exact drawing at the sliders, not the live one
    clearTimeout(state.poseTimer);
    await redraw();
  }
  const name = state.name;
  const copy = sheetCopy();
  copy.setAttributeNS(A.TP_NS, 'tp:sketch-of', name.split('/').pop()); // next to it
  copy.setAttributeNS(A.TP_NS, 'tp:saved', new Date().toISOString());
  growViewBox(copy, inkBounds(state.ink));
  const body = `<?xml version="1.0" encoding="UTF-8"?>\n${new XMLSerializer().serializeToString(copy)}\n`;
  state.gen++;
  try {
    const r = await fetchOk(api('sketch', name), {
      method: 'PUT', headers: { 'Content-Type': 'image/svg+xml', 'X-Tracepaper': '1' }, body,
    });
    const { mtime } = await r.json();
    if (state.name === name) Object.assign(state, { dirty: false, moved: new Map(), sketchMtime: mtime });
    toast(`Saved ${sketchName(name)}`);
  } catch (err) {
    toast(`Save failed: ${err.message}`, true);
  } finally {
    state.gen++;
    updateStatus();
  }
}

// ---- Following the files on disk ---------------------------------------------

async function sync(listing) {
  state.views = listing.views;
  renderViews();
  if (!state.name) {
    const sheets = listing.views.filter((v) => v.kind === 'sheet');
    const wanted = viewInUrl() ?? listing.initial;
    const first = (wanted && viewInfo(wanted)?.kind === 'sheet' ? viewInfo(wanted) : null) ?? sheets[0];
    if (first) await openSheet(first.name);
    else if (ui.empty.hidden || ui.empty.textContent === 'Loading…') showEmpty(`No sheets in ${listing.dir}`);
    return;
  }
  const cur = viewInfo(state.name);
  if (!cur) return;
  const sketch = cur.sketch?.mtime ?? null;
  if (cur.mtime !== state.sheetMtime) {
    const handled = state.sketchMtime && !sketch;
    await openSheet(cur.name, { reload: true });
    toast(handled ? 'Sheet updated, sketch handled' : 'Sheet updated');
  } else if (state.sketchMtime && !sketch) {
    // The AI deleted the sketch file: it has been dealt with.
    state.sketchMtime = null;
    if (!state.dirty) {
      state.ink.replaceChildren();
      state.undo = [];
      toast('Sketch handled');
    }
    updateStatus();
  } else if (sketch !== state.sketchMtime && !state.dirty) {
    state.sketchMtime = sketch;
    updateStatus();
  }
}

async function poll() {
  const gen = state.gen;
  let listing = null;
  try { listing = await listViews(); } catch { /* server stopped */ }
  if (state.offline !== !listing) { state.offline = !listing; updateStatus(); }
  if (listing && gen === state.gen && !state.busy) {
    try { await sync(listing); } catch (err) { console.error(err); }
  }
  setTimeout(poll, POLL_MS);
}

// ---- Tablet pen --------------------------------------------------------------
// The toolbar's button connects the reMarkable (tracepaper/pen.py) and lets it go. Its
// pen draws and erases on the sheet (ink.js's PenInput). With several windows open, the
// server sends it to the one used last with the mouse, or where Connect was pressed.

const tablet = new Pen.Tablet({
  client: CLIENT, stage: ui.stage, label: ui.pen, button: ui.penButton, toast,
  onStatus: (s) => {
    if (s?.state !== 'connected' || !tablet.here) {
      pen.end();
      pen.showCursor(null);
    }
    viewChanged();
  },
  onFrame: (f) => pen.frame(f),
});

const pen = new PenInput({
  ink, tablet, stage: ui.stage, cursor: ui.penCursor,
  ready: () => !!state.root && tablet.here, begin: beginStroke, ended: viewChanged,
});

// ---- The tablet's screen ---------------------------------------------------------
// While the tablet's pen draws in this window, the tablet shows this window's view (if it
// can show pictures, screen.js), shortly after the view changes.

const feed = new Pen.ScreenFeed({ tablet, pen, client: CLIENT, picture: renderScreen, busy: () => !!gesture });

function viewChanged() { feed.changed(); }

// The picture for the tablet's screen, or null if there is nothing to show.
function renderScreen() {
  const r = ui.stage.getBoundingClientRect();
  if (!state.root || !r.width || !r.height || !tablet.status?.pixels) return null;
  return renderView({ sheet: sheetCopy(), cam: state.cam, stage: r, size: tablet.size,
                      pixels: tablet.status.pixels, font: getComputedStyle(ui.stage).fontFamily });
}

// ---- Wiring -----------------------------------------------------------------

INK_COLORS.forEach((_, i) => {
  const b = swatch(i);
  b.addEventListener('click', () => setColor(i));
  ui.swatches.append(b);
});

// The last colour picked from the sheet, shown once there is one, and the picker itself.
ui.picked = document.createElement('button');
ui.picked.className = 'tpv-swatch tpv-picked';
ui.picked.hidden = true;
ui.picked.setAttribute('aria-label', 'Picked ink colour');
ui.picked.append(document.createElement('span'));
ui.picked.addEventListener('click', usePicked);
const pipette = document.createElement('button');
pipette.className = 'tpv-pipette';
pipette.dataset.tool = 'pick';
pipette.title = 'Pick an ink colour from the sheet (I)';
pipette.setAttribute('aria-label', 'Pick an ink colour from the sheet');
pipette.innerHTML = '<svg viewBox="0 0 16 16" width="15" height="15" aria-hidden="true" fill="none" '
  + 'stroke="currentColor" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round">'
  + '<path d="M10.2 3.2l1.3-1.3a1.8 1.8 0 0 1 2.6 2.6l-1.3 1.3"/><path d="M8.6 3.6l3.8 3.8"/>'
  + '<path d="M10.5 5.5l-6.6 6.6-.6 2.6 2.6-.6 6.6-6.6"/></svg>';
ui.swatches.append(ui.picked, pipette);

for (const b of document.querySelectorAll('[data-tool]')) b.addEventListener('click', () => setTool(b.dataset.tool));
ui.undo.addEventListener('click', undo);
ui.clear.addEventListener('click', clearInk);
ui.fit.addEventListener('click', fit);
ui.textSmaller.addEventListener('click', () => stepText(-1));
ui.textLarger.addEventListener('click', () => stepText(1));
ui.save.addEventListener('click', save);
// Don't leave toolbar buttons focused, or Space would press them.
ui.bar.addEventListener('click', (e) => { if (e.target.closest('button')) e.target.closest('button').blur(); });

ui.sheet.addEventListener('change', () => {
  const name = ui.sheet.value;
  if (name === state.name) return;
  if (state.dirty && !confirm(`Discard unsaved changes on ${state.name}?`)) {
    ui.sheet.value = state.name;
    return;
  }
  state.dirty = false;
  if (state.views.find((v) => v.name === name)?.kind === 'model') location.href = viewUrl(name); // the 3D page
  else openSheet(name);
});

// Using this window with the mouse (or a local pen) makes it the one the tablet draws in.
window.addEventListener('pointerdown', () => tablet.claim(), true);
ui.stage.addEventListener('wheel', () => tablet.claim(), { passive: true });

ui.stage.addEventListener('pointerdown', onPointerDown);
ui.stage.addEventListener('pointermove', onPointerMove);
ui.stage.addEventListener('pointerup', onPointerUp);
ui.stage.addEventListener('pointercancel', onPointerUp);
ui.stage.addEventListener('wheel', onWheel, { passive: false });
ui.stage.addEventListener('contextmenu', (e) => e.preventDefault());
ui.stage.addEventListener('pointerleave', () => { if (!gesture) ui.coords.textContent = ''; });

window.addEventListener('keydown', (e) => {
  if (e.target instanceof Element && e.target.closest('select, input, textarea')) return;
  const mod = e.ctrlKey || e.metaKey, key = e.key.toLowerCase();
  if (mod && key === 's') { e.preventDefault(); save(); return; }
  if (mod && key === 'z') { e.preventDefault(); undo(); return; }
  if (mod || e.altKey) return;
  if (frames.key(e)) return;
  if (e.key === ' ') {
    e.preventDefault();
    if (!spaceHeld) { spaceHeld = true; setHover(null); updateCursor(); }
  } else if (key === 'p') setTool('pen');
  else if (key === 'v' || key === 'm') setTool('move');
  else if (key === 'i') setTool('pick');
  else if (key === 'escape' && state.tool === 'pick') setTool(state.prevTool);
  else if (key === 'escape' && state.tool === 'measure') measure.clear();
  else if (key === 'r') setTool('measure');
  else if (key === 'f') fit();
  else if (['1', '2', '3', '4'].includes(e.key)) setColor(Number(e.key) - 1);
  else if (e.key === '5') usePicked();
});

window.addEventListener('keyup', (e) => {
  if (e.key === ' ') { spaceHeld = false; updateCursor(); }
});

window.addEventListener('beforeunload', (e) => { if (state.dirty) e.preventDefault(); });

// The window or the tree panel changes the view's shape, which the tablet's drawing area follows.
new ResizeObserver(() => { tablet.render(); viewChanged(); }).observe(ui.stage);
panelDivider(document.querySelector('main'));
// Anything drawn changing changes the tablet's picture; the pointer's marks and highlights don't.
new MutationObserver((changes) => {
  if (changes.some((c) => c.target !== ui.penCursor && c.target !== ui.pickBox && c.attributeName !== 'class')) {
    viewChanged();
  }
}).observe(ui.stage, { subtree: true, childList: true, attributes: true, characterData: true });

setColor(0);
setTool(state.tool);
applyText();
applyCamera();
updateStatus();
poll();
tablet.connect();

// For debugging from the console and for tests/test_browser.py.
window.tracepaper = { state, ready, A, Pen, pen, tablet, CLIENT, fontSizeOf, setTool, setColor, setLayerVisible, save, undo, fit,
  renderScreen, frames, fieldView, measure, sliders };
