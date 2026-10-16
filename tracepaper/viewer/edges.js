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
//
// Edges are strips a set width on screen (WIDTH), not lines, which WebGL always draws a
// single pixel wide: the view is drawn at a higher resolution than the screen's and
// scaled down (supersampling), and lines would come out faint.
import * as THREE from 'three';
import { mergeGeometries } from 'three/addons/utils/BufferGeometryUtils.js';

const NUMBERS = 1; // the camera layer the faces' numbers are drawn on
const EDGES = 2; // and the edges
const COLOR = [0x1f, 0x23, 0x28];
const WIDTH = 1; // CSS pixels
const TOLERANCE = 5e-5; // of the model's size

// Each segment of an edge is a quad from its start to its end, widened on screen.
const strip = `
  attribute vec3 start;
  attribute vec3 end;
  attribute float face;
  uniform vec2 resolution; // in pixels
  uniform float width; // in pixels
  varying float vFace;
  varying vec2 vCentre; // the pixel on the edge itself, across from this one
  void main() {
    vec4 a = projectionMatrix * modelViewMatrix * vec4(start, 1.0);
    vec4 b = projectionMatrix * modelViewMatrix * vec4(end, 1.0); // orthographic: w is 1
    vec2 d = (b.xy - a.xy) * resolution;
    vec2 along = length(d) > 0.0 ? normalize(d) : vec2(1.0, 0.0), across = vec2(-along.y, along.x);
    vec4 p = mix(a, b, position.x);
    vCentre = (p.xy * 0.5 + 0.5) * resolution;
    // Half the width to either side, and past either end, so that segments join.
    p.xy += (across * position.y + along * (position.x * 2.0 - 1.0)) * width / resolution;
    vFace = face;
    gl_Position = p;
  }`;

const onFrontFace = `
  uniform sampler2D faces;
  uniform sampler2D depths;
  uniform float tolerance; // in depth, which an orthographic camera spreads evenly from near to far
  uniform vec3 color;
  varying float vFace;
  varying vec2 vCentre;
  void main() {
    ivec2 last = textureSize(faces, 0) - 1;
    ivec2 p = clamp(ivec2(gl_FragCoord.xy), ivec2(0), last);
    vec3 c = texelFetch(faces, p, 0).rgb;
    float front = dot(floor(c * 255.0 + 0.5), vec3(1.0, 256.0, 65536.0));
    // Depth is compared under the edge itself: across the strip the edge keeps its depth,
    // but a surface at a slant doesn't, and a pixel aside it may be as far back as a veneer.
    float surface = texelFetch(depths, clamp(ivec2(vCentre), ivec2(0), last), 0).r;
    if (front != floor(vFace + 0.5) && gl_FragCoord.z > surface + tolerance) discard;
    gl_FragColor = vec4(color, 1.0);
  }`;

const plain = 'uniform vec3 color; void main() { gl_FragColor = vec4(color, 1.0); }';

const numberShader = {
  vertexShader: `attribute float face; varying float vFace;
    void main() { vFace = face; gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0); }`,
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

// Line segments (pairs of points, with a face number each) as strips.
function strips(lines) {
  const points = lines.attributes.position.array, faces = lines.attributes.face?.array, n = points.length / 6;
  const start = new Float32Array(3 * n), end = new Float32Array(3 * n), face = new Float32Array(n);
  for (let i = 0; i < n; i++) {
    start.set(points.subarray(6 * i, 6 * i + 3), 3 * i);
    end.set(points.subarray(6 * i + 3, 6 * i + 6), 3 * i);
    face[i] = faces ? faces[2 * i] : 0;
  }
  const geometry = new THREE.InstancedBufferGeometry();
  geometry.setAttribute('position', new THREE.Float32BufferAttribute([0, -1, 0, 1, -1, 0, 1, 1, 0, 0, 1, 0], 3));
  geometry.setIndex([0, 1, 2, 0, 2, 3]);
  geometry.setAttribute('start', new THREE.InstancedBufferAttribute(start, 3));
  geometry.setAttribute('end', new THREE.InstancedBufferAttribute(end, 3));
  geometry.setAttribute('face', new THREE.InstancedBufferAttribute(face, 1));
  geometry.instanceCount = n;
  return geometry;
}

export class Edges {
  constructor(renderer, camera) {
    Object.assign(this, { renderer, camera, shown: true, count: 0 });
    camera.layers.enable(EDGES);
    this.faces = new THREE.WebGLRenderTarget(1, 1, { minFilter: THREE.NearestFilter, magFilter: THREE.NearestFilter,
                                                      depthTexture: new THREE.DepthTexture(1, 1) });
    const color = { value: new THREE.Vector3(...COLOR.map((c) => c / 255)) };
    const size = { resolution: { value: new THREE.Vector2(1, 1) }, width: { value: WIDTH } };
    this.numbers = new THREE.ShaderMaterial({ ...numberShader, side: THREE.DoubleSide });
    this.material = new THREE.ShaderMaterial({
      vertexShader: strip, fragmentShader: onFrontFace, depthTest: false, depthWrite: false,
      uniforms: { ...size, color, faces: { value: this.faces.texture }, depths: { value: this.faces.depthTexture },
                  tolerance: { value: 0 } },
    });
    // Edges of transparent faces, which aren't numbered: tested against depth.
    this.depthTested = new THREE.ShaderMaterial({ vertexShader: strip, fragmentShader: plain, uniforms: { ...size, color } });
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
    const edges = (list, material) => {
      const mesh = new THREE.Mesh(strips(mergeGeometries(list)), material);
      Object.assign(mesh, { frustumCulled: false, renderOrder: 1 }); // after the faces, which it may not test
      mesh.layers.set(EDGES);
      part.add(mesh);
    };
    if (numbers.length) {
      const front = new THREE.Mesh(mergeGeometries(numbers), this.numbers);
      front.layers.set(NUMBERS);
      part.add(front);
      edges(lines, this.material);
    }
    if (clear.length) edges(clear, this.depthTested);
  }

  // The materials shared by every part, which a model being replaced mustn't dispose.
  shared(material) { return [this.material, this.numbers, this.depthTested].includes(material); }

  show(shown) {
    this.shown = shown;
    if (shown) this.camera.layers.enable(EDGES);
    else this.camera.layers.disable(EDGES);
  }

  resize() {
    const size = this.renderer.getDrawingBufferSize(new THREE.Vector2());
    this.faces.setSize(size.x, size.y);
    for (const m of [this.material, this.depthTested]) {
      m.uniforms.resolution.value.copy(size);
      m.uniforms.width.value = WIDTH * this.renderer.getPixelRatio();
    }
  }

  // Number the faces in front, for the edges of the render that follows. size: the model's.
  pass(scene, size) {
    if (!this.shown) return;
    const { renderer, camera } = this, background = scene.background, layers = camera.layers.mask;
    this.material.uniforms.tolerance.value = (TOLERANCE * size) / (camera.far - camera.near);
    scene.background = null;
    camera.layers.set(NUMBERS);
    renderer.setRenderTarget(this.faces);
    renderer.setClearColor(0x000000, 0);
    renderer.render(scene, camera);
    renderer.setRenderTarget(null);
    camera.layers.mask = layers;
    scene.background = background;
  }
}
