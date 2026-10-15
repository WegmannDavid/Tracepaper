// Tracepaper viewer: shows a sheet, toggles layers, lets the person drag dimension and
// constraint labels and sketch on top, with the mouse or a tablet's pen.
// Save writes <sheet>.sketch.svg (SPEC.md §3).
import * as A from './annotations.js';
import * as Pen from './pen.js';

const SVG_NS = 'http://www.w3.org/2000/svg';
const INK_NS = 'http://www.inkscape.org/namespaces/inkscape';
const SKETCH_ID = 'tp-sketch';
const REF_ID = 'tpv-ref';
const INK_COLORS = [['Red', '#e11d48'], ['Blue', '#2563eb'], ['Green', '#16a34a'], ['Black', '#1f2937']];
const INK_WIDTH_PX = 2.2; // stroke width on screen, at the zoom it was drawn at
const MIN_STEP_PX = 0.8;  // pointer samples closer than this to the last one are dropped
const HIT_PX = 8;         // how close the pointer must be to grab a label
const ERASER_PX = 10;     // radius of the pen's eraser end, on screen
const NUMBER = /-?(?:\d+\.?\d*|\.\d+)(?:e[-+]?\d+)?/gi;
const MIN_ZOOM = 0.05;
const MAX_ZOOM = 400;
const POLL_MS = 1000;

const $ = (id) => document.getElementById(id);
const ui = {
  bar: $('tpv-bar'), stage: $('tpv-stage'), camera: $('tpv-camera'), paper: $('tpv-paper'),
  host: $('tpv-host'), sheet: $('tpv-sheet'), layers: $('tpv-layers'), status: $('tpv-status'),
  coords: $('tpv-coords'), toast: $('tpv-toast'), empty: $('tpv-empty'), save: $('tpv-save'),
  undo: $('tpv-undo'), clear: $('tpv-clear'), fit: $('tpv-fit'), swatches: $('tpv-swatches'),
  pen: $('tpv-pen'), penCursor: $('tpv-pen-cursor'),
};

const state = {
  sheets: [], sheetsKey: '', name: null, sheetMtime: null, sketchMtime: null,
  root: null, ref: null, ink: null, annotations: [],
  dirty: false, undo: [], moved: new Map(), layerVis: new Map(),
  tool: 'pen', color: INK_COLORS[0][1], cam: { s: 1, x: 0, y: 0 },
  gen: 0, busy: false, offline: false,
};

let gesture = null;
let hover = null;
let spaceHeld = false;
const touches = new Map();
let resolveReady;
const ready = new Promise((resolve) => { resolveReady = resolve; });

const api = (kind, name) => `/api/${kind}/${encodeURIComponent(name)}`;
const sketchName = (name) => `${name.replace(/\.svg$/i, '')}.sketch.svg`;
const clamp = (v, lo, hi) => Math.min(hi, Math.max(lo, v));

async function fetchOk(url, init = {}) {
  const r = await fetch(url, { cache: 'no-store', ...init });
  if (!r.ok) throw new Error(`${r.status} ${await r.text()}`);
  return r;
}

// ---- Sheet document ---------------------------------------------------------

const isLayer = (n) => n.namespaceURI === SVG_NS && n.localName === 'g' && n.getAttributeNS(INK_NS, 'groupmode') === 'layer';
const layersOf = (root) => [...root.children].filter(isLayer);
const labelOf = (g) => g.getAttributeNS(INK_NS, 'label') || g.id || 'Layer';
const isHidden = (n) => n.getAttribute('display') === 'none' || n.style?.display === 'none';

function setHidden(g, hidden) {
  if (hidden) { g.style.display = 'none'; return; }
  g.style.removeProperty('display');
  if (g.getAttribute('display') === 'none') g.removeAttribute('display');
  if (!g.getAttribute('style')) g.removeAttribute('style');
}

function shown(el) {
  for (let n = el; n && n !== ui.host; n = n.parentNode) if (n.nodeType === 1 && isHidden(n)) return false;
  return true;
}

function parseSvg(text) {
  const doc = new DOMParser().parseFromString(text, 'image/svg+xml');
  const root = doc.documentElement;
  if (root.namespaceURI !== SVG_NS || root.localName !== 'svg') throw new Error('not a well-formed SVG file');
  for (const s of root.querySelectorAll('script')) s.remove();
  for (const el of [root, ...root.querySelectorAll('*')]) {
    for (const a of [...el.attributes]) if (/^on/i.test(a.localName)) el.removeAttributeNode(a);
  }
  return document.importNode(root, true);
}

const inkLayerOf = (root) => layersOf(root).find((g) => g.id === SKETCH_ID || labelOf(g) === 'Sketch') ?? null;

function ensureInkLayer(root) {
  let ink = inkLayerOf(root);
  if (!ink) {
    ink = document.createElementNS(SVG_NS, 'g');
    ink.setAttribute('id', SKETCH_ID);
    ink.setAttributeNS(INK_NS, 'inkscape:groupmode', 'layer');
    ink.setAttributeNS(INK_NS, 'inkscape:label', 'Sketch');
    ink.setAttribute('transform', 'scale(1,-1)');
  }
  root.appendChild(ink); // always on top
  return ink;
}

function mount(root) {
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
  ui.paper.setAttribute('width', root.width.baseVal.value);
  ui.paper.setAttribute('height', root.height.baseVal.value);
  ui.empty.hidden = true;
  hover = null;
}

function fontSizeOf(el) {
  for (let n = el; n && n.nodeType === 1; n = n.parentNode) {
    const v = parseFloat(n.getAttribute('font-size'));
    if (v > 0) return v;
    if (n === state.root) break;
  }
  return 3.5;
}

const findAnnotation = (id) => (id ? state.annotations.find((g) => g.id === id) ?? null : null);

async function openSheet(name, { reload = false } = {}) {
  const info = state.sheets.find((s) => s.name === name);
  if (!info) return;
  state.gen++;
  state.busy = true;
  try {
    // On reload with unsaved work, keep the ink and label moves in memory rather than
    // re-reading them from an older sketch file.
    const carry = reload && state.dirty
      ? { strokes: [...state.ink.children], moved: state.moved, undo: state.undo }
      : null;
    const sketchMtime = info.sketch?.mtime ?? null;
    let base = await (await fetchOk(api('sheet', name))).text();
    let inkFrom = null;
    if (sketchMtime && !carry) {
      const sketch = await fetchOk(api('sketch', name)).then((r) => r.text(), () => null);
      if (sketch && BigInt(sketchMtime) >= BigInt(info.mtime)) base = sketch; // still what was on screen
      else inkFrom = sketch; // the sheet changed since: show it, with the unhandled ink on top
    }
    const root = parseSvg(base);
    mount(root);
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
        if (g) A.restore(g, snap);
      }
      Object.assign(state, { moved: carry.moved, undo: carry.undo, dirty: true });
    } else {
      Object.assign(state, { moved: new Map(), undo: [], dirty: false });
    }
    for (const g of layersOf(root)) {
      const visible = state.layerVis.get(labelOf(g));
      if (visible !== undefined) setHidden(g, !visible);
    }
    Object.assign(state, { name, sheetMtime: info.mtime, sketchMtime });
    document.title = `${name} · Tracepaper`;
    renderLayers();
    renderSheets(true);
    if (!reload) fit();
    updateStatus();
    resolveReady();
  } catch (err) {
    toast(`Could not open ${name}: ${err.message}`, true);
  } finally {
    state.busy = false;
    state.gen++;
  }
}

function showEmpty(message) {
  ui.host.replaceChildren();
  ui.paper.setAttribute('width', 0);
  ui.paper.setAttribute('height', 0);
  Object.assign(state, { root: null, ref: null, ink: null, annotations: [], name: null });
  ui.empty.textContent = message;
  ui.empty.hidden = false;
  renderLayers();
  updateStatus();
}

// ---- Panels and status ------------------------------------------------------

function renderLayers() {
  ui.layers.replaceChildren();
  if (!state.root) return;
  for (const g of layersOf(state.root).reverse()) {
    const li = document.createElement('li');
    const label = document.createElement('label');
    const box = document.createElement('input');
    box.type = 'checkbox';
    box.checked = !isHidden(g);
    box.addEventListener('change', () => setLayerVisible(labelOf(g), box.checked));
    const name = document.createElement('span');
    name.textContent = labelOf(g);
    if (g === state.ink) li.className = 'tpv-ink';
    label.append(box, name);
    li.append(label);
    ui.layers.append(li);
  }
}

function setLayerVisible(label, visible) {
  const g = state.root && layersOf(state.root).find((l) => labelOf(l) === label);
  if (!g) return;
  setHidden(g, !visible);
  state.layerVis.set(label, visible);
  if (!visible && hover && g.contains(hover)) setHover(null);
  renderLayers();
}

function renderSheets(force = false) {
  const key = JSON.stringify(state.sheets.map((s) => [s.name, !!s.sketch]));
  if (force || key !== state.sheetsKey) {
    state.sheetsKey = key;
    ui.sheet.replaceChildren(...state.sheets.map((s) => new Option(s.sketch ? `${s.name} ✎` : s.name, s.name)));
  }
  ui.sheet.value = state.name ?? '';
  ui.sheet.disabled = state.sheets.length === 0;
}

function updateStatus() {
  let text = '', cls = '';
  if (state.offline) { text = 'Viewer server stopped'; cls = 'dirty'; }
  else if (state.dirty) { text = 'Unsaved changes'; cls = 'dirty'; }
  else if (state.sketchMtime && state.name) { text = `Saved ${sketchName(state.name)}, ready for the AI`; cls = 'pending'; }
  ui.status.textContent = text;
  ui.status.className = `tpv-status ${cls}`;
  ui.undo.disabled = state.undo.length === 0;
  ui.clear.disabled = !state.ink?.firstElementChild;
  ui.save.disabled = !state.root;
}

function markDirty() {
  state.dirty = true;
  updateStatus();
}

let toastTimer;
function toast(message, error = false) {
  ui.toast.textContent = message;
  ui.toast.classList.toggle('error', error);
  ui.toast.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { ui.toast.hidden = true; }, error ? 6000 : 2500);
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

function toLocal(el, clientX, clientY) {
  const m = el.getScreenCTM();
  if (!m) return null;
  const p = new DOMPoint(clientX, clientY).matrixTransform(m.inverse());
  return [p.x, p.y];
}

function pxPerUnit(el) {
  const m = el.getScreenCTM();
  return m ? Math.hypot(m.a, m.b) : 1;
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
    : state.tool === 'pen' ? 'crosshair'
    : hover ? 'grab' : 'default';
}

function capture(e) {
  try { ui.stage.setPointerCapture(e.pointerId); } catch { /* not an active pointer */ }
}

const startPan = (e) => ({ type: 'pan', id: e.pointerId, last: stagePoint(e) });

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

// Strokes and erasing take points in client coordinates, from the mouse or a tablet.

function beginStroke(clientX, clientY) {
  if (isHidden(state.ink)) setLayerVisible(labelOf(state.ink), true);
  const path = document.createElementNS(SVG_NS, 'path');
  path.setAttribute('stroke', state.color);
  path.setAttribute('stroke-width', A.fmt(INK_WIDTH_PX / pxPerUnit(state.ref), 3));
  path.setAttribute('fill', 'none');
  path.setAttribute('stroke-linecap', 'round');
  path.setAttribute('stroke-linejoin', 'round');
  state.ink.appendChild(path);
  const s = { path, pts: [], last: null };
  addPoint(s, clientX, clientY, true);
  path.setAttribute('d', strokeD(s.pts));
  markDirty(); // now, so a sheet reloaded mid-stroke keeps it
  return s;
}

function addPoint(s, clientX, clientY, force = false) {
  if (!force && Math.hypot(clientX - s.last.x, clientY - s.last.y) < MIN_STEP_PX) return false;
  const p = toLocal(state.ref, clientX, clientY);
  if (!p) return false;
  s.pts.push(`${A.fmt(p[0], 2)} ${A.fmt(p[1], 2)}`);
  s.last = { x: clientX, y: clientY };
  return true;
}

// A single tap becomes a zero-length segment, which round caps draw as a dot.
const strokeD = (pts) => `M${pts[0]} L${(pts.length > 1 ? pts.slice(1) : pts).join(' L')}`;

function extendStroke(s, points) {
  let changed = false;
  for (const [x, y] of points) changed = addPoint(s, x, y) || changed;
  if (changed) s.path.setAttribute('d', strokeD(s.pts));
}

function commitStroke(s) {
  state.undo.push({ type: 'stroke', el: s.path });
  updateStatus();
}

const coalesced = (e) => {
  const events = e.getCoalescedEvents?.() ?? [];
  return (events.length ? events : [e]).map((ev) => [ev.clientX, ev.clientY]);
};

const strokePoints = new WeakMap(); // path -> {d, pts}, parsed once per change

function pointsOf(path) {
  const d = path.getAttribute('d') ?? '';
  let cached = strokePoints.get(path);
  if (cached?.d !== d) {
    const n = d.match(NUMBER) ?? [], pts = [];
    for (let i = 0; i + 1 < n.length; i += 2) pts.push([+n[i], +n[i + 1]]);
    cached = { d, pts };
    strokePoints.set(path, cached);
  }
  return cached.pts;
}

// Remove every stroke the eraser touches at this point; removed collects them for undo.
function eraseAt(clientX, clientY, removed) {
  const p = toLocal(state.ref, clientX, clientY);
  if (!p) return;
  const r = ERASER_PX / pxPerUnit(state.ref);
  for (const path of [...state.ink.children]) {
    const pts = pointsOf(path), reach = r + (parseFloat(path.getAttribute('stroke-width')) || 0) / 2;
    const hit = pts.length === 1
      ? Math.hypot(p[0] - pts[0][0], p[1] - pts[0][1]) <= reach
      : pts.some((q, i) => i > 0 && A.segmentDistance(p, pts[i - 1], q) <= reach);
    if (hit) {
      removed.push({ el: path, next: path.nextSibling });
      path.remove();
    }
  }
}

function finishErase(removed) {
  if (!removed.length) return;
  state.undo.push({ type: 'erase', items: removed });
  markDirty();
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
  if (g?.type === 'stroke') commitStroke(g);
  else if (g?.type === 'erase') finishErase(g.removed);
  else if (g?.type === 'drag') {
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
    eraseAt(e.clientX, e.clientY, gesture.removed);
  } else if (e.button === 1 || (e.button === 0 && spaceHeld)) gesture = startPan(e);
  else if (e.button !== 0) return;
  else if (state.tool === 'pen') gesture = { type: 'stroke', id: e.pointerId, ...beginStroke(e.clientX, e.clientY) };
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
  if (p) ui.coords.textContent = `x ${p[0].toFixed(2)}   y ${p[1].toFixed(2)} mm`;
  if (e.pointerType === 'touch' && touches.has(e.pointerId)) touches.set(e.pointerId, stagePoint(e));
  if (!gesture) {
    if (state.tool === 'move' && e.pointerType !== 'touch' && !spaceHeld) {
      setHover(hitTest(e.clientX, e.clientY)?.el ?? null);
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
  } else if (gesture.type === 'stroke') extendStroke(gesture, coalesced(e));
  else if (gesture.type === 'erase') for (const [x, y] of coalesced(e)) eraseAt(x, y, gesture.removed);
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
  state.tool = tool;
  for (const b of document.querySelectorAll('[data-tool]')) b.setAttribute('aria-pressed', String(b.dataset.tool === tool));
  if (tool !== 'move') setHover(null);
  updateCursor();
}

function setColor(i) {
  const c = INK_COLORS[i]?.[1];
  if (!c) return;
  state.color = c;
  for (const b of ui.swatches.children) b.setAttribute('aria-pressed', String(b.dataset.color === c));
  setTool('pen');
}

function undo() {
  const u = state.undo.pop();
  if (!u) return;
  if (u.type === 'stroke') u.el.remove();
  else if (u.type === 'clear') state.ink.append(...u.els);
  else if (u.type === 'erase') {
    for (const { el, next } of [...u.items].reverse()) {
      state.ink.insertBefore(el, next?.parentNode === state.ink ? next : null);
    }
  }
  else if (u.type === 'annot') {
    const g = findAnnotation(u.id) ?? u.el;
    A.restore(g, u.before);
    if (u.id) state.moved.set(u.id, u.before);
  }
  markDirty();
}

function clearInk() {
  const els = [...state.ink.children];
  if (!els.length) return;
  for (const el of els) el.remove();
  state.undo.push({ type: 'clear', els });
  markDirty();
}

// Bounds of the ink in model coordinates, including stroke width.
function inkBounds() {
  let x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity, w = 0;
  for (const p of state.ink.querySelectorAll('path')) {
    const n = (p.getAttribute('d') ?? '').match(NUMBER) ?? [];
    for (let i = 0; i + 1 < n.length; i += 2) {
      const x = +n[i], y = +n[i + 1];
      x0 = Math.min(x0, x); x1 = Math.max(x1, x); y0 = Math.min(y0, y); y1 = Math.max(y1, y);
    }
    w = Math.max(w, parseFloat(p.getAttribute('stroke-width')) || 0);
  }
  return x0 <= x1 ? { x0: x0 - w, y0: y0 - w, x1: x1 + w, y1: y1 + w } : null;
}

function lengthMm(s) {
  const m = /^\s*([\d.]+)\s*(mm|cm|in|pt|px)?\s*$/.exec(s ?? '');
  if (!m) return NaN;
  const per = { mm: 1, cm: 10, in: 25.4, pt: 25.4 / 72, px: 25.4 / 96 };
  return +m[1] * per[m[2] ?? 'px'];
}

// Enlarge the saved sheet so ink drawn past its edge isn't cut off.
function growViewBox(svg, b) {
  const vb = (svg.getAttribute('viewBox') ?? '').trim().split(/[\s,]+/).map(Number);
  if (!b || vb.length !== 4 || !vb.every(Number.isFinite)) return;
  const [vx, vy, vw, vh] = vb, m = 2;
  const x0 = Math.min(vx, Math.floor(b.x0 - m)), y0 = Math.min(vy, Math.floor(-b.y1 - m));
  const x1 = Math.max(vx + vw, Math.ceil(b.x1 + m)), y1 = Math.max(vy + vh, Math.ceil(-b.y0 + m));
  if (x0 === vx && y0 === vy && x1 === vx + vw && y1 === vy + vh) return;
  const mmPerUnit = lengthMm(svg.getAttribute('width')) / vw || 1;
  svg.setAttribute('viewBox', `${x0} ${y0} ${x1 - x0} ${y1 - y0}`);
  svg.setAttribute('width', `${A.fmt((x1 - x0) * mmPerUnit)}mm`);
  svg.setAttribute('height', `${A.fmt((y1 - y0) * mmPerUnit)}mm`);
}

async function save() {
  if (!state.root || state.busy) return;
  const name = state.name;
  const copy = state.root.cloneNode(true);
  copy.querySelector(`#${REF_ID}`)?.remove();
  for (const n of copy.querySelectorAll('.tpv-hover')) {
    n.classList.remove('tpv-hover');
    if (!n.getAttribute('class')) n.removeAttribute('class');
  }
  copy.setAttributeNS(A.TP_NS, 'tp:sketch-of', name);
  copy.setAttributeNS(A.TP_NS, 'tp:saved', new Date().toISOString());
  growViewBox(copy, inkBounds());
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
  state.sheets = listing.sheets;
  renderSheets();
  if (!state.name) {
    const first = listing.sheets.find((s) => s.name === listing.initial) ?? listing.sheets[0];
    if (first) await openSheet(first.name);
    else if (ui.empty.hidden || ui.empty.textContent === 'Loading…') showEmpty(`No sheets in ${listing.dir}`);
    return;
  }
  const cur = listing.sheets.find((s) => s.name === state.name);
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
  try { listing = await (await fetchOk('/api/sheets')).json(); } catch { /* server stopped */ }
  if (state.offline !== !listing) { state.offline = !listing; updateStatus(); }
  if (listing && gen === state.gen && !state.busy) {
    try { await sync(listing); } catch (err) { console.error(err); }
  }
  setTimeout(poll, POLL_MS);
}

// ---- Tablet pen (tracepaper view --remarkable) ---------------------------------
// Frames give the pen's position in mm on the tablet; the tablet covers the
// canvas as pen.js fits it. The pen draws with the tip and erases with the other
// end, whatever the mouse is doing.

const pen = { status: null, size: null, down: false, ignored: false, stroke: null, removed: null };

function connectPen() {
  const source = new EventSource('/api/pen');
  source.addEventListener('status', (e) => {
    pen.status = JSON.parse(e.data);
    pen.size = { width: pen.status.width, height: pen.status.height };
    if (pen.status.state !== 'connected') {
      endPenTouch();
      showPenCursor(null);
    }
    renderPen();
  });
  source.addEventListener('pen', (e) => onPenFrame(JSON.parse(e.data)));
  source.addEventListener('error', () => {
    if (source.readyState === EventSource.CLOSED) pen.status = null; // no pen on this server
    else if (pen.status) pen.status = { ...pen.status, state: 'error', message: 'viewer server stopped' };
    endPenTouch();
    showPenCursor(null);
    renderPen();
  });
}

function onPenFrame(f) {
  const r = ui.stage.getBoundingClientRect();
  if (!state.root || !pen.size || !r.width || !r.height) return;
  const [x, y] = Pen.tabletToStage([f.x, f.y], pen.size, r);
  const inside = x >= 0 && y >= 0 && x <= r.width && y <= r.height;
  const clientX = r.left + x, clientY = r.top + y;
  showPenCursor(f.tool && inside ? { x, y, eraser: f.tool === 'eraser' } : null);
  if (f.touch && !pen.down) {
    pen.down = true;
    pen.ignored = !inside; // touched down on the unused part of the tablet
    if (pen.ignored) return;
    if (f.tool === 'eraser') {
      pen.removed = [];
      eraseAt(clientX, clientY, pen.removed);
    } else {
      pen.stroke = beginStroke(clientX, clientY);
    }
  } else if (f.touch && !pen.ignored) {
    if (pen.stroke) extendStroke(pen.stroke, [[clientX, clientY]]);
    else if (pen.removed) eraseAt(clientX, clientY, pen.removed);
  } else if (!f.touch && pen.down) {
    endPenTouch();
  }
}

function endPenTouch() {
  if (pen.stroke) commitStroke(pen.stroke);
  if (pen.removed) finishErase(pen.removed);
  Object.assign(pen, { down: false, ignored: false, stroke: null, removed: null });
}

function showPenCursor(c) {
  if (!c) { ui.penCursor.setAttribute('visibility', 'hidden'); return; }
  ui.penCursor.removeAttribute('visibility');
  ui.penCursor.setAttribute('cx', c.x);
  ui.penCursor.setAttribute('cy', c.y);
  ui.penCursor.setAttribute('r', c.eraser ? ERASER_PX : 4);
  ui.penCursor.classList.toggle('eraser', c.eraser);
}

function renderPen() {
  const s = pen.status;
  ui.pen.hidden = !s;
  if (!s) return;
  const r = ui.stage.getBoundingClientRect();
  let text = 'reMarkable: connecting…', title = s.message;
  if (s.state === 'connected' && r.width && r.height) {
    const a = Pen.activeArea(pen.size, r);
    text = `reMarkable · ${Math.round(a.width)} × ${Math.round(a.height)} mm`;
    title = `${s.message}. The middle ${Math.round(a.width)} × ${Math.round(a.height)} mm of the tablet `
      + 'covers the view, so drawings keep their proportions; the rest of the tablet does nothing.';
  } else if (s.state === 'error' || s.state === 'off') {
    text = `reMarkable: ${s.message || s.state}`;
  }
  ui.pen.textContent = text;
  ui.pen.title = title;
  ui.pen.className = `tpv-pen ${s.state}`;
}

// ---- Wiring -----------------------------------------------------------------

INK_COLORS.forEach(([name, c], i) => {
  const b = document.createElement('button');
  b.className = 'tpv-swatch';
  b.dataset.color = c;
  b.title = `${name} (${i + 1})`;
  b.setAttribute('aria-label', `${name} ink`);
  b.style.setProperty('--c', c);
  b.append(document.createElement('span'));
  b.addEventListener('click', () => setColor(i));
  ui.swatches.append(b);
});

for (const b of document.querySelectorAll('[data-tool]')) b.addEventListener('click', () => setTool(b.dataset.tool));
ui.undo.addEventListener('click', undo);
ui.clear.addEventListener('click', clearInk);
ui.fit.addEventListener('click', fit);
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
  openSheet(name);
});

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
  if (e.key === ' ') {
    e.preventDefault();
    if (!spaceHeld) { spaceHeld = true; setHover(null); updateCursor(); }
  } else if (key === 'p') setTool('pen');
  else if (key === 'v' || key === 'm') setTool('move');
  else if (key === 'f') fit();
  else if (['1', '2', '3', '4'].includes(e.key)) setColor(Number(e.key) - 1);
});

window.addEventListener('keyup', (e) => {
  if (e.key === ' ') { spaceHeld = false; updateCursor(); }
});

window.addEventListener('beforeunload', (e) => { if (state.dirty) e.preventDefault(); });

window.addEventListener('resize', renderPen); // the tablet's drawing area follows the canvas's shape

setColor(0);
applyCamera();
updateStatus();
poll();
connectPen();

// For debugging from the console and for tests/test_browser.py.
window.tracepaper = { state, ready, A, Pen, pen, fontSizeOf, setTool, setColor, setLayerVisible, save, undo, fit };
