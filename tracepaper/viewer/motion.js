// A scene's motion (SPEC.md §2.13): its sliders, and its nodes turned and slid by formulas of
// them, worked out here, live, as the person moves a slider. tracepaper/motion.py reads and
// works out the same language the same way, for the exact drawings: tests/formulas.json
// holds cases both must pass. Nothing here checks the result: parts go where the formulas
// put them, through each other or apart.
//
// Matrices are 4 × 4, row by row, in CAD coordinates (mm, z up), as three.js's Matrix4.set
// takes them: [m00, m01, m02, m03, m10, ...].

const DEG = Math.PI / 180;
const TOKEN = /\s*(?:((?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?)|([A-Za-z_][A-Za-z0-9_]*)|(\S))/y;

// NaN where there is no value, as Math gives it; angles in degrees, in and out.
export const FUNCTIONS = {
  sin: [1, 1, (x) => Math.sin(x * DEG)],
  cos: [1, 1, (x) => Math.cos(x * DEG)],
  tan: [1, 1, (x) => Math.tan(x * DEG)],
  asin: [1, 1, (x) => Math.asin(x) / DEG],
  acos: [1, 1, (x) => Math.acos(x) / DEG],
  atan: [1, 1, (x) => Math.atan(x) / DEG],
  atan2: [2, 2, (y, x) => Math.atan2(y, x) / DEG],
  sqrt: [1, 1, Math.sqrt],
  abs: [1, 1, Math.abs],
  min: [1, null, Math.min],
  max: [1, null, Math.max],
  clamp: [3, 3, (x, lo, hi) => (Number.isNaN(x) ? NaN : Math.min(Math.max(x, lo), hi))],
  floor: [1, 1, Math.floor],
  ceil: [1, 1, Math.ceil],
  round: [1, 1, (x) => Math.floor(x + 0.5)], // halves up, as motion.py has it too
  exp: [1, 1, Math.exp],
  ln: [1, 1, Math.log],
};
const CONSTANTS = { pi: Math.PI };

const num = (v) => String(Number(v.toPrecision(10)));

// A formula as a tree: ['n', number], ['v', name], ['neg', a], [op, a, b] for + - * / ^,
// ['f', name, [args]]. A number is a formula of itself. Throws an Error saying what is wrong
// and where, counting characters from 1.
export function parse(text) {
  if (typeof text === 'number') return ['n', text];
  if (typeof text !== 'string') throw new Error('must be a formula, such as "opening / 2", or a number');
  const tokens = [];
  TOKEN.lastIndex = 0;
  while (TOKEN.lastIndex < text.length) {
    const start = TOKEN.lastIndex, m = TOKEN.exec(text);
    if (!m) break; // only white space is left
    const at = start + m[0].length - m[0].trimStart().length + 1;
    if (m[1]) tokens.push(['num', Number(m[1]), at]);
    else if (m[2]) tokens.push(['name', m[2], at]);
    else tokens.push(['op', m[3], at]);
  }
  tokens.push(['end', null, text.length + 1]);
  let i = 0;
  const peek = () => tokens[i];
  const fail = (wanted) => {
    const [kind, value, at] = peek();
    const found = kind === 'end' ? 'the end' : `"${kind === 'num' ? num(value) : value}"`;
    throw new Error(wanted ? `expected ${wanted} at ${at}, found ${found}` : `unexpected ${found} at ${at}`);
  };
  const op = (...ops) => {
    const [kind, value] = peek();
    if (kind === 'op' && ops.includes(value)) { i++; return value; }
    return null;
  };
  const sum = () => {
    let a = product(), o;
    while ((o = op('+', '-')) !== null) a = [o, a, product()];
    return a;
  };
  const product = () => {
    let a = unary(), o;
    while ((o = op('*', '/')) !== null) a = [o, a, unary()];
    return a;
  };
  const unary = () => {
    const o = op('-', '+');
    if (o !== null) {
      const a = unary();
      return o === '-' ? ['neg', a] : a;
    }
    return power();
  };
  const power = () => {
    const a = atom();
    return op('^') !== null ? ['^', a, unary()] : a; // from the right: 2^3^2 is 2^9
  };
  const atom = () => {
    const [kind, value, at] = peek();
    if (kind === 'num') { i++; return ['n', value]; }
    if (kind === 'name') {
      i++;
      if (op('(') === null) {
        if (value in FUNCTIONS) throw new Error(`${value} at ${at} is a function: ${value}(...)`);
        return value in CONSTANTS ? ['n', CONSTANTS[value]] : ['v', value];
      }
      if (!(value in FUNCTIONS)) {
        throw new Error(`unknown function "${value}" at ${at}; there are ${Object.keys(FUNCTIONS).join(', ')}`);
      }
      const args = [];
      if (op(')') === null) {
        args.push(sum());
        while (op(',') !== null) args.push(sum());
        if (op(')') === null) fail('"," or ")"');
      }
      const [least, most] = FUNCTIONS[value];
      if (args.length < least || (most !== null && args.length > most)) {
        const count = least === most ? `${least}` : `at least ${least}`;
        throw new Error(`${value} at ${at} takes ${count} argument${least === 1 && most === 1 ? '' : 's'}, not ${args.length}`);
      }
      return ['f', value, args];
    }
    if (kind === 'op' && value === '(') {
      i++;
      const a = sum();
      if (op(')') === null) fail('")"');
      return a;
    }
    return fail('a number, a name or "("');
  };
  const tree = sum();
  if (peek()[0] !== 'end') fail();
  return tree;
}

// The names a formula uses.
export function names(tree, out = new Set()) {
  if (tree[0] === 'v') out.add(tree[1]);
  else if (tree[0] === 'f') tree[2].forEach((a) => names(a, out));
  else tree.slice(1).forEach((a) => Array.isArray(a) && names(a, out));
  return out;
}

// A formula's value for values {name: number}: NaN or infinite where it has none.
export function evaluate(tree, values) {
  switch (tree[0]) {
    case 'n': return tree[1];
    case 'v': return Number(values[tree[1]]);
    case 'neg': return -evaluate(tree[1], values);
    case 'f': return FUNCTIONS[tree[1]][2](...tree[2].map((a) => evaluate(a, values)));
    default: {
      const a = evaluate(tree[1], values), b = evaluate(tree[2], values);
      switch (tree[0]) {
        case '+': return a + b;
        case '-': return a - b;
        case '*': return a * b;
        case '/': return a / b;
        default: return a ** b;
      }
    }
  }
}

// ---- Matrices -------------------------------------------------------------------------------

export const IDENTITY = Object.freeze([1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1]);

// a · b: b first, then a.
export function multiply(a, b) {
  const out = new Array(16).fill(0);
  for (let r = 0; r < 4; r++) for (let c = 0; c < 4; c++) {
    let s = 0;
    for (let k = 0; k < 4; k++) s += a[4 * r + k] * b[4 * k + c];
    out[4 * r + c] = s;
  }
  return out;
}

const unit = (v) => {
  const n = Math.hypot(...v);
  return v.map((c) => c / n);
};

// A turn by degrees about the axis through a point, counter-clockwise looking against the axis.
export function turn(axis, through, degrees) {
  const [x, y, z] = unit(axis), a = degrees * DEG, c = Math.cos(a), s = Math.sin(a), t = 1 - c;
  const r = [t * x * x + c, t * x * y - s * z, t * x * z + s * y,
             t * x * y + s * z, t * y * y + c, t * y * z - s * x,
             t * x * z - s * y, t * y * z + s * x, t * z * z + c];
  const [px, py, pz] = through;
  const move = (i) => through[i] - (r[3 * i] * px + r[3 * i + 1] * py + r[3 * i + 2] * pz);
  return [r[0], r[1], r[2], move(0), r[3], r[4], r[5], move(1), r[6], r[7], r[8], move(2), 0, 0, 0, 1];
}

export function slide(along, mm) {
  const [x, y, z] = unit(along);
  return [1, 0, 0, x * mm, 0, 1, 0, y * mm, 0, 0, 1, z * mm, 0, 0, 0, 1];
}

export const apply = (m, [x, y, z]) => [0, 1, 2].map((r) => m[4 * r] * x + m[4 * r + 1] * y + m[4 * r + 2] * z + m[4 * r + 3]);

// ---- A scene's motion -----------------------------------------------------------------------

export class Motion {
  // info: as /api/motion gives it: {sliders: [{name, unit, range, start}], define: [[name,
  // formula]], moves: [{node, keys, turn: {axis, through, by}} or {node, keys, slide: {along, by}}]}.
  constructor(info) {
    this.sliders = info.sliders ?? [];
    this.define = (info.define ?? []).map(([name, text]) => [name, parse(text)]);
    this.moves = (info.moves ?? []).map((m) => ({ ...m, formula: parse((m.turn ?? m.slide).by) }));
    // The nodes that move, with every node inside them: a key, or the start of one ("Sash / ").
    this.keys = [...new Set(this.moves.flatMap((m) => m.keys))];
  }

  get on() { return this.sliders.length > 0 || this.moves.length > 0; }

  start() { return Object.fromEntries(this.sliders.map((s) => [s.name, s.start])); }

  // The sliders at given, else where they start, and the definitions there.
  values(given = {}) {
    const out = Object.fromEntries(this.sliders.map((s) => [s.name, given[s.name] ?? s.start]));
    for (const [name, tree] of this.define) out[name] = evaluate(tree, out);
    return out;
  }

  // The sliders' positions as text, "opening=30 handle=0", as tp:view's pose has them.
  label(given = {}) {
    const values = this.values(given);
    return this.sliders.map((s) => `${s.name}=${num(values[s.name])}`).join(' ');
  }

  // Each moved node's own move at given, Map key -> matrix: its moves one after another, in
  // the scene's coordinates at rest. A move whose formula has no value there is left out,
  // and named in skipped.
  own(given = {}) {
    const values = this.values(given), out = new Map(), skipped = [];
    this.moves.forEach((m, i) => {
      const v = evaluate(m.formula, values);
      if (!Number.isFinite(v)) { skipped.push(i); return; }
      const t = m.turn ? turn(m.turn.axis, m.turn.through ?? [0, 0, 0], v) : slide(m.slide.along, v);
      for (const key of m.keys) out.set(key, multiply(t, out.get(key) ?? IDENTITY));
    });
    out.skipped = skipped;
    return out;
  }

  // Where a node is, against where it rests: the moves of the nodes around it, outermost
  // last, after its own. own: as own() gives it.
  static world(own, key) {
    const parts = key.split(' / ');
    let m = IDENTITY;
    for (let k = 1; k <= parts.length; k++) {
      const t = own.get(parts.slice(0, k).join(' / '));
      if (t) m = multiply(m, t);
    }
    return m;
  }

  // Whether the node moves with any slider: it or a node around it has a move.
  moved(key) {
    return this.keys.some((k) => key === k || key.startsWith(`${k} / `));
  }
}
