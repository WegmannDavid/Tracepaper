// Mapping a drawing tablet onto the viewer's canvas (tracepaper/pen.py).
//
// The canvas's shape is fitted into the middle of the tablet at one scale for
// both axes, so shapes drawn on the tablet keep their proportions and every
// part of the canvas can be reached. The strips of the tablet left over (top
// and bottom, or left and right) do nothing.

// The part of the tablet that covers the canvas: {x, y, width, height} in tablet
// mm (x right, y down, as the tablet is held), and mmPerPx, the scale.
// tablet and stage are {width, height}, in mm and in pixels.
export function activeArea(tablet, stage) {
  const mmPerPx = Math.min(tablet.width / stage.width, tablet.height / stage.height);
  const width = stage.width * mmPerPx, height = stage.height * mmPerPx;
  return { x: (tablet.width - width) / 2, y: (tablet.height - height) / 2, width, height, mmPerPx };
}

// A point on the tablet (mm) as a point on the canvas (px from its top left).
export function tabletToStage(p, tablet, stage) {
  const a = activeArea(tablet, stage);
  return [(p[0] - a.x) / a.mmPerPx, (p[1] - a.y) / a.mmPerPx];
}

// Asks the viewer server to connect the tablet and draw in this window ('connect'),
// to only draw in this window ('active'), or to let the tablet go ('disconnect').
// client is this window's id. Resolves to the tablet's status.
export async function tablet(action, client) {
  const r = await fetch(`/api/pen/${action}`, {
    method: 'POST', headers: { 'Content-Type': 'application/json', 'X-Tracepaper': '1' },
    body: JSON.stringify({ client }),
  });
  if (!r.ok) throw new Error(`${r.status} ${await r.text()}`);
  return r.json();
}
