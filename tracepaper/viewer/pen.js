// The drawing tablet (tracepaper/pen.py) in the viewer's pages: how it maps onto a
// canvas, the requests to the server about it, its connection and toolbar (Tablet), and
// the pictures its screen shows (ScreenFeed).
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
// window the pen draws in, and the toolbar's status (label) and button for it. The page
// gets the pen's frames (onFrame) while the pen draws in it, over stage, the element the
// tablet covers. It can connect the tablet or let it go, and takes the pen when the
// person uses the page with the mouse (claim).
export class Tablet {
  constructor({ client, stage, label, button, toast, onStatus = () => {}, onFrame = () => {} }) {
    Object.assign(this, { client, stage, label, button, toast, onStatus, onFrame });
    this.status = null; // null: the server has no tablet
    button.addEventListener('click', () => this.toggle());
  }

  get here() { return this.status?.active === this.client; }

  // The tablet's size, as it is held, in mm.
  get size() { return this.status && { width: this.status.width, height: this.status.height }; }

  connect() {
    const source = new EventSource(`/api/pen?client=${encodeURIComponent(this.client)}`);
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
    this.button.hidden = !s;
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
      title = 'The pen draws in the window you used last. Click into this one, or press Connect here, to draw here.';
    } else if (s.state === 'connected') {
      ({ text, title } = this.describe(s));
    } else if (s.state === 'error' || s.state === 'off') {
      text = `reMarkable: ${s.message || s.state}`;
    }
    this.label.textContent = text;
    this.label.title = title;
    this.label.className = `tpv-pen ${s.state}`;
  }

  // The label while the pen draws here: how much of the tablet covers the view.
  describe(s) {
    const r = this.stage.getBoundingClientRect();
    if (!r.width || !r.height) return { text: 'reMarkable', title: s.message };
    const a = activeArea(this.size, r), size = `${Math.round(a.width)} × ${Math.round(a.height)} mm`;
    return { text: `reMarkable · ${size}`, title: `${s.message}. The middle ${size} of the tablet covers this `
      + 'view, so drawings keep their proportions; the rest of the tablet does nothing.' };
  }
}

const SCREEN_DELAY_MS = 400;

// While the tablet's pen draws in a page and the tablet has a screen, this sends the screen
// picture(), a canvas (or a promise of one, or null for nothing to show), shortly after
// the page says its view changed and not while busy() or the pen is down. Each picture
// says up to which stroke it has the pen's ink (pen.done, ink.js), which the tablet then
// stops drawing itself.
export class ScreenFeed {
  constructor({ tablet, pen, client, picture, busy = () => false }) {
    Object.assign(this, { tablet, pen, client, picture, busy, timer: null });
  }

  get showing() {
    const s = this.tablet.status;
    return s?.state === 'connected' && !!s.screen && this.tablet.here;
  }

  changed() {
    if (!this.showing) return;
    clearTimeout(this.timer);
    this.timer = setTimeout(() => this.send(), SCREEN_DELAY_MS);
  }

  async send() {
    if (!this.showing) return;
    if (this.pen.down || this.busy()) { this.changed(); return; }
    const done = this.pen.done; // the strokes up to this one are in the picture
    const canvas = await this.picture();
    const png = canvas && await new Promise((resolve) => canvas.toBlob(resolve, 'image/png'));
    if (!png || !this.showing) return;
    fetch(`/api/pen/view?client=${encodeURIComponent(this.client)}&done=${done}`, {
      method: 'POST', headers: { 'Content-Type': 'image/png', 'X-Tracepaper': '1' }, body: png,
    }).catch(() => {});
  }
}
