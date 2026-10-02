(() => {
  const source = document.getElementById('contest-problem-ids');
  const controls = document.getElementById('contest-problem-controls');
  if (!source || !controls) return;
  const list = document.getElementById('contest-problem-list');
  const input = document.getElementById('contest-add-id');
  const status = document.getElementById('contest-problem-status');
  const ids = source.value.split(/[\s,]+/).filter(Boolean);

  function label(position) {
    let value = position + 1, result = '';
    while (value > 0) {
      value -= 1;
      result = String.fromCharCode(65 + value % 26) + result;
      value = Math.floor(value / 26);
    }
    return result;
  }

  function render(focusIndex = -1, focusAction = '') {
    source.value = ids.join('\n');
    list.replaceChildren();
    ids.forEach((id, index) => {
      const row = document.createElement('li');
      row.dataset.problemId = id;
      const text = document.createElement('span');
      text.className = 'contest-problem-id mono';
      text.textContent = `${label(index)} · ${id}`;
      row.append(text);
      const actions = document.createElement('div');
      actions.className = 'actions';
      for (const [action, caption] of [['up', '↑'], ['down', '↓'], ['remove', 'Remove']]) {
        const button = document.createElement('button');
        button.type = 'button';
        button.className = 'button button-secondary';
        button.textContent = caption;
        button.dataset.action = action;
        button.setAttribute('aria-label', `${action === 'remove' ? 'Remove' : `Move ${action}`} ${id}`);
        button.disabled = action === 'up' && index === 0 || action === 'down' && index === ids.length - 1;
        button.addEventListener('click', () => {
          if (action === 'remove') {
            ids.splice(index, 1);
            render(Math.min(index, ids.length - 1), 'remove');
            if (!ids.length) input.focus();
            status.textContent = `Removed ${id}. Save contest to apply changes.`;
          } else {
            const target = index + (action === 'up' ? -1 : 1);
            [ids[index], ids[target]] = [ids[target], ids[index]];
            render(target, action);
            status.textContent = `Moved ${id} to ${label(target)}. Save contest to apply changes.`;
          }
        });
        actions.append(button);
      }
      row.append(actions);
      list.append(row);
    });
    const button = list.children[focusIndex]?.querySelector(`[data-action="${focusAction}"]`);
    if (button && !button.disabled) button.focus();
    else if (focusIndex >= 0) list.children[focusIndex]?.querySelector('[data-action="remove"]').focus();
  }

  function addProblem() {
    const id = input.value.trim();
    if (!/^[A-Za-z0-9][A-Za-z0-9-]{1,78}[A-Za-z0-9]$/.test(id)) {
      status.textContent = 'Enter a valid problem ID (3–80 ASCII letters, digits or -, starting and ending with a letter or digit).';
    } else if (ids.includes(id)) {
      status.textContent = `${id} is already in this contest.`;
    } else if (ids.length >= 100) {
      status.textContent = 'A contest supports at most 100 problems.';
    } else {
      ids.push(id);
      render();
      input.value = '';
      status.textContent = `Added ${id}. Save contest to apply changes; the server checks availability.`;
    }
    input.focus();
  }

  document.getElementById('contest-add-problem').addEventListener('click', addProblem);
  input.addEventListener('keydown', event => {
    if (event.key === 'Enter') {
      event.preventDefault();
      addProblem();
    }
  });
  render();
  source.closest('label').hidden = true;
  controls.hidden = false;
})();
