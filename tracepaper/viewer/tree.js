// The tree panel of both views: named nodes you can hide, per window.
//
// A node is { key, name, hidden, children }, where key is its path of names
// ("Steel / Z profile"). Each view builds its tree from its file (a sheet's named
// groups, a model's named nodes), does the hiding itself in onToggle, and calls
// renderTree again to show the result.

export const flatten = (nodes) => nodes.flatMap((n) => [n, ...flatten(n.children)]);

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
    const label = document.createElement('label');
    const box = document.createElement('input');
    box.type = 'checkbox';
    box.checked = !node.hidden;
    box.addEventListener('change', () => onToggle(node, !box.checked));
    const name = document.createElement('span');
    name.textContent = node.name;
    label.append(box, name);
    row.append(twisty, label);
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
