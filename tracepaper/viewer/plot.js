// A plot's sheet (SPEC.md §2.12): its panels' axes, from its tp:view, and its series'
// numbers, from their groups, so the page reads values under the pointer in the data's
// terms rather than in millimetres. tracepaper/plots.py writes them.
import { TP_NS } from './annotations.js';

const NEAR_PX = 8; // how close a series' point must be to be read out

// A value as the plot's tick labels show it: up to 4 significant digits, a real minus.
export function num(v) {
  if (v === 0) return '0';
  const s = Number(v.toPrecision(4)).toString();
  return (/e/.test(s) ? s.replace(/e\+?(-?\d+)/, '·10^$1') : s).replace(/-/g, '−');
}

class Axis {
  constructor(text, label, unit) {
    const [scale, lo, hi, a, b] = text.split(/\s+/);
    Object.assign(this, { log: scale === 'log', lo: +lo, hi: +hi, a: +a, b: +b, label, unit });
  }

  at(v) {
    const t = this.log ? (Math.log10(v) - Math.log10(this.lo)) / (Math.log10(this.hi) - Math.log10(this.lo))
      : (v - this.lo) / (this.hi - this.lo);
    return this.a + t * (this.b - this.a);
  }

  value(mm) {
    const t = (mm - this.a) / (this.b - this.a);
    return this.log ? 10 ** (Math.log10(this.lo) + t * (Math.log10(this.hi) - Math.log10(this.lo)))
      : this.lo + t * (this.hi - this.lo);
  }

  drawable(v) { return v !== null && (v > 0 || !this.log); }

  text(v) { return this.unit ? `${num(v)} ${this.unit}` : num(v); }
}

const numbers = (s) => (s ? s.trim().split(/\s+/).map((t) => (t === '-' ? null : +t)) : null);

// The plot a sheet shows: {panels: [{name, x, y}], series: [{g, name, panel, xs, ys, low, high}]},
// or null for a sheet that isn't one.
export function plotOf(root) {
  const panels = [...root.getElementsByTagNameNS(TP_NS, 'panel')].map((el) => ({
    name: el.getAttribute('name') ?? '',
    x: new Axis(el.getAttribute('x-axis'), el.getAttribute('x-label'), el.getAttribute('x-unit')),
    y: new Axis(el.getAttribute('y-axis'), el.getAttribute('y-label'), el.getAttribute('y-unit')),
  }));
  if (!panels.length) return null;
  const series = [...root.querySelectorAll('g')].filter((g) => g.hasAttributeNS(TP_NS, 'series')).map((g) => ({
    g, name: g.getAttributeNS(TP_NS, 'series'), panel: Number(g.getAttributeNS(TP_NS, 'panel')),
    xs: numbers(g.getAttributeNS(TP_NS, 'xs')), ys: numbers(g.getAttributeNS(TP_NS, 'ys')),
    low: numbers(g.getAttributeNS(TP_NS, 'low')), high: numbers(g.getAttributeNS(TP_NS, 'high')),
  }));
  return { panels, series };
}

// What lies under p (the sheet's mm, y up) in the plot: the axes' values there, and the
// nearest point of a series shown within a few pixels, with its spread. null outside the panels.
export function readout(plot, p, pxPerMm, shown) {
  const k = plot.panels.findIndex(({ x, y }) => p[0] >= Math.min(x.a, x.b) && p[0] <= Math.max(x.a, x.b)
    && p[1] >= Math.min(y.a, y.b) && p[1] <= Math.max(y.a, y.b));
  if (k < 0) return null;
  const { x, y } = plot.panels[k];
  const name = (axis, fallback) => axis.label || fallback;
  let text = `${name(x, 'x')} ${x.text(x.value(p[0]))}   ${name(y, 'y')} ${y.text(y.value(p[1]))}`;
  let best = null;
  for (const s of plot.series) {
    if (s.panel !== k || !shown(s.g)) continue;
    s.xs.forEach((a, i) => {
      const b = s.ys[i];
      if (!x.drawable(a) || !y.drawable(b)) return;
      const d = Math.hypot(x.at(a) - p[0], y.at(b) - p[1]) * pxPerMm;
      if (d <= NEAR_PX && (!best || d < best.d)) best = { d, s, i };
    });
  }
  if (best) {
    const { s, i } = best, lo = s.low?.[i], hi = s.high?.[i];
    text += `   ${s.name}: ${x.text(s.xs[i])}, ${y.text(s.ys[i])}`;
    if (lo != null || hi != null) text += ` (${num(lo ?? s.ys[i])} … ${num(hi ?? s.ys[i])})`;
  }
  return text;
}
