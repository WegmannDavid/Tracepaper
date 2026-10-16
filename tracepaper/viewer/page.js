// What the viewer's pages share: this window's id, links to views and the menu of them,
// fetching from the server, short messages, and the divider before the tree panel.

export const $ = (id) => document.getElementById(id);

// Identifies this window to the server, which sends the tablet's pen to the window used last.
export const CLIENT = crypto.randomUUID?.() ?? `${Date.now()}-${Math.random()}`;

export const viewUrl = (name) => `/view/${name.split('/').map(encodeURIComponent).join('/')}`;

export async function fetchOk(url, init = {}) {
  const r = await fetch(url, { cache: 'no-store', ...init });
  if (!r.ok) throw new Error(`${r.status} ${await r.text()}`);
  return r;
}

// The project's views: {dir, initial, views: [{name, kind, mtime, sketch}]}.
export const listViews = async () => (await fetchOk('/api/views')).json();

// The menu of views in a page's toolbar, with current chosen. It is only rebuilt when
// the views change, so that it doesn't close while the person has it open.
export function renderViewsMenu(select, views, current) {
  const key = JSON.stringify(views.map((v) => [v.name, v.kind, !!v.sketch]));
  if (select.dataset.key !== key) {
    select.dataset.key = key;
    select.replaceChildren(...views.map((v) => new Option(
      `${v.name}${v.kind === 'model' ? '  (3D)' : ''}${v.sketch ? ' ✎' : ''}`, v.name)));
  }
  select.value = current ?? '';
  select.disabled = views.length === 0;
}

// A function that shows a message in el for a moment; longer for errors.
export function toaster(el) {
  let timer;
  return (message, error = false) => {
    el.textContent = message;
    el.classList.toggle('error', error);
    el.hidden = false;
    clearTimeout(timer);
    timer = setTimeout(() => { el.hidden = true; }, error ? 6000 : 2500);
  };
}

// The divider between a page's view and its tree panel (in main), dragged to share the
// width between them. The width is remembered in this browser, for every window;
// double-clicking the divider goes back to the usual width.
const PANEL_KEY = 'tracepaper.panel';
const MIN_PANEL = 140, MIN_VIEW = 200; // pixels

export function panelDivider(main) {
  const divider = document.createElement('div');
  divider.className = 'tpv-splitter';
  divider.setAttribute('role', 'separator');
  divider.setAttribute('aria-orientation', 'vertical');
  divider.title = 'Drag to make the tree wider or narrower; double-click for the usual width';
  main.append(divider);
  let width = null;
  const set = (px) => {
    width = Math.round(Math.max(MIN_PANEL, Math.min(px, (main.clientWidth || window.innerWidth) - MIN_VIEW)));
    main.style.setProperty('--tpv-panel', `${width}px`);
  };
  try {
    const saved = Number(localStorage.getItem(PANEL_KEY));
    if (saved > 0) set(saved);
  } catch { /* no storage: the usual width */ }

  divider.addEventListener('pointerdown', (e) => {
    if (e.button !== 0) return;
    e.preventDefault();
    try { divider.setPointerCapture(e.pointerId); } catch { /* still works while over it */ }
    divider.classList.add('dragging');
    const move = (ev) => set(main.getBoundingClientRect().right - ev.clientX);
    const end = () => {
      divider.removeEventListener('pointermove', move);
      divider.removeEventListener('pointerup', end);
      divider.removeEventListener('pointercancel', end);
      divider.classList.remove('dragging');
      try { if (width !== null) localStorage.setItem(PANEL_KEY, String(width)); } catch { /* not remembered */ }
    };
    divider.addEventListener('pointermove', move);
    divider.addEventListener('pointerup', end);
    divider.addEventListener('pointercancel', end);
  });
  window.addEventListener('resize', () => { if (width !== null) set(width); }); // still leaving the view room
  divider.addEventListener('dblclick', () => {
    width = null;
    main.style.removeProperty('--tpv-panel');
    try { localStorage.removeItem(PANEL_KEY); } catch { /* nothing remembered */ }
  });
}
