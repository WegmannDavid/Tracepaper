// A track of sliders (SPEC.md §2.13): sliders branching off each other ("on"), each drawn as a
// line of points ("draw") along which its value runs, from the lowest at the first point to
// the highest at the last, by length; "closed" joins a line's ends, so its value goes round.
// The knob runs along the lines and passes from one to another only where they join: so a
// tilt-and-turn window's sash turns only with its handle sideways, and the handle turns only
// with the sash shut. The geometry and the knob's moves; sliders.js draws it.

const BRANCH = 0.6; // a branch without a drawing: square to where it joins, this much of the first line's length

const sub = (a, b) => [a[0] - b[0], a[1] - b[1]];

// A line of points, with its length to each: {points, at, length, closed}.
function line(points, closed) {
  const pts = closed ? [...points, points[0]] : points.map((p) => [...p]);
  const at = [0];
  for (let i = 1; i < pts.length; i++) at.push(at[i - 1] + Math.hypot(...sub(pts[i], pts[i - 1])));
  return { points: pts, at, length: at[at.length - 1], closed: !!closed };
}

// The point a share f of the way along, and the line's direction there.
function along(l, f) {
  f = l.closed ? ((f % 1) + 1) % 1 : Math.min(1, Math.max(0, f));
  const s = f * l.length;
  let i = 1;
  while (i < l.points.length - 1 && l.at[i] < s) i++;
  const a = l.points[i - 1], b = l.points[i], span = l.at[i] - l.at[i - 1];
  const t = span > 0 ? (s - l.at[i - 1]) / span : 0, d = sub(b, a), n = Math.hypot(...d) || 1;
  return { point: [a[0] + t * d[0], a[1] + t * d[1]], dir: [d[0] / n, d[1] / n] };
}

// The share of the way along a line nearest p, and how far p is from it there.
function nearest(l, p) {
  let best = { f: 0, d: Infinity };
  for (let i = 1; i < l.points.length; i++) {
    const a = l.points[i - 1], d = sub(l.points[i], a), span = l.at[i] - l.at[i - 1];
    const t = span > 0 ? Math.min(1, Math.max(0, ((p[0] - a[0]) * d[0] + (p[1] - a[1]) * d[1]) / (span * span))) : 0;
    const q = [a[0] + t * d[0], a[1] + t * d[1]], dist = Math.hypot(...sub(p, q));
    if (dist < best.d) best = { f: l.length > 0 ? (l.at[i - 1] + t * span) / l.length : 0, d: dist };
  }
  if (l.closed && best.f >= 1) best.f = 0;
  return best;
}

export class Track {
  // sliders: those of one track, each after the one it branches off: [{name, range, start, on?, draw?, closed?}].
  constructor(sliders) {
    this.sliders = sliders;
    this.by = new Map(sliders.map((s) => [s.name, s]));
    this.root = sliders[0];
    this.lines = new Map();
    for (const s of sliders) this.lay(s);
  }

  share(s, v) { return (v - s.range[0]) / (s.range[1] - s.range[0]); }

  value(s, f) {
    const v = s.range[0] + f * (s.range[1] - s.range[0]);
    return Math.min(s.range[1], Math.max(s.range[0], v));
  }

  // A slider's line: its own drawing, or the viewer's; a branch moved so that where it starts
  // lies where it joins the one it branches off.
  lay(s) {
    if (!s.on) {
      this.lines.set(s.name, line(s.draw ?? [[0, 0], [1, 0]], s.closed));
      return;
    }
    const [parent, at] = Object.entries(s.on)[0];
    const joint = along(this.lines.get(parent), this.share(this.by.get(parent), at));
    let points = s.draw;
    if (!points) { // square to the line it joins, to its left
      const length = BRANCH * this.lines.get(this.root.name).length, [dx, dy] = joint.dir;
      points = [[0, 0], [-dy * length, dx * length]];
    }
    const own = line(points, s.closed), start = along(own, this.share(s, s.start)).point;
    const shift = sub(joint.point, start);
    this.lines.set(s.name, line(points.map((p) => [p[0] + shift[0], p[1] + shift[1]]), s.closed));
  }

  point(name, v) { return along(this.lines.get(name), this.share(this.by.get(name), v)).point; }

  // Where the knob can pass to from a slider: the sliders branching off it, and the one it
  // branches off; each with the value of this one where they join.
  joints(name) {
    const s = this.by.get(name), out = [];
    for (const c of this.sliders) if (c.on && Object.keys(c.on)[0] === name) out.push({ to: c.name, at: Object.values(c.on)[0] });
    if (s.on) out.push({ to: Object.keys(s.on)[0], at: s.start });
    return out;
  }

  // The knob dragged towards p, from {name, v}: along its slider to the point nearest p, or on
  // to a slider joined to it, through the joint, where that comes nearer; and so on.
  drag(knob, p) {
    let name = knob.name;
    for (let k = 0; k < 32; k++) {
      const here = nearest(this.lines.get(name), p);
      let best = { name, f: here.f, d: here.d };
      for (const j of this.joints(name)) {
        const there = nearest(this.lines.get(j.to), p);
        if (there.d < best.d - 1e-9) best = { name: j.to, f: there.f, d: there.d };
      }
      if (best.name === name) return { name, v: this.value(this.by.get(name), best.f) };
      name = best.name;
    }
    return { name, v: this.value(this.by.get(name), nearest(this.lines.get(name), p).f) };
  }

  // Every slider's value with the knob at {name, v}: those it branches off, one after another,
  // where they join; the rest where they start.
  values(knob) {
    const out = Object.fromEntries(this.sliders.map((s) => [s.name, s.start]));
    out[knob.name] = knob.v;
    for (let s = this.by.get(knob.name); s.on; s = this.by.get(Object.keys(s.on)[0])) {
      const [parent, at] = Object.entries(s.on)[0];
      out[parent] = at;
    }
    return out;
  }

  // Where the knob is for values: on the furthest branch away from where it starts, else on
  // the first slider.
  knob(values) {
    const depth = (s) => (s.on ? 1 + depth(this.by.get(Object.keys(s.on)[0])) : 0);
    const off = this.sliders.filter((s) => s.on && values[s.name] !== s.start).sort((a, b) => depth(b) - depth(a));
    const s = off[0] ?? this.root;
    return { name: s.name, v: values[s.name] ?? s.start };
  }

  // [x0, y0, x1, y1] round all the lines.
  box() {
    const pts = [...this.lines.values()].flatMap((l) => l.points);
    const xs = pts.map((p) => p[0]), ys = pts.map((p) => p[1]);
    return [Math.min(...xs), Math.min(...ys), Math.max(...xs), Math.max(...ys)];
  }
}

// The sliders as tracks: each slider with those branching off it, the others on their own.
// A track counts as one only if some slider in it branches, draws its own line or is closed;
// else it is a plain slider. [{track: Track | null, sliders}].
export function tracks(sliders) {
  const rootOf = (s) => (s.on ? rootOf(sliders.find((x) => x.name === Object.keys(s.on)[0])) : s);
  const groups = new Map();
  for (const s of sliders) {
    const r = rootOf(s).name;
    groups.set(r, [...(groups.get(r) ?? []), s]);
  }
  return [...groups.values()].map((group) => ({
    sliders: group, track: group.some((s) => s.on || s.draw || s.closed) ? new Track(group) : null,
  }));
}
