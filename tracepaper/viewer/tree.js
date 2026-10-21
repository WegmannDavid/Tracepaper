// The tree panel of both views: named nodes you can hide, per window, as in the OCP viewer.
//
// A node is { key, name, hidden, own, color, children }, where key is its path of names
// ("Steel / Z profile"). hidden says whether the node's own drawing is hidden: what it
// draws itself, not what the nodes inside it draw, which have their own. own is false for
// a node that draws nothing itself, such as a group of parts; color, if given, is the one
// colour it is drawn in.
//
// Each node's eye shows the node with everything inside it: all shown, all hidden, or
// partly (half an eye). Clicking it shows or hides all of that, so a node inside a
// hidden one can be shown again on its own, whatever the nodes around it are. Ctrl+click
// on a name shows only that node (and once it does, everything again); Alt+click hides it.
// Each view builds its tree from its file, makes the changes in onChange, and calls
// renderTree again to show the result.

export const flatten = (nodes) => nodes.flatMap((n) => [n, ...flatten(n.children)]);

const LEFT = '<path d="M8 3.5Q3 3.5 1.5 8Q3 12.5 8 12.5"/>';
const RIGHT = '<path d="M8 3.5Q13 3.5 14.5 8Q13 12.5 8 12.5"/>';
const PUPIL = '<circle cx="8" cy="8" r="2"/>';
const EYES = {
  visible: LEFT + RIGHT + PUPIL,
  partial: `${LEFT}${PUPIL}<g opacity="0.3">${RIGHT}</g>`, // partly shown: half an eye
  hidden: `${LEFT}${RIGHT}${PUPIL}<path d="M2.5 2.5l11 11"/>`,
};
const icon = (paths) => '<svg viewBox="0 0 16 16" width="16" height="16" aria-hidden="true" fill="none" '
  + `stroke="currentColor" stroke-width="1.3" stroke-linecap="round" stroke-linejoin="round">${paths}</svg>`;

const draws = (node) => node.own !== false || !node.children.length;

// 'visible', 'hidden' or 'partial': whether the node and everything inside it shows.
export function stateOf(node) {
  const states = new Set(node.children.map(stateOf));
  if (draws(node)) states.add(node.hidden ? 'hidden' : 'visible');
  return states.size === 1 ? [...states][0] : 'partial';
}

// The changes, [[node, hidden]], each hiding or showing a node with everything inside it,
// that show only node: or everything again, if that is all that shows already.
export function isolating(roots, node) {
  const mine = new Set(flatten([node]));
  const only = flatten(roots).every((n) => !draws(n) || (mine.has(n) ? !n.hidden : n.hidden));
  return only ? roots.map((r) => [r, false]) : [...roots.map((r) => [r, true]), [node, false]];
}

// The nodes whose own drawing is hidden while that of the node around them isn't, and those
// shown inside a node whose own drawing is hidden: {hidden: [key], shown: [key]}, for what
// a saved sketch says was hidden.
export function hiddenAndShown(nodes, hiddenAbove = false) {
  const out = { hidden: [], shown: [] };
  for (const n of nodes) {
    if (n.hidden && !hiddenAbove) out.hidden.push(n.key);
    if (!n.hidden && hiddenAbove) out.shown.push(n.key);
    const below = hiddenAndShown(n.children, n.hidden);
    out.hidden.push(...below.hidden);
    out.shown.push(...below.shown);
  }
  return out;
}

// expanded: a Set of the keys of unfolded nodes, kept by the view. Everything else
// is folded, so a tree starts with only its top level showing.
// onChange(changes): show or hide, for each [node, hidden] in turn, the node with
// everything inside it.
// topFirst: list children last-to-first, for drawings where later is on top.
// mark(node): extra class names for a node's row.
export function renderTree(container, roots, { expanded, onChange, topFirst = false, mark = () => '' }) {
  const order = (nodes) => (topFirst ? [...nodes].reverse() : nodes);
  const item = (node) => {
    const open = expanded.has(node.key);
    const li = document.createElement('li');
    const row = document.createElement('div');
    row.className = 'tpv-layer';
    const twisty = document.createElement('button');
    twisty.className = 'tpv-twisty';
    if (node.children.length) {
      twisty.textContent = open ? '▾' : '▸';
      twisty.setAttribute('aria-label', `${open ? 'Fold' : 'Unfold'} ${node.name}`);
      twisty.addEventListener('click', () => {
        if (open) expanded.delete(node.key); else expanded.add(node.key);
        renderTree(container, roots, { expanded, onChange, topFirst, mark });
      });
    } else {
      twisty.disabled = true;
    }
    const eye = document.createElement('button');
    const state = stateOf(node);
    eye.className = 'tpv-eye';
    eye.dataset.visible = String(state !== 'hidden');
    eye.dataset.state = state;
    eye.title = state === 'visible' ? 'Hide' : state === 'partial' ? 'Show all of it (some of it is hidden)' : 'Show';
    eye.setAttribute('aria-label', `${state === 'visible' ? 'Hide' : 'Show'} ${node.name}`);
    eye.innerHTML = icon(EYES[state]);
    eye.addEventListener('click', () => onChange([[node, state === 'visible']]));
    const name = document.createElement('span');
    name.className = 'tpv-node-name';
    name.textContent = node.name;
    // In full, for when the panel is too narrow for it.
    name.title = `${node.name}\nCtrl+click: show only this · Alt+click: hide it`;
    name.addEventListener('click', (e) => {
      if (e.ctrlKey || e.metaKey) onChange(isolating(roots, node));
      else if (e.altKey) onChange([[node, true]]);
      else return;
      e.preventDefault();
    });
    name.addEventListener('mousedown', (e) => { if (e.ctrlKey || e.metaKey || e.altKey) e.preventDefault(); }); // no text selection
    row.append(twisty, eye);
    if (node.color) {
      const dot = document.createElement('span');
      dot.className = 'tpv-node-color';
      dot.style.setProperty('--c', node.color);
      row.append(dot);
    }
    row.append(name);
    li.append(row);
    li.className = mark(node);
    if (node.children.length && open) {
      const ul = document.createElement('ul');
      ul.append(...order(node.children).map(item));
      li.append(ul);
    }
    return li;
  };
  container.replaceChildren(...order(roots).map(item));
}
