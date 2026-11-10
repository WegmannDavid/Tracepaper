// The picture of a window's view that the tablet's screen shows (tracepaper/pen.py): the
// tablet's whole screen as it is held, with the view drawn over the part of it that the
// pen covers (pen.js), so the pen lines up with what it draws on, and a grey frame round
// that part. A sheet's view is drawn from the sheet (renderView), a 3D view's from what
// its window shows (renderLayers).
import { activeArea } from './pen.js';
import { SVG_NS } from './sheetdoc.js';

// sheet: a copy of the sheet's root that can be changed; cam: its pan and zoom in the
// window, {s, x, y}; stage: the view's size in CSS pixels; size: the tablet in mm and
// pixels: its screen, both as held; font: the page's font, which the sheet's text
// inherits. Gives a canvas.
export async function renderView({ sheet, cam, stage, size, pixels, font }) {
  const a = activeArea(size, stage), k = pixels[0] / size.width; // screen pixels per tablet mm
  sheet.setAttribute('overflow', 'visible');
  const svg = `<svg xmlns="${SVG_NS}" width="${a.width * k}" height="${a.height * k}" `
    + `viewBox="0 0 ${stage.width} ${stage.height}" font-family="${font.replaceAll('"', "'")}">`
    + '<style>* { vector-effect: non-scaling-stroke; }</style>' // as in the window: lines in pixels (SPEC-v0.md §2.1)
    + `<g transform="translate(${cam.x} ${cam.y}) scale(${cam.s})">${new XMLSerializer().serializeToString(sheet)}</g></svg>`;
  const url = URL.createObjectURL(new Blob([svg], { type: 'image/svg+xml' }));
  try {
    const img = new Image();
    img.src = url;
    await img.decode();
    return screen(pixels, a, k, (ctx) => ctx.drawImage(img, a.x * k, a.y * k, a.width * k, a.height * k));
  } finally {
    URL.revokeObjectURL(url);
  }
}

// The tablet's screen with layers drawn over the part the pen covers, the first lowest,
// for a 3D window: each a canvas, or an SVG element to draw at the stage's size, such as
// the ink over the model. All are taken now, so a WebGL canvas just drawn still holds its
// picture.
export async function renderLayers({ layers, stage, size, pixels }) {
  const a = activeArea(size, stage), k = pixels[0] / size.width;
  const images = layers.map((layer) => {
    if (!(layer instanceof SVGSVGElement)) {
      const copy = Object.assign(document.createElement('canvas'), { width: layer.width, height: layer.height });
      copy.getContext('2d').drawImage(layer, 0, 0);
      return copy;
    }
    const svg = layer.cloneNode(true);
    svg.setAttribute('width', stage.width);
    svg.setAttribute('height', stage.height);
    return svg;
  });
  const drawn = [];
  for (const image of images) {
    if (!(image instanceof SVGSVGElement)) { drawn.push(image); continue; }
    const url = URL.createObjectURL(new Blob([new XMLSerializer().serializeToString(image)], { type: 'image/svg+xml' }));
    try {
      const img = new Image();
      img.src = url;
      await img.decode();
      drawn.push(img);
    } finally {
      URL.revokeObjectURL(url);
    }
  }
  return screen(pixels, a, k, (ctx) => {
    for (const img of drawn) ctx.drawImage(img, a.x * k, a.y * k, a.width * k, a.height * k);
  });
}

// A white screen of pixels with draw(ctx) on it, and a grey frame round a: the part the
// pen covers, in tablet mm, k screen pixels to the mm.
function screen([w, h], a, k, draw) {
  const canvas = document.createElement('canvas');
  Object.assign(canvas, { width: w, height: h });
  const ctx = canvas.getContext('2d');
  ctx.fillStyle = '#fff';
  ctx.fillRect(0, 0, w, h);
  draw(ctx);
  ctx.strokeStyle = '#999'; // the edge of where the pen draws
  ctx.lineWidth = 2;
  ctx.strokeRect(a.x * k - 1, a.y * k - 1, a.width * k + 2, a.height * k + 2);
  return canvas;
}
