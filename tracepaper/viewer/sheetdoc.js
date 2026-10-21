// A sheet as an SVG document (SPEC.md §2): its tree of named groups and which are hidden,
// its ink layer, sizes, and the colours things on it are drawn in.
import { fmt } from './annotations.js';
import { flatten } from './tree.js';

export const SVG_NS = 'http://www.w3.org/2000/svg';
const INK_NS = 'http://www.inkscape.org/namespaces/inkscape';
const SKETCH_ID = 'tp-sketch';
export const NUMBER = /-?(?:\d+\.?\d*|\.\d+)(?:e[-+]?\d+)?/gi;

export const isLayer = (n) => n.namespaceURI === SVG_NS && n.localName === 'g' && n.getAttributeNS(INK_NS, 'groupmode') === 'layer';
export const layersOf = (parent) => [...parent.children].filter(isLayer); // its top-level layers
export const labelOf = (g) => g.getAttributeNS(INK_NS, 'label') || g.id || 'Layer';

// The nodes of the tree (SPEC.md §2.4): groups with a name. Top-level ones are layers.
export const isNode = (n) => isLayer(n) || (n.namespaceURI === SVG_NS && n.localName === 'g' && n.hasAttributeNS(INK_NS, 'label'));
const NOT_DRAWN = new Set(['defs', 'metadata', 'pattern', 'clipPath', 'mask', 'marker', 'symbol', 'style', 'script']);

// A node's place in the tree under root, e.g. "Dimensions / Overall": how a window remembers it is hidden.
export function pathOf(g, root) {
  const names = [];
  for (let n = g; n && n !== root; n = n.parentNode) if (isNode(n)) names.unshift(labelOf(n));
  return names.join(' / ');
}

// The tree of named groups under parent; unnamed groups in between don't count. A node's
// hidden is whether its own drawing is (tree.js).
export function buildTree(parent, above = '') {
  const nodes = [];
  for (const child of parent.children) {
    if (isNode(child)) {
      const key = above ? `${above} / ${labelOf(child)}` : labelOf(child);
      nodes.push({ key, name: labelOf(child), hidden: isHidden(child), own: drawsItself(child), el: child,
                   children: buildTree(child, key) });
    } else if (!NOT_DRAWN.has(child.localName)) {
      nodes.push(...buildTree(child, above));
    }
  }
  return nodes;
}

// Whether a group draws anything itself, outside the nodes inside it.
const drawsItself = (g) => [...g.children].some((c) => !isNode(c) && !NOT_DRAWN.has(c.localName)
  && (c.localName !== 'g' || drawsItself(c)));

export const allNodes = (root) => flatten(buildTree(root)).map((n) => n.el);

const displayNone = (n) => n.getAttribute('display') === 'none' || n.style?.display === 'none';

// Whether an element of a sheet is hidden: by display="none" on it or around it, or by
// SVG's visibility, which is inherited unless set again. The viewer hides nodes with the
// latter, so that a node inside a hidden one can be shown (tree.js).
export function isHidden(el) {
  for (let n = el; n?.namespaceURI === SVG_NS; n = n.parentNode) if (displayNone(n)) return true;
  for (let n = el; n?.namespaceURI === SVG_NS; n = n.parentNode) {
    const v = n.getAttribute('visibility');
    if (v === 'hidden' || v === 'collapse') return true;
    if (v === 'visible') return false;
  }
  return false;
}

// Hide or show a node with everything inside it: the nodes inside follow it again.
export function setHidden(g, hidden) {
  for (const n of g.querySelectorAll('g')) if (isNode(n)) n.removeAttribute('visibility');
  if (displayNone(g)) {
    g.style.removeProperty('display');
    if (g.getAttribute('display') === 'none') g.removeAttribute('display');
    if (!g.getAttribute('style')) g.removeAttribute('style');
  }
  g.removeAttribute('visibility');
  if (isHidden(g) !== hidden) g.setAttribute('visibility', hidden ? 'hidden' : 'visible');
}

// Nodes that start hidden (display="none", SPEC.md §2.4) hidden by visibility instead, which
// the nodes inside them can override.
export function hideByVisibility(root) {
  for (const g of allNodes(root)) {
    if (!displayNone(g)) continue;
    g.style.removeProperty('display');
    if (g.getAttribute('display') === 'none') g.removeAttribute('display');
    if (!g.getAttribute('style')) g.removeAttribute('style');
    g.setAttribute('visibility', 'hidden');
  }
}

export function parseSvg(text) {
  const doc = new DOMParser().parseFromString(text, 'image/svg+xml');
  const root = doc.documentElement;
  if (root.namespaceURI !== SVG_NS || root.localName !== 'svg') throw new Error('not a well-formed SVG file');
  for (const s of root.querySelectorAll('script')) s.remove();
  for (const el of [root, ...root.querySelectorAll('*')]) {
    for (const a of [...el.attributes]) if (/^on/i.test(a.localName)) el.removeAttributeNode(a);
  }
  return document.importNode(root, true);
}

export const inkLayerOf = (root) => layersOf(root).find((g) => g.id === SKETCH_ID || labelOf(g) === 'Sketch') ?? null;

export function ensureInkLayer(root) {
  let ink = inkLayerOf(root);
  if (!ink) {
    ink = document.createElementNS(SVG_NS, 'g');
    ink.setAttribute('id', SKETCH_ID);
    ink.setAttributeNS(INK_NS, 'inkscape:groupmode', 'layer');
    ink.setAttributeNS(INK_NS, 'inkscape:label', 'Sketch');
    ink.setAttribute('transform', 'scale(1,-1)');
  }
  root.appendChild(ink); // always on top
  return ink;
}

// The font size an annotation is drawn with, inherited up to the sheet's root.
export function fontSizeOf(el, root) {
  for (let n = el; n && n.nodeType === 1; n = n.parentNode) {
    const v = parseFloat(n.getAttribute('font-size'));
    if (v > 0) return v;
    if (n === root) break;
  }
  return 3.5;
}

const SHAPES = new Set(['path', 'line', 'polyline', 'polygon', 'circle', 'ellipse', 'rect', 'text', 'tspan']);
// The point itself first, then rings out to 6 px, so thin lines are easy to hit.
const PICK_OFFSETS = [[0, 0], ...[2, 4, 6].flatMap((r) => Array.from({ length: 8 },
  (_, i) => [r * Math.cos(i * Math.PI / 4), r * Math.sin(i * Math.PI / 4)]))];

function toHex(css) {
  const m = /^rgba?\(\s*([\d.]+)[\s,]+([\d.]+)[\s,]+([\d.]+)(?:[\s,/]+([\d.]+)(%?))?\s*\)$/.exec(css);
  if (!m || (m[4] !== undefined && +m[4] === 0)) return null; // not a plain colour, or transparent
  return `#${[m[1], m[2], m[3]].map((v) => Math.round(+v).toString(16).padStart(2, '0')).join('')}`;
}

// The colour an element is drawn in: text by its fill, other shapes by their outline, else their fill.
function paintOf(el) {
  const cs = getComputedStyle(el);
  const paints = el.localName === 'text' || el.localName === 'tspan' ? [cs.fill] : [cs.stroke, cs.fill];
  for (const p of paints) {
    const hex = toHex(p);
    if (hex) return hex;
  }
  return null;
}

// The visible shape of the sheet root at a point of the window, and its colour: {el,
// color, from}, where from names the nearest element with an id (the shape, its
// dimension, or its layer) and the layer.
export function colourAt(root, clientX, clientY) {
  for (const [dx, dy] of PICK_OFFSETS) {
    for (const el of document.elementsFromPoint(clientX + dx, clientY + dy)) {
      if (!SHAPES.has(el.localName) || !root.contains(el)) continue;
      const color = paintOf(el);
      if (!color) continue; // e.g. the hatching's pattern fill: look further down
      const named = el.closest('[id]');
      const id = named && named !== root && root.contains(named) ? named.id : null;
      const layer = allNodes(root).filter((g) => g.contains(el)).pop(); // the innermost
      return { el, color, from: { id, layer: layer ? pathOf(layer, root) : null } };
    }
  }
  return null;
}

// The box the strokes in the ink layer cover, in its coordinates. Their width is on screen,
// not in these: growViewBox's margin leaves room for it.
export function inkBounds(ink) {
  let x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity;
  for (const p of ink.querySelectorAll('path')) {
    const n = (p.getAttribute('d') ?? '').match(NUMBER) ?? [];
    for (let i = 0; i + 1 < n.length; i += 2) {
      const x = +n[i], y = +n[i + 1];
      x0 = Math.min(x0, x); x1 = Math.max(x1, x); y0 = Math.min(y0, y); y1 = Math.max(y1, y);
    }
  }
  return x0 <= x1 ? { x0, y0, x1, y1 } : null;
}

function lengthMm(s) {
  const m = /^\s*([\d.]+)\s*(mm|cm|in|pt|px)?\s*$/.exec(s ?? '');
  if (!m) return NaN;
  const per = { mm: 1, cm: 10, in: 25.4, pt: 25.4 / 72, px: 25.4 / 96 };
  return +m[1] * per[m[2] ?? 'px'];
}

// Enlarge the saved sheet so ink drawn past its edge isn't cut off.
export function growViewBox(svg, b) {
  const vb = (svg.getAttribute('viewBox') ?? '').trim().split(/[\s,]+/).map(Number);
  if (!b || vb.length !== 4 || !vb.every(Number.isFinite)) return;
  const [vx, vy, vw, vh] = vb, m = 2;
  const x0 = Math.min(vx, Math.floor(b.x0 - m)), y0 = Math.min(vy, Math.floor(-b.y1 - m));
  const x1 = Math.max(vx + vw, Math.ceil(b.x1 + m)), y1 = Math.max(vy + vh, Math.ceil(-b.y0 + m));
  if (x0 === vx && y0 === vy && x1 === vx + vw && y1 === vy + vh) return;
  const mmPerUnit = lengthMm(svg.getAttribute('width')) / vw || 1;
  svg.setAttribute('viewBox', `${x0} ${y0} ${x1 - x0} ${y1 - y0}`);
  svg.setAttribute('width', `${fmt((x1 - x0) * mmPerUnit)}mm`);
  svg.setAttribute('height', `${fmt((y1 - y0) * mmPerUnit)}mm`);
}
