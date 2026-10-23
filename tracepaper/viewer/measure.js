// Measuring on a drawing (SPEC.md §5): pick points, snapped to what is drawn, and read the
// distance between them, and the angle at the middle one of three.
//
// Points snap, within SNAP_PX of the pointer, to vertices (the ends of lines, arcs and curves),
// to the centres of circles and arcs, to points (a scene's dots) and the points annotations
// measure from; failing those, to the nearest point on a line or curve; failing that, they are
// where the pointer is. What is hidden, the annotations' own lines, fields and ink don't count.
// The measurement is drawn over the drawing, in a drawing of its own, so it is never saved.
import * as A from './annotations.js';
import { NUMBER, SVG_NS, isHidden, labelOf } from './sheetdoc.js';

const SNAP_PX = 10;
const MARK_PX = 5; // half the snap mark's size
const KINDS = { vertex: 'end point', centre: 'centre', point: 'point', curve: 'on a line', free: '' };
const SHAPES = new Set(['path', 'line', 'polyline', 'polygon', 'circle', 'ellipse', 'rect']);

// A path's data as segments, absolute, in its own coordinates: {type: 'L', a, b} for lines,
// {type: 'A', a, b, c, rx, ry, phi, t0, dt} for elliptical arcs (centre c, angles in radians),
// {type: 'C', pts: [a, c1, c2, b]} for Bézier curves (quadratic ones raised to cubic).
export function segments(d) {
  const out = [], toks = d.match(/[a-zA-Z]|-?(?:\d+\.?\d*|\.\d+)(?:e[-+]?\d+)?/gi) ?? [];
  let i = 0, cmd = null, cur = [0, 0], start = [0, 0], lastCtrl = null;
  const num = () => Number(toks[i++]);
  while (i < toks.length) {
    if (/[a-zA-Z]/.test(toks[i])) cmd = toks[i++];
    if (!cmd) break;
    const rel = cmd === cmd.toLowerCase(), C = cmd.toUpperCase();
    const pt = () => { const x = num(), y = num(); return rel ? [cur[0] + x, cur[1] + y] : [x, y]; };
    let ctrl = null;
    if (C === 'M') { cur = start = pt(); cmd = rel ? 'l' : 'L'; }
    else if (C === 'L') { const b = pt(); out.push({ type: 'L', a: cur, b }); cur = b; }
    else if (C === 'H') { const x = num(), b = [rel ? cur[0] + x : x, cur[1]]; out.push({ type: 'L', a: cur, b }); cur = b; }
    else if (C === 'V') { const y = num(), b = [cur[0], rel ? cur[1] + y : y]; out.push({ type: 'L', a: cur, b }); cur = b; }
    else if (C === 'C' || C === 'S') {
      const c1 = C === 'C' ? pt() : lastCtrl ? [2 * cur[0] - lastCtrl[0], 2 * cur[1] - lastCtrl[1]] : cur;
      const c2 = pt(), b = pt();
      out.push({ type: 'C', pts: [cur, c1, c2, b] });
      ctrl = c2;
      cur = b;
    } else if (C === 'Q' || C === 'T') {
      const q = C === 'Q' ? pt() : lastCtrl ? [2 * cur[0] - lastCtrl[0], 2 * cur[1] - lastCtrl[1]] : cur;
      const b = pt();
      out.push({ type: 'C', pts: [cur, [cur[0] + 2 / 3 * (q[0] - cur[0]), cur[1] + 2 / 3 * (q[1] - cur[1])],
                                  [b[0] + 2 / 3 * (q[0] - b[0]), b[1] + 2 / 3 * (q[1] - b[1])], b] });
      ctrl = q;
      cur = b;
    } else if (C === 'A') {
      const rx = Math.abs(num()), ry = Math.abs(num()), phi = num() * Math.PI / 180, large = num(), sweep = num();
      const b = pt();
      out.push(arc(cur, b, rx, ry, phi, large, sweep));
      cur = b;
    } else if (C === 'Z') { if (Math.hypot(cur[0] - start[0], cur[1] - start[1]) > 1e-9) out.push({ type: 'L', a: cur, b: start }); cur = start; }
    else i++;
    lastCtrl = ctrl;
  }
  return out;
}

// An SVG arc from a to b as its centre and angles (SVG 1.1 F.6.5).
function arc(a, b, rx, ry, phi, large, sweep) {
  if (!rx || !ry) return { type: 'L', a, b };
  const cos = Math.cos(phi), sin = Math.sin(phi);
  const dx = (a[0] - b[0]) / 2, dy = (a[1] - b[1]) / 2;
  const x1 = cos * dx + sin * dy, y1 = -sin * dx + cos * dy;
  const k = (x1 * x1) / (rx * rx) + (y1 * y1) / (ry * ry);
  if (k > 1) { rx *= Math.sqrt(k); ry *= Math.sqrt(k); }
  const num = rx * rx * ry * ry - rx * rx * y1 * y1 - ry * ry * x1 * x1;
  const f = (large !== sweep ? 1 : -1) * Math.sqrt(Math.max(0, num / (rx * rx * y1 * y1 + ry * ry * x1 * x1)));
  const cx1 = f * rx * y1 / ry, cy1 = -f * ry * x1 / rx;
  const c = [cos * cx1 - sin * cy1 + (a[0] + b[0]) / 2, sin * cx1 + cos * cy1 + (a[1] + b[1]) / 2];
  const ang = (ux, uy) => Math.atan2(uy, ux);
  const t0 = ang((x1 - cx1) / rx, (y1 - cy1) / ry);
  let dt = ang((-x1 - cx1) / rx, (-y1 - cy1) / ry) - t0;
  if (sweep && dt < 0) dt += 2 * Math.PI;
  if (!sweep && dt > 0) dt -= 2 * Math.PI;
  return { type: 'A', a, b, c, rx, ry, phi, t0, dt };
}

const at = (s, t) => { // a point of an arc or a Bézier curve, t from 0 to 1
  if (s.type === 'A') {
    const u = s.t0 + t * s.dt, x = s.rx * Math.cos(u), y = s.ry * Math.sin(u);
    return [s.c[0] + Math.cos(s.phi) * x - Math.sin(s.phi) * y, s.c[1] + Math.sin(s.phi) * x + Math.cos(s.phi) * y];
  }
  const [p0, p1, p2, p3] = s.pts, m = 1 - t;
  return [0, 1].map((k) => m * m * m * p0[k] + 3 * m * m * t * p1[k] + 3 * m * t * t * p2[k] + t * t * t * p3[k]);
};

// The nearest point of a segment to q.
function nearest(s, q) {
  if (s.type === 'L') {
    const [ax, ay] = s.a, dx = s.b[0] - ax, dy = s.b[1] - ay, len = dx * dx + dy * dy;
    const t = len ? Math.max(0, Math.min(1, ((q[0] - ax) * dx + (q[1] - ay) * dy) / len)) : 0;
    return [ax + t * dx, ay + t * dy];
  }
  if (s.type === 'A' && Math.abs(s.rx - s.ry) < 1e-9 * s.rx) { // a circle's arc: exactly
    const u = Math.atan2(q[1] - s.c[1], q[0] - s.c[0]) - s.phi;
    for (const turn of [0, -1, 1, -2, 2]) { // the pointer's angle, a turn or two either way, on the arc?
      const t = (u + turn * 2 * Math.PI - s.t0) / s.dt;
      if (t >= 0 && t <= 1) return at(s, t);
    }
    const da = Math.hypot(q[0] - s.a[0], q[1] - s.a[1]), db = Math.hypot(q[0] - s.b[0], q[1] - s.b[1]);
    return da < db ? s.a : s.b; // else the nearer end
  }
  // Ellipses and Bézier curves: the nearest of many points, then closer in around it.
  const n = 48;
  let best = 0, bd = Infinity;
  for (let k = 0; k <= n; k++) { const p = at(s, k / n), d = (p[0] - q[0]) ** 2 + (p[1] - q[1]) ** 2; if (d < bd) { bd = d; best = k / n; } }
  let lo = Math.max(0, best - 1 / n), hi = Math.min(1, best + 1 / n);
  for (let k = 0; k < 30; k++) {
    const m1 = lo + (hi - lo) / 3, m2 = hi - (hi - lo) / 3;
    const d1 = (at(s, m1)[0] - q[0]) ** 2 + (at(s, m1)[1] - q[1]) ** 2, d2 = (at(s, m2)[0] - q[0]) ** 2 + (at(s, m2)[1] - q[1]) ** 2;
    if (d1 < d2) hi = m2; else lo = m1;
  }
  return at(s, (lo + hi) / 2);
}

const fmt3 = (v) => A.fmt(v, 3);

export class Measure {
  // stage: the page's SVG, for the snap mark in window pixels; host: where the drawing is,
  // for the measurement drawn over it; ref(): an element in the drawing's coordinates;
  // root(): the drawing; fontSize(): its text height; changed(text): the readout.
  constructor({ stage, host, ref, root, fontSize, changed }) {
    Object.assign(this, { stage, host, ref, root, fontSize, changed });
    this.picks = []; // [{x, y, kind}]
    this.hover = null;
    this.items = null; // what snaps: {points: [{p, kind, el}], segs: [{s, el}]}, built when needed
    this.mark = document.createElementNS(SVG_NS, 'g');
    this.mark.setAttribute('class', 'tpv-snap');
    stage.append(this.mark);
  }

  // Forget what snaps: the drawing, or what is hidden in it, changed.
  stale() { this.items = null; }

  // Everything that snaps, in the drawing's coordinates (model mm, y up).
  index() {
    const root = this.root(), ref = this.ref();
    const points = [], segs = [];
    if (!root || !ref) return { points, segs };
    const toModel = ref.getCTM()?.inverse();
    const skip = (el) => {
      for (let n = el; n && n !== root; n = n.parentNode) {
        if (n.getAttributeNS?.(A.TP_NS, 'role') === 'fields' || labelOf(n) === 'Sketch' && n.parentNode === root) return true;
        if (n !== el && n.localName === 'g' && A.isAnnotation(n)) return true; // its own lines
      }
      return false;
    };
    for (const el of root.querySelectorAll('*')) {
      if (!SHAPES.has(el.localName) || skip(el) || isHidden(el) || !el.getCTM()) continue;
      const m = toModel.multiply(el.getCTM());
      const P = ([x, y]) => { const q = new DOMPoint(x, y).matrixTransform(m); return [q.x, q.y]; };
      const n = (a) => Number(el.getAttribute(a)) || 0;
      let local = [];
      if (el.localName === 'path') local = segments(el.getAttribute('d') ?? '');
      else if (el.localName === 'line') local = [{ type: 'L', a: [n('x1'), n('y1')], b: [n('x2'), n('y2')] }];
      else if (el.localName === 'polyline' || el.localName === 'polygon') {
        const v = (el.getAttribute('points') ?? '').match(NUMBER)?.map(Number) ?? [];
        const pts = []; for (let k = 0; k + 1 < v.length; k += 2) pts.push([v[k], v[k + 1]]);
        if (el.localName === 'polygon' && pts.length > 2) pts.push(pts[0]);
        local = pts.slice(1).map((b, k) => ({ type: 'L', a: pts[k], b }));
      } else if (el.localName === 'circle' || el.localName === 'ellipse') {
        const c = [n('cx'), n('cy')], rx = el.localName === 'circle' ? n('r') : n('rx'), ry = el.localName === 'circle' ? n('r') : n('ry');
        local = [{ type: 'A', a: [c[0] + rx, c[1]], b: [c[0] + rx, c[1]], c, rx, ry, phi: 0, t0: 0, dt: 2 * Math.PI }];
      } else if (el.localName === 'rect') {
        const x = n('x'), y = n('y'), w = n('width'), h = n('height'), c = [[x, y], [x + w, y], [x + w, y + h], [x, y + h], [x, y]];
        local = c.slice(1).map((b, k) => ({ type: 'L', a: c[k], b }));
      }
      // In the drawing's coordinates. An arc stays an arc where the element is only moved, as
      // every shape of a sheet is; under another transform, many short lines along it do.
      const moved = Math.abs(m.a - 1) + Math.abs(m.b) + Math.abs(m.c) + Math.abs(m.d - 1) < 1e-9;
      local = local.flatMap((s) => (s.type !== 'A' || moved ? [s] : Array.from({ length: 32 }, (_, k) => (
        { type: 'L', a: at(s, k / 32), b: at(s, (k + 1) / 32), centre: k ? null : s.c, ends: [k === 0, k === 31] }))));
      for (const s of local) {
        const g = s.type === 'L' ? { type: 'L', a: P(s.a), b: P(s.b) }
          : s.type === 'C' ? { type: 'C', pts: s.pts.map(P) }
          : { ...s, a: P(s.a), b: P(s.b), c: P(s.c) };
        segs.push({ s: g, el });
        if (s.centre) points.push({ p: P(s.centre), kind: 'centre', el });
        const ends = g.type === 'C' ? [g.pts[0], g.pts[3]] : [g.a, g.b];
        ends.forEach((p, k) => { if (!s.ends || s.ends[k]) points.push({ p, kind: 'vertex', el }); });
        if (g.type === 'A') points.push({ p: g.c, kind: 'centre', el });
      }
    }
    for (const g of [...root.querySelectorAll('g')].filter(A.isAnnotation)) { // the points annotations measure from: last, so that on a tie the drawing's own win
      if (isHidden(g)) continue;
      const m = toModel && g.getCTM() ? toModel.multiply(g.getCTM()) : null;
      for (const name of ['p1', 'p2', 'center', 'anchor']) {
        const v = (g.getAttributeNS(A.TP_NS, name) ?? '').match(NUMBER)?.map(Number);
        if (v?.length === 2 && m) {
          const q = new DOMPoint(v[0], v[1]).matrixTransform(m);
          points.push({ p: [q.x, q.y], kind: 'point', el: g });
        }
      }
    }
    return { points, segs };
  }

  // Where the pointer's point snaps to: {x, y, kind, el}.
  snap(clientX, clientY) {
    const ref = this.ref();
    if (!ref?.getScreenCTM()) return null;
    if (!this.items) this.items = this.index();
    const m = ref.getScreenCTM(), q = new DOMPoint(clientX, clientY).matrixTransform(m.inverse());
    const k = Math.hypot(m.a, m.b), reach = SNAP_PX / k;
    let best = null, bd = reach;
    for (const it of this.items.points) {
      const d = Math.hypot(it.p[0] - q.x, it.p[1] - q.y);
      if (d < bd) { bd = d; best = { x: it.p[0], y: it.p[1], kind: it.kind, el: it.el }; }
    }
    if (!best) {
      bd = reach;
      for (const { s, el } of this.items.segs) {
        const p = nearest(s, [q.x, q.y]), d = Math.hypot(p[0] - q.x, p[1] - q.y);
        if (d < bd) { bd = d; best = { x: p[0], y: p[1], kind: 'curve', el }; }
      }
    }
    return best ?? { x: q.x, y: q.y, kind: 'free', el: null };
  }

  move(clientX, clientY) {
    this.hover = this.snap(clientX, clientY);
    this.render();
  }

  // A pick: the first, second and third point; then the first of the next measurement.
  pick(clientX, clientY) {
    const p = this.snap(clientX, clientY);
    if (!p) return;
    if (this.picks.length >= 3) this.picks = [];
    this.picks.push(p);
    this.render();
  }

  clear() {
    this.picks = [];
    this.hover = null;
    this.render();
  }

  // What has been measured, with the pointer's snapped point as the next pick: text.
  readout() {
    const pts = [...this.picks, ...(this.hover && this.picks.length < 3 ? [this.hover] : [])];
    if (pts.length < 2) return this.hover ? `${KINDS[this.hover.kind]}${this.hover.el ? ` of ${name(this.hover.el)}` : ''}` : '';
    const dist = (a, b) => Math.hypot(b.x - a.x, b.y - a.y);
    const [a, b, c] = pts;
    let text = `AB ${fmt3(dist(a, b))} mm  Δx ${fmt3(b.x - a.x)}  Δy ${fmt3(b.y - a.y)}`;
    if (c) text += `   BC ${fmt3(dist(b, c))} mm   ∠ABC ${A.fmt(angle(a, b, c), 2)}°`;
    return text;
  }

  // The marks: where the pointer snaps (in window pixels), the picks and the lines between
  // them with their lengths and the angle (in the drawing, over it).
  render() {
    this.mark.replaceChildren();
    const ref = this.ref();
    const m = ref?.getScreenCTM(), stageM = this.stage.getScreenCTM();
    if (this.hover && m && stageM && this.hover.kind !== 'free') {
      const s = new DOMPoint(this.hover.x, this.hover.y).matrixTransform(m).matrixTransform(stageM.inverse());
      const shape = this.hover.kind === 'curve'
        ? `<path d="M${s.x - MARK_PX} ${s.y - MARK_PX} L${s.x + MARK_PX} ${s.y + MARK_PX} M${s.x - MARK_PX} ${s.y + MARK_PX} L${s.x + MARK_PX} ${s.y - MARK_PX}"/>`
        : this.hover.kind === 'centre' ? `<circle cx="${s.x}" cy="${s.y}" r="${MARK_PX}"/>`
        : `<rect x="${s.x - MARK_PX}" y="${s.y - MARK_PX}" width="${2 * MARK_PX}" height="${2 * MARK_PX}"/>`;
      this.mark.innerHTML = shape;
    }
    this.host.querySelector('.tpv-measure')?.remove();
    const root = this.root();
    const pts = [...this.picks, ...(this.hover && this.picks.length && this.picks.length < 3 ? [this.hover] : [])];
    if (root && pts.length) {
      const over = document.createElementNS(SVG_NS, 'svg');
      over.setAttribute('class', 'tpv-measure');
      for (const a of ['viewBox', 'width', 'height']) over.setAttribute(a, root.getAttribute(a));
      const fs = this.fontSize(), f = (v) => A.fmt(v, 4);
      let inner = `<g transform="scale(1,-1)" font-size="${f(fs)}">`;
      inner += `<path class="tpv-measure-line" d="M${pts.map((p) => `${f(p.x)} ${f(p.y)}`).join(' L')}"/>`;
      inner += pts.map((p) => `<path class="tpv-measure-dot" d="M${f(p.x)} ${f(p.y)} L${f(p.x)} ${f(p.y)}"/>`).join('');
      const label = (x, y, text) => `<text transform="translate(${f(x)} ${f(y)}) scale(1,-1)" text-anchor="middle">${text}</text>`;
      for (let i = 1; i < pts.length; i++) {
        const a = pts[i - 1], b = pts[i];
        inner += label((a.x + b.x) / 2, (a.y + b.y) / 2 + fs * 0.4, fmt3(Math.hypot(b.x - a.x, b.y - a.y)));
      }
      if (pts.length === 3) inner += label(pts[1].x, pts[1].y - fs * 1.4, `${A.fmt(angle(...pts), 2)}°`);
      over.innerHTML = `${inner}</g>`;
      this.host.append(over);
    }
    this.changed(this.readout());
  }
}

// The angle at b between the lines to a and to c, in degrees, 0 to 180.
function angle(a, b, c) {
  const u = [a.x - b.x, a.y - b.y], v = [c.x - b.x, c.y - b.y];
  return Math.abs(Math.atan2(u[0] * v[1] - u[1] * v[0], u[0] * v[0] + u[1] * v[1])) * 180 / Math.PI;
}

// What an element is, for the readout: its part (the innermost named group) or id.
function name(el) {
  const g = el.closest('g[*|label]');
  return g ? labelOf(g) : el.id || el.localName;
}
