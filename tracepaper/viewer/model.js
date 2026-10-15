// Tracepaper's 3D view: a .glb model, such as a CadQuery assembly's export, in its
// colours and with the edges between its faces. Rotate, pan and zoom; it reloads
// when the file changes and keeps the camera.
import * as THREE from 'three';
import { TrackballControls } from 'three/addons/controls/TrackballControls.js';
import { GLTFLoader } from 'three/addons/loaders/GLTFLoader.js';
import { flatten, renderTree } from './tree.js';

const POLL_MS = 1000;
const EDGE_ANGLE = 25; // degrees: within a face, triangles meeting at less than this get no edge line
// Directions to look from and which way is up on screen, in CAD coordinates (z up). Fronts face -y.
const LOOKS = {
  iso: { dir: [1, -1, 1], up: [0, 0, 1] },
  top: { dir: [0, 0, 1], up: [0, 1, 0] },
  front: { dir: [0, -1, 0], up: [0, 0, 1] },
  right: { dir: [1, 0, 0], up: [0, 0, 1] },
};
const CLIENT = crypto.randomUUID?.() ?? `${Date.now()}-${Math.random()}`;

const $ = (id) => document.getElementById(id);
const ui = {
  canvas: $('tpv-canvas'), view: $('tpv-view'), status: $('tpv-status'), empty: $('tpv-empty'),
  toast: $('tpv-toast'), pen: $('tpv-pen'), fit: $('tpv-fit'), tree: $('tpv-layers'),
};
const viewName = decodeURIComponent(location.pathname.slice('/view/'.length));
const viewUrl = (name) => `/view/${name.split('/').map(encodeURIComponent).join('/')}`;

// glTF is y up; CAD files exported from z-up CAD carry a turn into it on their root,
// so a CAD direction (x, y, z) is (x, z, -y) in the scene.
const toScene = ([x, y, z]) => new THREE.Vector3(x, z, -y);

// Without WebGL (switched off, or no working GPU process) there is nothing to draw with:
// say so, and keep the rest of the page working.
let renderer = null;
try {
  renderer = new THREE.WebGLRenderer({ canvas: ui.canvas, antialias: true });
  renderer.setPixelRatio(window.devicePixelRatio);
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
// A trackball, as in the OCP viewer: dragging turns the model freely about any axis,
// not only about the vertical one. Scrolling zooms about the middle of the view, so
// the rotation centre stays put.
const controls = new TrackballControls(camera, ui.canvas);
Object.assign(controls, { rotateSpeed: 2.2, zoomSpeed: 2.5, panSpeed: 0.5, staticMoving: true, keys: [] });
controls.addEventListener('change', render);
(function follow() { // the controls move the camera in update(); it renders only on a change
  controls.update();
  requestAnimationFrame(follow);
})();

const edgeMaterial = new THREE.LineBasicMaterial({ color: 0x1f2328 });
const state = {
  model: null, mtime: null, half: 1, loads: 0, views: [], viewsKey: '',
  tree: [], expanded: new Set(),
  choices: new Map(), // key -> hidden, what this window's person switched; kept when the model reloads
};
let resolveReady;
const ready = new Promise((resolve) => { resolveReady = resolve; });

function render() {
  renderer?.render(scene, camera);
}

function resize() {
  const w = ui.canvas.clientWidth, h = ui.canvas.clientHeight;
  if (!w || !h) return;
  renderer?.setSize(w, h, false);
  controls.handleResize();
  const aspect = w / h, half = state.half;
  Object.assign(camera, aspect >= 1
    ? { left: -half * aspect, right: half * aspect, top: half, bottom: -half }
    : { left: -half, right: half, top: half / aspect, bottom: -half / aspect });
  camera.updateProjectionMatrix();
  render();
}

// Look at the whole model (what is shown of it) from one of LOOKS; without one, keep
// the current direction and roll.
function frame(look) {
  if (!state.model) return;
  const box = new THREE.Box3();
  state.model.updateMatrixWorld(true);
  state.model.traverseVisible((o) => { if (o.isMesh) box.expandByObject(o); });
  if (box.isEmpty()) box.setFromObject(state.model);
  const sphere = box.getBoundingSphere(new THREE.Sphere());
  if (!(sphere.radius > 0)) return;
  const dir = look ? toScene(look.dir).normalize() : camera.position.clone().sub(controls.target).normalize();
  if (look) camera.up.copy(toScene(look.up).normalize()); // the trackball rolls it; a standard view resets it
  controls.target.copy(sphere.center);
  camera.position.copy(sphere.center).addScaledVector(dir, sphere.radius * 4);
  camera.lookAt(sphere.center);
  camera.near = sphere.radius * 0.01;
  camera.far = sphere.radius * 10;
  camera.zoom = 1;
  state.half = sphere.radius * 1.08;
  resize();
  controls.update();
}

function prepare(root) {
  const meshes = [];
  root.traverse((o) => { if (o.isMesh) meshes.push(o); });
  for (const mesh of meshes) {
    for (const m of [mesh.material].flat()) {
      // CAD colours, not metal: the glTF default would be fully metallic.
      Object.assign(m, { metalness: 0, roughness: 0.7, side: THREE.DoubleSide,
                         polygonOffset: true, polygonOffsetFactor: 1, polygonOffsetUnits: 1 });
    }
    // Each CAD face is its own mesh, so its outline and any sharp folds are the CAD edges.
    mesh.add(new THREE.LineSegments(new THREE.EdgesGeometry(mesh.geometry, EDGE_ANGLE), edgeMaterial));
  }
}

// The tree of the model's named glTF nodes (SPEC.md §2.8). Faces and unnamed nodes don't
// count; a single top node (the assembly itself) is left out, so its parts are the top level.
function buildTree(gltf) {
  const json = gltf.parser.json.nodes ?? [];
  const nodeOf = (o) => json[gltf.parser.associations.get(o)?.nodes];
  const under = (obj, above) => obj.children.flatMap((child) => {
    const def = nodeOf(child);
    if (!def?.name) return under(child, above);
    const key = above ? `${above} / ${def.name}` : def.name;
    return [{ key, name: def.name, hidden: !!def.extras?.tracepaper?.hidden, obj: child, children: under(child, key) }];
  });
  const top = under(gltf.scene, '');
  if (top.length !== 1 || !top[0].children.length) return top;
  const rekey = (n, above) => {
    const key = above ? `${above} / ${n.name}` : n.name;
    return { ...n, key, children: n.children.map((c) => rekey(c, key)) };
  };
  return top[0].children.map((c) => rekey(c, ''));
}

// Hide what the file or this window's person hid.
function applyTree() {
  for (const node of flatten(state.tree)) {
    if (state.choices.has(node.key)) node.hidden = state.choices.get(node.key);
    node.obj.visible = !node.hidden;
  }
  renderPanel();
}

function renderPanel() {
  renderTree(ui.tree, state.tree, {
    expanded: state.expanded,
    onToggle: (node, hidden) => {
      state.choices.set(node.key, hidden);
      node.hidden = hidden;
      node.obj.visible = !hidden;
      renderPanel();
      render();
    },
  });
}

function dispose(root) {
  root.traverse((o) => {
    o.geometry?.dispose();
    if (o.material && o.material !== edgeMaterial) [o.material].flat().forEach((m) => m.dispose());
  });
}

async function load(mtime) {
  const r = await fetch(`/api/file/${encodeURIComponent(viewName)}`, { cache: 'no-store' });
  if (!r.ok) throw new Error(`${r.status} ${await r.text()}`);
  const gltf = await new GLTFLoader().parseAsync(await r.arrayBuffer(), '');
  prepare(gltf.scene);
  const first = !state.model;
  if (state.model) {
    scene.remove(state.model);
    dispose(state.model);
  }
  state.model = gltf.scene;
  scene.add(state.model);
  state.tree = buildTree(gltf);
  applyTree();
  state.mtime = mtime;
  state.loads += 1;
  ui.empty.hidden = true;
  if (first) frame(LOOKS.iso);
  else render();
  setStatus(`Updated ${new Date().toLocaleTimeString()}`);
  if (first) resolveReady();
}

function setStatus(text, error = false) {
  ui.status.textContent = text;
  ui.status.className = `tpv-status${error ? ' dirty' : ''}`;
}

let toastTimer;
function toast(message) {
  ui.toast.textContent = message;
  ui.toast.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { ui.toast.hidden = true; }, 2500);
}

function renderViews() {
  const key = JSON.stringify(state.views.map((v) => [v.name, v.kind]));
  if (key !== state.viewsKey) {
    state.viewsKey = key;
    ui.view.replaceChildren(...state.views.map((v) => new Option(`${v.name}${v.kind === 'model' ? '  (3D)' : ''}`, v.name)));
  }
  ui.view.value = viewName;
}

async function poll() {
  try {
    const listing = await (await fetch('/api/views', { cache: 'no-store' })).json();
    state.views = listing.views;
    renderViews();
    const me = listing.views.find((v) => v.name === viewName);
    if (!me) setStatus(`${viewName} is gone`, true);
    else if (me.mtime !== state.mtime) {
      const reload = !!state.model;
      try {
        await load(me.mtime);
        if (reload) toast('Model updated');
      } catch (err) {
        setStatus(`Could not load ${viewName}: ${err.message}`, true); // e.g. half written: tried again
      }
    }
  } catch {
    setStatus('Viewer server stopped', true);
  }
  setTimeout(poll, POLL_MS);
}

// ---- Tablet pen: it doesn't draw on 3D views yet, but using this window takes it from the others.

let pen = null;

function connectPen() {
  const source = new EventSource(`/api/pen?client=${encodeURIComponent(CLIENT)}&draws=0`);
  source.addEventListener('status', (e) => { pen = JSON.parse(e.data); renderPen(); });
  source.addEventListener('error', () => {
    if (source.readyState === EventSource.CLOSED) { pen = null; renderPen(); }
  });
}

function renderPen() {
  ui.pen.hidden = !pen;
  if (!pen) return;
  const here = pen.active === CLIENT;
  ui.pen.textContent = pen.state !== 'connected' ? `reMarkable: ${pen.state}`
    : here ? 'reMarkable · not on 3D views yet' : 'reMarkable · in another window';
  ui.pen.className = `tpv-pen ${pen.state}`;
}

function claimPen() {
  if (!pen || pen.active === CLIENT) return;
  fetch('/api/pen/active', {
    method: 'POST', headers: { 'Content-Type': 'application/json', 'X-Tracepaper': '1' },
    body: JSON.stringify({ client: CLIENT }),
  }).catch(() => {});
}

// ---- Wiring

for (const b of document.querySelectorAll('[data-look]')) b.addEventListener('click', () => frame(LOOKS[b.dataset.look]));
ui.fit.addEventListener('click', () => frame(null));
ui.view.addEventListener('change', () => { if (ui.view.value !== viewName) location.href = viewUrl(ui.view.value); });
window.addEventListener('pointerdown', claimPen, true);
ui.canvas.addEventListener('wheel', claimPen, { passive: true });
window.addEventListener('keydown', (e) => {
  if (e.ctrlKey || e.metaKey || e.altKey || (e.target instanceof Element && e.target.closest('select'))) return;
  const look = { 0: 'iso', 7: 'top', 1: 'front', 3: 'right' }[e.key];
  if (look) frame(LOOKS[look]);
  else if (e.key.toLowerCase() === 'f') frame(null);
});
new ResizeObserver(resize).observe(ui.canvas);
document.title = `${viewName} · Tracepaper`;

poll();
connectPen();

// For debugging from the console and for tests/test_browser.py.
window.tracepaper3d = { THREE, scene, camera, controls, state, ready, frame, LOOKS, toScene, CLIENT, flatten };
