// The person's ink: strokes drawn with the mouse or a tablet's pen into the Sketch layer
// of an SVG drawn in mm with y up (SPEC.md §3), a sheet's or the one over a 3D view, and
// erased with the pen's other end. Shared by the sheet page (app.js) and the 3D page
// (model.js).
import { TP_NS, fmt, segmentDistance } from './annotations.js';
import * as Pen from './pen.js';
import { NUMBER, SVG_NS } from './sheetdoc.js';

export const INK_COLORS = [['Red', '#e11d48'], ['Blue', '#2563eb'], ['Green', '#16a34a'], ['Black', '#1f2937']];
export const ERASER_PX = 10; // radius of the pen's eraser end, on screen
const INK_WIDTH_PX = 2.2; // stroke width on screen, at any zoom (SPEC-v0.md §2.1)
const MIN_STEP_PX = 0.8; // pointer samples closer than this to the last one are dropped

// A toolbar button for ink colour i of INK_COLORS; key: whether key i + 1 picks it.
export function swatch(i, key = true) {
  const [name, color] = INK_COLORS[i];
  const b = document.createElement('button');
  b.className = 'tpv-swatch';
  b.dataset.color = color;
  b.title = key ? `${name} (${i + 1})` : name;
  b.setAttribute('aria-label', `${name} ink`);
  b.style.setProperty('--c', color);
  b.append(document.createElement('span'));
  return b;
}

// A point of the window in el's coordinates.
export function toLocal(el, clientX, clientY) {
  const m = el.getScreenCTM();
  if (!m) return null;
  const p = new DOMPoint(clientX, clientY).matrixTransform(m.inverse());
  return [p.x, p.y];
}

export function pxPerUnit(el) {
  const m = el.getScreenCTM();
  return m ? Math.hypot(m.a, m.b) : 1;
}

// The points of a pointer event, with those the browser merged into it.
export const coalesced = (e) => {
  const events = e.getCoalescedEvents?.() ?? [];
  return (events.length ? events : [e]).map((ev) => [ev.clientX, ev.clientY]);
};

// A single tap becomes a zero-length segment, which round caps draw as a dot.
const strokeD = (pts) => `M${pts[0]} L${(pts.length > 1 ? pts.slice(1) : pts).join(' L')}`;

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

// The ink of a page. layer() is the Sketch layer, ref() an element in its coordinates that
// is never hidden, undo() the page's undo list, which the ink adds its steps to, and
// changed() is called whenever the ink changes.
export class Ink {
  constructor({ layer, ref, undo, changed }) {
    Object.assign(this, { layer, ref, undo, changed });
  }

  // A stroke starting at a point of the window, in color; from: the id of what the colour
  // was picked from, if it was.
  begin(clientX, clientY, color, from = null) {
    const path = document.createElementNS(SVG_NS, 'path');
    path.setAttribute('stroke', color);
    if (from) path.setAttributeNS(TP_NS, 'tp:color-from', from);
    path.setAttribute('stroke-width', INK_WIDTH_PX);
    path.setAttribute('vector-effect', 'non-scaling-stroke');
    path.setAttribute('fill', 'none');
    path.setAttribute('stroke-linecap', 'round');
    path.setAttribute('stroke-linejoin', 'round');
    this.layer().appendChild(path);
    const s = { path, pts: [], last: null };
    this.add(s, clientX, clientY, true);
    path.setAttribute('d', strokeD(s.pts));
    this.changed(); // now, so a view reloaded mid-stroke keeps it
    return s;
  }

  add(s, clientX, clientY, force = false) {
    if (!force && Math.hypot(clientX - s.last.x, clientY - s.last.y) < MIN_STEP_PX) return false;
    const p = toLocal(this.ref(), clientX, clientY);
    if (!p) return false;
    s.pts.push(`${fmt(p[0], 2)} ${fmt(p[1], 2)}`);
    s.last = { x: clientX, y: clientY };
    return true;
  }

  extend(s, points) {
    let changed = false;
    for (const [x, y] of points) changed = this.add(s, x, y) || changed;
    if (changed) s.path.setAttribute('d', strokeD(s.pts));
  }

  commit(s) {
    this.undo().push({ type: 'stroke', el: s.path });
    this.changed();
  }

  // Remove every stroke the eraser touches at this point; removed collects them for undo.
  eraseAt(clientX, clientY, removed) {
    const ref = this.ref(), p = toLocal(ref, clientX, clientY);
    if (!p) return;
    const k = pxPerUnit(ref);
    for (const path of [...this.layer().children]) {
      const pts = pointsOf(path), reach = (ERASER_PX + (parseFloat(path.getAttribute('stroke-width')) || 0) / 2) / k;
      const hit = pts.length === 1
        ? Math.hypot(p[0] - pts[0][0], p[1] - pts[0][1]) <= reach
        : pts.some((q, i) => i > 0 && segmentDistance(p, pts[i - 1], q) <= reach);
      if (hit) {
        removed.push({ el: path, next: path.nextSibling });
        path.remove();
      }
    }
  }

  finishErase(removed) {
    if (!removed.length) return;
    this.undo().push({ type: 'erase', items: removed });
    this.changed();
  }

  clear() {
    const els = [...this.layer().children];
    if (!els.length) return;
    for (const el of els) el.remove();
    this.undo().push({ type: 'clear', els });
    this.changed();
  }

  get empty() { return !this.layer()?.firstElementChild; }

  // Take back an undo step if it is the ink's; whether it was.
  undoStep(u) {
    const layer = this.layer();
    if (u.type === 'stroke') u.el.remove();
    else if (u.type === 'clear') layer.append(...u.els);
    else if (u.type === 'erase') {
      for (const { el, next } of [...u.items].reverse()) layer.insertBefore(el, next?.parentNode === layer ? next : null);
    } else return false;
    this.changed();
    return true;
  }
}

// A tablet's pen (pen.js) drawing in a page: its frames, which give the pen's position in
// mm on the tablet, become strokes and erasing on stage, the element the tablet covers as
// pen.js fits it. The pen draws with the tip and erases with the other end, whatever the
// mouse is doing. cursor is a circle in an SVG over stage that shows where the pen is.
// ready() says whether the pen draws now; begin(clientX, clientY) starts a stroke in the
// page's colour; ended() is called after the pen lifts.
export class PenInput {
  constructor({ ink, tablet, stage, cursor, ready, begin, ended }) {
    Object.assign(this, { ink, tablet, stage, cursor, ready, begin, ended });
    // done: when the pen touched down for the last stroke that ended (pen.py's Decoder).
    Object.assign(this, { down: false, ignored: false, stroke: null, removed: null, done: 0 });
  }

  frame(f) {
    this.done = f.done ?? this.done;
    const r = this.stage.getBoundingClientRect();
    if (!this.ready() || !r.width || !r.height) return;
    const [x, y] = Pen.tabletToStage([f.x, f.y], this.tablet.size, r);
    const inside = x >= 0 && y >= 0 && x <= r.width && y <= r.height;
    const clientX = r.left + x, clientY = r.top + y;
    this.showCursor(f.tool && inside ? { x, y, eraser: f.tool === 'eraser' } : null);
    if (f.touch && !this.down) {
      this.down = true;
      this.ignored = !inside; // touched down on the unused part of the tablet
      if (this.ignored) return;
      if (f.tool === 'eraser') {
        this.removed = [];
        this.ink.eraseAt(clientX, clientY, this.removed);
      } else {
        this.stroke = this.begin(clientX, clientY);
      }
    } else if (f.touch && !this.ignored) {
      if (this.stroke) this.ink.extend(this.stroke, [[clientX, clientY]]);
      else if (this.removed) this.ink.eraseAt(clientX, clientY, this.removed);
    } else if (!f.touch && this.down) {
      this.end();
    }
  }

  // The pen lifted, or stopped drawing here.
  end() {
    const wasDown = this.down;
    if (this.stroke) this.ink.commit(this.stroke);
    if (this.removed) this.ink.finishErase(this.removed);
    Object.assign(this, { down: false, ignored: false, stroke: null, removed: null });
    if (wasDown) this.ended();
  }

  showCursor(c) {
    const el = this.cursor;
    if (!c) { el.setAttribute('visibility', 'hidden'); return; }
    el.removeAttribute('visibility');
    el.setAttribute('cx', c.x);
    el.setAttribute('cy', c.y);
    el.setAttribute('r', c.eraser ? ERASER_PX : 4);
    el.classList.toggle('eraser', c.eraser);
  }
}
