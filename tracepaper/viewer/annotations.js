// Annotations (SPEC-v0.md §2.6–2.7): read a dimension or constraint symbol from its
// tp: attributes, draw it, and move it when the person drags it.
// tracepaper/sheet.py draws the same shapes; keep the two in sync.

export const TP_NS = 'urn:tracepaper:0';
const SVG_NS = 'http://www.w3.org/2000/svg';
const DEG = Math.PI / 180;
const EPS = 1e-6;

// Sizes as multiples of the font size.
export const K = {
  arrowLen: 0.75, arrowHalf: 0.15, extGap: 0.3, extOver: 0.5, textGap: 0.25, charW: 0.62,
  tail: 0.6, insideMin: 2.0, leaderMin: 1.2, leaderStop: 0.7, dotR: 0.1, vCenter: 0.35,
};

const add = (p, q) => [p[0] + q[0], p[1] + q[1]];
const sub = (p, q) => [p[0] - q[0], p[1] - q[1]];
const mul = (p, k) => [p[0] * k, p[1] * k];
const dot = (p, q) => p[0] * q[0] + p[1] * q[1];
const len = (p) => Math.hypot(p[0], p[1]);
const unit = (p) => { const l = len(p); return l > 1e-12 ? mul(p, 1 / l) : null; };
const round = (x, digits) => { const k = 10 ** digits; return Math.round(x * k) / k; };

export function fmt(x, digits = 4) {
  const v = round(x, digits);
  return String(v === 0 ? 0 : v);
}

const textWidth = (label, fs) => [...label].length * K.charW * fs;

function num(s, fallback) {
  if (s === null || s.trim() === '') return fallback;
  const v = Number(s);
  return Number.isFinite(v) ? v : fallback;
}

function point(s) {
  if (!s) return null;
  const v = s.trim().split(/[\s,]+/).map(Number);
  return v.length === 2 && v.every(Number.isFinite) ? v : null;
}

export const isAnnotation = (g) => g.hasAttributeNS(TP_NS, 'dim') || g.hasAttributeNS(TP_NS, 'anchor');

// The annotation described by a group's attributes, or null if it isn't one we can move.
export function read(g) {
  const get = (name) => g.getAttributeNS(TP_NS, name);
  const label = get('label') ?? g.querySelector('text')?.textContent ?? '';
  const dim = get('dim');
  if (dim === 'linear') {
    const p1 = point(get('p1')), p2 = point(get('p2'));
    if (!p1 || !p2) return null;
    const dir = ['x', 'y'].includes(get('dir')) ? get('dir') : 'aligned';
    return { kind: 'linear', p1, p2, dir, offset: num(get('offset'), 0), textAt: num(get('text-at'), 0.5), label };
  }
  if (dim === 'angular') {
    const center = point(get('center')), p1 = point(get('p1')), p2 = point(get('p2'));
    if (!center || !p1 || !p2 || !(len(sub(p1, center)) > 0) || !(len(sub(p2, center)) > 0)) return null;
    const offset = num(get('offset'), len(sub(p1, center)));
    if (!(offset > 0)) return null;
    return { kind: 'angular', center, p1, p2, offset, textAt: num(get('text-at'), 0.5), label };
  }
  if (dim === 'radius' || dim === 'diameter') {
    const center = point(get('center')), r = num(get('r'), NaN);
    if (!center || !(r > 0)) return null;
    return { kind: dim, center, r, angle: num(get('angle'), 45), offset: num(get('offset'), 0), label };
  }
  const anchor = point(get('anchor'));
  if (!anchor) return null;
  return { kind: 'symbol', anchor, offset: point(get('offset')) ?? [0, 0], label };
}

function empty() {
  return { lines: [], arcs: [], arrows: [], dots: [], texts: [], box: null, frame: null };
}

// The angle of text along v in degrees, turned half round if need be to read from the left.
function readable(v) {
  const ang = Math.atan2(v[1], v[0]) / DEG;
  if (ang > 90 + EPS) return ang - 180;
  if (ang <= -90 + EPS) return ang + 180;
  return ang;
}

function linear(a0, fs) {
  const { p1, p2, dir, offset, textAt, label } = a0;
  const u = dir === 'x' ? [1, 0] : dir === 'y' ? [0, 1] : unit(sub(p2, p1)) ?? [1, 0];
  const n = [-u[1], u[0]];
  const level = dot(p1, n) + offset;
  const a = add(p1, mul(n, level - dot(p1, n)));
  const b = add(p2, mul(n, level - dot(p2, n)));
  const g = empty();
  for (const [f, e] of [[p1, a], [p2, b]]) {
    const d = sub(e, f), l = len(d);
    if (l > K.extGap * fs) {
      const k = mul(d, 1 / l);
      g.lines.push([add(f, mul(k, K.extGap * fs)), add(e, mul(k, K.extOver * fs))]);
    }
  }
  const L = len(sub(b, a));
  const v = L > 1e-9 ? mul(sub(b, a), 1 / L) : u;
  let s0 = 0, s1 = L;
  if (L >= K.insideMin * fs) {
    g.arrows.push({ tip: a, dir: mul(v, -1) }, { tip: b, dir: v });
  } else {
    const t = (K.arrowLen + K.tail) * fs;
    s0 = -t; s1 = L + t;
    g.arrows.push({ tip: a, dir: v }, { tip: b, dir: mul(v, -1) });
  }
  const tw = textWidth(label, fs), sT = textAt * L, hw = tw / 2 + K.textGap * fs;
  if (sT < 0 || sT > L) { s0 = Math.min(s0, sT - hw); s1 = Math.max(s1, sT + hw); }
  g.lines.push([add(a, mul(v, s0)), add(a, mul(v, s1))]);
  const ang = readable(v);
  const up = [-Math.sin(ang * DEG), Math.cos(ang * DEG)];
  const base = add(add(a, mul(v, sT)), mul(up, K.textGap * fs));
  g.texts.push({ at: base, angle: ang, label });
  g.box = { center: add(base, mul(up, K.vCenter * fs)), angle: ang, hw: tw / 2, hh: 0.5 * fs };
  g.frame = { v, n, L };
  return g;
}

// Where the legs of an angle at c through p1 and p2 point: the first one's direction, and how
// far counter-clockwise the second one is from it, both in radians.
function legs(c, p1, p2) {
  const t1 = Math.atan2(p1[1] - c[1], p1[0] - c[0]);
  let sweep = Math.atan2(p2[1] - c[1], p2[0] - c[0]) - t1;
  if (sweep < 0) sweep += 2 * Math.PI;
  return [t1, sweep];
}

// Like linear(), but along an arc of radius offset round the vertex, s being the length along it.
function angular(a0, fs) {
  const { center: c, p1, p2, offset: r, textAt, label } = a0;
  const [t1, sweep] = legs(c, p1, p2);
  const on = (t) => add(c, [r * Math.cos(t), r * Math.sin(t)]);
  const g = empty();
  // The legs run from the vertex to p1 and p2; where the arc is further out, a leg goes on
  // to it as an extension line.
  for (const [p, t] of [[p1, t1], [p2, t1 + sweep]]) {
    const d = len(sub(p, c));
    if (r > d + K.extGap * fs) {
      const k = [Math.cos(t), Math.sin(t)];
      g.lines.push([add(c, mul(k, d + K.extGap * fs)), add(c, mul(k, r + K.extOver * fs))]);
    }
  }
  const L = r * sweep;
  // An arrowhead from its tip at angle t, its base on the arc at angle t + side * head.
  const head = 2 * Math.asin(Math.min(1, K.arrowLen * fs / (2 * r)));
  const arrow = (t, side) => {
    const tip = on(t);
    return { tip, dir: unit(sub(tip, on(t + side * head))) ?? [1, 0] };
  };
  let s0 = 0, s1 = L;
  if (L >= K.insideMin * fs) {
    g.arrows.push(arrow(t1, 1), arrow(t1 + sweep, -1));
  } else {
    const t = (K.arrowLen + K.tail) * fs;
    s0 = -t; s1 = L + t;
    g.arrows.push(arrow(t1, -1), arrow(t1 + sweep, 1));
  }
  const tw = textWidth(label, fs), sT = textAt * L, hw = tw / 2 + K.textGap * fs;
  if (sT < 0 || sT > L) { s0 = Math.min(s0, sT - hw); s1 = Math.max(s1, sT + hw); }
  g.arcs.push({ center: c, r, from: (t1 + s0 / r) / DEG, to: (t1 + s1 / r) / DEG });
  const tT = t1 + sT / r;
  const ang = readable([-Math.sin(tT), Math.cos(tT)]);
  const up = [-Math.sin(ang * DEG), Math.cos(ang * DEG)];
  const base = add(on(tT), mul(up, K.textGap * fs));
  g.texts.push({ at: base, angle: ang, label });
  g.box = { center: add(base, mul(up, K.vCenter * fs)), angle: ang, hw: tw / 2, hh: 0.5 * fs };
  return g;
}

function radial(a0, fs) {
  const { kind, center: c, r, angle, offset, label } = a0;
  const diameter = kind === 'diameter';
  const d = [Math.cos(angle * DEG), Math.sin(angle * DEG)];
  const q = add(c, mul(d, r)), end = add(c, mul(d, r + offset));
  const g = empty();
  let lo, hi;
  if (diameter && 2 * r >= K.insideMin * fs) {
    lo = Math.min(-r, r + offset); hi = Math.max(r, r + offset);
    g.arrows.push({ tip: q, dir: d }, { tip: sub(c, mul(d, r)), dir: mul(d, -1) });
  } else if (diameter) {
    // Small hole: arrows outside, pointing in.
    const t = (K.arrowLen + K.tail) * fs;
    lo = Math.min(-r - t, r + offset); hi = Math.max(r + t, r + offset);
    g.arrows.push({ tip: q, dir: mul(d, -1) }, { tip: sub(c, mul(d, r)), dir: d });
  } else {
    lo = Math.min(r, r + offset); hi = Math.max(r, r + offset);
    g.arrows.push({ tip: q, dir: offset >= 0 ? mul(d, -1) : d });
  }
  g.lines.push([add(c, mul(d, lo)), add(c, mul(d, hi))]);
  const side = (diameter ? r + offset : offset) >= 0 ? d : mul(d, -1);
  const sx = side[0] >= -1e-9 ? 1 : -1;
  const tw = textWidth(label, fs), gap = K.textGap * fs;
  g.lines.push([end, add(end, [sx * (tw + 2 * gap), 0])]);
  const base = add(end, [sx * (tw / 2 + gap), gap]);
  g.texts.push({ at: base, angle: 0, label });
  g.box = { center: add(base, [0, K.vCenter * fs]), angle: 0, hw: tw / 2, hh: 0.5 * fs };
  return g;
}

function symbol(a0, fs) {
  const { anchor, offset, label } = a0;
  const at = add(anchor, offset), l = len(offset);
  const g = empty();
  if (l > K.leaderMin * fs) {
    const k = mul(offset, 1 / l);
    g.lines.push([anchor, sub(at, mul(k, K.leaderStop * fs))]);
    g.dots.push(anchor);
  }
  g.texts.push({ at: [at[0], at[1] - K.vCenter * fs], angle: 0, label });
  g.box = { center: at, angle: 0, hw: textWidth(label, fs) / 2, hh: 0.5 * fs };
  return g;
}

export function geometry(ann, fs) {
  if (ann.kind === 'linear') return linear(ann, fs);
  if (ann.kind === 'symbol') return symbol(ann, fs);
  if (ann.kind === 'angular') return angular(ann, fs);
  return radial(ann, fs);
}

export function segmentDistance(p, a, b) {
  const ab = sub(b, a), l2 = dot(ab, ab);
  const t = l2 > 0 ? Math.min(1, Math.max(0, dot(sub(p, a), ab) / l2)) : 0;
  return len(sub(p, add(a, mul(ab, t))));
}

// Distance from p to an arc, {center, r, from, to} with the angles in degrees, from < to.
function arcDistance(p, { center, r, from, to }) {
  const w = sub(p, center);
  let t = Math.atan2(w[1], w[0]) / DEG;
  t -= 360 * Math.floor((t - from) / 360); // from ≤ t < from + 360
  if (t <= to) return Math.abs(len(w) - r);
  const end = (a) => add(center, [r * Math.cos(a * DEG), r * Math.sin(a * DEG)]);
  return Math.min(len(sub(p, end(from))), len(sub(p, end(to))));
}

// Distance in mm from p to the annotation's label box or lines (0 inside the label).
export function distance(ann, fs, p) {
  const g = geometry(ann, fs);
  const { center, angle, hw, hh } = g.box;
  const c = Math.cos(-angle * DEG), s = Math.sin(-angle * DEG), d = sub(p, center);
  const lx = d[0] * c - d[1] * s, ly = d[0] * s + d[1] * c;
  let best = Math.hypot(Math.max(Math.abs(lx) - hw, 0), Math.max(Math.abs(ly) - hh, 0));
  for (const [a, b] of g.lines) best = Math.min(best, segmentDistance(p, a, b));
  for (const arc of g.arcs) best = Math.min(best, arcDistance(p, arc));
  return best;
}

// The annotation after dragging its label by delta (model mm).
export function dragged(ann, delta, fs) {
  if (ann.kind === 'linear') {
    const { v, n, L } = geometry(ann, fs).frame;
    return {
      ...ann,
      offset: round(ann.offset + dot(delta, n), 2),
      textAt: L > 1e-9 ? round(ann.textAt + dot(delta, v) / L, 3) : ann.textAt,
    };
  }
  if (ann.kind === 'symbol') {
    return { ...ann, offset: [round(ann.offset[0] + delta[0], 2), round(ann.offset[1] + delta[1], 2)] };
  }
  if (ann.kind === 'angular') {
    // The arc follows the point where the label sits on it: its radius, and the label's
    // angle, counted from the middle of the angle so it can go round either way.
    const [t1, sweep] = legs(ann.center, ann.p1, ann.p2);
    const t = t1 + ann.textAt * sweep;
    const w = add([ann.offset * Math.cos(t), ann.offset * Math.sin(t)], delta);
    const rw = len(w);
    if (rw < 1e-6) return ann;
    let phi = Math.atan2(w[1], w[0]) - t1;
    phi -= 2 * Math.PI * Math.ceil((phi - sweep / 2 - Math.PI) / (2 * Math.PI)); // within half a turn of the middle
    return { ...ann, offset: round(rw, 2), textAt: sweep > 1e-9 ? round(phi / sweep, 3) : ann.textAt };
  }
  const d = [Math.cos(ann.angle * DEG), Math.sin(ann.angle * DEG)];
  const w = sub(add(add(ann.center, mul(d, ann.r + ann.offset)), delta), ann.center);
  const rw = len(w);
  if (rw < 1e-6) return ann;
  // Keep the label on the side of the centre it started on.
  if (ann.r + ann.offset >= 0) {
    return { ...ann, angle: round(Math.atan2(w[1], w[0]) / DEG, 2), offset: round(rw - ann.r, 2) };
  }
  return { ...ann, angle: round(Math.atan2(-w[1], -w[0]) / DEG, 2), offset: round(-rw - ann.r, 2) };
}

const P = (p) => `${fmt(p[0])} ${fmt(p[1])}`;

// An arc's path, counter-clockwise in model coordinates.
function arcPath({ center, r, from, to }) {
  const end = (a) => add(center, [r * Math.cos(a * DEG), r * Math.sin(a * DEG)]);
  return `M${P(end(from))} A${fmt(r)} ${fmt(r)} 0 ${to - from > 180 ? 1 : 0} 1 ${P(end(to))}`;
}

function arrowPath({ tip, dir }, fs) {
  const base = sub(tip, mul(dir, K.arrowLen * fs));
  const side = mul([-dir[1], dir[0]], K.arrowHalf * fs);
  return `M${P(tip)} L${P(add(base, side))} L${P(sub(base, side))} Z`;
}

export function textTransform(at, angle) {
  const rotate = Math.abs(angle) > 1e-9 ? ` rotate(${fmt(angle)})` : '';
  return `translate(${P(at)})${rotate} scale(1,-1)`;
}

// Replace the group's children with the drawing of g (from geometry()).
export function render(group, g, fs) {
  const doc = group.ownerDocument;
  const make = (tag, attrs) => {
    const el = doc.createElementNS(SVG_NS, tag);
    for (const [k, v] of Object.entries(attrs)) el.setAttribute(k, v);
    return el;
  };
  const kids = [];
  if (g.lines.length || g.arcs.length) {
    const d = [...g.lines.map(([a, b]) => `M${P(a)} L${P(b)}`), ...g.arcs.map(arcPath)].join(' ');
    kids.push(make('path', { d, fill: 'none',
                             'vector-effect': 'non-scaling-stroke' })); // its width on screen, as sheet.py writes it
  }
  if (g.arrows.length) {
    kids.push(make('path', { d: g.arrows.map((a) => arrowPath(a, fs)).join(' '), stroke: 'none' }));
  }
  for (const c of g.dots) kids.push(make('circle', { cx: fmt(c[0]), cy: fmt(c[1]), r: fmt(K.dotR * fs), stroke: 'none' }));
  for (const t of g.texts) {
    const el = make('text', { transform: textTransform(t.at, t.angle), 'text-anchor': 'middle', stroke: 'none' });
    el.textContent = t.label;
    kids.push(el);
  }
  group.replaceChildren(...kids);
}

// Write the annotation's placement attributes and redraw it.
export function update(group, ann, fs) {
  const set = (name, value) => group.setAttributeNS(TP_NS, `tp:${name}`, value);
  set('offset', ann.kind === 'symbol' ? `${fmt(ann.offset[0])} ${fmt(ann.offset[1])}` : fmt(ann.offset));
  if (ann.kind === 'linear' || ann.kind === 'angular') set('text-at', fmt(ann.textAt));
  if (ann.kind === 'radius' || ann.kind === 'diameter') set('angle', fmt(ann.angle));
  render(group, geometry(ann, fs), fs);
}

const PLACEMENT = ['offset', 'text-at', 'angle'];

export function snapshot(group) {
  return {
    attrs: Object.fromEntries(PLACEMENT.map((n) => [n, group.getAttributeNS(TP_NS, n)])),
    nodes: [...group.childNodes].map((n) => n.cloneNode(true)),
  };
}

export function restore(group, snap) {
  for (const [n, v] of Object.entries(snap.attrs)) {
    if (v === null) group.removeAttributeNS(TP_NS, n);
    else group.setAttributeNS(TP_NS, `tp:${n}`, v);
  }
  group.replaceChildren(...snap.nodes.map((n) => n.cloneNode(true)));
}

export const samePlacement = (a, b) => PLACEMENT.every((n) => a.attrs[n] === b.attrs[n]);
