// Sections cut live from the 3D view's triangles (SPEC.md §2.13): while a slider moves, a
// drawing's moving parts are cut here, as fast as the slider goes, to within the 3D view's
// tessellation (0.05 mm); the exact section follows from the server once the sliders rest.

// The parts of a scene's 3D view (a .glb as scene.py writes it: each part's faces in CAD
// coordinates, under one root that turns them into glTF's y up), by key as the tree names
// them ("Sash / handle"): Map key -> {positions, indices, color}, color sRGB from 0 to 1.
export async function loadParts(url) {
  const r = await fetch(url, { cache: 'no-store' });
  if (!r.ok) throw new Error(`${r.status} ${await r.text()}`);
  return readGlb(await r.arrayBuffer());
}

export function readGlb(buffer) {
  const view = new DataView(buffer);
  if (view.getUint32(0, true) !== 0x46546c67) throw new Error('not a glTF binary');
  let at = 12, json = null, bin = null;
  while (at < buffer.byteLength) {
    const length = view.getUint32(at, true), type = view.getUint32(at + 4, true);
    if (type === 0x4e4f534a) json = JSON.parse(new TextDecoder().decode(new Uint8Array(buffer, at + 8, length)));
    else if (type === 0x004e4942) bin = buffer.slice(at + 8, at + 8 + length);
    at += 8 + length;
  }
  const accessor = (k) => {
    const a = json.accessors[k], v = json.bufferViews[a.bufferView], offset = (v.byteOffset ?? 0) + (a.byteOffset ?? 0);
    const Kind = { 5126: Float32Array, 5125: Uint32Array, 5123: Uint16Array, 5121: Uint8Array }[a.componentType];
    return new Kind(bin, offset, a.count * { SCALAR: 1, VEC3: 3 }[a.type]);
  };
  const srgb = (c) => (c <= 0.0031308 ? 12.92 * c : 1.055 * c ** (1 / 2.4) - 0.055);
  const parts = new Map();
  const walk = (k, above) => {
    const node = json.nodes[k];
    const key = above === null ? '' : above ? `${above} / ${node.name}` : node.name;
    if (node.mesh !== undefined && key) {
      const positions = [], indices = [];
      let color = [0.6, 0.6, 0.6];
      for (const p of json.meshes[node.mesh].primitives) {
        const pos = accessor(p.attributes.POSITION), idx = accessor(p.indices), base = positions.length / 3;
        for (const v of pos) positions.push(v);
        for (const i of idx) indices.push(base + i);
        const m = json.materials?.[p.material]?.pbrMetallicRoughness?.baseColorFactor;
        if (m) color = m.slice(0, 3).map(srgb);
      }
      parts.set(key, { positions: Float64Array.from(positions), indices: Uint32Array.from(indices), color });
    }
    for (const c of node.children ?? []) walk(c, key);
  };
  // The root that turns z up into y up holds the scene's top nodes: their names are the keys.
  for (const root of json.scenes[json.scene ?? 0].nodes) for (const c of json.nodes[root].children ?? []) walk(c, '');
  return parts;
}

const dot = (a, b) => a[0] * b[0] + a[1] * b[1] + a[2] * b[2];
const cross = (a, b) => [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]];
const f = (v) => String(Number(v.toFixed(4)));

// Path data of where a plane cuts a part placed by m (a 4 × 4 matrix, row by row, from where
// it rests), in the plane's coordinates, y up: closed loops, to fill evenodd as a section's
// regions are. plane: {origin, normal, xdir}, unit vectors, in CAD coordinates.
export function cutPath(part, m, { origin, normal, xdir }) {
  // The plane's axes, back into where the part rests: a · (m p − origin) = (Rᵀ a) · p + a · (t − origin).
  const back = (a) => [m[0] * a[0] + m[4] * a[1] + m[8] * a[2], m[1] * a[0] + m[5] * a[1] + m[9] * a[2],
                       m[2] * a[0] + m[6] * a[1] + m[10] * a[2]];
  const d = [m[3] - origin[0], m[7] - origin[1], m[11] - origin[2]], ydir = cross(normal, xdir);
  const [n, x, y] = [normal, xdir, ydir].map(back), [nc, xc, yc] = [normal, xdir, ydir].map((a) => dot(a, d));
  const P = part.positions, I = part.indices, count = P.length / 3;
  const side = new Float64Array(count);
  for (let i = 0; i < count; i++) side[i] = n[0] * P[3 * i] + n[1] * P[3 * i + 1] + n[2] * P[3 * i + 2] + nc;
  const before = (a, b) => P[3 * a] < P[3 * b] || (P[3 * a] === P[3 * b] && (P[3 * a + 1] < P[3 * b + 1]
    || (P[3 * a + 1] === P[3 * b + 1] && P[3 * a + 2] < P[3 * b + 2])));
  // Where an edge crosses the plane, worked out the same way from either face beside it, whose
  // corners are copies: so that the ends of the pieces meet exactly.
  const crossing = (a, b) => {
    if (before(b, a)) [a, b] = [b, a];
    const t = side[a] / (side[a] - side[b]);
    const q = [0, 1, 2].map((k) => P[3 * a + k] + t * (P[3 * b + k] - P[3 * a + k]));
    return [dot(x, q) + xc, dot(y, q) + yc];
  };
  const pieces = [];
  for (let k = 0; k < I.length; k += 3) {
    const corners = [I[k], I[k + 1], I[k + 2]], above = corners.map((i) => side[i] >= 0);
    if (above[0] === above[1] && above[1] === above[2]) continue;
    const ends = [];
    for (let e = 0; e < 3; e++) if (above[e] !== above[(e + 1) % 3]) ends.push(crossing(corners[e], corners[(e + 1) % 3]));
    pieces.push(ends);
  }
  return chain(pieces);
}

// Pieces [[p, q]] joined into lines where they meet, as path data: closed loops end in Z.
function chain(pieces) {
  const key = (p) => `${p[0]},${p[1]}`;
  const at = new Map();
  pieces.forEach(([p, q], i) => {
    for (const k of [key(p), key(q)]) at.set(k, [...(at.get(k) ?? []), i]);
  });
  const used = new Uint8Array(pieces.length), out = [];
  const next = (point) => {
    const i = (at.get(key(point)) ?? []).find((j) => !used[j]);
    if (i === undefined) return null;
    used[i] = 1;
    const [p, q] = pieces[i];
    return key(p) === key(point) ? q : p;
  };
  for (let i = 0; i < pieces.length; i++) {
    if (used[i]) continue;
    used[i] = 1;
    const line = [...pieces[i]];
    let closed = false;
    for (let p = next(line.at(-1)); p; p = next(line.at(-1))) {
      if (key(p) === key(line[0])) { closed = true; break; }
      line.push(p);
    }
    if (!closed) for (let p = next(line[0]); p; p = next(line[0])) line.unshift(p);
    out.push(`M${line.map((p) => `${f(p[0])} ${f(p[1])}`).join(' L')}${closed ? ' Z' : ''}`);
  }
  return out.join(' ');
}
