// The CAD edges of a 3D view: each face's outline and sharp folds.
//
// As usual (and as in the OCP viewer), the faces are pushed back a little in depth
// (polygonOffset, where model.js prepares the parts), and the edges are drawn after them,
// tested against the depth buffer: an edge shows where nothing is in front of it. The push
// is a fraction of a pixel's depth, so zoomed far out, the edges of things just under a
// surface (screw holes under a veneer) can show through it.
//
// Edges are strips a set width on screen (WIDTH), not lines, which WebGL always draws a
// single pixel wide: the view is drawn at a higher resolution than the screen's and
// scaled down (supersampling), and lines would come out faint.
//
// Every opaque face also draws its number into a picture of the view (pass), from which
// the slice planes (slices.js) draw the outline of a cut: their caps take part in the
// numbering with numbers of their own. All of it can be cut by the slice planes.
import * as THREE from 'three';
import { mergeGeometries } from 'three/addons/utils/BufferGeometryUtils.js';

export const NUMBERS = 1; // the camera layer the faces' numbers are drawn on
const EDGES = 2; // and the edges
const LAST = 1e9; // edges are drawn after the faces they are tested against
const COLOR = [0x1f, 0x23, 0x28];
const WIDTH = 1; // CSS pixels

// Each segment of an edge is a quad from its start to its end, widened on screen.
const strip = `
  attribute vec3 start;
  attribute vec3 end;
  uniform vec2 resolution; // in pixels
  uniform float width; // in pixels
  #include <clipping_planes_pars_vertex>
  void main() {
    vec4 mvPosition = modelViewMatrix * vec4(mix(start, end, position.x), 1.0);
    #include <clipping_planes_vertex>
    vec4 a = projectionMatrix * modelViewMatrix * vec4(start, 1.0);
    vec4 b = projectionMatrix * modelViewMatrix * vec4(end, 1.0);
    vec2 d = (b.xy / b.w - a.xy / a.w) * resolution; // twice the length in pixels
    if (length(d) < 1.0) { gl_Position = vec4(0.0, 0.0, 2.0, 1.0); return; } // seen end on: nothing to see
    vec2 along = normalize(d), across = vec2(-along.y, along.x);
    vec4 p = mix(a, b, position.x);
    // Half the width to either side, and past either end, so that segments join.
    p.xy += (across * position.y + along * (position.x * 2.0 - 1.0)) * width / resolution * p.w; // in perspective too
    gl_Position = p;
  }`;

const plain = `
  uniform vec3 color;
  #include <clipping_planes_pars_fragment>
  void main() {
    #include <clipping_planes_fragment>
    gl_FragColor = vec4(color, 1.0);
  }`;

const numberShader = {
  vertexShader: `attribute float face; varying float vFace;
    #include <clipping_planes_pars_vertex>
    void main() {
      vFace = face;
      vec4 mvPosition = modelViewMatrix * vec4(position, 1.0);
      gl_Position = projectionMatrix * mvPosition;
      #include <clipping_planes_vertex>
    }`,
  fragmentShader: `
    varying float vFace;
    #include <clipping_planes_pars_fragment>
    void main() {
      #include <clipping_planes_fragment>
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

// Line segments (pairs of points) as strips.
function strips(lines) {
  const points = lines.attributes.position.array, n = points.length / 6;
  const start = new Float32Array(3 * n), end = new Float32Array(3 * n);
  for (let i = 0; i < n; i++) {
    start.set(points.subarray(6 * i, 6 * i + 3), 3 * i);
    end.set(points.subarray(6 * i + 3, 6 * i + 6), 3 * i);
  }
  const geometry = new THREE.InstancedBufferGeometry();
  geometry.setAttribute('position', new THREE.Float32BufferAttribute([0, -1, 0, 1, -1, 0, 1, 1, 0, 0, 1, 0], 3));
  geometry.setIndex([0, 1, 2, 0, 2, 3]);
  geometry.setAttribute('start', new THREE.InstancedBufferAttribute(start, 3));
  geometry.setAttribute('end', new THREE.InstancedBufferAttribute(end, 3));
  geometry.instanceCount = n;
  return geometry;
}

export class Edges {
  constructor(renderer, camera) {
    Object.assign(this, { renderer, camera, shown: true, count: 0 });
    camera.layers.enable(EDGES);
    // With a stencil buffer, which the slices' caps need.
    this.faces = new THREE.WebGLRenderTarget(1, 1, { minFilter: THREE.NearestFilter, magFilter: THREE.NearestFilter,
                                                      stencilBuffer: true });
    this.numbers = new THREE.ShaderMaterial({ ...numberShader, side: THREE.DoubleSide, clipping: true });
    this.material = new THREE.ShaderMaterial({
      // Transparent, though drawn opaque: so drawn after see-through faces, which would cover them.
      vertexShader: strip, fragmentShader: plain, depthWrite: false, clipping: true, transparent: true,
      uniforms: { resolution: { value: new THREE.Vector2(1, 1) }, width: { value: WIDTH },
                  color: { value: new THREE.Vector3(...COLOR.map((c) => c / 255)) } },
    });
    this.materials = [this.numbers, this.material]; // all of them, for the slice planes
  }

  // Give a part (an object the tree can hide) the edges of its faces, [{geometry,
  // material}] in its coordinates: each face's outline and folds sharper than angle
  // degrees. A part's faces are numbered and drawn together.
  add(part, faces, angle) {
    const numbers = [], lines = [];
    for (const { geometry, material } of faces) {
      lines.push(new THREE.EdgesGeometry(geometry, angle));
      if (!material.transparent) numbers.push(numbered(geometry, ++this.count)); // glass isn't numbered
    }
    if (numbers.length) {
      const front = new THREE.Mesh(mergeGeometries(numbers), this.numbers);
      front.layers.set(NUMBERS);
      part.add(front);
    }
    if (lines.length) {
      const mesh = new THREE.Mesh(strips(mergeGeometries(lines)), this.material);
      Object.assign(mesh, { frustumCulled: false, renderOrder: LAST });
      mesh.layers.set(EDGES);
      part.add(mesh);
    }
  }

  // The materials shared by every part, which a model being replaced mustn't dispose.
  shared(material) { return this.materials.includes(material); }

  show(shown) {
    this.shown = shown;
    if (shown) this.camera.layers.enable(EDGES);
    else this.camera.layers.disable(EDGES);
  }

  resize() {
    const size = this.renderer.getDrawingBufferSize(new THREE.Vector2());
    this.faces.setSize(size.x, size.y);
    this.material.uniforms.resolution.value.copy(size);
    this.material.uniforms.width.value = WIDTH * this.renderer.getPixelRatio();
  }

  // Number the faces in front, for the outline of a cut in the render that follows with camera.
  pass(scene, camera = this.camera) {
    if (!this.shown) return;
    const { renderer } = this, background = scene.background, layers = camera.layers.mask;
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
