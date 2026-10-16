// The CAD edges of a 3D view: each face's outline and sharp folds, drawn where they are
// on the surface in front.
//
// Testing lines against the depth buffer, as usual, needs the faces pushed back a little,
// or edges vanish into their own faces; that push is a fraction of a pixel's depth, so
// zoomed out it grows to millimetres, and the edges of things just under a surface
// (screw holes under a veneer) show through it. Instead, every opaque face draws its
// number and depth into a picture of the view (pass), and an edge is drawn where
//   - the face in front is its own: every edge between two faces is drawn by each, so
//     where its line strays onto the neighbour, that one's copy shows; or
//   - it is no further behind the surface in front than a fixed small distance, the same
//     at any zoom (TOLERANCE of the model's size); in front of everything, as along
//     silhouettes, it always is.
// Behind transparent faces (glass), whose own edges are tested against depth as before,
// the faces behind still count as in front.
import * as THREE from 'three';
import { mergeGeometries } from 'three/addons/utils/BufferGeometryUtils.js';

const LAYER = 1; // the faces' numbers are drawn on this camera layer only
const COLOR = [0x1f, 0x23, 0x28];
const TOLERANCE = 5e-5; // of the model's size

const position = 'gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0);';

const lineShader = {
  vertexShader: `attribute float face; varying float vFace; void main() { vFace = face; ${position} }`,
  fragmentShader: `
    uniform sampler2D faces;
    uniform sampler2D depths;
    uniform float tolerance; // in depth, which an orthographic camera spreads evenly from near to far
    uniform vec3 color;
    varying float vFace;
    ivec2 at(ivec2 p) { return clamp(p, ivec2(0), textureSize(faces, 0) - 1); }
    float faceAt(ivec2 p) {
      vec3 c = texelFetch(faces, p, 0).rgb;
      return dot(floor(c * 255.0 + 0.5), vec3(1.0, 256.0, 65536.0));
    }
    void main() {
      ivec2 p = at(ivec2(gl_FragCoord.xy));
      if (faceAt(p) != floor(vFace + 0.5) && gl_FragCoord.z > texelFetch(depths, p, 0).r + tolerance) discard;
      gl_FragColor = vec4(color, 1.0);
    }`,
};

const faceShader = {
  vertexShader: `attribute float face; varying float vFace; void main() { vFace = face; ${position} }`,
  fragmentShader: `
    varying float vFace;
    void main() {
      float n = floor(vFace + 0.5);
      gl_FragColor = vec4(mod(n, 256.0), mod(floor(n / 256.0), 256.0), floor(n / 65536.0), 255.0) / 255.0;
    }`,
};

// A copy of geometry's points with every one marked as face n.
function numbered(geometry, n) {
  const copy = new THREE.BufferGeometry(), count = geometry.attributes.position.count;
  copy.setAttribute('position', geometry.attributes.position);
  copy.setIndex(geometry.index ?? [...Array(count).keys()]); // all alike, to be merged
  copy.setAttribute('face', new THREE.Float32BufferAttribute(new Float32Array(count).fill(n), 1));
  return copy;
}

export class Edges {
  constructor(renderer) {
    this.renderer = renderer;
    this.faces = new THREE.WebGLRenderTarget(1, 1, { minFilter: THREE.NearestFilter, magFilter: THREE.NearestFilter,
                                                      depthTexture: new THREE.DepthTexture(1, 1) });
    this.material = new THREE.ShaderMaterial({
      ...lineShader, depthTest: false, depthWrite: false,
      uniforms: {
        faces: { value: this.faces.texture }, depths: { value: this.faces.depthTexture }, tolerance: { value: 0 },
        color: { value: new THREE.Vector3(...COLOR.map((c) => c / 255)) },
      },
    });
    this.numbers = new THREE.ShaderMaterial({ ...faceShader, side: THREE.DoubleSide });
    // Edges of transparent faces, which aren't numbered: tested against depth.
    this.depthTested = new THREE.LineBasicMaterial({ color: new THREE.Color(...COLOR.map((c) => c / 255)) });
    this.count = 0;
  }

  // Give a part (an object the tree can hide) the edges of its faces, [{geometry,
  // material}] in its coordinates: each face's outline and folds sharper than angle
  // degrees. A part's faces are numbered and drawn together.
  add(part, faces, angle) {
    const numbers = [], lines = [], clear = [];
    for (const { geometry, material } of faces) {
      const outline = new THREE.EdgesGeometry(geometry, angle);
      if (material.transparent) {
        clear.push(outline);
        continue;
      }
      const n = ++this.count;
      numbers.push(numbered(geometry, n));
      lines.push(numbered(outline, n));
    }
    if (numbers.length) {
      const front = new THREE.Mesh(mergeGeometries(numbers), this.numbers);
      front.layers.set(LAYER);
      const edges = new THREE.LineSegments(mergeGeometries(lines), this.material);
      edges.renderOrder = 1; // after all the faces, since it doesn't test depth
      part.add(front, edges);
    }
    if (clear.length) part.add(new THREE.LineSegments(mergeGeometries(clear), this.depthTested));
  }

  // The materials shared by every part, which a model being replaced mustn't dispose.
  shared(material) { return [this.material, this.numbers, this.depthTested].includes(material); }

  resize() {
    const size = this.renderer.getDrawingBufferSize(new THREE.Vector2());
    this.faces.setSize(size.x, size.y);
  }

  // Number the faces in front, for the edges of the render that follows. size: the model's.
  pass(scene, camera, size) {
    this.material.uniforms.tolerance.value = (TOLERANCE * size) / (camera.far - camera.near);
    const { renderer } = this, background = scene.background;
    scene.background = null;
    camera.layers.set(LAYER);
    renderer.setRenderTarget(this.faces);
    renderer.setClearColor(0x000000, 0);
    renderer.render(scene, camera);
    renderer.setRenderTarget(null);
    camera.layers.set(0);
    scene.background = background;
  }
}
