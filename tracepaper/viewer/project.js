// The project page: every view in the project folder, each opened in a window of its own.
const $ = (id) => document.getElementById(id);
const viewUrl = (name) => `/view/${name.split('/').map(encodeURIComponent).join('/')}`;
let key = '';

function ago(mtimeNs) {
  const s = Math.max(0, Math.round((Date.now() - Number(BigInt(mtimeNs) / 1000000n)) / 1000));
  if (s < 60) return 'just now';
  if (s < 3600) return `${Math.round(s / 60)} min ago`;
  if (s < 86400) return `${Math.round(s / 3600)} h ago`;
  return `${Math.round(s / 86400)} days ago`;
}

function render(listing) {
  $('tpv-dir').textContent = listing.dir;
  document.title = `${listing.dir.split('/').pop()} · Tracepaper`;
  const next = JSON.stringify(listing.views);
  if (next === key) return;
  key = next;
  $('tpv-list').replaceChildren(...listing.views.map((v) => {
    const li = document.createElement('li');
    const a = document.createElement('a');
    a.href = viewUrl(v.name);
    a.className = 'tpv-view-link';
    const kind = document.createElement('span');
    kind.className = `tpv-kind ${v.kind}`;
    kind.textContent = v.kind === 'model' ? '3D' : '2D';
    const name = document.createElement('span');
    name.className = 'tpv-view-name';
    name.textContent = v.name;
    const info = document.createElement('span');
    info.className = 'tpv-view-info';
    info.textContent = `${v.sketch ? 'sketch waiting for the AI · ' : ''}${ago(v.mtime)}`;
    a.append(kind, name, info);
    a.addEventListener('click', (e) => {
      if (e.ctrlKey || e.metaKey || e.shiftKey || e.button !== 0) return; // the browser's own way: a tab
      e.preventDefault();
      // One window per view: clicking again brings the open one forward.
      window.open(a.href, `tracepaper:${v.name}`, 'popup,width=1280,height=860');
    });
    li.append(a);
    return li;
  }));
  $('tpv-empty').hidden = listing.views.length > 0;
  $('tpv-empty').textContent = 'No views yet: sheets (.svg) and models (.glb) in this folder show up here.';
}

async function refresh() {
  try {
    render(await (await fetch('/api/views', { cache: 'no-store' })).json());
    $('tpv-status').textContent = '';
  } catch {
    $('tpv-status').textContent = 'Viewer server stopped';
  }
  setTimeout(refresh, 2000);
}

refresh();
