// A scene's frames (SPEC.md §2.10), in the side panel of either page: a slider for each axis,
// steps and Play, the frame's values, and a plot of each value against the first axis (or
// another, or another value) with the frame shown marked, and its limits. Frames where a value
// is outside its limits are marked on the slider and in red. The page loads the frame chosen
// (onFrame) and keeps everything else.

const NS = 'http://www.w3.org/2000/svg';
const PLAY_MS = 150; // per frame
const PLOT = { w: 196, h: 64, pad: 6 };

const num = (v, unit = '') => `${Number.isInteger(v) ? v : +v.toPrecision(4)}${unit ? ` ${unit}` : ''}`;
const el = (tag, cls, text) => {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (text !== undefined) e.textContent = text;
  return e;
};
const svg = (tag, attrs) => {
  const e = document.createElementNS(NS, tag);
  for (const [k, v] of Object.entries(attrs)) e.setAttribute(k, v);
  return e;
};

export class FramePanel {
  // container: the panel's element for them; onFrame(index): show that frame;
  // onTrail(trail): show every frame faint, of one node (its key) or all ('') or none
  // (null), omitted: no Trail option; nodes(): the keys of the parts that can be trailed.
  constructor({ container, onFrame, onTrail = null, nodes = () => [] }) {
    Object.assign(this, { container, onFrame, onTrail, nodes });
    this.info = null; // {axes, quantities, frames}
    this.at = {}; // axis -> index into this.values[axis]
    this.values = {}; // axis -> its values, in order
    this.playing = null;
    this.trail = null;
    this.against = null; // what the plots are against: an axis's or a quantity's name, null for the first axis
    container.hidden = true;
  }

  get on() { return !!this.info?.frames?.length; }

  // The frames of the view, as /api/frames gives them, or {} for none; start: a frame
  // index to show, else the one shown before if it is still there, else the first.
  set(info, start = null) {
    const keep = this.on && start === null ? this.frame : null;
    this.info = info?.frames?.length ? info : null;
    this.stop();
    this.container.hidden = !this.on;
    if (!this.on) { this.container.replaceChildren(); return; }
    this.values = Object.fromEntries(this.info.axes.map(({ name }) => [
      name, [...new Set(this.info.frames.map((f) => f.at[name]))].sort((a, b) => a - b)]));
    const first = this.info.frames[start ?? 0] ?? this.info.frames[0];
    const goal = keep ?? first;
    this.at = Object.fromEntries(this.info.axes.map(({ name }) => [name, Math.max(0, this.values[name].indexOf(goal.at[name]))]));
    this.render();
  }

  // The frame at the sliders' values, or null if there is none there.
  get frame() {
    return this.info?.frames.find((f) => this.info.axes.every(({ name }) => f.at[name] === this.values[name][this.at[name]])) ?? null;
  }

  get index() { return this.info ? this.info.frames.indexOf(this.frame) : -1; }

  // "insertion=2.5", as sketch files record it.
  get label() {
    const f = this.frame;
    return f ? this.info.axes.map(({ name }) => `${name}=${num(f.at[name])}`).join(' ') : null;
  }

  // Whether a value is outside a quantity's limits; whether a frame has one that is.
  outside(q, v) {
    return v !== undefined && ((q.max != null && v > q.max) || (q.min != null && v < q.min));
  }

  over(frame) {
    return this.info.quantities.some((q) => this.outside(q, frame.values[q.name]));
  }

  // Move along an axis (the first by default) by step frames; wrap: from the end to the start.
  step(by, axis = this.info?.axes[0]?.name, wrap = false) {
    if (!this.on) return;
    const n = this.values[axis].length;
    const k = this.at[axis] + by;
    this.go(axis, wrap ? (k + n) % n : Math.max(0, Math.min(n - 1, k)));
  }

  go(axis, k) {
    if (this.at[axis] === k) return;
    this.at[axis] = k;
    this.render();
    if (this.index >= 0) this.onFrame(this.index);
  }

  play() {
    if (this.playing) { this.stop(); return; }
    this.playing = setInterval(() => this.step(1, undefined, true), PLAY_MS);
    this.render();
  }

  stop() {
    if (!this.playing) return;
    clearInterval(this.playing);
    this.playing = null;
    this.render();
  }

  render() {
    if (!this.on) return;
    if (this.built !== this.info) this.build();
    this.update();
  }

  // The panel's elements, once for each set of frames: update() fills them in, so that a
  // slider being dragged stays the same element, and keeps the drag.
  build() {
    this.built = this.info;
    const { axes, quantities } = this.info;
    this.ui = { axes: {}, quantities: {} };
    const parts = [];
    for (const [i, { name }] of axes.entries()) {
      const values = this.values[name];
      const row = el('div', 'tpv-frame-axis');
      row.dataset.axis = name;
      const head = el('div', 'tpv-frame-head');
      const value = el('span', 'tpv-frame-value');
      head.append(el('span', 'tpv-frame-name', name), value);
      const buttons = el('div', 'tpv-frame-buttons');
      const button = (text, title, act) => {
        const b = el('button', '', text);
        b.title = title;
        b.addEventListener('click', act);
        return b;
      };
      buttons.append(button('◀', `Back along ${name} (${i ? '↓' : '←'})`, () => this.step(-1, name)));
      let play = null;
      if (i === 0) {
        play = button('', '', () => this.play());
        play.className = 'tpv-frame-play';
        buttons.append(play);
      }
      buttons.append(button('▶', `On along ${name} (${i ? '↑' : '→'})`, () => this.step(1, name)));
      const range = el('input');
      Object.assign(range, { type: 'range', min: 0, max: values.length - 1, step: 1 });
      range.setAttribute('aria-label', name);
      range.addEventListener('input', () => this.go(name, Number(range.value)));
      const ticks = svg('svg', { class: 'tpv-frame-ticks', viewBox: `0 0 ${Math.max(1, values.length - 1)} 1`,
                                 preserveAspectRatio: 'none' });
      row.append(head, buttons, range, ticks);
      parts.push(row);
      this.ui.axes[name] = { value, range, ticks, play };
    }
    this.ui.missing = el('p', 'tpv-frame-missing', 'No frame here');
    parts.push(this.ui.missing);
    // What the plots are against: an axis, or another quantity (force against deflection).
    const choices = [...axes.map((a) => a.name), ...quantities.map((q) => q.name)];
    if (quantities.length && (axes.length > 1 || quantities.length > 1)) { // more than the one axis to choose
      const label = el('label', 'tpv-frame-against', 'Plot against ');
      const select = el('select');
      select.setAttribute('aria-label', 'Plot against');
      select.append(...choices.map((c) => new Option(c, c)));
      select.value = this.against ?? axes[0].name;
      select.addEventListener('change', () => { this.against = select.value === axes[0].name ? null : select.value; this.update(); });
      label.append(select);
      parts.push(label);
    }
    for (const q of quantities) {
      const box = el('div', 'tpv-frame-quantity');
      const head = el('div', 'tpv-frame-head');
      const value = el('span', 'tpv-frame-value');
      const limits = [q.min != null && `at least ${num(q.min, q.unit)}`, q.max != null && `at most ${num(q.max, q.unit)}`];
      if (limits.some(Boolean)) value.title = limits.filter(Boolean).join(', ');
      head.append(el('span', 'tpv-frame-name', q.name), value);
      const chart = this.plot(q);
      box.append(head, chart);
      parts.push(box);
      this.ui.quantities[q.name] = { value, chart };
    }
    if (this.onTrail) {
      const label = el('label', 'tpv-frame-against', 'Trail ');
      label.title = 'Show every frame along the first axis faintly behind this one: of everything, or of one part';
      this.ui.trail = el('select');
      this.ui.trail.setAttribute('aria-label', 'Trail');
      this.ui.trail.addEventListener('change', () => {
        const v = this.ui.trail.value;
        this.trail = v === 'off' ? null : v === 'all' ? '' : v;
        this.onTrail(this.trail);
      });
      label.append(this.ui.trail);
      parts.push(label);
    }
    this.container.replaceChildren(...parts);
  }

  // Fill the panel in for the frame shown.
  update() {
    const { axes, quantities } = this.info, frame = this.frame;
    for (const { name, unit } of axes) {
      const values = this.values[name], { value, range, ticks, play } = this.ui.axes[name];
      value.textContent = num(values[this.at[name]], unit);
      if (Number(range.value) !== this.at[name]) range.value = this.at[name];
      // A tick for each frame along this axis (the others where they are), red where a value
      // is over its limit.
      ticks.replaceChildren(...values.flatMap((v, k) => {
        const f = this.info.frames.find((g) => g.at[name] === v
          && axes.every((a) => a.name === name || g.at[a.name] === this.values[a.name][this.at[a.name]]));
        return f ? [svg('line', { x1: k, x2: k, y1: 0, y2: 1, class: this.over(f) ? 'over' : '' })] : [];
      }));
      if (play) {
        play.textContent = this.playing ? '⏸' : '⏵';
        play.title = this.playing ? 'Stop' : 'Play, round and round';
        play.setAttribute('aria-pressed', String(!!this.playing));
      }
    }
    this.ui.missing.hidden = !!frame;
    for (const q of quantities) {
      const v = frame?.values[q.name], ui = this.ui.quantities[q.name];
      ui.value.textContent = v === undefined ? '–' : num(v, q.unit);
      ui.value.classList.toggle('over', this.outside(q, v));
      const chart = this.plot(q);
      ui.chart.replaceWith(chart);
      ui.chart = chart;
    }
    if (this.ui.trail) { // the parts, as the page's tree has them now
      const keys = this.nodes(), sel = this.ui.trail;
      const want = ['off', 'all', ...keys].join('\n');
      if (sel.dataset.options !== want) {
        sel.dataset.options = want;
        sel.replaceChildren(new Option('off', 'off'), new Option('everything', 'all'), ...keys.map((k) => new Option(k, k)));
      }
      sel.value = this.trail === null ? 'off' : this.trail === '' ? 'all' : this.trail;
    }
  }

  // A quantity against what the plots are against (the first axis by default), the other
  // axes where they are: its line, through the frames in order, the frame shown, its limits
  // dashed, and the points outside them in red.
  plot(q) {
    const { w, h, pad } = PLOT, first = this.info.axes[0].name;
    const against = this.against ?? first;
    const isAxis = this.info.axes.some((a) => a.name === against);
    const x = (f) => (isAxis ? f.at[against] : f.values[against]);
    const others = this.info.axes.filter((a) => a.name !== (isAxis ? against : first));
    const pts = this.info.frames
      .filter((f) => q.name in f.values && x(f) !== undefined
        && others.every((a) => f.at[a.name] === this.values[a.name][this.at[a.name]]))
      .sort((a, b) => (isAxis ? x(a) - x(b) : a.at[first] - b.at[first]))
      .map((f) => [x(f), f.values[q.name], f]);
    const chart = svg('svg', { class: 'tpv-frame-plot', viewBox: `0 0 ${w} ${h}`, role: 'img',
                               'aria-label': `${q.name} against ${against}` });
    if (!pts.length) return chart;
    const xs = pts.map((p) => p[0]), ys = pts.map((p) => p[1]);
    for (const limit of [q.min, q.max]) if (limit != null) ys.push(limit);
    const [x0, x1] = [Math.min(...xs), Math.max(...xs)], [y0, y1] = [Math.min(0, ...ys), Math.max(...ys)];
    const X = (v) => pad + (x1 > x0 ? (v - x0) / (x1 - x0) : 0.5) * (w - 2 * pad);
    const Y = (v) => h - pad - (y1 > y0 ? (v - y0) / (y1 - y0) : 0.5) * (h - 2 * pad);
    chart.append(svg('line', { class: 'tpv-plot-axis', x1: pad, x2: w - pad, y1: Y(Math.max(y0, 0)), y2: Y(Math.max(y0, 0)) }));
    for (const limit of [q.min, q.max]) {
      if (limit != null) chart.append(svg('line', { class: 'tpv-plot-limit', x1: pad, x2: w - pad, y1: Y(limit), y2: Y(limit) }));
    }
    chart.append(svg('polyline', { class: 'tpv-plot-line', points: pts.map(([a, b]) => `${X(a)},${Y(b)}`).join(' ') }));
    for (const [a, b] of pts) {
      if (this.outside(q, b)) chart.append(svg('circle', { class: 'tpv-plot-over', cx: X(a), cy: Y(b), r: 2.5 }));
    }
    const shown = this.frame;
    if (shown && x(shown) !== undefined) {
      chart.append(svg('line', { class: 'tpv-plot-cursor', x1: X(x(shown)), x2: X(x(shown)), y1: pad / 2, y2: h - pad / 2 }));
      if (q.name in shown.values) chart.append(svg('circle', { class: 'tpv-plot-dot', cx: X(x(shown)), cy: Y(shown.values[q.name]), r: 3 }));
    }
    return chart;
  }

  // The arrow keys, when the page passes them on: true if taken.
  key(e) {
    if (!this.on) return false;
    const [first, second] = this.info.axes;
    if (e.key === 'ArrowRight') this.step(1, first.name);
    else if (e.key === 'ArrowLeft') this.step(-1, first.name);
    else if (e.key === 'ArrowUp' && second) this.step(1, second.name);
    else if (e.key === 'ArrowDown' && second) this.step(-1, second.name);
    else return false;
    e.preventDefault();
    return true;
  }
}
