// Tracepaper's 3D view: a .glb model, such as a CadQuery assembly's export, in its
// colours and with the edges between its faces, or a slicer's project (.3mf), which the
// server turns into one. Rotate, pan and zoom; it reloads when the file changes and keeps
// the camera. Sketch over it with the mouse or a tablet's pen; Save writes
// <model>.glb.sketch.svg (SPEC.md §3.1).
import * as THREE from 'three';
import { GLTFLoader } from 'three/addons/loaders/GLTFLoader.js';
import { mergeGeometries } from 'three/addons/utils/BufferGeometryUtils.js';
import { TP_NS, fmt } from './annotations.js';
import { CadControls } from './controls.js';
import { Edges } from './edges.js';
import { INK_COLORS, Ink, PenInput, coalesced, swatch } from './ink.js';
import { $, CLIENT, fetchOk, listViews, panelDivider, renderViewsMenu, toaster, viewUrl } from './page.js';
import { ScreenFeed, Tablet } from './pen.js';
import { renderBlank } from './screen.js';
import { SVG_NS, growViewBox, inkBounds, inkLayerOf, parseSvg } from './sheetdoc.js';
import { Slices } from './slices.js';
import { flatten, hiddenAndShown, renderTree } from './tree.js';

const POLL_MS = 1000;
// Drawn at this many times the screen's resolution and scaled down, which smooths edges and
// outlines; less on screens that are sharp already.
const SUPERSAMPLE = window.devicePixelRatio >= 2 ? 1.5 : 2;
const EDGE_ANGLE = 25; // degrees: within a face, triangles meeting at less than this get no edge line
// Directions to look from and which way is up on screen, in CAD coordinates (z up). Fronts face -y.
const LOOKS = {
  iso: { dir: [1, -1, 1], up: [0, 0, 1] },
  top: { dir: [0, 0, 1], up: [0, 1, 0] },
  front: { dir: [0, -1, 0], up: [0, 0, 1] },
  right: { dir: [1, 0, 0], up: [0, 0, 1] },
};
const ui = {
  canvas: $('tpv-canvas'), view: $('tpv-view'), status: $('tpv-status'), empty: $('tpv-empty'),
  toast: $('tpv-toast'), axes: $('tpv-axes'), edges: $('tpv-edges'), slices: $('tpv-slices'), pen: $('tpv-pen'),
  penButton: $('tpv-pen-button'), penCursor: $('tpv-pen-cursor'), fit: $('tpv-fit'), tree: $('tpv-layers'),
  bar: $('tpv-bar'), ink: $('tpv-ink'), inkLayer: $('tp-sketch'), sketch: $('tpv-sketch'), swatches: $('tpv-swatches'),
  undo: $('tpv-undo'), clear: $('tpv-clear'), save: $('tpv-save'), hint: $('tpv-hint'),
  transparent: $('tpv-transparent'),
};
const viewName = decodeURIComponent(location.pathname.slice('/view/'.length));
const toast = toaster(ui.toast);

// CAD's axes, drawn in the bottom left as the camera sees them.
const AXES = [['x', [1, 0, 0], '#dc2626'], ['y', [0, 1, 0], '#16a34a'], ['z', [0, 0, 1], '#2563eb']];
const AXIS_PX = 30;

// glTF is y up; CAD files exported from z-up CAD carry a turn into it on their root,
// so a CAD direction (x, y, z) is (x, z, -y) in the scene.
const toScene = ([x, y, z]) => new THREE.Vector3(x, z, -y);
const fromScene = (v) => [v.x, -v.z, v.y];

// Without WebGL (switched off, or no working GPU process) there is nothing to draw with:
// say so, and keep the rest of the page working.
let renderer = null;
try {
  renderer = new THREE.WebGLRenderer({ canvas: ui.canvas, antialias: true, stencil: true }); // stencil: slice caps
  renderer.localClippingEnabled = true;
  renderer.setPixelRatio(window.devicePixelRatio * SUPERSAMPLE);
} catch (err) {
  ui.empty.textContent = `This browser can't draw 3D right now: ${err.message} `
    + '(WebGL is off or unavailable; see about:support in Firefox, brave://gpu or chrome://gpu otherwise).';
}
const scene = new THREE.Scene();
scene.background = new THREE.Color(0xf4f3ef);
scene.add(new THREE.HemisphereLight(0xffffff, 0x9a968c, 1.6));
const camera = new THREE.OrthographicCamera(-1, 1, 1, -1, 0.1, 1000);
const headlight = new THREE.DirectionalLight(0xffffff, 1.8);
headlight.position.set(0.5, 1, 2); // follows the camera, so the side facing you is always lit
camera.add(headlight);
scene.add(camera);
// Rotate, pan and zoom as in the OCP viewer.
const controls = new CadControls(camera, ui.canvas);
controls.addEventListener('change', render);

const edges = renderer && new Edges(renderer, camera);
const slices = new Slices();
const state = {
  model: null, mtime: null, half: 1, loads: 0,
  tree: [], expanded: new Set(),
  choices: new Map(), // key -> hidden, what this window's person switched; kept when the model reloads
  message: { text: '', error: false }, // how loading went
  // Sketching: drawing with the mouse, in color; the undo list; whether the ink is unsaved;
  // the sketch file's time, while there is one; gen counts saves, which polls in flight ignore.
  drawing: false, color: INK_COLORS[0][1], undo: [], dirty: false, sketchMtime: null, gen: 0, saving: false,
  transparent: false, // the transparent view
};
let resolveReady;
const ready = new Promise((resolve) => { resolveReady = resolve; });

function render() {
  if (renderer) {
    if (slices.on) edges.pass(scene); // for the outline of the cut
    renderer.render(scene, camera);
    slices.outline(renderer, edges);
  }
  renderAxes();
  placeInk();
}

// Each axis as the camera sees it, the ones pointing away drawn first. Pointing at the
// viewer or away, an axis shrinks to its label.
function renderAxes() {
  const toView = camera.quaternion.clone().invert();
  const axes = AXES.map(([name, dir, color]) => {
    const v = toScene(dir).applyQuaternion(toView); // x right, y up, z towards the viewer
    return { name, color, x: v.x * AXIS_PX, y: -v.y * AXIS_PX, z: v.z };
  }).sort((a, b) => a.z - b.z);
  const f = (n) => n.toFixed(1);
  ui.axes.innerHTML = '<circle r="2.5" fill="#57606a"/>' + axes.map(({ name, color, x, y }) => (
    `<line data-axis="${name}" x1="0" y1="0" x2="${f(x)}" y2="${f(y)}" stroke="${color}"/>`
    + `<text x="${f(x * 1.3)}" y="${f(y * 1.3)}" fill="${color}" text-anchor="middle" dominant-baseline="central">`
    + `${name.toUpperCase()}</text>`)).join('');
}

function resize() {
  const w = ui.canvas.clientWidth, h = ui.canvas.clientHeight;
  if (!w || !h) return;
  renderer?.setSize(w, h, false);
  edges?.resize();
  const aspect = w / h, half = state.half;
  Object.assign(camera, aspect >= 1
    ? { left: -half * aspect, right: half * aspect, top: half, bottom: -half }
    : { left: -half, right: half, top: half / aspect, bottom: -half / aspect });
  camera.updateProjectionMatrix();
  render();
}

// Look at the whole model (what is shown of it) from one of LOOKS, or another {dir, up};
// without one, keep the current direction and roll. Turning the view with ink on it asks
// first (mayTurn).
function frame(look) {
  if (!state.model || (look && turns(look) && !mayTurn())) return;
  const box = new THREE.Box3();
  state.model.updateMatrixWorld(true);
  state.model.traverseVisible((o) => {
    if (!o.isMesh || !o.layers.isEnabled(0) || o.userData.slice || !o.geometry.attributes.position) return;
    o.geometry.computeBoundingBox(); // its own, without its children: a part holds its slice planes' meshes
    box.union(o.geometry.boundingBox.clone().applyMatrix4(o.matrixWorld));
  });
  if (box.isEmpty()) box.setFromObject(state.model);
  const sphere = box.getBoundingSphere(new THREE.Sphere());
  if (!(sphere.radius > 0)) return;
  const dir = look ? toScene(look.dir).normalize() : camera.position.clone().sub(controls.target).normalize();
  controls.target.copy(sphere.center);
  camera.position.copy(sphere.center).addScaledVector(dir, sphere.radius * 4);
  if (look) { // upright again; the controls turn the camera without regard to its up
    camera.up.copy(toScene(look.up).normalize());
    camera.lookAt(sphere.center);
  }
  camera.near = sphere.radius * 0.01;
  camera.far = sphere.radius * 10;
  camera.zoom = 1;
  state.half = sphere.radius * 1.08;
  resize();
}

// Each CAD face is a mesh of its own in the file. They are drawn merged instead, one mesh
// per part and colour, which takes far fewer draw calls: a part is the nearest object the
// tree can hide (a named node), and the tree hides whole parts. Each face's edges
// (edges.js) are worked out before. Gives the parts, [{part, meshes}].
function prepare(gltf) {
  const root = gltf.scene, json = gltf.parser.json.nodes ?? [];
  const named = (o) => !!json[gltf.parser.associations.get(o)?.nodes]?.name;
  root.updateMatrixWorld(true);
  const parts = new Map();
  root.traverse((o) => {
    if (!o.isMesh) return;
    let part = o;
    while (part !== root && !named(part)) part = part.parent;
    parts.set(part, [...(parts.get(part) ?? []), o]);
  });
  const prepared = [];
  for (const [part, meshes] of parts) {
    const drawn = [];
    const toPart = part.matrixWorld.clone().invert();
    const faces = meshes.map((mesh) => ({
      geometry: mesh.geometry.clone().applyMatrix4(toPart.clone().multiply(mesh.matrixWorld)), material: mesh.material,
    }));
    // What the part draws itself, apart from the parts inside it: the tree hides it on its own.
    const own = new THREE.Group();
    part.add(own);
    part.userData.own = own;
    edges?.add(own, faces, EDGE_ANGLE);
    const materials = [...new Set(faces.map((f) => f.material))];
    for (const material of materials) {
      // CAD colours, not metal: the glTF default would be fully metallic.
      Object.assign(material, { metalness: 0, roughness: 0.7, side: THREE.DoubleSide,
                                polygonOffset: true, polygonOffsetFactor: 1, polygonOffsetUnits: 1 });
      material.userData.base = { transparent: material.transparent, opacity: material.opacity,
                                 depthWrite: material.depthWrite }; // as in the file, for the transparent view
      const geometries = faces.filter((f) => f.material === material).map((f) => f.geometry);
      const merged = mergeGeometries(geometries); // null if they don't fit together: then one by one
      for (const geometry of merged ? [merged] : geometries) {
        const mesh = new THREE.Mesh(geometry, material);
        own.add(mesh);
        drawn.push(mesh);
      }
    }
    own.userData.materials = materials;
    for (const mesh of meshes) {
      mesh.geometry.dispose();
      if (mesh === part) mesh.geometry = new THREE.BufferGeometry(); // the tree holds on to it
      else mesh.removeFromParent();
    }
    prepared.push({ part: own, meshes: drawn });
  }
  return prepared;
}

// The tree of the model's named glTF nodes (SPEC.md §2.8). Faces and unnamed nodes don't
// count; a single top node (the assembly itself) is left out, so its parts are the top level.
function buildTree(gltf) {
  const json = gltf.parser.json.nodes ?? [];
  const nodeOf = (o) => json[gltf.parser.associations.get(o)?.nodes];
  // A node hidden in the file starts hidden with everything inside it. A part drawn in one
  // colour, with no parts inside it, shows that colour.
  const under = (obj, above, hiddenAbove) => obj.children.flatMap((child) => {
    const def = nodeOf(child);
    if (!def?.name) return under(child, above, hiddenAbove);
    const key = above ? `${above} / ${def.name}` : def.name;
    const hidden = hiddenAbove || !!def.extras?.tracepaper?.hidden, own = child.userData.own;
    const children = under(child, key, hidden);
    const colors = new Set(own?.userData.materials.map((m) => `#${m.color.getHexString()}`));
    const color = !children.length && colors.size === 1 ? [...colors][0] : null;
    return [{ key, name: def.name, hidden, own: !!own, color, obj: child, children }];
  });
  const top = under(gltf.scene, '', false);
  if (top.length !== 1 || !top[0].children.length) return top;
  const rekey = (n, above) => {
    const key = above ? `${above} / ${n.name}` : n.name;
    return { ...n, key, children: n.children.map((c) => rekey(c, key)) };
  };
  return top[0].children.map((c) => rekey(c, ''));
}

// Hide what the file or this window's person hid: each part's own drawing, so that the parts
// inside a hidden one can still show (tree.js).
function applyTree() {
  for (const node of flatten(state.tree)) {
    if (state.choices.has(node.key)) node.hidden = state.choices.get(node.key);
    node.obj.visible = true;
    if (node.obj.userData.own) node.obj.userData.own.visible = !node.hidden;
  }
  renderPanel();
}

function renderPanel() {
  renderTree(ui.tree, state.tree, {
    expanded: state.expanded,
    onChange: (changes) => {
      for (const [node, hidden] of changes) {
        for (const n of flatten([node])) {
          n.hidden = hidden;
          state.choices.set(n.key, hidden);
        }
      }
      applyTree();
      render();
    },
  });
}

// ---- Slice planes along CAD's axes (slices.js), set in the panel

// A section only, through one plane, is seen straight on, as a 2D section would be.
const FACING = { x: LOOKS.right, y: LOOKS.front, z: LOOKS.top };
function faceSection() {
  const on = slices.planes.filter((p) => p.on);
  if (slices.sectionOnly && on.length === 1 && ink.empty) frame(FACING[on[0].axis]); // ink: stay
  else render();
}

function buildSliceControls() {
  const colors = Object.fromEntries(AXES.map(([name, , color]) => [name, color]));
  const rows = slices.planes.map((p) => {
    const row = document.createElement('div');
    row.className = 'tpv-slice';
    row.dataset.axis = p.axis;
    row.style.setProperty('--axis', colors[p.axis]);
    row.innerHTML = `<label title="Slice along ${p.axis.toUpperCase()}"><input type="checkbox"> ${p.axis.toUpperCase()}</label>`
      + '<input type="range"><input type="number" step="any">'
      + '<button class="tpv-flip" aria-pressed="false" title="Cut away the other side">⇄</button>';
    const on = row.querySelector('input[type="checkbox"]'), range = row.querySelector('input[type="range"]');
    const number = row.querySelector('input[type="number"]'), flip = row.querySelector('button');
    range.setAttribute('aria-label', `Slice along ${p.axis.toUpperCase()}, in mm`);
    number.setAttribute('aria-label', `Where to slice along ${p.axis.toUpperCase()}, in mm`);
    number.title = 'Where the plane is, in mm: type a position';
    // A plane just switched on is faced in a section only.
    const changed = (wasOn) => {
      slices.update();
      if (p.on && !wasOn) faceSection();
      else render();
    };
    const move = (at) => {
      const wasOn = p.on;
      Object.assign(p, { at, on: true });
      on.checked = true;
      changed(wasOn);
    };
    on.addEventListener('change', () => { const wasOn = p.on; p.on = on.checked; changed(wasOn); });
    range.addEventListener('input', () => { number.value = Number(range.value).toFixed(1); move(Number(range.value)); });
    number.addEventListener('change', () => {
      const at = number.value.trim() === '' ? NaN : Number(number.value);
      if (!Number.isFinite(at)) { number.value = p.at.toFixed(1); return; }
      range.value = at;
      move(at);
    });
    flip.addEventListener('click', () => { p.flip = !p.flip; flip.setAttribute('aria-pressed', String(p.flip)); changed(p.on); });
    p.ui = { range, number };
    return row;
  });
  const option = (text, title, set) => {
    const label = document.createElement('label');
    label.className = 'tpv-slice-option';
    label.title = title;
    label.innerHTML = `<input type="checkbox"> ${text}`;
    label.querySelector('input').addEventListener('change', (e) => set(e.target.checked));
    return label;
  };
  const corner = option('Cut a corner', 'Cut away only what lies beyond every plane that is on, as in a cutaway', (cut) => {
    slices.corner = cut;
    slices.update();
    render();
  });
  const section = option('Section only', 'Show only where the planes cut the model, seen straight on when there is one',
    (only) => {
      slices.sectionOnly = only;
      slices.update();
      if (only) faceSection();
      else render();
    });
  section.dataset.option = 'section';
  corner.dataset.option = 'corner';
  ui.slices.replaceChildren(...rows, section, corner);
}

// The sliders span the model along CAD's axes. A plane starts in the middle, and keeps its
// place when the model is rewritten, if that is still in it.
function setSliceRanges(box) {
  const span = { x: [box.min.x, box.max.x], y: [-box.max.z, -box.min.z], z: [box.min.y, box.max.y] };
  for (const p of slices.planes) {
    const [lo, hi] = span[p.axis];
    Object.assign(p.ui.range, { min: lo, max: hi, step: (hi - lo) / 1000 || 1 });
    if (!state.sliced || !(p.at >= lo && p.at <= hi)) p.at = (lo + hi) / 2;
    p.ui.range.value = p.at;
    p.ui.number.value = p.at.toFixed(1);
  }
  state.sliced = true;
  slices.update();
}

// The CAD edges on or off, in this window.
// The transparent view, as in the OCP viewer: every face see-through, so that what is inside
// or behind shows, with its edges.
const SEE_THROUGH = 0.5; // of each face's opacity in the file

function setTransparent(on) {
  state.transparent = on;
  const materials = new Set();
  state.model?.traverse((o) => o.userData.materials?.forEach((m) => materials.add(m)));
  for (const m of materials) {
    const base = m.userData.base;
    Object.assign(m, on ? { transparent: true, opacity: base.opacity * SEE_THROUGH, depthWrite: false } : base);
    m.needsUpdate = true;
  }
  ui.transparent.setAttribute('aria-pressed', String(on));
  render();
}

function showEdges(shown) {
  edges?.show(shown);
  ui.edges.setAttribute('aria-pressed', String(shown));
  render();
}

function dispose(root) {
  root.traverse((o) => {
    o.geometry?.dispose();
    if (o.material && !edges?.shared(o.material)) [o.material].flat().forEach((m) => m.dispose());
  });
}

async function load(mtime) {
  const r = await fetch(`/api/file/${encodeURIComponent(viewName)}`, { cache: 'no-store' });
  if (!r.ok) throw new Error(`${r.status} ${await r.text()}`);
  const gltf = await new GLTFLoader().parseAsync(await r.arrayBuffer(), '');
  const parts = prepare(gltf);
  const box = new THREE.Box3();
  gltf.scene.updateMatrixWorld(true);
  for (const { meshes } of parts) meshes.forEach((m) => box.expandByObject(m));
  slices.setModel(parts, edges?.materials ?? [], box);
  setSliceRanges(box);
  const first = !state.model;
  if (state.model) {
    scene.remove(state.model);
    dispose(state.model);
  }
  state.model = gltf.scene;
  scene.add(state.model);
  state.tree = buildTree(gltf);
  applyTree();
  setTransparent(state.transparent);
  state.mtime = mtime;
  state.loads += 1;
  ui.empty.hidden = true;
  if (first) frame(LOOKS.iso);
  else render();
  setStatus(`Updated ${new Date().toLocaleTimeString()}`);
  if (first) resolveReady();
}

function setStatus(text, error = false) {
  state.message = { text, error };
  updateStatus();
}

// The toolbar's status (how loading went, else whether the ink is saved), which of its
// buttons work, and what the mouse does.
function updateStatus() {
  const { text, error } = state.message;
  const [shown, cls] = error ? [text, 'dirty'] : state.dirty ? ['Unsaved changes', 'dirty']
    : state.sketchMtime ? [`Saved ${sketchFile}, ready for the AI`, 'pending'] : [text, ''];
  ui.status.textContent = ui.status.title = shown; // the title: for when it is cut short
  ui.status.className = `tpv-status ${cls}`;
  ui.undo.disabled = !state.undo.length;
  ui.clear.disabled = ink.empty;
  ui.save.disabled = !state.model;
  ui.hint.textContent = state.drawing ? 'Drag to draw · right-drag to pan · scroll to zoom'
    : ink.empty ? 'Drag to rotate · right-drag to pan · scroll to zoom'
    : 'Turning the view wipes the ink · right-drag to pan · scroll to zoom';
}

async function poll() {
  const gen = state.gen;
  try {
    const listing = await listViews();
    renderViewsMenu(ui.view, listing.views, viewName);
    const me = listing.views.find((v) => v.name === viewName);
    if (!me) setStatus(`${viewName} is gone`, true);
    else {
      if (me.mtime !== state.mtime) {
        const first = !state.model;
        try {
          await load(me.mtime);
          if (!first) toast('Model updated');
          else if (me.sketch) await reopenSketch();
        } catch (err) {
          setStatus(`Could not load ${viewName}: ${err.message}`, true); // e.g. half written: tried again
        }
      }
      if (gen === state.gen && !state.saving) followSketch(me.sketch?.mtime ?? null);
    }
  } catch {
    setStatus('Viewer server stopped', true);
  }
  setTimeout(poll, POLL_MS);
}

// ---- Sketching over the view (SPEC.md §3.1)
// The ink is drawn on the plane the camera looks at, in mm with y up, measured from the
// line through the scene's origin along the line of sight. It is in an SVG over the
// canvas whose viewBox follows the camera, so panning and zooming keep it on the model.
// Turning the view would leave it pointing nowhere, so while there is ink the view only
// turns once the person agrees to wipe it.

const sketchFile = `${viewName.split('/').pop()}.sketch.svg`;
const PICTURE_SCALE = 2; // pixels of the saved picture of the view to each of the window's
const LOOK_NAMES = { iso: 'isometric', top: 'from above', front: 'from the front', right: 'from the right' };
const NS = { inkscape: 'http://www.inkscape.org/namespaces/inkscape', tp: TP_NS, xlink: 'http://www.w3.org/1999/xlink' };

const ink = new Ink({
  layer: () => ui.inkLayer, ref: () => ui.inkLayer, undo: () => state.undo,
  changed: () => { state.dirty = true; updateStatus(); },
});
let gesture = null; // a stroke, or erasing, with the mouse or a pen on this computer

// The camera's axes in the scene: right and up on screen, and back towards the viewer.
function viewAxes() {
  const q = camera.quaternion;
  return { right: new THREE.Vector3(1, 0, 0).applyQuaternion(q), up: new THREE.Vector3(0, 1, 0).applyQuaternion(q),
           back: new THREE.Vector3(0, 0, 1).applyQuaternion(q) };
}

// What the view shows of the ink's plane: {x0, y0, x1, y1} in mm, x right and y up.
function viewArea() {
  const { right, up } = viewAxes();
  const x = camera.position.dot(right), y = camera.position.dot(up);
  const w = (camera.right - camera.left) / 2 / camera.zoom, h = (camera.top - camera.bottom) / 2 / camera.zoom;
  return { x0: x - w, y0: y - h, x1: x + w, y1: y + h };
}

function placeInk() {
  const a = viewArea();
  ui.ink.setAttribute('viewBox', `${a.x0} ${-a.y1} ${a.x1 - a.x0} ${a.y1 - a.y0}`);
}

// Pan and zoom so that the view shows area, [x0, y0, x1, y1] on the ink's plane.
function showArea([x0, y0, x1, y1]) {
  const now = viewArea(), { right, up } = viewAxes();
  const shift = right.multiplyScalar((x0 + x1 - now.x0 - now.x1) / 2).addScaledVector(up, (y0 + y1 - now.y0 - now.y1) / 2);
  camera.position.add(shift);
  controls.target.add(shift);
  camera.zoom *= Math.min((now.x1 - now.x0) / (x1 - x0), (now.y1 - now.y0) / (y1 - y0));
  camera.updateProjectionMatrix();
  render();
}

// Whether looking from look would turn the view.
function turns(look) {
  const { up, back } = viewAxes(), dir = toScene(look.dir).normalize();
  const upright = toScene(look.up).projectOnPlane(dir).normalize();
  return dir.dot(back) < 1 - 1e-6 || upright.dot(up) < 1 - 1e-6;
}

// Whether the view may turn now: with ink on it, only once the person agrees to wipe the
// ink. dragging: asked as a drag starts, which the question ends, so the next drag turns.
function mayTurn(dragging = false) {
  if (!ink.empty) {
    const what = state.dirty ? 'wipes your unsaved ink' : `wipes the ink here (${sketchFile} stays for the AI)`;
    if (!confirm(`Turning the view ${what}: it is drawn for this direction.\n\nWipe it and turn?`)) return false;
    wipe();
    if (dragging) return false;
  }
  state.undo = []; // ink brought back now would be on another plane
  updateStatus();
  return true;
}

controls.mayRotate = () => mayTurn(true);

function wipe() {
  ui.inkLayer.replaceChildren();
  Object.assign(state, { undo: [], dirty: false });
  updateStatus();
}

function setDrawing(on) {
  state.drawing = controls.drawing = on;
  ui.sketch.setAttribute('aria-pressed', String(on));
  ui.canvas.style.cursor = on ? 'crosshair' : '';
  updateStatus();
}

function setColor(i) {
  state.color = INK_COLORS[i][1];
  for (const b of ui.swatches.children) b.setAttribute('aria-pressed', String(b.dataset.color === state.color));
}

const beginStroke = (clientX, clientY) => ink.begin(clientX, clientY, state.color);

function undo() {
  const u = state.undo.pop();
  if (u) ink.undoStep(u);
  updateStatus();
}

// The mouse draws while Sketch is on; a pen's eraser end erases whatever the tool. The
// camera controls take the rest.
function onPointerDown(e) {
  if (!state.model || gesture) return;
  if (e.pointerType === 'pen' && (e.button === 5 || e.buttons & 32)) {
    gesture = { type: 'erase', id: e.pointerId, removed: [] };
    ink.eraseAt(e.clientX, e.clientY, gesture.removed);
  } else if (state.drawing && e.button === 0 && !e.shiftKey && e.pointerType !== 'touch') {
    gesture = { type: 'stroke', id: e.pointerId, ...beginStroke(e.clientX, e.clientY) };
  } else return;
  try { ui.canvas.setPointerCapture(e.pointerId); } catch { /* not an active pointer */ }
  e.preventDefault();
}

function onPointerMove(e) {
  if (gesture?.id !== e.pointerId) return;
  if (gesture.type === 'stroke') ink.extend(gesture, coalesced(e));
  else for (const [x, y] of coalesced(e)) ink.eraseAt(x, y, gesture.removed);
}

function onPointerUp(e) {
  if (gesture?.id !== e.pointerId) return;
  if (gesture.type === 'stroke') ink.commit(gesture);
  else ink.finishErase(gesture.removed);
  gesture = null;
}

// A point of the ink's plane in the scene, for placing the sketch in 3D: the section's
// when the view looks straight at one, else the middle of the view.
function inkPlanePoint(back) {
  const on = slices.planes.filter((p) => p.on);
  const plane = slices.sectionOnly && on.length === 1 ? on[0].plane : null;
  if (plane && Math.abs(plane.normal.dot(back)) > 1 - 1e-6) return plane.normal.clone().multiplyScalar(-plane.constant);
  return controls.target.clone();
}

// What the slice planes leave of the model, in words.
function describeSlices() {
  const on = slices.planes.filter((p) => p.on);
  if (!on.length) return null;
  if (slices.sectionOnly) return `section${on.length > 1 ? 's' : ''} at ${on.map((p) => `${p.axis} = ${fmt(p.at, 2)}`).join(', ')}`;
  if (slices.corner) return `cut away: ${on.map((p) => `${p.axis} ${p.flip ? '<' : '>'} ${fmt(p.at, 2)}`).join(' and ')}`;
  return `kept: ${on.map((p) => `${p.axis} ${p.flip ? '≥' : '≤'} ${fmt(p.at, 2)}`).join(', ')}`;
}

function lookName(back) {
  const name = Object.keys(LOOKS).find((k) => toScene(LOOKS[k].dir).normalize().dot(back) > 1 - 1e-6);
  return name ? LOOK_NAMES[name] : `from (${fromScene(back).map((c) => c.toFixed(2)).join(', ')})`;
}

// The view as a sketch file (SPEC.md §3.1): a picture of it on a layer of its own, the ink
// over it, both in mm on the ink's plane, and where that plane is in the model.
function sketchDocument() {
  const a = viewArea(), { right, back } = viewAxes();
  render(); // the canvas keeps no copy of what it shows, so it is taken right after drawing it
  const picture = Object.assign(document.createElement('canvas'), {
    width: Math.round(ui.canvas.clientWidth * PICTURE_SCALE), height: Math.round(ui.canvas.clientHeight * PICTURE_SCALE),
  });
  picture.getContext('2d').drawImage(ui.canvas, 0, 0, picture.width, picture.height);

  const doc = document.implementation.createDocument(SVG_NS, 'svg', null), svg = doc.documentElement;
  const set = (el, attrs) => {
    for (const [k, v] of Object.entries(attrs)) {
      if (v === null || v === undefined) continue;
      const prefix = k.split(':')[1] ? k.split(':')[0] : null;
      if (prefix) el.setAttributeNS(NS[prefix], k, String(v));
      else el.setAttribute(k, String(v));
    }
    return el;
  };
  const add = (parent, tag, attrs) => parent.appendChild(set(doc.createElementNS(tag.startsWith('tp:') ? TP_NS : SVG_NS, tag), attrs));
  const vector = (v) => fromScene(v).map((c) => fmt(c, 6)).join(' ');
  const w = a.x1 - a.x0, h = a.y1 - a.y0, name = viewName.split('/').pop();
  for (const [prefix, uri] of Object.entries(NS)) svg.setAttributeNS('http://www.w3.org/2000/xmlns/', `xmlns:${prefix}`, uri);
  set(svg, { width: `${fmt(w)}mm`, height: `${fmt(h)}mm`, viewBox: `${fmt(a.x0)} ${fmt(-a.y1)} ${fmt(w)} ${fmt(h)}`,
             'tp:sketch-of': name, 'tp:saved': new Date().toISOString() });
  const { hidden, shown } = hiddenAndShown(state.tree);
  add(add(svg, 'metadata', {}), 'tp:view', {
    title: `${name}, ${lookName(back)}`, kind: 'model view',
    origin: vector(back.clone().multiplyScalar(inkPlanePoint(back).dot(back))), normal: vector(back), xdir: vector(right),
    area: [a.x0, a.y0, a.x1, a.y1].map((v) => fmt(v)).join(' '), hidden: hidden.length ? hidden.join('; ') : null,
    shown: shown.length ? shown.join('; ') : null, slices: describeSlices(),
    transparent: state.transparent ? 'true' : null,
  });
  const layer = (id, label) => add(svg, 'g', { id, 'inkscape:groupmode': 'layer', 'inkscape:label': label, transform: 'scale(1,-1)' });
  add(layer('tp-view', 'View'), 'image', {
    width: fmt(w), height: fmt(h), preserveAspectRatio: 'none', transform: `translate(${fmt(a.x0)} ${fmt(a.y1)}) scale(1,-1)`,
    'xlink:href': picture.toDataURL('image/png'),
  });
  const sketch = layer('tp-sketch', 'Sketch');
  for (const path of ui.inkLayer.children) sketch.appendChild(doc.importNode(path, true));
  growViewBox(svg, inkBounds(sketch)); // ink drawn and then panned out of view stays
  return `<?xml version="1.0" encoding="UTF-8"?>\n${new XMLSerializer().serializeToString(doc)}\n`;
}

async function save() {
  if (!state.model || state.saving) return;
  state.saving = true;
  state.gen++;
  try {
    const r = await fetchOk(`/api/sketch/${encodeURIComponent(viewName)}`, {
      method: 'PUT', headers: { 'Content-Type': 'image/svg+xml', 'X-Tracepaper': '1' }, body: sketchDocument(),
    });
    const { mtime } = await r.json();
    Object.assign(state, { dirty: false, sketchMtime: mtime });
    toast(`Saved ${sketchFile}`);
  } catch (err) {
    toast(`Save failed: ${err.message}`, true);
  } finally {
    state.saving = false;
    state.gen++;
    updateStatus();
  }
}

// The sketch file on disk: once the AI has deleted it, it is handled, and its ink goes.
function followSketch(sketch) {
  if (state.sketchMtime && !sketch) {
    state.sketchMtime = null;
    if (!state.dirty && !ink.empty) {
      wipe();
      toast('Sketch handled');
    }
  } else if (sketch !== state.sketchMtime && !state.dirty) {
    state.sketchMtime = sketch;
  }
  updateStatus();
}

// An unhandled sketch file's ink, and the view it was drawn on, when the window opens.
async function reopenSketch() {
  let root;
  try {
    root = parseSvg(await (await fetchOk(`/api/sketch/${encodeURIComponent(viewName)}`)).text());
  } catch {
    return;
  }
  const view = root.getElementsByTagNameNS(TP_NS, 'view')[0];
  const numbers = (attr) => (view?.getAttribute(attr) ?? '').trim().split(/[\s,]+/).map(Number);
  const [n, x, area] = ['normal', 'xdir', 'area'].map(numbers);
  if (n.length !== 3 || x.length !== 3 || ![...n, ...x].every(Number.isFinite)) return;
  frame({ dir: n, up: [n[1] * x[2] - n[2] * x[1], n[2] * x[0] - n[0] * x[2], n[0] * x[1] - n[1] * x[0]] });
  if (area.length === 4 && area.every(Number.isFinite) && area[2] > area[0] && area[3] > area[1]) showArea(area);
  ui.inkLayer.append(...(inkLayerOf(root)?.children ?? []));
  updateStatus();
}

// ---- Tablet pen
// The toolbar's button connects the reMarkable (tracepaper/pen.py) and lets it go. Its pen
// draws and erases over the view (ink.js's PenInput). The tablet's screen shows no
// picture of a 3D view, only the frame of where the pen draws (screen.js), which also
// takes the tablet's own ink of each stroke away once it ends: the person looks here.

const tablet = new Tablet({
  client: CLIENT, stage: ui.canvas, label: ui.pen, button: ui.penButton, toast,
  onStatus: (s) => {
    if (s?.state !== 'connected' || !tablet.here) {
      pen.end();
      pen.showCursor(null);
    }
    screenChanged();
  },
  onFrame: (f) => pen.frame(f),
});

const pen = new PenInput({
  ink, tablet, stage: ui.canvas, cursor: ui.penCursor,
  ready: () => !!state.model && tablet.here, begin: beginStroke, ended: screenChanged,
});

const feed = new ScreenFeed({
  tablet, pen, client: CLIENT, busy: () => !!gesture,
  picture: () => {
    const r = ui.canvas.getBoundingClientRect();
    return r.width && r.height && tablet.status?.pixels
      ? renderBlank({ stage: r, size: tablet.size, pixels: tablet.status.pixels }) : null;
  },
});

function screenChanged() { feed.changed(); }

// ---- Wiring

for (const b of document.querySelectorAll('[data-look]')) b.addEventListener('click', () => frame(LOOKS[b.dataset.look]));
ui.fit.addEventListener('click', () => frame(null));
ui.edges.addEventListener('click', () => showEdges(!edges?.shown));
ui.transparent.addEventListener('click', () => setTransparent(!state.transparent));
buildSliceControls();
INK_COLORS.forEach((_, i) => {
  const b = swatch(i, false); // the number keys look from the standard directions here
  b.addEventListener('click', () => setColor(i));
  ui.swatches.append(b);
});
ui.sketch.addEventListener('click', () => setDrawing(!state.drawing));
ui.undo.addEventListener('click', undo);
ui.clear.addEventListener('click', () => ink.clear());
ui.save.addEventListener('click', save);
// Don't leave toolbar buttons focused, or keys would press them.
ui.bar.addEventListener('click', (e) => e.target.closest('button')?.blur());
ui.view.addEventListener('change', () => {
  if (ui.view.value === viewName) return;
  if (state.dirty && !confirm(`Discard the unsaved ink on ${viewName}?`)) {
    ui.view.value = viewName;
    return;
  }
  state.dirty = false;
  location.href = viewUrl(ui.view.value);
});
ui.canvas.addEventListener('pointerdown', onPointerDown);
ui.canvas.addEventListener('pointermove', onPointerMove);
ui.canvas.addEventListener('pointerup', onPointerUp);
ui.canvas.addEventListener('pointercancel', onPointerUp);
// Using this window with the mouse makes it the one the tablet draws in.
window.addEventListener('pointerdown', () => tablet.claim(), true);
ui.canvas.addEventListener('wheel', () => tablet.claim(), { passive: true });
window.addEventListener('keydown', (e) => {
  if (e.target instanceof Element && e.target.closest('select, input, textarea')) return;
  const mod = e.ctrlKey || e.metaKey, key = e.key.toLowerCase();
  if (mod && key === 's') { e.preventDefault(); save(); return; }
  if (mod && key === 'z') { e.preventDefault(); undo(); return; }
  if (mod || e.altKey) return;
  const look = { 0: 'iso', 7: 'top', 1: 'front', 3: 'right' }[e.key];
  if (look) frame(LOOKS[look]);
  else if (key === 'f') frame(null);
  else if (key === 'e') showEdges(!edges?.shown);
  else if (key === 't') setTransparent(!state.transparent);
  else if (key === 'p') setDrawing(!state.drawing);
  else if (key === 'escape') setDrawing(false);
});
window.addEventListener('beforeunload', (e) => { if (state.dirty) e.preventDefault(); });
// The window or the tree panel changes the view's shape, which the tablet's drawing area follows.
new ResizeObserver(() => { resize(); tablet.render(); screenChanged(); }).observe(ui.canvas);
panelDivider(document.querySelector('main'));
document.title = `${viewName} · Tracepaper`;

setColor(0);
updateStatus();
poll();
tablet.connect();

// For debugging from the console and for tests/test_browser.py.
window.tracepaper3d = { THREE, scene, camera, controls, state, ready, frame, LOOKS, toScene, CLIENT, tablet, flatten,
  showEdges, setTransparent, slices, ink, pen, save, undo, setDrawing, viewArea };
