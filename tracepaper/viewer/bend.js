// A part bent along a path (SPEC.md §2.13, "follow"), live: the 3D view's triangles cut across
// all along the part, once, and each corner then put where the path puts it as the slider
// moves. tracepaper/motion.py's Path is the same, for the exact drawings (tracepaper/bend.py):
// tests/paths.json holds cases both must pass.

const LIVE = 0.02; // mm: how far the straight pieces of a bend may be from its true curve
const LIVE_ANGLE = 0.15; // radians: and how far they may turn from one to the next, at most
const CUTS_MOST = 4000; // cuts along one part: further apart for a longer one
const STRAIGHT = 1e-9; // radians: points of a path turning less than this are on one line

const sub = (a, b) => [a[0] - b[0], a[1] - b[1], a[2] - b[2]];
const dot = (a, b) => a[0] * b[0] + a[1] * b[1] + a[2] * b[2];
const cross = (a, b) => [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]];
const add = (a, b, k = 1) => [a[0] + k * b[0], a[1] + k * b[1], a[2] + k * b[2]];

// Straight lines through points, each corner rounded by an arc of radius round, all in one
// plane. Lengths along it count from its first point; before that and past its end it runs
// on straight. The frame at a length u: the point c there, the direction t, and n = b × t,
// with b the plane's normal. Throws an Error for a path that can't be.
export class Path {
  constructor(points, round = 0) {
    const pts = points.map((p) => p.map(Number));
    Object.assign(this, { points: pts, round });
    const dirs = [];
    for (let i = 1; i < pts.length; i++) {
      const d = sub(pts[i], pts[i - 1]), n = Math.hypot(...d);
      if (n === 0) throw new Error('two of its points are the same');
      dirs.push([d.map((c) => c / n), n]);
    }
    this.origin = pts[0];
    this.t0 = dirs[0][0];
    const turns = dirs.slice(1).map(([d], i) => cross(dirs[i][0], d));
    let first = turns.find((t) => Math.hypot(...t) > STRAIGHT);
    if (!first) { // one straight line: any plane through it
      const k = [0, 1, 2].reduce((best, i) => (Math.abs(this.t0[i]) < Math.abs(this.t0[best]) ? i : best), 0);
      first = cross(this.t0, [0, 1, 2].map((i) => (i === k ? 1 : 0)));
    }
    const fn = Math.hypot(...first);
    this.b = first.map((c) => c / fn);
    this.n0 = cross(this.b, this.t0);
    const size = Math.max(...dirs.map(([, n]) => n));
    if (pts.some((p) => Math.abs(dot(sub(p, pts[0]), this.b)) > 1e-6 * size)) throw new Error('its points must lie in one plane');
    const corners = turns.map((turn, i) => {
      const sine = Math.hypot(...turn), cosine = dot(dirs[i][0], dirs[i + 1][0]);
      if (sine <= STRAIGHT) {
        if (cosine < 0) throw new Error(`it turns right back at its point ${i + 2}`);
        return null;
      }
      if (!(round > 0)) throw new Error('a path with corners needs "round", the radius of their arcs, above 0');
      const angle = Math.atan2(sine, cosine);
      return [round * Math.tan(angle / 2), angle, dot(turn, this.b) > 0 ? 1 : -1];
    });
    this.pieces = []; // {arc, u (at its start), length, ...}
    let u = 0, at = pts[0];
    dirs.forEach(([d, n], i) => {
      const before = i > 0 && corners[i - 1] ? corners[i - 1][0] : 0, corner = corners[i] ?? null;
      const length = n - before - (corner ? corner[0] : 0);
      if (length < -1e-9 * size) {
        throw new Error(`the line from its point ${i + 1} to ${i + 2} is too short for arcs of radius ${round} at its ends`);
      }
      if (length > 0) {
        this.pieces.push({ arc: false, u, length, at, d });
        u += length;
        at = add(at, d, length);
      }
      if (corner) {
        const [, angle, sign] = corner, centre = add(at, cross(this.b, d), sign * round);
        this.pieces.push({ arc: true, u, length: round * angle, centre, arm: sub(at, centre), d, sign });
        u += round * angle;
        at = add(centre, this.turned(sub(at, centre), sign * angle));
      }
    });
    Object.assign(this, { length: u, end: at, t1: dirs.at(-1)[0] });
  }

  // v, square to b, turned about it.
  turned(v, angle) {
    const c = Math.cos(angle), s = Math.sin(angle), w = cross(this.b, v);
    return [c * v[0] + s * w[0], c * v[1] + s * w[1], c * v[2] + s * w[2]];
  }

  get arcs() { return this.pieces.filter((p) => p.arc).map((p) => [p.u, p.u + p.length]); }

  // [the point at length u, the direction there].
  frame(u) {
    if (u <= 0) return [add(this.origin, this.t0, u), this.t0];
    if (u >= this.length) return [add(this.end, this.t1, u - this.length), this.t1];
    let piece = this.pieces[0];
    for (const p of this.pieces) if (p.u <= u) piece = p;
    if (!piece.arc) return [add(piece.at, piece.d, u - piece.u), piece.d];
    const angle = piece.sign * (u - piece.u) / this.round;
    return [add(piece.centre, this.turned(piece.arm, angle)), this.turned(piece.d, angle)];
  }

  along(p) { return dot(sub(p, this.origin), this.t0); }

  aside(p) { return dot(sub(p, this.origin), this.n0); }

  // Where a point p of the part at rest is, the part moved by mm along the path: as far
  // along the path as p is along the first line, and by, and as far to its sides.
  place(p, by) {
    const d = sub(p, this.origin), [c, t] = this.frame(dot(d, this.t0) + by);
    return add(add(c, cross(this.b, t), dot(d, this.n0)), this.b, dot(d, this.b));
  }

  // Points at rest, [x, y, z, ...], placed into out, and directions at those points (normals),
  // if given, turned with the path into outDirections.
  placeAll(points, by, out, directions = null, outDirections = null) {
    const { origin: o, t0, n0, b } = this;
    for (let i = 0; i < points.length; i += 3) {
      const d = [points[i] - o[0], points[i + 1] - o[1], points[i + 2] - o[2]];
      const s = dot(d, t0), n = dot(d, n0), h = dot(d, b), [c, t] = this.frame(s + by), side = cross(b, t);
      for (let k = 0; k < 3; k++) out[i + k] = c[k] + n * side[k] + h * b[k];
      if (directions) {
        const v = [directions[i], directions[i + 1], directions[i + 2]];
        const vs = dot(v, t0), vn = dot(v, n0), vh = dot(v, b);
        for (let k = 0; k < 3; k++) outDirections[i + k] = vs * t[k] + vn * side[k] + vh * b[k];
      }
    }
    return out;
  }

  // Where to cut a part at rest across, whose points are positions [x, y, z, ...]: lengths
  // along the first line, evenly all along the part, close enough for pieces within LIVE of
  // an arc wherever the part is on the path. None for a straight path.
  cuts(positions) {
    if (!this.arcs.length || !positions.length) return [];
    let lo = Infinity, hi = -Infinity, reach = 0;
    for (let i = 0; i < positions.length; i += 3) {
      const p = [positions[i], positions[i + 1], positions[i + 2]], s = this.along(p);
      lo = Math.min(lo, s);
      hi = Math.max(hi, s);
      reach = Math.max(reach, Math.abs(this.aside(p)));
    }
    const angle = Math.min(LIVE_ANGLE, 2 * Math.acos(Math.max(0, 1 - LIVE / (this.round + reach))));
    const step = Math.max(this.round * angle, (hi - lo) / CUTS_MOST);
    const out = [];
    for (let k = Math.ceil(lo / step); k * step < hi; k++) if (k * step > lo) out.push(k * step);
    return out;
  }
}

// Triangles cut across at cuts (lengths along path's first line, rising): {indices, made},
// the triangles they come apart into, turning the same way, and the points made for them,
// numbered on from the points there were: [[i, j, t]], each a share t of the way from point i
// to point j. Points at the same place, as the corners of faces side by side are, give made
// points at the same place exactly.
export function split(positions, indices, path, cuts) {
  const count = positions.length / 3, s = new Float64Array(count);
  for (let i = 0; i < count; i++) s[i] = path.along([positions[3 * i], positions[3 * i + 1], positions[3 * i + 2]]);
  if (!cuts.length) return { indices: Array.from(indices), made: [] };
  const P = positions;
  const before = (a, b) => P[3 * a] < P[3 * b] || (P[3 * a] === P[3 * b] && (P[3 * a + 1] < P[3 * b + 1]
    || (P[3 * a + 1] === P[3 * b + 1] && P[3 * a + 2] < P[3 * b + 2])));
  const made = [], found = new Map();
  const between = (i, j, k) => {
    if (before(j, i)) [i, j] = [j, i];
    const key = `${i} ${j} ${k}`;
    if (!found.has(key)) {
      made.push([i, j, (cuts[k] - s[i]) / (s[j] - s[i])]);
      found.set(key, count + made.length - 1);
    }
    return found.get(key);
  };
  // The first cut above v, and the first at or above it.
  const above = (v, orEqual) => {
    let lo = 0, hi = cuts.length;
    while (lo < hi) {
      const mid = (lo + hi) >> 1;
      if (cuts[mid] < v || (!orEqual && cuts[mid] === v)) lo = mid + 1; else hi = mid;
    }
    return lo;
  };
  const out = [];
  for (let f = 0; f < indices.length; f += 3) {
    const tri = [indices[f], indices[f + 1], indices[f + 2]];
    const order = [0, 1, 2].sort((x, y) => s[tri[x]] - s[tri[y]] || x - y);
    const [a, b, c] = order.map((k) => tri[k]);
    const first = above(s[a], false), last = above(s[c], true); // those strictly between a and c
    if (first >= last) { out.push(...tri); continue; }
    const right = (k) => (cuts[k] === s[b] ? b : cuts[k] < s[b] ? between(a, b, k) : between(b, c, k));
    const left = [a];
    for (let k = first; k < last; k++) left.push(between(a, c, k));
    left.push(c);
    const turned = !['012', '120', '201'].includes(order.join(''));
    for (let j = 0; j < left.length - 1; j++) {
      const low = j === 0 ? s[a] : cuts[first + j - 1], high = j === left.length - 2 ? s[c] : cuts[first + j];
      let ring = [left[j]];
      if (j > 0) ring.push(right(first + j - 1));
      if ((low < s[b] && s[b] < high) || (j === 0 && s[b] === s[a]) || (j === left.length - 2 && s[b] === s[c])) ring.push(b);
      if (j < left.length - 2) ring.push(right(first + j));
      ring.push(left[j + 1]);
      ring = ring.filter((p, i) => p !== ring[(i + ring.length - 1) % ring.length]);
      for (let i = 1; i < ring.length - 1; i++) out.push(ring[0], ...(turned ? [ring[i + 1], ring[i]] : [ring[i], ring[i + 1]]));
    }
  }
  return { indices: out, made };
}

// Values of the points (size numbers each), with those of the made points after them.
export function withMade(values, size, made, Kind = Float64Array) {
  const count = values.length / size, out = new Kind((count + made.length) * size);
  out.set(values);
  made.forEach(([i, j, t], m) => {
    for (let k = 0; k < size; k++) out[(count + m) * size + k] = values[i * size + k] + t * (values[j * size + k] - values[i * size + k]);
  });
  return out;
}

// Line segments [x, y, z, x, y, z, ...] cut across at cuts: the segments they come apart into.
export function splitSegments(points, path, cuts) {
  const out = [];
  for (let i = 0; i < points.length; i += 6) {
    const p = [points[i], points[i + 1], points[i + 2]], q = [points[i + 3], points[i + 4], points[i + 5]];
    const sp = path.along(p), sq = path.along(q), lo = Math.min(sp, sq), hi = Math.max(sp, sq);
    const stops = [0, ...cuts.filter((c) => c > lo && c < hi).map((c) => (c - sp) / (sq - sp)).sort((x, y) => x - y), 1];
    for (let k = 1; k < stops.length; k++) {
      for (const t of [stops[k - 1], stops[k]]) out.push(p[0] + t * (q[0] - p[0]), p[1] + t * (q[1] - p[1]), p[2] + t * (q[2] - p[2]));
    }
  }
  return out;
}

// A part of a 3D view as livecut.js reads it, {positions, indices, color}, ready to bend
// along path: cut across, with its points at rest.
export function bendable(part, path) {
  const { indices, made } = split(part.positions, part.indices, path, path.cuts(part.positions));
  const rest = withMade(part.positions, 3, made);
  return { ...part, rest, positions: new Float64Array(rest.length), indices: Uint32Array.from(indices), path };
}

// The part moved by mm along its path.
export function bent(part, by) {
  part.path.placeAll(part.rest, by, part.positions);
  return part;
}
