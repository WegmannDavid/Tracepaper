// Fields on a sheet (SPEC.md §2.11), in the sheet page's side panel: each field's colour map
// with its range, which the person can change, only where it is outside its limits, isolines,
// the mesh's edges, the mesh moved by the field's displacement times a scale, a vector field's
// arrows, longer or shorter and with their values, and the value under the pointer.
//
// The server draws each field (drawing.py) as triangles, bands along lines and dots at bare
// points, carrying the values at their corners (tp:v), and, where the field moves the mesh,
// each corner's displacement (tp:u) and place before it (tp:p). The values are linear across
// each triangle and line, so everything here is worked out from them exactly: colours by each
// one's mean, the part over a level and the isolines by where the level crosses its sides.
// The triangles of an element of more than one (a quadrilateral) carry its number (tp:c), so
// that the mesh leaves out the sides inside it. A vector field's colours are its lengths', and
// its arrows carry their point (tp:at), vector (tp:vec), length (tp:v) and displacement (tp:u),
// drawn at the layer's tp:scale, mm for each of its unit, from or to their point (tp:pivot).

import { K, TP_NS, fmt, segmentDistance, textTransform } from './annotations.js';
import { NUMBER, SVG_NS, fontSizeOf } from './sheetdoc.js';

const VIRIDIS = ['#440154', '#472d7b', '#3b528b', '#2c728e', '#21918c', '#28ae80', '#5ec962', '#addc30', '#fde725'];
const OVER = '#b91c1c';
const INK = '#1a1a1a';
const MESH = '#374151';
const LINE_PX = 5; // a band along a line, as drawing.py draws it
const DOT_PX = 7;
const ISOLINES = 8;
const NEAR_PX = 6; // how near the pointer must be to an arrow for its value
const nums = (s) => (s ?? '').match(NUMBER)?.map(Number) ?? [];
const pairs = (a) => a.reduce((out, v, i) => (i % 2 ? out : [...out, [v, a[i + 1]]]), []);
const hex = (c) => [1, 3, 5].map((i) => parseInt(c.slice(i, i + 2), 16));
const sig = (x) => String(Number(x.toPrecision(3))); // a value to three figures
const P = ([x, y]) => `${fmt(x, 4)} ${fmt(y, 4)}`;
const setShown = (el, on) => (on ? el.removeAttribute('display') : el.setAttribute('display', 'none'));

export function fieldColor(t) {
  const x = Math.min(1, Math.max(0, t)) * (VIRIDIS.length - 1), i = Math.min(Math.floor(x), VIRIDIS.length - 2);
  const a = hex(VIRIDIS[i]), b = hex(VIRIDIS[i + 1]);
  return `#${a.map((v, k) => Math.round(v + (x - i) * (b[k] - v)).toString(16).padStart(2, '0')).join('')}`;
}

// The part of a triangle (corners p, values v) where the value is over level (under it, if
// below): a polygon.
function overPart(p, v, level, below = false) {
  const out = [];
  const beyond = (x) => (below ? x < level : x > level);
  for (let i = 0; i < 3; i++) {
    const j = (i + 1) % 3, a = beyond(v[i]), b = beyond(v[j]);
    if (a) out.push(p[i]);
    if (a !== b) {
      const t = (level - v[i]) / (v[j] - v[i]);
      out.push([p[i][0] + t * (p[j][0] - p[i][0]), p[i][1] + t * (p[j][1] - p[i][1])]);
    }
  }
  return out;
}

// The part of a line (ends p, values v) over level (under it, if below): its ends, or null.
function overSegment(p, v, level, below = false) {
  const beyond = (x) => (below ? x < level : x > level);
  const a = beyond(v[0]), b = beyond(v[1]);
  if (!a && !b) return null;
  if (a && b) return p;
  const t = (level - v[0]) / (v[1] - v[0]);
  const m = [p[0][0] + t * (p[1][0] - p[0][0]), p[0][1] + t * (p[1][1] - p[0][1])];
  return a ? [p[0], m] : [m, p[1]];
}

// Where a level crosses a triangle: a segment, or null.
function crossing(p, v, level) {
  const pts = [];
  for (let i = 0; i < 3; i++) {
    const j = (i + 1) % 3;
    if ((v[i] > level) !== (v[j] > level)) {
      const t = (level - v[i]) / (v[j] - v[i]);
      pts.push([p[i][0] + t * (p[j][0] - p[i][0]), p[i][1] + t * (p[j][1] - p[i][1])]);
    }
  }
  return pts.length === 2 ? pts : null;
}

const pathOf = (pts) => `M${pts.map(P).join(' L')} Z`;

// A field's arrow, as drawing.py's arrow(): [tail, tip, its path] at point at for vector vec,
// scale mm for each of its unit, from the point (pivot 'tail') or to it ('tip'); null if it
// has no length. The shaft goes up to the head, a dimension's arrowhead, shorter on a short arrow.
export function arrowPath(at, vec, scale, pivot, fs) {
  const a = [vec[0] * scale, vec[1] * scale], length = Math.hypot(a[0], a[1]);
  if (!(length > 1e-9)) return null;
  const tail = pivot === 'tail' ? at : [at[0] - a[0], at[1] - a[1]];
  const tip = pivot === 'tail' ? [at[0] + a[0], at[1] + a[1]] : at;
  const head = Math.min(K.arrowLen * fs, 0.5 * length), u = [a[0] / length, a[1] / length];
  const base = [tip[0] - u[0] * head, tip[1] - u[1] * head];
  const w = head * K.arrowHalf / K.arrowLen, side = [-u[1] * w, u[0] * w];
  return [tail, tip, `M${P(tail)} L${P(base)} M${P(tip)} L${P([base[0] + side[0], base[1] + side[1]])} `
    + `L${P([base[0] - side[0], base[1] - side[1]])} Z`];
}

// The mesh's edges, from its triangles: [triangle, corner, corner] for each side, once, but
// not the sides inside an element of more than one triangle (a quadrilateral, a cut through a
// tetrahedron), which share their tp:c.
function meshSides(tris) {
  const key = (p) => `${p[0].toFixed(5)} ${p[1].toFixed(5)}`;
  const sideOf = (t, i) => { const a = key(t.base[i]), b = key(t.base[(i + 1) % 3]); return a < b ? `${a}|${b}` : `${b}|${a}`; };
  const inCell = new Map();
  for (const t of tris) {
    if (t.cell === null) continue;
    for (let i = 0; i < 3; i++) { const k = `${t.cell}#${sideOf(t, i)}`; inCell.set(k, (inCell.get(k) ?? 0) + 1); }
  }
  const seen = new Set(), out = [];
  for (const t of tris) {
    for (let i = 0; i < 3; i++) {
      const side = sideOf(t, i);
      if ((t.cell !== null && inCell.get(`${t.cell}#${side}`) > 1) || seen.has(side)) continue;
      seen.add(side);
      out.push([t, i, (i + 1) % 3]);
    }
  }
  return out;
}

export class FieldView {
  // container: the panel's element for them; changed(): the sheet was redrawn.
  constructor({ container, head, changed = () => {} }) {
    Object.assign(this, { container, head, changed });
    this.fields = []; // the fields of the sheet shown: their range, limits, shapes and arrows
    this.settings = new Map(); // name -> the person's settings, kept over frames and reloads
    this.fs = 3.5;
    container.hidden = head.hidden = true;
  }

  // The fields of a sheet just shown: its layers with tp:field.
  attach(root) {
    this.fs = fontSizeOf(root, root);
    this.fields = [...root.querySelectorAll('g')].filter((g) => g.hasAttributeNS(TP_NS, 'field')).map((layer) => {
      const part = (name) => [...layer.children].find((c) => c.getAttributeNS(TP_NS, 'part') === name) ?? null;
      const colours = part('colours') ?? layer; // a sheet saved before arrows: its triangles in the layer
      const shapes = [...colours.children].filter((c) => c.localName === 'path' && c.hasAttributeNS(TP_NS, 'v')).map((el) => {
        const v = nums(el.getAttributeNS(TP_NS, 'v')), u = pairs(nums(el.getAttributeNS(TP_NS, 'u')));
        return { el, v, base: pairs(nums(el.getAttributeNS(TP_NS, 'p') ?? el.getAttribute('d'))).slice(0, v.length),
                 u: u.length === v.length ? u : null, cell: el.getAttributeNS(TP_NS, 'c') || null };
      });
      const arrowGroup = part('arrows');
      const arrows = [...(arrowGroup?.children ?? [])].filter((c) => c.localName === 'path').map((el) => ({
        el, at: pairs(nums(el.getAttributeNS(TP_NS, 'at')))[0], vec: pairs(nums(el.getAttributeNS(TP_NS, 'vec')))[0],
        v: Number(el.getAttributeNS(TP_NS, 'v')), u: pairs(nums(el.getAttributeNS(TP_NS, 'u')))[0] ?? null,
      }));
      const num = (n) => (layer.hasAttributeNS(TP_NS, n) ? Number(layer.getAttributeNS(TP_NS, n)) : null);
      const [from, to] = nums(layer.getAttributeNS(TP_NS, 'range'));
      const tris = shapes.filter((t) => t.v.length === 3);
      return { name: layer.getAttributeNS(TP_NS, 'field'), unit: layer.getAttributeNS(TP_NS, 'unit') ?? '',
               from: from ?? 0, to: to ?? 1, min: num('min'), max: num('max'), layer,
               colours: colours === layer ? null : colours, tris, lines: shapes.filter((t) => t.v.length === 2),
               dots: shapes.filter((t) => t.v.length === 1), sides: meshSides(tris), arrowGroup, arrows,
               scale: num('scale'), pivot: layer.getAttributeNS(TP_NS, 'pivot') || 'tail',
               moves: shapes.some((t) => t.u) || arrows.some((a) => a.u) };
    });
    for (const f of this.fields) {
      if (!this.settings.has(f.name)) {
        this.settings.set(f.name, { lo: null, hi: null, overOnly: false, iso: false, mesh: false, scale: 1,
                                    colours: !f.arrowGroup, arrows: true, values: false, length: 1 });
      }
      this.apply(f);
    }
    this.render();
  }

  get on() { return this.fields.length > 0; }

  // Redraw a field as its settings say.
  apply(f) {
    const s = this.settings.get(f.name), lo = s.lo ?? f.from, hi = s.hi ?? f.to, span = hi - lo || 1, fs = this.fs;
    f.layer.querySelectorAll('.tpv-field-extra').forEach((e) => e.remove());
    if (f.colours) setShown(f.colours, s.colours);
    if (f.arrowGroup) setShown(f.arrowGroup, s.arrows);
    const limits = [[f.max, false], [f.min, true]].filter(([l]) => l !== null);
    const over = [], overLines = [], overDots = [], iso = [];
    const levels = Array.from({ length: ISOLINES }, (_, k) => lo + (k + 0.5) * span / ISOLINES);
    for (const t of [...f.tris, ...f.lines, ...f.dots]) {
      const n = t.v.length;
      t.pts = t.u ? t.base.map(([x, y], k) => [x + s.scale * t.u[k][0], y + s.scale * t.u[k][1]]) : t.base;
      if (t.u) t.el.setAttribute('d', n === 3 ? pathOf(t.pts) : `M${P(t.pts[0])} L${P(t.pts[n - 1])}`);
      const color = fieldColor((t.v.reduce((a, b) => a + b, 0) / n - lo) / span);
      if (n === 3) t.el.setAttribute('fill', color);
      t.el.setAttribute('stroke', color);
      t.el.setAttribute('visibility', s.overOnly ? 'hidden' : 'inherit');
      if (s.overOnly) {
        for (const [limit, below] of limits) {
          if (n === 3) {
            const part = overPart(t.pts, t.v, limit, below);
            if (part.length >= 3) over.push(pathOf(part));
          } else if (n === 2) {
            const seg = overSegment(t.pts, t.v, limit, below);
            if (seg) overLines.push(`M${P(seg[0])} L${P(seg[1])}`);
          } else if (below ? t.v[0] < limit : t.v[0] > limit) overDots.push(`M${P(t.pts[0])} L${P(t.pts[0])}`);
        }
      }
      if (s.iso && n === 3) for (const level of levels) { const seg = crossing(t.pts, t.v, level); if (seg) iso.push(`M${P(seg[0])} L${P(seg[1])}`); }
    }
    const extra = (parent, name, attrs) => {
      const el = document.createElementNS(SVG_NS, name);
      el.setAttribute('class', 'tpv-field-extra');
      for (const [k, v] of Object.entries(attrs)) el.setAttribute(k, v);
      parent.append(el);
      return el;
    };
    const thin = { 'vector-effect': 'non-scaling-stroke' };
    const colours = f.colours ?? f.layer;
    if (over.length) extra(colours, 'path', { d: over.join(' '), fill: OVER, stroke: OVER, 'stroke-width': '0.6', ...thin });
    if (overLines.length) extra(colours, 'path', { d: overLines.join(' '), fill: 'none', stroke: OVER, 'stroke-width': LINE_PX, 'stroke-linecap': 'round', ...thin });
    if (overDots.length) extra(colours, 'path', { d: overDots.join(' '), fill: 'none', stroke: OVER, 'stroke-width': DOT_PX, 'stroke-linecap': 'round', ...thin });
    if (iso.length) extra(colours, 'path', { d: iso.join(' '), fill: 'none', stroke: '#111827', 'stroke-width': '0.8', ...thin });
    if (s.mesh && f.sides.length) {
      extra(f.layer, 'path', { d: f.sides.map(([t, i, j]) => `M${P(t.pts[i])} L${P(t.pts[j])}`).join(' '),
                               fill: 'none', stroke: MESH, 'stroke-width': '0.6', ...thin });
    }
    const k = (f.scale ?? 0) * s.length;
    for (const a of f.arrows) {
      const at = a.u ? [a.at[0] + s.scale * a.u[0], a.at[1] + s.scale * a.u[1]] : a.at;
      const drawn = k > 0 ? arrowPath(at, a.vec, k, f.pivot, fs) : null;
      const outside = limits.some(([limit, below]) => (below ? a.v < limit : a.v > limit));
      const color = outside ? OVER : INK;
      a.ends = drawn && drawn.slice(0, 2);
      a.el.setAttribute('d', drawn ? drawn[2] : '');
      a.el.setAttribute('fill', color);
      a.el.setAttribute('stroke', color);
      a.el.setAttribute('visibility', s.overOnly && !outside ? 'hidden' : 'inherit');
      if (!s.values || !drawn || (s.overOnly && !outside)) continue;
      // Its value beyond its free end, away from its point.
      const [tail, tip] = a.ends, length = Math.hypot(tip[0] - tail[0], tip[1] - tail[1]);
      const away = f.pivot === 'tail' ? [(tip[0] - tail[0]) / length, (tip[1] - tail[1]) / length]
        : [(tail[0] - tip[0]) / length, (tail[1] - tip[1]) / length];
      const end = f.pivot === 'tail' ? tip : tail;
      const label = extra(f.arrowGroup, 'text', {
        transform: textTransform([end[0] + away[0] * 0.3 * fs, end[1] + away[1] * 0.3 * fs], 0),
        'text-anchor': away[0] > 0.38 ? 'start' : away[0] < -0.38 ? 'end' : 'middle',
        y: fmt(away[1] > 0.38 ? 0 : away[1] < -0.38 ? 0.8 * fs : 0.35 * fs, 4), // above, below or beside it
        'font-size': fmt(fs, 4), fill: color, stroke: 'none',
      });
      label.textContent = sig(a.v);
    }
  }

  render() {
    this.container.hidden = this.head.hidden = !this.on;
    const parts = this.fields.map((f) => {
      const s = this.settings.get(f.name), lo = s.lo ?? f.from, hi = s.hi ?? f.to;
      const vector = !!f.arrowGroup, area = f.tris.length > 0;
      const box = document.createElement('div');
      box.className = 'tpv-field';
      box.dataset.field = f.name;
      const id = `tpv-g-${f.name.replace(/\W/g, '')}`;
      const stops = VIRIDIS.map((c, i) => `<stop offset="${i / (VIRIDIS.length - 1)}" stop-color="${c}"/>`).join('');
      const tick = [f.min, f.max].filter((l) => l !== null && hi > lo && l >= lo && l <= hi).map((l) => (l - lo) / (hi - lo) * 100)
        .map((x) => `<line x1="${x}" x2="${x}" y1="-2" y2="12" stroke="${OVER}" stroke-width="2" vector-effect="non-scaling-stroke"/>`).join('');
      const limits = [f.min !== null && `≥ ${fmt(f.min, 3)}`, f.max !== null && `≤ ${fmt(f.max, 3)}`].filter(Boolean).join(', ');
      const option = (opt, text) => `<label class="tpv-slice-option"><input type="checkbox" data-opt="${opt}"> ${text}</label>`;
      const slider = (key, text, min, max, step) => `<div class="tpv-field-scale" data-key="${key}"><span>${text}</span>`
        + `<input type="range" min="${min}" max="${max}" step="${step}" aria-label="${text}"><span class="tpv-frame-value"></span></div>`;
      box.innerHTML = `<div class="tpv-frame-head"><span class="tpv-frame-name">${f.name}</span>`
        + `<span class="tpv-frame-value">${f.unit}${limits ? ` · ${limits}` : ''}</span></div>`
        + (vector ? option('arrows', 'Arrows') + slider('length', 'Length ×', 0.25, 4, 0.25)
          + `<div class="tpv-field-note">${f.scale ? `1 mm of arrow: ${sig(1 / (f.scale * s.length))} ${f.unit}` : ''}</div>`
          + option('values', 'Values') + option('colours', 'Colours') : '')
        + (!vector || s.colours
          ? `<svg class="tpv-legend" viewBox="0 -2 100 14" preserveAspectRatio="none"><defs><linearGradient id="${id}">${stops}</linearGradient></defs>`
            + `<rect width="100" height="10" fill="url(#${id})"/>${tick}</svg>`
            + '<div class="tpv-field-range"><input type="number" step="any" data-end="lo" aria-label="Lowest value"><input type="number" step="any" data-end="hi" aria-label="Highest value"></div>'
            + (area ? option('iso', 'Isolines') : '')
          : '')
        + (limits ? option('overOnly', 'Only outside its limits') : '')
        + (area ? option('mesh', 'Mesh') : '')
        + (f.moves ? slider('scale', 'Deformation ×', 0, 10, 0.5) : '');
      for (const input of box.querySelectorAll('[data-end]')) {
        input.value = fmt(input.dataset.end === 'lo' ? lo : hi, 4);
        input.title = 'The colour map\'s ends; empty: the field\'s own';
        input.addEventListener('change', () => {
          const v = input.value.trim() === '' ? null : Number(input.value);
          s[input.dataset.end] = Number.isFinite(v) ? v : null;
          this.update(f);
        });
      }
      for (const check of box.querySelectorAll('[data-opt]')) {
        check.checked = s[check.dataset.opt];
        check.addEventListener('change', () => { s[check.dataset.opt] = check.checked; this.update(f); });
      }
      for (const row of box.querySelectorAll('.tpv-field-scale')) {
        const input = row.querySelector('input'), shown = row.querySelector('.tpv-frame-value'), key = row.dataset.key;
        input.value = s[key];
        shown.textContent = fmt(s[key], 2);
        input.addEventListener('input', () => {
          s[key] = Number(input.value);
          shown.textContent = fmt(s[key], 2);
          const note = box.querySelector('.tpv-field-note');
          if (note && f.scale) note.textContent = `1 mm of arrow: ${sig(1 / (f.scale * s.length))} ${f.unit}`;
          this.apply(f);
          this.changed();
        });
      }
      return box;
    });
    this.container.replaceChildren(...parts);
  }

  update(f) {
    this.apply(f);
    this.render();
    this.changed();
  }

  // The value of the field under a point of the window, as text, or null: an arrow's near the
  // pointer, else interpolated across the triangle or along the line there.
  valueAt(clientX, clientY) {
    const unit = (f) => (f.unit ? ` ${f.unit}` : '');
    let best = null;
    for (const f of this.fields) {
      const m = f.arrowGroup?.getScreenCTM();
      if (!m || f.arrowGroup.closest('[display="none"]')) continue;
      for (const a of f.arrows) {
        if (!a.ends || getComputedStyle(a.el).visibility === 'hidden') continue;
        const [p, q] = a.ends.map(([x, y]) => new DOMPoint(x, y).matrixTransform(m));
        const d = segmentDistance([clientX, clientY], [p.x, p.y], [q.x, q.y]);
        if (d <= NEAR_PX && (!best || d < best.d)) best = { d, f, a };
      }
    }
    if (best) {
      const { f, a } = best;
      return `${f.name} ${sig(a.v)}${unit(f)} (${sig(a.vec[0])}, ${sig(a.vec[1])})`;
    }
    for (const el of document.elementsFromPoint(clientX, clientY)) {
      const f = this.fields.find((g) => g.layer.contains(el));
      const t = f && [...f.tris, ...f.lines, ...f.dots].find((tr) => tr.el === el);
      if (!t) continue;
      const m = el.getScreenCTM();
      if (!m) continue;
      const q = new DOMPoint(clientX, clientY).matrixTransform(m.inverse());
      let v = t.v[0];
      if (t.v.length === 3) {
        const [[x1, y1], [x2, y2], [x3, y3]] = t.pts;
        const det = (y2 - y3) * (x1 - x3) + (x3 - x2) * (y1 - y3);
        if (!det) continue;
        const a = ((y2 - y3) * (q.x - x3) + (x3 - x2) * (q.y - y3)) / det;
        const b = ((y3 - y1) * (q.x - x3) + (x1 - x3) * (q.y - y3)) / det;
        v = a * t.v[0] + b * t.v[1] + (1 - a - b) * t.v[2];
      } else if (t.v.length === 2) {
        const [[x1, y1], [x2, y2]] = t.pts, l2 = (x2 - x1) ** 2 + (y2 - y1) ** 2;
        const s = l2 ? Math.min(1, Math.max(0, ((q.x - x1) * (x2 - x1) + (q.y - y1) * (y2 - y1)) / l2)) : 0;
        v = t.v[0] + s * (t.v[1] - t.v[0]);
      }
      return `${f.name} ${fmt(v, 3)}${unit(f)}`;
    }
    return null;
  }
}
