// Fields on a sheet (SPEC.md §2.11), in the sheet page's side panel: each field's colour map
// with its range, which the person can change, only where it is outside its limits, isolines,
// the mesh moved by the field's displacement times a scale, and the value under the pointer.
//
// The server draws each field as triangles (drawing.py) carrying the values at their corners
// (tp:v), and, where the field moves the mesh, each corner's displacement (tp:u) and place
// before it (tp:p). The values are linear across each triangle, so everything here is
// worked out from them exactly: colours by each triangle's mean, the part over a level and
// the isolines by where the level crosses its sides.

import { TP_NS, fmt } from './annotations.js';
import { NUMBER, SVG_NS } from './sheetdoc.js';

const VIRIDIS = ['#440154', '#472d7b', '#3b528b', '#2c728e', '#21918c', '#28ae80', '#5ec962', '#addc30', '#fde725'];
const OVER = '#b91c1c';
const ISOLINES = 8;
const nums = (s) => (s ?? '').match(NUMBER)?.map(Number) ?? [];
const pairs = (a) => a.reduce((out, v, i) => (i % 2 ? out : [...out, [v, a[i + 1]]]), []);
const hex = (c) => [1, 3, 5].map((i) => parseInt(c.slice(i, i + 2), 16));

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

const pathOf = (pts) => `M${pts.map(([x, y]) => `${fmt(x, 4)} ${fmt(y, 4)}`).join(' L')} Z`;

export class FieldView {
  // container: the panel's element for them; changed(): the sheet was redrawn.
  constructor({ container, head, changed = () => {} }) {
    Object.assign(this, { container, head, changed });
    this.fields = []; // [{name, unit, from, to, min, max, layer, tris}] of the sheet shown: its range and its limits
    this.settings = new Map(); // name -> {lo, hi, overOnly, iso, scale}: the person's, kept over frames and reloads
    container.hidden = head.hidden = true;
  }

  // The fields of a sheet just shown: its layers with tp:field.
  attach(root) {
    this.fields = [...root.querySelectorAll('g')].filter((g) => g.hasAttributeNS(TP_NS, 'field')).map((layer) => {
      const tris = [...layer.children].filter((c) => c.localName === 'path' && c.hasAttributeNS(TP_NS, 'v')).map((el) => {
        const u = nums(el.getAttributeNS(TP_NS, 'u'));
        return { el, v: nums(el.getAttributeNS(TP_NS, 'v')),
                 base: pairs(nums(el.getAttributeNS(TP_NS, 'p') ?? el.getAttribute('d'))).slice(0, 3),
                 u: u.length === 6 ? pairs(u) : null };
      });
      const num = (n) => (layer.hasAttributeNS(TP_NS, n) ? Number(layer.getAttributeNS(TP_NS, n)) : null);
      const [from, to] = nums(layer.getAttributeNS(TP_NS, 'range'));
      return { name: layer.getAttributeNS(TP_NS, 'field'), unit: layer.getAttributeNS(TP_NS, 'unit') ?? '',
               from: from ?? 0, to: to ?? 1, min: num('min'), max: num('max'), layer, tris,
               moves: tris.some((t) => t.u) };
    });
    for (const f of this.fields) {
      if (!this.settings.has(f.name)) this.settings.set(f.name, { lo: null, hi: null, overOnly: false, iso: false, scale: 1 });
      this.apply(f);
    }
    this.render();
  }

  get on() { return this.fields.length > 0; }

  // Redraw a field as its settings say.
  apply(f) {
    const s = this.settings.get(f.name), lo = s.lo ?? f.from, hi = s.hi ?? f.to, span = hi - lo || 1;
    f.layer.querySelectorAll('.tpv-field-extra').forEach((e) => e.remove());
    const over = [], iso = [];
    const levels = Array.from({ length: ISOLINES }, (_, k) => lo + (k + 0.5) * span / ISOLINES);
    for (const t of f.tris) {
      t.pts = t.u ? t.base.map(([x, y], k) => [x + s.scale * t.u[k][0], y + s.scale * t.u[k][1]]) : t.base;
      if (t.u) t.el.setAttribute('d', pathOf(t.pts));
      const color = fieldColor((t.v.reduce((a, b) => a + b, 0) / 3 - lo) / span);
      t.el.setAttribute('fill', color);
      t.el.setAttribute('stroke', color);
      t.el.setAttribute('visibility', s.overOnly ? 'hidden' : 'inherit');
      if (s.overOnly) {
        for (const [limit, below] of [[f.max, false], [f.min, true]]) {
          if (limit === null) continue;
          const part = overPart(t.pts, t.v, limit, below);
          if (part.length >= 3) over.push(pathOf(part));
        }
      }
      if (s.iso) for (const level of levels) { const seg = crossing(t.pts, t.v, level); if (seg) iso.push(`M${seg.map(([x, y]) => `${fmt(x, 4)} ${fmt(y, 4)}`).join(' L')}`); }
    }
    const extra = (d, attrs) => {
      const el = document.createElementNS(SVG_NS, 'path');
      el.setAttribute('class', 'tpv-field-extra');
      el.setAttribute('d', d);
      for (const [k, v] of Object.entries(attrs)) el.setAttribute(k, v);
      f.layer.append(el);
    };
    if (over.length) extra(over.join(' '), { fill: OVER, stroke: OVER, 'stroke-width': '0.6', 'vector-effect': 'non-scaling-stroke' });
    if (iso.length) extra(iso.join(' '), { fill: 'none', stroke: '#111827', 'stroke-width': '0.8', 'vector-effect': 'non-scaling-stroke' });
  }

  render() {
    this.container.hidden = this.head.hidden = !this.on;
    const parts = this.fields.map((f) => {
      const s = this.settings.get(f.name), lo = s.lo ?? f.from, hi = s.hi ?? f.to;
      const box = document.createElement('div');
      box.className = 'tpv-field';
      box.dataset.field = f.name;
      const stops = VIRIDIS.map((c, i) => `<stop offset="${i / (VIRIDIS.length - 1)}" stop-color="${c}"/>`).join('');
      const tick = [f.min, f.max].filter((l) => l !== null && hi > lo && l >= lo && l <= hi).map((l) => (l - lo) / (hi - lo) * 100)
        .map((x) => `<line x1="${x}" x2="${x}" y1="-2" y2="12" stroke="${OVER}" stroke-width="2" vector-effect="non-scaling-stroke"/>`).join('');
      const limits = [f.min !== null && `≥ ${fmt(f.min, 3)}`, f.max !== null && `≤ ${fmt(f.max, 3)}`].filter(Boolean).join(', ');
      box.innerHTML = `<div class="tpv-frame-head"><span class="tpv-frame-name">${f.name}</span>`
        + `<span class="tpv-frame-value">${f.unit}${limits ? ` · ${limits}` : ''}</span></div>`
        + `<svg class="tpv-legend" viewBox="0 -2 100 14" preserveAspectRatio="none"><defs><linearGradient id="tpv-g-${box.dataset.field.replace(/\W/g, '')}">${stops}</linearGradient></defs>`
        + `<rect width="100" height="10" fill="url(#tpv-g-${box.dataset.field.replace(/\W/g, '')})"/>${tick}</svg>`
        + '<div class="tpv-field-range"><input type="number" step="any" data-end="lo" aria-label="Lowest value"><input type="number" step="any" data-end="hi" aria-label="Highest value"></div>'
        + (limits ? '<label class="tpv-slice-option"><input type="checkbox" data-opt="overOnly"> Only outside its limits</label>' : '')
        + '<label class="tpv-slice-option"><input type="checkbox" data-opt="iso"> Isolines</label>'
        + (f.moves ? '<div class="tpv-field-scale"><span>Deformation ×</span><input type="range" min="0" max="10" step="0.5" aria-label="Deformation scale"><span class="tpv-frame-value"></span></div>' : '');
      const [loIn, hiIn] = box.querySelectorAll('[data-end]');
      loIn.value = fmt(lo, 4);
      hiIn.value = fmt(hi, 4);
      loIn.title = hiIn.title = 'The colour map\'s ends; empty: the field\'s own';
      for (const input of [loIn, hiIn]) {
        input.addEventListener('change', () => {
          const v = input.value.trim() === '' ? null : Number(input.value);
          s[input.dataset.end] = Number.isFinite(v) ? v : null;
          this.update(f);
        });
      }
      for (const box2 of box.querySelectorAll('[data-opt]')) {
        box2.checked = s[box2.dataset.opt];
        box2.addEventListener('change', () => { s[box2.dataset.opt] = box2.checked; this.update(f); });
      }
      const scale = box.querySelector('.tpv-field-scale input');
      if (scale) {
        scale.value = s.scale;
        scale.nextElementSibling.textContent = fmt(s.scale, 2);
        scale.addEventListener('input', () => {
          s.scale = Number(scale.value);
          scale.nextElementSibling.textContent = fmt(s.scale, 2);
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

  // The value of the field under a point of the window, as text, or null: interpolated
  // across the triangle there.
  valueAt(clientX, clientY) {
    for (const el of document.elementsFromPoint(clientX, clientY)) {
      const f = this.fields.find((g) => g.layer.contains(el));
      const t = f?.tris.find((tr) => tr.el === el);
      if (!t) continue;
      const m = el.getScreenCTM();
      if (!m) continue;
      const q = new DOMPoint(clientX, clientY).matrixTransform(m.inverse());
      const [[x1, y1], [x2, y2], [x3, y3]] = t.pts;
      const det = (y2 - y3) * (x1 - x3) + (x3 - x2) * (y1 - y3);
      if (!det) continue;
      const a = ((y2 - y3) * (q.x - x3) + (x3 - x2) * (q.y - y3)) / det;
      const b = ((y3 - y1) * (q.x - x3) + (x1 - x3) * (q.y - y3)) / det;
      const v = a * t.v[0] + b * t.v[1] + (1 - a - b) * t.v[2];
      return `${f.name} ${fmt(v, 3)}${f.unit ? ` ${f.unit}` : ''}`;
    }
    return null;
  }
}
