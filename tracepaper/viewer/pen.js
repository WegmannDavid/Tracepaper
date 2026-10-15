// The drawing tablet (tracepaper/pen.py) in the viewer's pages: how it maps onto a
// canvas, the requests to the server about it, and its connection and toolbar (Tablet).
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

// The tablet as a page sees it: its status, which the server sends to every window, which
// window the pen draws in, and the toolbar's status (label) and button for it. A page
// that draws (a sheet's) gets the pen's frames (onFrame) and can connect the tablet;
// any page can let it go while the pen is with it, and takes the pen when the person
// uses the page with the mouse (claim). describe(status) gives {text, title} for the
// label while the tablet draws in this window.
export class Tablet {
  constructor({ client, draws, label, button, toast, describe, onStatus = () => {}, onFrame = () => {} }) {
    Object.assign(this, { client, draws, label, button, toast, describe, onStatus, onFrame });
    this.status = null; // null: the server has no tablet
    button.addEventListener('click', () => this.toggle());
  }

  get here() { return this.status?.active === this.client; }

  // The tablet's size, as it is held, in mm.
  get size() { return this.status && { width: this.status.width, height: this.status.height }; }

  connect() {
    const source = new EventSource(`/api/pen?client=${encodeURIComponent(this.client)}&draws=${this.draws ? 1 : 0}`);
    source.addEventListener('status', (e) => this.update(JSON.parse(e.data)));
    source.addEventListener('pen', (e) => this.onFrame(JSON.parse(e.data)));
    source.addEventListener('error', () => {
      if (source.readyState === EventSource.CLOSED) this.update(null); // no tablet on this server
      else if (this.status) this.update({ ...this.status, state: 'error', message: 'viewer server stopped' });
    });
  }

  update(status) {
    this.status = status;
    this.onStatus(status);
    this.render();
  }

  claim() {
    if (this.status && !this.here) tablet('active', this.client).catch(() => {});
  }

  // Connect to draw here, taking the tablet from wherever it is; or let it go.
  async toggle() {
    const action = this.button.dataset.action;
    this.button.disabled = true;
    if (action === 'disconnect') this.button.textContent = 'Disconnecting…';
    try {
      await tablet(action, this.client);
    } catch (err) {
      this.toast(`Could not ${action} the tablet: ${err.message}`, true);
    } finally {
      this.button.disabled = false;
      this.render();
    }
  }

  render() {
    const s = this.status;
    const mine = !!s && s.state !== 'off' && this.here;
    this.label.hidden = !s || (s.state === 'off' && !s.message);
    this.button.hidden = !s || !(this.draws || mine);
    if (!s) return;
    this.button.dataset.action = mine ? 'disconnect' : 'connect';
    if (!this.button.disabled) {
      this.button.textContent = mine ? 'Disconnect' : s.state === 'off' ? 'Connect reMarkable' : 'Connect here';
    }
    this.button.title = mine ? 'Let the tablet go: its own app starts again'
      : s.state === 'off' ? "Draw with the reMarkable's pen in this window. Its own app is closed until you "
        + 'disconnect; another Tracepaper viewer that has it lets it go.'
      : "Draw with the reMarkable's pen in this window";
    let text = 'reMarkable: connecting…', title = s.message;
    if (s.state === 'connected' && !this.here) {
      text = 'reMarkable · in another window';
      title = this.draws ? 'The pen draws in the window you used last. Click into this one, or press Connect here, '
        + 'to draw here.' : 'The pen draws in the window you used last.';
    } else if (s.state === 'connected') {
      ({ text, title } = this.describe(s));
    } else if (s.state === 'error' || s.state === 'off') {
      text = `reMarkable: ${s.message || s.state}`;
    }
    this.label.textContent = text;
    this.label.title = title;
    this.label.className = `tpv-pen ${s.state}`;
  }
}
