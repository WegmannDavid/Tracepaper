// Annotations (SPEC.md §2.6–2.7): read a dimension or constraint symbol from its
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
  return { lines: [], arrows: [], dots: [], texts: [], box: null, frame: null };
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
  let ang = Math.atan2(v[1], v[0]) / DEG;
  if (ang > 90 + EPS) ang -= 180;
  else if (ang <= -90 + EPS) ang += 180;
  const up = [-Math.sin(ang * DEG), Math.cos(ang * DEG)];
  const base = add(add(a, mul(v, sT)), mul(up, K.textGap * fs));
  g.texts.push({ at: base, angle: ang, label });
  g.box = { center: add(base, mul(up, K.vCenter * fs)), angle: ang, hw: tw / 2, hh: 0.5 * fs };
  g.frame = { v, n, L };
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
  return radial(ann, fs);
}

function segmentDistance(p, a, b) {
  const ab = sub(b, a), l2 = dot(ab, ab);
  const t = l2 > 0 ? Math.min(1, Math.max(0, dot(sub(p, a), ab) / l2)) : 0;
  return len(sub(p, add(a, mul(ab, t))));
}

// Distance in mm from p to the annotation's label box or lines (0 inside the label).
export function distance(ann, fs, p) {
  const g = geometry(ann, fs);
  const { center, angle, hw, hh } = g.box;
  const c = Math.cos(-angle * DEG), s = Math.sin(-angle * DEG), d = sub(p, center);
  const lx = d[0] * c - d[1] * s, ly = d[0] * s + d[1] * c;
  let best = Math.hypot(Math.max(Math.abs(lx) - hw, 0), Math.max(Math.abs(ly) - hh, 0));
  for (const [a, b] of g.lines) best = Math.min(best, segmentDistance(p, a, b));
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
  if (g.lines.length) {
    kids.push(make('path', { d: g.lines.map(([a, b]) => `M${P(a)} L${P(b)}`).join(' '), fill: 'none' }));
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
  if (ann.kind === 'linear') { set('offset', fmt(ann.offset)); set('text-at', fmt(ann.textAt)); }
  else if (ann.kind === 'symbol') set('offset', `${fmt(ann.offset[0])} ${fmt(ann.offset[1])}`);
  else { set('angle', fmt(ann.angle)); set('offset', fmt(ann.offset)); }
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
