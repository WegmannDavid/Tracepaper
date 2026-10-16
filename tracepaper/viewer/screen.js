// The picture of a sheet window's view that the tablet's screen shows (tracepaper/pen.py):
// the tablet's whole screen as it is held, with the view drawn over the part of it that
// the pen covers (pen.js), so the pen lines up with what it draws on, and a grey frame
// round that part.
import { activeArea } from './pen.js';
import { SVG_NS } from './sheetdoc.js';

// sheet: a copy of the sheet's root that can be changed; cam: its pan and zoom in the
// window, {s, x, y}; stage: the view's size in CSS pixels; size: the tablet in mm and
// pixels: its screen, both as held; font: the page's font, which the sheet's text
// inherits. Gives a canvas.
export async function renderView({ sheet, cam, stage, size, pixels, font }) {
  const [w, h] = pixels;
  const a = activeArea(size, stage), k = w / size.width; // screen pixels per tablet mm
  sheet.setAttribute('overflow', 'visible');
  const svg = `<svg xmlns="${SVG_NS}" width="${a.width * k}" height="${a.height * k}" `
    + `viewBox="0 0 ${stage.width} ${stage.height}" font-family="${font.replaceAll('"', "'")}">`
    + '<style>* { vector-effect: non-scaling-stroke; }</style>' // as in the window: lines in pixels (SPEC.md §2.1)
    + `<g transform="translate(${cam.x} ${cam.y}) scale(${cam.s})">${new XMLSerializer().serializeToString(sheet)}</g></svg>`;
  const url = URL.createObjectURL(new Blob([svg], { type: 'image/svg+xml' }));
  try {
    const img = new Image();
    img.src = url;
    await img.decode();
    const canvas = document.createElement('canvas');
    Object.assign(canvas, { width: w, height: h });
    const ctx = canvas.getContext('2d');
    ctx.fillStyle = '#fff';
    ctx.fillRect(0, 0, w, h);
    ctx.drawImage(img, a.x * k, a.y * k, a.width * k, a.height * k);
    ctx.strokeStyle = '#999'; // the edge of where the pen draws
    ctx.lineWidth = 2;
    ctx.strokeRect(a.x * k - 1, a.y * k - 1, a.width * k + 2, a.height * k + 2);
    return canvas;
  } finally {
    URL.revokeObjectURL(url);
  }
}
