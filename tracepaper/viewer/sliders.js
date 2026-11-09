// A scene's sliders (SPEC.md §2.13) in the side panel of either page: plain ones, and tracks of
// sliders branching off each other (track.js), drawn as the scene's .tp.json draws them, with a
// knob that runs along them. Every window of the same scene shares them: moving a slider in one
// moves it in the others (a BroadcastChannel), so the scene's drawings follow its 3D view. A
// window opening asks the others where they are.
import { Motion } from './motion.js';
import { tracks } from './track.js';

const SVG_NS = 'http://www.w3.org/2000/svg';
const svgEl = (tag, attrs = {}) => {
  const e = document.createElementNS(SVG_NS, tag);
  for (const [k, v] of Object.entries(attrs)) e.setAttribute(k, v);
  return e;
};

const CHANNEL = 'tracepaper-motion';

const el = (tag, cls, text) => {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (text !== undefined) e.textContent = text;
  return e;
};
const num = (v) => String(Number(v.toPrecision(4)));

export class SliderPanel {
  // container, head: the panel's elements for the sliders and its heading; scene: the scene's
  // name ("dir/a.step"), which windows share sliders by; onPose(values, settled): the sliders
  // moved, here or in another window; settled once they rest (let go, or a value typed).
  constructor({ container, head, scene, onPose }) {
    Object.assign(this, { container, head, scene, onPose });
    this.motion = null;
    this.values = {};
    this.inputs = new Map(); // name -> {range, number}
    this.channel = 'BroadcastChannel' in window ? new BroadcastChannel(CHANNEL) : null;
    this.channel?.addEventListener('message', (e) => this.heard(e.data));
    container.hidden = head.hidden = true;
  }

  get on() { return !!this.motion?.on; }

  // The scene's motion, as /api/motion gives it, or {} for none. The sliders still there keep
  // their positions; a window showing the scene already says where they are.
  set(info) {
    let motion = null;
    try {
      motion = info?.sliders?.length || info?.moves?.length ? new Motion(info) : null;
    } catch (err) {
      this.container.replaceChildren(el('p', 'tpv-frame-missing', `Can't move the parts: ${err.message}`));
      this.container.hidden = this.head.hidden = false;
      this.motion = null;
      return;
    }
    const had = this.values;
    this.motion = motion;
    this.values = motion ? Object.fromEntries(motion.sliders.map((s) => [s.name, clamp(had[s.name] ?? s.start, s)])) : {};
    this.build();
    if (motion) this.post({ ask: true });
  }

  get label() { return this.motion ? this.motion.label(this.values) : null; }

  build() {
    this.inputs.clear();
    this.tracks = [];
    this.container.hidden = this.head.hidden = !this.on;
    if (!this.on) { this.container.replaceChildren(); return; }
    const parts = tracks(this.motion.sliders).flatMap(({ track, sliders }) => (track
      ? [this.buildTrack(track, sliders)] : sliders.map((s) => this.buildSlider(s))));
    this.container.replaceChildren(...parts);
    this.show();
  }

  // A slider's name, and its value to type in.
  nameAndValue(s, onTyped) {
    const head = el('div', 'tpv-frame-head');
    const number = el('input', 'tpv-slider-value');
    Object.assign(number, { type: 'number', min: s.range[0], max: s.range[1], step: 'any' });
    number.setAttribute('aria-label', `${s.name}${s.unit ? `, ${s.unit}` : ''}`);
    const value = el('span', 'tpv-frame-value');
    value.append(number, ...(s.unit ? [el('span', 'tpv-slider-unit', s.unit)] : []));
    head.append(el('span', 'tpv-frame-name', s.name), value);
    number.addEventListener('change', () => {
      if (number.value !== '' && Number.isFinite(Number(number.value))) onTyped(Number(number.value));
      else this.show();
    });
    return { head, number };
  }

  // A track: its lines as the scene draws them, the knob, and each slider's value to type in,
  // which puts the knob there, through the joints on the way.
  buildTrack(track, sliders) {
    const box = el('div', 'tpv-track');
    box.dataset.track = track.root.name;
    const [x0, y0, x1, y1] = track.box(), size = Math.max(x1 - x0, y1 - y0) || 1, fs = 0.08 * size;
    const svg = svgEl('svg', { class: 'tpv-track-svg', 'font-size': fs });
    const xy = ([x, y]) => `${x} ${-y}`;
    const bounds = [x0, y0, x1, y1];
    for (const s of sliders) {
      const l = track.lines.get(s.name);
      svg.append(svgEl('path', { class: 'tpv-track-line', 'data-slider': s.name, d: `M${l.points.map(xy).join(' L')}` }));
      // Its name past where it ends, or a first line's past where it starts, clear of the knob.
      const [a, b] = s.on && !l.closed ? [l.points.at(-2), l.points.at(-1)] : [l.points[1], l.points[0]];
      const d = [b[0] - a[0], b[1] - a[1]], n = Math.hypot(...d) || 1, gap = 0.1 * size;
      const at = [b[0] + gap * d[0] / n, b[1] + gap * d[1] / n];
      const anchor = Math.abs(d[0]) < 0.3 * n ? 'middle' : d[0] > 0 ? 'start' : 'end';
      const text = svgEl('text', { class: 'tpv-track-name', x: at[0], y: -at[1], 'dominant-baseline': 'middle', 'text-anchor': anchor });
      text.textContent = s.name;
      svg.append(text);
      const w = 0.6 * fs * s.name.length, left = { start: 0, middle: -w / 2, end: -w }[anchor];
      bounds[0] = Math.min(bounds[0], at[0] + left);
      bounds[2] = Math.max(bounds[2], at[0] + left + w);
      bounds[1] = Math.min(bounds[1], at[1] - fs);
      bounds[3] = Math.max(bounds[3], at[1] + fs);
    }
    const m = 0.07 * size;
    svg.setAttribute('viewBox', `${bounds[0] - m} ${-bounds[3] - m} ${bounds[2] - bounds[0] + 2 * m} ${bounds[3] - bounds[1] + 2 * m}`);
    const knob = svgEl('circle', { class: 'tpv-track-knob', r: 0.05 * size });
    svg.append(knob);
    svg.addEventListener('pointerdown', (e) => {
      svg.setPointerCapture(e.pointerId);
      this.dragging = true;
      this.drag(track, svg, e, false);
    });
    svg.addEventListener('pointermove', (e) => { if (this.dragging && svg.hasPointerCapture(e.pointerId)) this.drag(track, svg, e, false); });
    const done = (e) => {
      if (!this.dragging) return;
      this.dragging = false;
      this.drag(track, svg, e, true);
    };
    svg.addEventListener('pointerup', done);
    svg.addEventListener('pointercancel', done);
    svg.addEventListener('dblclick', () => this.place(track, { name: track.root.name, v: track.root.start }, true));
    const rows = sliders.map((s) => {
      const row = el('div', 'tpv-frame-axis tpv-slider');
      row.dataset.slider = s.name;
      const { head, number } = this.nameAndValue(s, (v) => this.place(track, { name: s.name, v: clamp(v, s) }, true));
      row.append(head);
      this.inputs.set(s.name, { range: null, number });
      return row;
    });
    box.append(svg, ...rows);
    this.tracks.push({ track, knob });
    return box;
  }

  drag(track, svg, e, settled) {
    const p = new DOMPoint(e.clientX, e.clientY).matrixTransform(svg.getScreenCTM().inverse());
    this.place(track, track.drag(track.knob(this.values), [p.x, -p.y]), settled);
  }

  // The knob of a track at {name, v}: its sliders' values as that puts them.
  place(track, knob, settled) {
    this.values = { ...this.values, ...track.values(knob) };
    this.show();
    this.post({ values: this.values, settled });
    this.onPose(this.values, settled);
  }

  // A plain slider.
  buildSlider(s) {
    const row = el('div', 'tpv-frame-axis tpv-slider');
    row.dataset.slider = s.name;
    const { head, number } = this.nameAndValue(s, (v) => this.move(s.name, v, true));
    const range = el('input');
    Object.assign(range, { type: 'range', min: s.range[0], max: s.range[1], step: 'any' });
    range.title = `${s.name}: ${num(s.range[0])} to ${num(s.range[1])}${s.unit ? ` ${s.unit}` : ''}; double-click: where it starts`;
    range.addEventListener('input', () => this.move(s.name, Number(range.value), false));
    range.addEventListener('change', () => this.move(s.name, Number(range.value), true));
    range.addEventListener('dblclick', () => this.move(s.name, s.start, true));
    row.append(head, range);
    this.inputs.set(s.name, { range, number });
    return row;
  }

  show() {
    for (const [name, { range, number }] of this.inputs) {
      if (range) range.value = this.values[name];
      if (document.activeElement !== number) number.value = num(this.values[name]);
    }
    for (const { track, knob } of this.tracks ?? []) {
      const k = track.knob(this.values), [x, y] = track.point(k.name, k.v);
      knob.setAttribute('cx', x);
      knob.setAttribute('cy', -y);
    }
  }

  // A slider moved here: to value, clamped to its range.
  move(name, value, settled) {
    const s = this.motion.sliders.find((x) => x.name === name);
    this.values = { ...this.values, [name]: clamp(value, s) };
    this.show();
    this.post({ values: this.values, settled });
    this.onPose(this.values, settled);
  }

  // Set every slider at once, as a test or the page wants: the others' windows follow.
  setValues(values, settled = true) {
    if (!this.on) return;
    this.values = Object.fromEntries(this.motion.sliders.map((s) => [s.name, clamp(values[s.name] ?? this.values[s.name], s)]));
    this.show();
    this.post({ values: this.values, settled });
    this.onPose(this.values, settled);
  }

  // Take the positions a sketch was drawn at, telling the other windows, but not onPose: the
  // page shows the sketch, drawn there.
  adopt(values) {
    if (!this.on) return;
    this.values = Object.fromEntries(this.motion.sliders.map((s) => [
      s.name, Number.isFinite(values[s.name]) ? clamp(values[s.name], s) : this.values[s.name]]));
    this.show();
    this.post({ values: this.values, settled: true });
  }

  post(message) {
    this.channel?.postMessage({ scene: this.scene, ...message });
  }

  // Another window of the scene: asking where the sliders are, or saying it moved them.
  heard(message) {
    if (message?.scene !== this.scene || !this.on) return;
    if (message.ask) { this.post({ values: this.values, settled: true }); return; }
    if (!message.values) return;
    const values = Object.fromEntries(this.motion.sliders.map((s) => [
      s.name, Number.isFinite(message.values[s.name]) ? clamp(message.values[s.name], s) : this.values[s.name]]));
    if (this.motion.sliders.every((s) => values[s.name] === this.values[s.name]) && !message.settled) return;
    this.values = values;
    this.show();
    this.onPose(this.values, !!message.settled);
  }
}

function clamp(v, slider) {
  return Math.min(slider.range[1], Math.max(slider.range[0], v));
}
