// The project page: the views folder's tree, where each view opens in a window of its own.
// Folders fold with a click, and this browser remembers which are folded.
import { $, listViews, viewTree, viewUrl } from './page.js';

let key = '';
let dir = null;
let folded = new Set(); // paths of the folders folded, for this views folder

const storageKey = () => `tracepaper.folded:${dir}`;

function remember() {
  try { localStorage.setItem(storageKey(), JSON.stringify([...folded])); } catch { /* not remembered */ }
}

function recall() {
  try { folded = new Set(JSON.parse(localStorage.getItem(storageKey())) ?? []); } catch { folded = new Set(); }
}

function ago(mtimeNs) {
  const s = Math.max(0, Math.round((Date.now() - Number(BigInt(mtimeNs) / 1000000n)) / 1000));
  if (s < 60) return 'just now';
  if (s < 3600) return `${Math.round(s / 60)} min ago`;
  if (s < 86400) return `${Math.round(s / 3600)} h ago`;
  return `${Math.round(s / 86400)} days ago`;
}

function span(className, text) {
  const el = document.createElement('span');
  el.className = className;
  el.textContent = text;
  return el;
}

const WAITING = 'sketch waiting for the AI · ';
const count = (f) => f.views.length + f.folders.reduce((n, sub) => n + count(sub), 0);
const waiting = (f) => f.views.some((v) => v.sketch) || f.folders.some(waiting);

function viewItem(v) {
  const li = document.createElement('li');
  const a = document.createElement('a');
  a.href = viewUrl(v.name);
  a.className = 'tpv-view-link';
  a.title = v.name;
  const info = span('tpv-view-info', ago(v.mtime));
  if (v.sketch) info.prepend(span('tpv-waiting', WAITING));
  a.append(span(`tpv-kind ${v.kind}`, v.kind === 'model' ? '3D' : '2D'), span('tpv-view-name', v.file), info);
  a.addEventListener('click', (e) => {
    if (e.ctrlKey || e.metaKey || e.shiftKey || e.button !== 0) return; // the browser's own way: a tab
    e.preventDefault();
    // One window per view: clicking again brings the open one forward.
    window.open(a.href, `tracepaper:${v.name}`, 'popup,width=1280,height=860');
  });
  li.append(a);
  return li;
}

// A folder's row, which folds it, and its contents below. While folded, the row says
// whether a sketch inside is waiting.
function folderItem(f) {
  const li = document.createElement('li');
  const row = document.createElement('button');
  row.className = 'tpv-folder';
  row.title = f.path;
  const twisty = span('tpv-folder-twisty', '');
  const n = count(f);
  const info = span('tpv-view-info', `${n} view${n === 1 ? '' : 's'}`);
  if (waiting(f)) info.prepend(span('tpv-waiting', WAITING));
  row.append(twisty, span('tpv-view-name', f.name), info);
  const contents = list(f);
  const show = (open) => {
    contents.hidden = !open;
    row.setAttribute('aria-expanded', String(open));
    twisty.textContent = open ? '▾' : '▸';
  };
  show(!folded.has(f.path));
  row.addEventListener('click', () => {
    const open = contents.hidden;
    show(open);
    if (open) folded.delete(f.path); else folded.add(f.path);
    remember();
  });
  li.append(row, contents);
  return li;
}

// Folders first, as in a file manager, then the views.
function list(f) {
  const ul = document.createElement('ul');
  ul.append(...f.folders.map(folderItem), ...f.views.map(viewItem));
  return ul;
}

function render(listing) {
  $('tpv-dir').textContent = listing.dir;
  document.title = `${listing.dir.split('/').pop()} · Tracepaper`;
  if (listing.dir !== dir) {
    dir = listing.dir;
    recall();
  }
  const next = JSON.stringify(listing.views);
  if (next === key) return;
  key = next;
  $('tpv-list').replaceChildren(...list(viewTree(listing.views)).children);
  $('tpv-empty').hidden = listing.views.length > 0;
  $('tpv-empty').textContent = 'No views yet: sheets (.svg) and models (.glb) in this folder and its subfolders show up here.';
}

async function refresh() {
  try {
    render(await listViews());
    $('tpv-status').textContent = '';
  } catch {
    $('tpv-status').textContent = 'Viewer server stopped';
  }
  setTimeout(refresh, 2000);
}

refresh();
