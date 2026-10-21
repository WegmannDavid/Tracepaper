// Slicing a 3D view with planes along CAD's axes, as in the OCP viewer. Each plane that
// is on cuts away what lies beyond it (flipped: before it); in a corner cut, only what
// lies beyond every plane that is on goes, as in a cutaway. A section only shows just
// where the planes cut: the caps and their outline, without the rest of the model.
//
// The cut parts are closed with caps in their colours, drawn with the stencil buffer: for
// a part and a plane, the part's remaining faces are counted along each pixel's ray, back
// faces up and front faces down; where the count isn't zero, the ray enters the solid at
// the plane, so the plane is drawn there. That needs closed meshes whose triangles all
// face out. A part that is transparent all through isn't taken for a solid and gets no
// caps, like a slicer project's build volume. Caps also go into the edges' numbering pass
// (edges.js), each with a number of its own, apart from the faces' ones, and the outline
// of the cut is drawn where a cap meets anything else (outline), as in a section view.
// Edges can't draw it: there the faces lie flat along the line of sight, and theirs would
// come and go as the view moves.
import * as THREE from 'three';
import { NUMBERS } from './edges.js';

export const AXES = ['x', 'y', 'z'];
const SCENE = { x: [1, 0, 0], y: [0, 0, -1], z: [0, 1, 0] }; // CAD's axes in the scene, which is y up
const CAPS = 100; // render order of the first part's first plane
const FIRST_CAP = 2 ** 23; // caps are numbered from here, faces below

const counter = (side, op) => new THREE.MeshBasicMaterial({
  side, colorWrite: false, depthWrite: false, depthTest: false,
  stencilWrite: true, stencilFunc: THREE.AlwaysStencilFunc, stencilFail: op, stencilZFail: op, stencilZPass: op,
});
const capped = { stencilWrite: true, stencilRef: 0, stencilFunc: THREE.NotEqualStencilFunc,
                 stencilFail: THREE.ReplaceStencilOp, stencilZFail: THREE.ReplaceStencilOp, stencilZPass: THREE.ReplaceStencilOp };
// A cap in the numbering pass: its number n, as edges.js encodes them.
const numbered = (n) => new THREE.ShaderMaterial({
  ...capped, side: THREE.DoubleSide, clipping: true,
  uniforms: { number: { value: new THREE.Vector3(n % 256, Math.floor(n / 256) % 256, Math.floor(n / 65536)).divideScalar(255) } },
  vertexShader: `#include <clipping_planes_pars_vertex>
    void main() {
      vec4 mvPosition = modelViewMatrix * vec4(position, 1.0);
      gl_Position = projectionMatrix * mvPosition;
      #include <clipping_planes_vertex>
    }`,
  fragmentShader: `uniform vec3 number;
    #include <clipping_planes_pars_fragment>
    void main() {
      #include <clipping_planes_fragment>
      gl_FragColor = vec4(number, 1.0);
    }`,
});

// The outline of the cut: drawn over the view where, within a pixel, a cap meets another
// cap, a face or nothing.
const outline = new THREE.ShaderMaterial({
  depthTest: false, depthWrite: false,
  uniforms: { faces: { value: null }, color: { value: new THREE.Vector3() }, reach: { value: 1 } },
  vertexShader: 'void main() { gl_Position = vec4(position.xy, 0.0, 1.0); }',
  fragmentShader: `
    uniform sampler2D faces;
    uniform vec3 color;
    uniform int reach; // pixels
    float numberAt(ivec2 p) {
      vec3 c = texelFetch(faces, clamp(p, ivec2(0), textureSize(faces, 0) - 1), 0).rgb;
      return dot(floor(c * 255.0 + 0.5), vec3(1.0, 256.0, 65536.0));
    }
    void main() {
      ivec2 p = ivec2(gl_FragCoord.xy);
      float here = numberAt(p);
      bool cap = here >= ${FIRST_CAP}.0, meets = false;
      for (int dy = -reach; dy <= reach; dy++) {
        for (int dx = -reach; dx <= reach; dx++) {
          float n = numberAt(p + ivec2(dx, dy));
          if (n != here) { meets = true; cap = cap || n >= ${FIRST_CAP}.0; }
        }
      }
      if (!(cap && meets)) discard;
      gl_FragColor = vec4(color, 1.0);
    }`,
});
const overlay = new THREE.Scene().add(Object.assign(new THREE.Mesh(new THREE.PlaneGeometry(2, 2), outline),
                                                    { frustumCulled: false })); // the whole view, always
const screen = new THREE.OrthographicCamera(-1, 1, 1, -1, 0, 1);
const square = new THREE.PlaneGeometry(1, 1);
const clearStencil = (renderer) => renderer.clearStencil(); // for the next part's count

export class Slices {
  constructor() {
    // at: where the plane is along its axis, in mm; flip: cut away what is before it instead.
    this.planes = AXES.map((axis) => ({
      axis, on: false, at: 0, flip: false, plane: new THREE.Plane(),
      up: counter(THREE.BackSide, THREE.IncrementWrapStencilOp),
      down: counter(THREE.FrontSide, THREE.DecrementWrapStencilOp),
    }));
    this.corner = false;
    this.sectionOnly = false;
    this.caps = []; // [{plane index, cap, number, matrix}] of the model shown
    this.clipped = []; // the materials that the planes cut: the model's own, faces and edges
  }

  // A model: parts [{part, meshes}] whose meshes are its faces, the materials besides
  // theirs that the planes cut (the edges'), and its bounding box in the scene.
  setModel(parts, materials, box) {
    this.caps = [];
    this.clipped = [...new Set([...parts.flatMap((p) => p.meshes.map((m) => m.material)), ...materials])];
    const size = box.getSize(new THREE.Vector3()).length() * 1.1, centre = box.getCenter(new THREE.Vector3());
    parts.forEach(({ part, meshes }, k) => {
      // Transparent in the file: no solid, like a slicer's build volume, so no caps.
      if (meshes.every((m) => m.material.userData.base?.transparent ?? m.material.transparent)) return;
      this.planes.forEach((p, i) => {
        const order = CAPS + 2 * (AXES.length * k + i);
        for (const mesh of meshes) {
          for (const material of [p.up, p.down]) {
            const count = new THREE.Mesh(mesh.geometry, material);
            Object.assign(count, { renderOrder: order, visible: false });
            count.userData.slice = true; // not part of the model's shape
            count.layers.enable(NUMBERS); // and in the numbering pass
            part.add(count);
          }
        }
        const color = meshes[0].material.color ?? new THREE.Color(0x999999);
        const cap = new THREE.Mesh(square, new THREE.MeshStandardMaterial({
          ...capped, color, metalness: 0, roughness: 0.7, side: THREE.DoubleSide,
        }));
        const number = new THREE.Mesh(square, numbered(FIRST_CAP + AXES.length * k + i)); // cut like the cap
        number.layers.set(NUMBERS);
        for (const m of [cap, number]) {
          Object.assign(m, { renderOrder: order + 1, visible: false, matrixAutoUpdate: false, onAfterRender: clearStencil });
          m.userData.slice = true;
          part.add(m);
        }
        this.caps.push({ i, part, cap, number, size, centre });
      });
    });
    this.update();
  }

  // Apply what this.planes, this.corner and this.sectionOnly say.
  update() {
    for (const p of this.planes) {
      const along = new THREE.Vector3(...SCENE[p.axis]);
      // Kept is where the plane's normal points: before it, or flipped, beyond it.
      p.plane.set(p.flip ? along : along.clone().negate(), p.flip ? -p.at : p.at);
    }
    const on = this.planes.filter((p) => p.on).map((p) => p.plane);
    for (const m of this.clipped) {
      if (m.clippingPlanes?.length !== on.length || m.clipIntersection !== this.corner) m.needsUpdate = true;
      m.clippingPlanes = on;
      m.clipIntersection = this.corner;
      m.visible = !(this.sectionOnly && on.length); // with no plane on, the whole model still
    }
    this.planes.forEach((p) => { p.up.clippingPlanes = [p.plane]; p.down.clippingPlanes = [p.plane]; });
    for (const { i, part, cap, number, size, centre } of this.caps) {
      const p = this.planes[i];
      // A cap is cut by the other planes; in a corner cut, it shows only where they cut too.
      const others = this.planes.filter((q, j) => j !== i && q.on)
        .map((q) => (this.corner ? q.plane.clone().negate() : q.plane));
      for (const m of [cap, number]) {
        m.visible = p.on;
        if (m.material.clippingPlanes?.length !== others.length) m.material.needsUpdate = true;
        m.material.clippingPlanes = others;
      }
      // The square covers the model, on the plane, in the part's own coordinates.
      const at = centre.clone().projectOnPlane(p.plane.normal).addScaledVector(p.plane.normal, -p.plane.constant);
      const world = new THREE.Matrix4().compose(at, new THREE.Quaternion().setFromUnitVectors(
        new THREE.Vector3(0, 0, 1), p.plane.normal), new THREE.Vector3(size, size, 1));
      part.updateWorldMatrix(true, false);
      const local = part.matrixWorld.clone().invert().multiply(world);
      cap.matrix.copy(local);
      number.matrix.copy(local);
    }
    // The counting meshes are there while their plane is on.
    for (const { i, part } of this.caps) {
      const { up, down, on: shown } = this.planes[i];
      for (const c of part.children) if (c.material === up || c.material === down) c.visible = shown;
    }
  }

  get on() { return this.planes.some((p) => p.on); }

  // Draw the outline of the cut over the view just rendered, from the edges' numbering
  // pass (edges.js), in their colour and width.
  outline(renderer, edges) {
    if (!this.on || !edges.shown) return;
    const u = outline.uniforms, line = edges.material.uniforms;
    u.faces.value = edges.faces.texture;
    u.color.value.copy(line.color.value);
    u.reach.value = Math.max(1, Math.round(line.width.value / 2));
    const clear = renderer.autoClear;
    renderer.autoClear = false;
    renderer.render(overlay, screen);
    renderer.autoClear = clear;
  }
}
