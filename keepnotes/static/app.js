'use strict';

const COLORS = ['default', 'red', 'orange', 'yellow', 'green', 'teal', 'blue', 'purple', 'pink', 'brown', 'grey'];
const VIEW_NAMES = { notes: 'Notes', reminders: 'Reminders', archive: 'Archive', bin: 'Bin' };
const EMPTY_TEXT = {
  notes: 'Notes you add appear here.',
  reminders: 'Notes with a reminder appear here.',
  archive: 'Archived notes appear here.',
  bin: 'The bin is empty.',
};
const REORDERABLE_VIEWS = ['notes', 'archive'];
const REFRESH_MS = 60000;
const SEARCH_DELAY_MS = 250;
const TOAST_MS = 3500;
const ICONS = {
  pin: 'M12 17v5M9 3h6l-1 6 3 3H7l3-3-1-6z',
  palette: 'M12 3a9 9 0 1 0 0 18c1.1 0 2-.9 2-2 0-1.1-.9-1.5-.9-2.6 0-1 .9-1.9 1.9-1.9H17a4 4 0 0 0 4-4C21 6.4 17 3 12 3zM7.5 11.5h.01M10 7.5h.01M14.5 7.5h.01',
  archive: 'M3 4h18v4H3zM5 8v12h14V8M10 12h4',
  unarchive: 'M3 4h18v4H3zM5 8v12h14V8M12 18v-6M9.5 14.5 12 12l2.5 2.5',
  trash: 'M4 7h16M10 11v6M14 11v6M6 7l1 13h10l1-13M9 7V4h6v3',
  restore: 'M3 12a9 9 0 1 0 3-6.7L3 8M3 3v5h5',
  forever: 'M4 7h16M6 7l1 13h10l1-13M9 7V4h6v3M10 11l4 5M14 11l-4 5',
  grip: 'M9 6h.01M15 6h.01M9 12h.01M15 12h.01M9 18h.01M15 18h.01',
  bell: 'M6 8a6 6 0 1 1 12 0c0 7 3 9 3 9H3s3-2 3-9M10.3 21a1.9 1.9 0 0 0 3.4 0',
  edit: 'M4 20h4L19 9l-4-4L4 16zM14 6l4 4',
  close: 'M6 6l12 12M18 6 6 18',
};

const state = { view: 'notes', query: '', tag: '', notes: [], tags: [], due: 0, csrf: '', editing: null, dragging: false };
const byId = (id) => document.getElementById(id);
const dateFormat = new Intl.DateTimeFormat(undefined, { weekday: 'short', day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' });

class ApiError extends Error {
  constructor(message, status) {
    super(message);
    this.status = status;
  }
}

async function api(method, path, body) {
  const headers = { Accept: 'application/json' };
  if (method !== 'GET') headers['X-CSRF-Token'] = state.csrf;
  if (body !== undefined) headers['Content-Type'] = 'application/json';
  const response = await fetch(path, { method, headers, body: body === undefined ? undefined : JSON.stringify(body), credentials: 'same-origin' });
  if (response.status === 204) return null;
  const data = await response.json().catch(() => null);
  if (!response.ok) {
    const message = data && data.error ? data.error : `the server answered ${response.status}`;
    throw new ApiError(message, response.status);
  }
  return data;
}

let toastTimer = 0;
function showToast(message, isError = false) {
  const toast = byId('toast');
  toast.textContent = message;
  toast.classList.toggle('error', isError);
  toast.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { toast.hidden = true; }, TOAST_MS);
}

function reportError(error) {
  if (error instanceof ApiError && error.status === 401) {
    showToast('You are signed out. Reload the page to sign in again.', true);
    return;
  }
  showToast(error.message || String(error), true);
}

function icon(name) {
  const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
  svg.setAttribute('viewBox', '0 0 24 24');
  svg.setAttribute('class', 'icon');
  svg.setAttribute('aria-hidden', 'true');
  const path = document.createElementNS('http://www.w3.org/2000/svg', 'path');
  path.setAttribute('d', ICONS[name]);
  svg.append(path);
  return svg;
}

function element(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

function iconButton(name, label, onClick) {
  const button = element('button', 'icon-btn');
  button.type = 'button';
  button.title = label;
  button.setAttribute('aria-label', label);
  button.append(icon(name));
  button.addEventListener('click', (event) => {
    event.stopPropagation();
    onClick(event);
  });
  return button;
}

function toLocalInputValue(iso) {
  if (!iso) return '';
  const moment = new Date(iso);
  const pad = (value) => String(value).padStart(2, '0');
  return `${moment.getFullYear()}-${pad(moment.getMonth() + 1)}-${pad(moment.getDate())}T${pad(moment.getHours())}:${pad(moment.getMinutes())}`;
}

function fromLocalInputValue(value) {
  return value ? new Date(value).toISOString() : null;
}

function splitTags(text) {
  return text.split(',').map((tag) => tag.trim()).filter(Boolean);
}

function linesToItems(text) {
  return text.split('\n').map((line) => line.replace(/^\s*(?:[-*]\s*)?(?:\[[ xX]\]\s*)?/, '').trim()).filter(Boolean).map((line) => ({ text: line, checked: false }));
}

function itemsToLines(items) {
  return items.map((item) => item.text).join('\n');
}

async function changeNote(note, fields, message) {
  try {
    await api('PATCH', `/api/notes/${note.id}`, fields);
    if (message) showToast(message);
    await refresh();
  } catch (error) {
    reportError(error);
  }
}

async function noteAction(note, action, message) {
  try {
    await api('POST', `/api/notes/${note.id}/${action}`);
    showToast(message);
    await refresh();
  } catch (error) {
    reportError(error);
  }
}

async function deleteForever(note) {
  if (!confirm('Delete this note for good? This cannot be undone.')) return;
  try {
    await api('DELETE', `/api/notes/${note.id}`);
    showToast('Note deleted');
    await refresh();
  } catch (error) {
    reportError(error);
  }
}

function renderChecklist(note, isLocked) {
  const list = element('ul', 'checklist');
  for (const item of note.items) {
    const row = element('li', `check-row${item.checked ? ' done' : ''}`);
    const box = element('input');
    box.type = 'checkbox';
    box.checked = item.checked;
    box.disabled = isLocked;
    box.setAttribute('aria-label', item.text);
    box.addEventListener('click', (event) => event.stopPropagation());
    box.addEventListener('change', async () => {
      try {
        await api('PATCH', `/api/notes/${note.id}/items/${item.id}`, { checked: box.checked });
        row.classList.toggle('done', box.checked);
        item.checked = box.checked;
      } catch (error) {
        box.checked = !box.checked;
        reportError(error);
      }
    });
    row.append(box, element('span', 'check-text', item.text));
    list.append(row);
  }
  return list;
}

function renderChips(note) {
  const chips = element('div', 'chips');
  if (note.reminderAt) {
    const chip = element('button', `chip${note.reminderDue ? ' due' : ''}`);
    chip.type = 'button';
    chip.append(icon('bell'), element('span', '', dateFormat.format(new Date(note.reminderAt))));
    chip.title = note.reminderDue ? 'Reminder due: press to mark it done' : 'Reminder';
    chip.addEventListener('click', (event) => {
      event.stopPropagation();
      if (note.reminderDue && !note.trashed) changeNote(note, { reminderAt: null }, 'Reminder done');
    });
    chips.append(chip);
  }
  for (const tag of note.tags) {
    const chip = element('button', 'chip', `#${tag}`);
    chip.type = 'button';
    chip.addEventListener('click', (event) => {
      event.stopPropagation();
      selectTag(tag);
    });
    chips.append(chip);
  }
  return chips;
}

function openPalette(card, note) {
  const existing = card.querySelector('.palette');
  if (existing) {
    existing.remove();
    return;
  }
  const palette = element('div', 'palette');
  palette.setAttribute('role', 'radiogroup');
  palette.setAttribute('aria-label', 'Colour');
  for (const color of COLORS) {
    const swatch = element('button', 'swatch');
    swatch.type = 'button';
    swatch.dataset.color = color;
    swatch.title = color;
    swatch.setAttribute('role', 'radio');
    swatch.setAttribute('aria-label', color);
    swatch.setAttribute('aria-checked', String(note.color === color));
    swatch.addEventListener('click', (event) => {
      event.stopPropagation();
      palette.remove();
      changeNote(note, { color });
    });
    palette.append(swatch);
  }
  card.append(palette);
}

function renderTools(card, note) {
  const tools = element('div', 'note-tools');
  if (note.trashed) {
    tools.append(
      iconButton('restore', 'Restore', () => noteAction(note, 'restore', 'Note restored')),
      iconButton('forever', 'Delete for good', () => deleteForever(note)),
    );
    return tools;
  }
  if (REORDERABLE_VIEWS.includes(state.view)) {
    const grip = iconButton('grip', 'Drag to reorder, or use the arrow keys', () => {});
    grip.classList.add('grip');
    grip.addEventListener('pointerdown', (event) => startDrag(event, card));
    grip.addEventListener('keydown', (event) => moveWithKeys(event, card));
    tools.append(grip);
  }
  tools.append(
    iconButton('palette', 'Colour', () => openPalette(card, note)),
    iconButton('edit', 'Edit', () => openEditor(note)),
    note.archived
      ? iconButton('unarchive', 'Unarchive', () => changeNote(note, { archived: false }, 'Note unarchived'))
      : iconButton('archive', 'Archive', () => changeNote(note, { archived: true }, 'Note archived')),
    iconButton('trash', 'Move to bin', () => noteAction(note, 'trash', 'Note moved to the bin')),
  );
  return tools;
}

function renderNote(note) {
  const card = element('article', 'note');
  card.dataset.id = String(note.id);
  card.dataset.color = note.color;
  card.tabIndex = 0;
  const head = element('div', 'note-head');
  head.append(element('h3', 'note-title', note.title));
  if (!note.trashed) {
    const pin = iconButton('pin', note.pinned ? 'Unpin' : 'Pin', () => changeNote(note, { pinned: !note.pinned }));
    pin.setAttribute('aria-pressed', String(note.pinned));
    head.append(pin);
  }
  card.append(head);
  if (note.kind === 'checklist') {
    card.append(renderChecklist(note, note.trashed));
  } else if (note.bodyHtml) {
    const body = element('div', 'note-body');
    body.innerHTML = note.bodyHtml;
    card.append(body);
  }
  if (note.tags.length || note.reminderAt) card.append(renderChips(note));
  card.append(renderTools(card, note));
  card.addEventListener('click', (event) => {
    if (note.trashed || event.target.closest('a, button, input, .palette')) return;
    openEditor(note);
  });
  card.addEventListener('keydown', (event) => {
    if (event.key === 'Enter' && event.target === card && !note.trashed) openEditor(note);
  });
  return card;
}

function renderNotes() {
  const pinned = state.notes.filter((note) => note.pinned && !note.trashed && state.view !== 'reminders');
  const others = state.notes.filter((note) => !pinned.includes(note));
  byId('pinned-grid').replaceChildren(...pinned.map(renderNote));
  byId('others-grid').replaceChildren(...others.map(renderNote));
  byId('pinned-section').hidden = pinned.length === 0;
  byId('others-title').hidden = pinned.length === 0 || others.length === 0;
  const empty = byId('empty-state');
  empty.hidden = state.notes.length > 0;
  empty.textContent = state.query || state.tag ? 'No notes match.' : EMPTY_TEXT[state.view];
}

function renderSidebar() {
  for (const button of document.querySelectorAll('.view-btn')) {
    button.setAttribute('aria-current', String(button.dataset.view === state.view && !state.tag));
  }
  const tagButtons = state.tags.map((tag) => {
    const button = element('button', 'tag-btn');
    button.type = 'button';
    button.setAttribute('aria-current', String(state.tag.toLowerCase() === tag.name.toLowerCase()));
    button.append(element('span', '', tag.name), element('span', 'count', String(tag.count)));
    button.addEventListener('click', () => selectTag(tag.name));
    return button;
  });
  byId('tag-list').replaceChildren(...tagButtons);
  const badge = byId('due-badge');
  badge.hidden = state.due === 0;
  badge.textContent = String(state.due);
  byId('due-banner').hidden = state.due === 0 || state.view === 'reminders';
  byId('due-text').textContent = state.due === 1 ? '1 reminder is due' : `${state.due} reminders are due`;
  byId('composer').hidden = state.view !== 'notes' || Boolean(state.tag);
  byId('bin-line').hidden = state.view !== 'bin' || state.notes.length === 0;
  const filters = [];
  if (state.tag) filters.push(`tag #${state.tag}`);
  if (state.query) filters.push(`search "${state.query}"`);
  byId('filter-line').hidden = filters.length === 0;
  byId('filter-text').textContent = `${VIEW_NAMES[state.view]}, ${filters.join(' and ')}`;
}

async function refresh() {
  const parameters = new URLSearchParams({ view: state.view });
  if (state.query) parameters.set('q', state.query);
  if (state.tag) parameters.set('tag', state.tag);
  const [listing, tags] = await Promise.all([api('GET', `/api/notes?${parameters}`), api('GET', '/api/tags')]);
  state.notes = listing.notes;
  state.due = listing.dueReminders;
  state.tags = tags.tags;
  renderNotes();
  renderSidebar();
}

function safeRefresh() {
  refresh().catch(reportError);
}

function selectView(view) {
  state.view = view;
  state.tag = '';
  safeRefresh();
}

function selectTag(tag) {
  state.view = 'notes';
  state.tag = tag;
  safeRefresh();
}

function sectionIds() {
  return [...document.querySelectorAll('#pinned-grid .note, #others-grid .note')].map((card) => Number(card.dataset.id));
}

async function saveOrder() {
  try {
    await api('POST', '/api/notes/reorder', { ids: sectionIds() });
  } catch (error) {
    reportError(error);
  }
  await refresh().catch(reportError);
}

function startDrag(event, card) {
  if (event.button !== 0) return;
  event.preventDefault();
  const grid = card.parentElement;
  const pointerId = event.pointerId;
  card.classList.add('dragging');
  state.dragging = true;
  let moved = false;

  const onMove = (moveEvent) => {
    if (moveEvent.pointerId !== pointerId) return;
    const target = document.elementsFromPoint(moveEvent.clientX, moveEvent.clientY)
      .map((node) => node.closest('.note'))
      .find((node) => node && node !== card && node.parentElement === grid);
    if (!target) return;
    const cards = [...grid.children];
    const isAfter = cards.indexOf(target) > cards.indexOf(card);
    grid.insertBefore(card, isAfter ? target.nextSibling : target);
    moved = true;
  };
  const onUp = (upEvent) => {
    if (upEvent.pointerId !== pointerId) return;
    window.removeEventListener('pointermove', onMove);
    window.removeEventListener('pointerup', onUp);
    window.removeEventListener('pointercancel', onUp);
    card.classList.remove('dragging');
    state.dragging = false;
    if (moved) saveOrder();
  };
  window.addEventListener('pointermove', onMove);
  window.addEventListener('pointerup', onUp);
  window.addEventListener('pointercancel', onUp);
}

function moveWithKeys(event, card) {
  const backwards = event.key === 'ArrowUp' || event.key === 'ArrowLeft';
  const forwards = event.key === 'ArrowDown' || event.key === 'ArrowRight';
  if (!backwards && !forwards) return;
  event.preventDefault();
  const neighbour = backwards ? card.previousElementSibling : card.nextElementSibling;
  if (!neighbour) return;
  card.parentElement.insertBefore(card, backwards ? neighbour : neighbour.nextSibling);
  saveOrder().then(() => {
    const grip = document.querySelector(`.note[data-id="${card.dataset.id}"] .grip`);
    if (grip) grip.focus();
  });
}

function setComposerKind(isChecklist) {
  const toggle = byId('composer-kind');
  toggle.setAttribute('aria-pressed', String(isChecklist));
  byId('composer-body').placeholder = isChecklist ? 'One item per line' : 'Take a note (Markdown works)';
}

async function addNote(event) {
  event.preventDefault();
  const isChecklist = byId('composer-kind').getAttribute('aria-pressed') === 'true';
  const title = byId('composer-title').value;
  const text = byId('composer-body').value;
  const fields = isChecklist ? { kind: 'checklist', title, items: linesToItems(text) } : { kind: 'text', title, body: text };
  try {
    await api('POST', '/api/notes', fields);
    byId('composer').reset();
    setComposerKind(false);
    await refresh();
  } catch (error) {
    reportError(error);
  }
}

function editorItemRow(item) {
  const row = element('li', 'editor-item');
  const box = element('input');
  box.type = 'checkbox';
  box.checked = item.checked;
  box.setAttribute('aria-label', 'Done');
  const text = element('input', 'item-text');
  text.value = item.text;
  text.maxLength = 1000;
  text.setAttribute('aria-label', 'Item');
  row.append(box, text, iconButton('close', 'Remove item', () => row.remove()));
  return row;
}

function editorItems() {
  return [...byId('editor-item-list').children]
    .map((row) => ({ text: row.querySelector('.item-text').value.trim(), checked: row.querySelector('input[type="checkbox"]').checked }))
    .filter((item) => item.text);
}

function setEditorKind(kind) {
  const editor = byId('editor');
  editor.dataset.kind = kind;
  byId('editor-body').hidden = kind === 'checklist';
  byId('editor-items').hidden = kind !== 'checklist';
  byId('editor-kind').textContent = kind === 'checklist' ? 'Make text note' : 'Make checklist';
}

function switchEditorKind() {
  if (byId('editor').dataset.kind === 'checklist') {
    byId('editor-body').value = itemsToLines(editorItems());
    setEditorKind('text');
    return;
  }
  byId('editor-item-list').replaceChildren(...linesToItems(byId('editor-body').value).map(editorItemRow));
  setEditorKind('checklist');
}

function setEditorColor(color) {
  byId('editor').dataset.color = color;
  for (const swatch of byId('editor-colors').children) swatch.setAttribute('aria-checked', String(swatch.dataset.color === color));
}

function openEditor(note) {
  state.editing = note;
  byId('editor-title').value = note.title;
  byId('editor-body').value = note.body;
  byId('editor-item-list').replaceChildren(...note.items.map(editorItemRow));
  byId('editor-tags').value = note.tags.join(', ');
  byId('editor-reminder').value = toLocalInputValue(note.reminderAt);
  setEditorKind(note.kind);
  setEditorColor(note.color);
  byId('editor').showModal();
}

async function saveEditor(event) {
  event.preventDefault();
  const note = state.editing;
  if (!note) return;
  const editor = byId('editor');
  const kind = editor.dataset.kind;
  const fields = {
    kind,
    title: byId('editor-title').value,
    color: editor.dataset.color,
    tags: splitTags(byId('editor-tags').value),
    reminderAt: fromLocalInputValue(byId('editor-reminder').value),
  };
  if (kind === 'checklist') Object.assign(fields, { items: editorItems(), body: '' });
  else Object.assign(fields, { body: byId('editor-body').value, items: [] });
  try {
    await api('PATCH', `/api/notes/${note.id}`, fields);
    editor.close();
    state.editing = null;
    await refresh();
  } catch (error) {
    reportError(error);
  }
}

async function importFile(file) {
  try {
    const payload = JSON.parse(await file.text());
    const result = await api('POST', '/api/import', payload);
    showToast(`Imported ${result.imported} notes`);
    await refresh();
  } catch (error) {
    reportError(error instanceof SyntaxError ? new Error('That file is not a Keep Notes export') : error);
  }
}

async function emptyBin() {
  if (!confirm('Delete every note in the bin for good?')) return;
  try {
    const result = await api('POST', '/api/bin/empty');
    showToast(`Deleted ${result.removed} notes`);
    await refresh();
  } catch (error) {
    reportError(error);
  }
}

function buildEditorSwatches() {
  const swatches = COLORS.map((color) => {
    const swatch = element('button', 'swatch');
    swatch.type = 'button';
    swatch.dataset.color = color;
    swatch.title = color;
    swatch.setAttribute('role', 'radio');
    swatch.setAttribute('aria-label', color);
    swatch.addEventListener('click', () => setEditorColor(color));
    return swatch;
  });
  byId('editor-colors').replaceChildren(...swatches);
}

function wireEvents() {
  for (const button of document.querySelectorAll('.view-btn')) {
    button.addEventListener('click', () => selectView(button.dataset.view));
  }
  let searchTimer = 0;
  byId('search').addEventListener('input', (event) => {
    clearTimeout(searchTimer);
    searchTimer = setTimeout(() => {
      state.query = event.target.value.trim();
      safeRefresh();
    }, SEARCH_DELAY_MS);
  });
  byId('composer').addEventListener('submit', addNote);
  byId('composer-kind').addEventListener('click', () => setComposerKind(byId('composer-kind').getAttribute('aria-pressed') !== 'true'));
  byId('filter-clear').addEventListener('click', () => {
    state.tag = '';
    state.query = '';
    byId('search').value = '';
    safeRefresh();
  });
  byId('due-show').addEventListener('click', () => selectView('reminders'));
  byId('bin-empty').addEventListener('click', emptyBin);
  byId('import-btn').addEventListener('click', () => byId('import-file').click());
  byId('import-file').addEventListener('change', (event) => {
    const [file] = event.target.files;
    event.target.value = '';
    if (file) importFile(file);
  });
  byId('editor-form').addEventListener('submit', saveEditor);
  byId('editor-cancel').addEventListener('click', () => byId('editor').close());
  byId('editor').addEventListener('close', () => { state.editing = null; });
  byId('editor-kind').addEventListener('click', switchEditorKind);
  byId('editor-reminder-clear').addEventListener('click', () => { byId('editor-reminder').value = ''; });
  byId('editor-new-item').addEventListener('keydown', (event) => {
    if (event.key !== 'Enter') return;
    event.preventDefault();
    const text = event.target.value.trim();
    if (!text) return;
    byId('editor-item-list').append(editorItemRow({ text, checked: false }));
    event.target.value = '';
  });
  document.addEventListener('click', (event) => {
    if (!event.target.closest('.palette')) for (const palette of document.querySelectorAll('.palette')) palette.remove();
  });
  setInterval(() => {
    if (!state.editing && !state.dragging && document.visibilityState === 'visible') safeRefresh();
  }, REFRESH_MS);
}

async function start() {
  buildEditorSwatches();
  wireEvents();
  try {
    const session = await api('GET', '/api/session');
    state.csrf = session.csrfToken;
    await refresh();
  } catch (error) {
    reportError(error);
  }
}

start();
