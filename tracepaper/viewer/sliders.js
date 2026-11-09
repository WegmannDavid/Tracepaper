// A scene's sliders (SPEC.md §2.13) in the side panel of either page. Every window of the same
// scene shares them: moving a slider in one moves it in the others (a BroadcastChannel), so the
// scene's drawings follow its 3D view. A window opening asks the others where they are.
import { Motion } from './motion.js';

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
    this.container.hidden = this.head.hidden = !this.on;
    if (!this.on) { this.container.replaceChildren(); return; }
    const rows = this.motion.sliders.map((s) => {
      const row = el('div', 'tpv-frame-axis tpv-slider');
      row.dataset.slider = s.name;
      const head = el('div', 'tpv-frame-head');
      const number = el('input', 'tpv-slider-value');
      Object.assign(number, { type: 'number', min: s.range[0], max: s.range[1], step: 'any' });
      number.setAttribute('aria-label', `${s.name}${s.unit ? `, ${s.unit}` : ''}`);
      const value = el('span', 'tpv-frame-value');
      value.append(number, ...(s.unit ? [el('span', 'tpv-slider-unit', s.unit)] : []));
      head.append(el('span', 'tpv-frame-name', s.name), value);
      const range = el('input');
      Object.assign(range, { type: 'range', min: s.range[0], max: s.range[1], step: 'any' });
      range.title = `${s.name}: ${num(s.range[0])} to ${num(s.range[1])}${s.unit ? ` ${s.unit}` : ''}; double-click: where it starts`;
      range.addEventListener('input', () => this.move(s.name, Number(range.value), false));
      range.addEventListener('change', () => this.move(s.name, Number(range.value), true));
      range.addEventListener('dblclick', () => this.move(s.name, s.start, true));
      number.addEventListener('change', () => {
        if (number.value !== '' && Number.isFinite(Number(number.value))) this.move(s.name, Number(number.value), true);
        else this.show();
      });
      row.append(head, range);
      this.inputs.set(s.name, { range, number });
      return row;
    });
    this.container.replaceChildren(...rows);
    this.show();
  }

  show() {
    for (const [name, { range, number }] of this.inputs) {
      range.value = this.values[name];
      if (document.activeElement !== number) number.value = num(this.values[name]);
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
