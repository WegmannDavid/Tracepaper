// Mouse controls of the 3D view, done the way the OCP CAD viewer does them: its
// trackball in "Holroyd" mode, with its default speeds (three-cad-viewer's
// CADTrackballControls and Controls, MIT, by Bernhard Walter).
//
// - Left drag rotates as if the model sat in a ball filling most of the view: grab
//   it near the middle to tip it over, near the edge to turn it about the line of
//   sight. The camera keeps no fixed "up", so nothing ties it to a vertical axis.
//   With Ctrl only up/down movements count, with Alt only sideways ones.
// - Right drag, or Shift + left drag, pans: the model follows the pointer.
// - The wheel and middle drag zoom about the middle of the view, so the point
//   everything rotates about (target) stays put.
//
// The camera is orthographic. Its orientation is camera.quaternion, which these
// controls turn directly; position is target plus the turned eye vector.
//
// While the page draws (drawing), the left button is the page's, and Shift + left
// drag still pans. mayRotate() is asked before a drag turns the view; a page with ink
// on the view says no until the ink is gone.
import * as THREE from 'three';

const RADIUS = 0.9; // of the ball, in half widths and half heights of the view
const PAN = 0.22 * 4; // OCP's default pan speed and its factor for orthographic cameras
const ZOOM = 2; // OCP's default zoom speed for its trackball
const WHEEL = [0.00025, 0.01, 0.025]; // zoom per wheel delta in pixels, lines, pages

const ROTATE = 'rotate', MOVE = 'pan', DOLLY = 'zoom';

export class CadControls extends THREE.EventDispatcher {
  constructor(camera, element) {
    super();
    this.camera = camera;
    this.element = element;
    this.target = new THREE.Vector3();
    this.drag = null; // { mode, id, x, y, sideways, upDown } while a button is down
    this.drawing = false;
    this.mayRotate = () => true;
    element.style.touchAction = 'none';
    element.addEventListener('pointerdown', (e) => this.down(e));
    element.addEventListener('pointermove', (e) => this.move(e));
    element.addEventListener('pointerup', (e) => this.up(e));
    element.addEventListener('pointercancel', (e) => this.up(e));
    element.addEventListener('wheel', (e) => this.wheel(e), { passive: false });
    element.addEventListener('contextmenu', (e) => e.preventDefault());
  }

  down(e) {
    if (this.drag || (e.pointerType === 'pen' && (e.button === 5 || e.buttons & 32))) return; // a pen's eraser end
    const mode = e.button === 1 ? DOLLY : e.button === 2 || (e.button === 0 && e.shiftKey) ? MOVE
      : e.button === 0 && !this.drawing ? ROTATE : null;
    if (!mode || (mode === ROTATE && !this.mayRotate())) return;
    e.preventDefault();
    try { this.element.setPointerCapture(e.pointerId); } catch { /* not an active pointer */ }
    this.drag = { mode, id: e.pointerId, x: e.clientX, y: e.clientY, sideways: !e.ctrlKey, upDown: !e.altKey };
  }

  move(e) {
    const d = this.drag;
    if (!d || e.pointerId !== d.id || (e.clientX === d.x && e.clientY === d.y)) return;
    if (d.mode === ROTATE) this.rotate(d.x, d.y, e.clientX, e.clientY, d);
    else if (d.mode === MOVE) this.pan(e.clientX - d.x, e.clientY - d.y);
    else this.zoomBy(1 + ((e.clientY - d.y) / this.element.clientHeight) * ZOOM);
    d.x = e.clientX;
    d.y = e.clientY;
  }

  up(e) {
    if (this.drag?.id === e.pointerId) this.drag = null;
  }

  wheel(e) {
    e.preventDefault();
    this.zoomBy(1 + e.deltaY * (WHEEL[e.deltaMode] ?? WHEEL[0]) * ZOOM);
  }

  // A point of the view on the ball: inside, its sphere; outside, a hyperbolic sheet
  // that turns the model about the line of sight as the pointer goes round the edge.
  onBall(clientX, clientY, { sideways, upDown }) {
    const r = this.element.getBoundingClientRect();
    const x = sideways ? (clientX - r.left) / (r.width / 2) - 1 : 0;
    const y = upDown ? 1 - (clientY - r.top) / (r.height / 2) : 0;
    const r2 = RADIUS * RADIUS, d2 = x * x + y * y;
    return new THREE.Vector3(x, y, d2 <= r2 / 2 ? Math.sqrt(r2 - d2) : r2 / (2 * Math.sqrt(d2)));
  }

  rotate(x0, y0, x1, y1, keys) {
    const a = this.onBall(x0, y0, keys), b = this.onBall(x1, y1, keys);
    const axis = new THREE.Vector3().crossVectors(a, b);
    const angle = Math.atan2(axis.length(), a.dot(b));
    if (!angle) return;
    axis.normalize().applyQuaternion(this.camera.quaternion); // view space to the scene
    const turn = new THREE.Quaternion().setFromAxisAngle(axis, -2 * angle);
    const eye = this.camera.position.clone().sub(this.target).applyQuaternion(turn);
    this.camera.quaternion.premultiply(turn).normalize();
    this.camera.position.copy(this.target).add(eye);
    this.changed();
  }

  // dx, dy: pointer movement in pixels.
  pan(dx, dy) {
    const c = this.camera, w = this.element.clientWidth, h = this.element.clientHeight;
    const shift = new THREE.Vector3(
      -(dx / w) * ((c.right - c.left) / c.zoom) * PAN,
      (dy / h) * ((c.top - c.bottom) / c.zoom) * PAN, 0,
    ).applyQuaternion(c.quaternion);
    c.position.add(shift);
    this.target.add(shift);
    this.changed();
  }

  // factor > 1 zooms out.
  zoomBy(factor) {
    if (!(factor > 0) || factor === 1) return;
    this.camera.zoom /= factor;
    this.camera.updateProjectionMatrix();
    this.changed();
  }

  changed() {
    this.camera.updateMatrixWorld();
    this.dispatchEvent({ type: 'change' });
  }
}
