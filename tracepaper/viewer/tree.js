// The tree panel of both views: named nodes you can hide, per window.
//
// A node is { key, name, hidden, children }, where key is its path of names
// ("Steel / Z profile"). Each view builds its tree from its file (a sheet's named
// groups, a model's named nodes), does the hiding itself in onToggle, and calls
// renderTree again to show the result.

export const flatten = (nodes) => nodes.flatMap((n) => [n, ...flatten(n.children)]);

const LEFT = '<path d="M8 3.5Q3 3.5 1.5 8Q3 12.5 8 12.5"/>';
const RIGHT = '<path d="M8 3.5Q13 3.5 14.5 8Q13 12.5 8 12.5"/>';
const PUPIL = '<circle cx="8" cy="8" r="2"/>';
const EYES = {
  visible: LEFT + RIGHT + PUPIL,
  partial: `${LEFT}${PUPIL}<g opacity="0.3">${RIGHT}</g>`, // shown, but something below is hidden: half an eye
  hidden: `${LEFT}${RIGHT}${PUPIL}<path d="M2.5 2.5l11 11"/>`,
};
const hiddenBelow = (node) => node.children.some((c) => c.hidden || hiddenBelow(c));
const icon = (paths) => '<svg viewBox="0 0 16 16" width="16" height="16" aria-hidden="true" fill="none" '
  + `stroke="currentColor" stroke-width="1.3" stroke-linecap="round" stroke-linejoin="round">${paths}</svg>`;

// collapsed: a Set of the keys of folded nodes, kept by the view.
// topFirst: list children last-to-first, for drawings where later is on top.
// mark(node): extra class names for a node's row.
export function renderTree(container, roots, { collapsed, onToggle, topFirst = false, mark = () => '' }) {
  const order = (nodes) => (topFirst ? [...nodes].reverse() : nodes);
  const item = (node, hiddenAbove) => {
    const open = !collapsed.has(node.key);
    const li = document.createElement('li');
    const row = document.createElement('div');
    row.className = `tpv-layer${hiddenAbove ? ' tpv-under-hidden' : ''}`;
    const twisty = document.createElement('button');
    twisty.className = 'tpv-twisty';
    if (node.children.length) {
      twisty.textContent = open ? '▾' : '▸';
      twisty.setAttribute('aria-label', `${open ? 'Fold' : 'Unfold'} ${node.name}`);
      twisty.addEventListener('click', () => {
        if (open) collapsed.add(node.key); else collapsed.delete(node.key);
        renderTree(container, roots, { collapsed, onToggle, topFirst, mark });
      });
    } else {
      twisty.disabled = true;
    }
    const eye = document.createElement('button');
    const state = node.hidden ? 'hidden' : hiddenBelow(node) ? 'partial' : 'visible';
    eye.className = 'tpv-eye';
    eye.dataset.visible = String(!node.hidden);
    eye.dataset.state = state;
    eye.title = node.hidden ? 'Show' : state === 'partial' ? 'Hide (some parts below are hidden)' : 'Hide';
    eye.setAttribute('aria-label', `${node.hidden ? 'Show' : 'Hide'} ${node.name}`);
    eye.innerHTML = icon(EYES[state]);
    eye.addEventListener('click', () => onToggle(node, !node.hidden));
    const name = document.createElement('span');
    name.className = 'tpv-node-name';
    name.textContent = node.name;
    row.append(twisty, eye, name);
    li.append(row);
    li.className = mark(node);
    if (node.children.length && open) {
      const ul = document.createElement('ul');
      ul.append(...order(node.children).map((c) => item(c, hiddenAbove || node.hidden)));
      li.append(ul);
    }
    return li;
  };
  container.replaceChildren(...order(roots).map((n) => item(n, false)));
}
