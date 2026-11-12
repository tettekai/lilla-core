// notes 拡張のダッシュボード画面（「ノート」タブ）。
//
// SPA が import() して mount(el, ctx) を呼ぶ。ctx は authedFetch と formatDate だけを持つ。
// 本文は Markdown をレンダリングせず、textContent で入れる（innerHTML は使わない。
// ノートの中身は LLM や外部から来た文字列を含みうるため、XSS を避ける）。

const API = '/api/notes';

const STYLE = `
.notes-page { display: flex; gap: 16px; align-items: flex-start; }
.notes-side { flex: 0 0 300px; min-width: 220px; }
.notes-main { flex: 1; min-width: 0; }
.notes-page input, .notes-page textarea {
  background: var(--bg-tertiary); border: 1px solid var(--border); border-radius: 6px;
  color: var(--text); font-family: var(--font-mono); font-size: 12px; padding: 6px 8px; width: 100%;
}
.notes-page textarea { min-height: 420px; resize: vertical; }
.notes-page button {
  background: var(--bg-tertiary); border: 1px solid var(--border); border-radius: 6px;
  color: var(--text); cursor: pointer; font-family: var(--font-mono); font-size: 12px; padding: 4px 10px;
}
.notes-page button:hover { border-color: var(--accent); }
.notes-page button.danger:hover { border-color: var(--error); color: var(--error); }
.notes-row { display: flex; gap: 6px; margin-bottom: 8px; }
.notes-tree { list-style: none; padding-left: 0; }
.notes-tree ul { list-style: none; padding-left: 14px; }
.notes-tree li { cursor: pointer; padding: 1px 0; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.notes-tree li.selected > .notes-label { color: var(--accent); }
.notes-dir > .notes-label { color: var(--text-muted); }
.notes-meta { color: var(--text-muted); font-size: 11px; margin: 4px 0 8px; }
.notes-body {
  background: var(--bg-secondary); border: 1px solid var(--border); border-radius: 6px;
  padding: 10px; white-space: pre-wrap; word-break: break-word; font-family: var(--font-mono);
}
.notes-error { color: var(--error); margin: 6px 0; }
.notes-empty { color: var(--text-muted); }
.notes-match { cursor: pointer; padding: 3px 0; border-bottom: 1px solid var(--border); }
.notes-match .notes-meta { margin: 0; }
.notes-title { font-size: 14px; margin-bottom: 4px; word-break: break-all; }
`;

let root = null;
let state = null;

/** 要素を作る小さなヘルパー（子は文字列なら textContent として入れる）。 */
function h(tag, attrs = {}, ...children) {
  const el = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs)) {
    if (key === 'class') el.className = value;
    else if (key.startsWith('on')) el.addEventListener(key.slice(2), value);
    else if (value !== undefined && value !== null) el.setAttribute(key, value);
  }
  for (const child of children.flat()) {
    if (child === null || child === undefined || child === false) continue;
    el.append(typeof child === 'string' ? document.createTextNode(child) : child);
  }
  return el;
}

/** API を呼び、JSON を返す。失敗は `{"error"}` の文言で例外にする。 */
async function api(path, options = {}) {
  if (!state) throw new Error('unmounted');
  const res = await state.ctx.authedFetch(API + path, options);
  if (res.status === 204) return null;
  let body = null;
  try {
    body = await res.json();
  } catch (_) {
    body = null;
  }
  if (!res.ok) throw new Error((body && body.error) || `HTTP ${res.status}`);
  return body;
}

function q(path) {
  return encodeURIComponent(path);
}

function fmt(value) {
  return value ? state.ctx.formatDate(value) : '-';
}

/** ファイルの一覧からディレクトリの木を作る。 */
function buildTree(items) {
  const top = { dirs: new Map(), files: [] };
  for (const item of items) {
    const parts = item.path.split('/');
    let node = top;
    let prefix = '';
    for (const part of parts.slice(0, -1)) {
      prefix = prefix ? `${prefix}/${part}` : part;
      if (!node.dirs.has(part)) node.dirs.set(part, { path: prefix, dirs: new Map(), files: [] });
      node = node.dirs.get(part);
    }
    node.files.push({ name: parts[parts.length - 1], ...item });
  }
  return top;
}

function renderTree(node) {
  const list = h('ul', { class: 'notes-tree' });
  const dirNames = [...node.dirs.keys()].sort();
  for (const name of dirNames) {
    const dir = node.dirs.get(name);
    const open = state.open.has(dir.path);
    const li = h('li', { class: 'notes-dir' },
      h('span', {
        class: 'notes-label',
        onclick: (ev) => {
          ev.stopPropagation();
          if (open) state.open.delete(dir.path); else state.open.add(dir.path);
          render();
        },
      }, `${open ? '▾' : '▸'} ${name}/`));
    if (open) li.append(renderTree(dir));
    list.append(li);
  }
  for (const file of node.files.sort((a, b) => a.name.localeCompare(b.name))) {
    list.append(h('li', {
      class: state.selected === file.path ? 'selected' : '',
      title: file.path,
      onclick: (ev) => { ev.stopPropagation(); if (confirmDiscard()) openNote(file.path); },
    }, h('span', { class: 'notes-label' }, file.name)));
  }
  return list;
}

function renderSide() {
  const search = h('input', { type: 'search', placeholder: '本文を検索', value: state.query });
  const side = h('div', { class: 'notes-side' },
    h('form', {
      class: 'notes-row',
      onsubmit: (ev) => { ev.preventDefault(); if (confirmDiscard()) runSearch(search.value); },
    }, search, h('button', { type: 'submit' }, '検索')),
    h('div', { class: 'notes-row' },
      h('button', { onclick: () => { if (confirmDiscard()) startNew(); } }, '新規作成'),
      h('button', { onclick: () => loadFiles() }, '再読み込み')),
  );
  if (state.items === null) side.append(h('div', { class: 'notes-empty' }, '読み込み中…'));
  else if (state.items.length === 0) side.append(h('div', { class: 'notes-empty' }, 'ノートはまだありません。'));
  else side.append(renderTree(buildTree(state.items)));
  return side;
}

function renderMain() {
  const main = h('div', { class: 'notes-main' });
  if (state.error) main.append(h('div', { class: 'notes-error' }, state.error));
  const view = state.view;
  if (view.kind === 'empty') {
    main.append(h('div', { class: 'notes-empty' }, '左のツリーからノートを選んでください。'));
  } else if (view.kind === 'note') {
    const note = view.note;
    main.append(
      h('div', { class: 'notes-title' }, note.path),
      h('div', { class: 'notes-meta' },
        `${note.size} 文字 · 作成 ${fmt(note.created_at)} · 更新 ${fmt(note.updated_at)}`),
      h('div', { class: 'notes-row' },
        h('button', { onclick: () => startEdit(note) }, '編集'),
        h('button', { onclick: () => openHistory(note.path) }, '直前の版'),
        h('button', { class: 'danger', onclick: () => deleteNote(note.path) }, '削除')),
      h('pre', { class: 'notes-body' }, note.content),
    );
  } else if (view.kind === 'edit' || view.kind === 'new') {
    const isNew = view.kind === 'new';
    // 再描画（ツリーの開閉・再読み込みなど）で入力が消えないよう、入力のたびに状態へ書き戻す
    const pathInput = h('input', {
      type: 'text',
      placeholder: 'diary/2026-11-08.md',
      value: view.path,
      oninput: () => { view.path = pathInput.value; },
    });
    const text = h('textarea', { oninput: () => { view.content = text.value; } });
    text.value = view.content;
    main.append(
      isNew ? h('div', { class: 'notes-row' }, pathInput) : h('div', { class: 'notes-title' }, view.path),
      text,
      h('div', { class: 'notes-row', style: 'margin-top:8px' },
        h('button', {
          onclick: () => saveNote(isNew ? pathInput.value : view.path, text.value, isNew),
        }, '保存'),
        h('button', {
          onclick: () => {
            if (!confirmDiscard()) return;
            if (isNew) showEmpty(); else openNote(view.path);
          },
        }, 'キャンセル')),
    );
  } else if (view.kind === 'history') {
    const version = view.version;
    const reason = { create: '作成', overwrite: '上書き', append: '追記', delete: '削除', restore: '復元' }[version.reason] || version.reason;
    main.append(
      h('div', { class: 'notes-title' }, `${version.path}（直前の版）`),
      h('div', { class: 'notes-meta' }, `${reason}の前の内容 · 変更 ${fmt(version.saved_at)}`),
      h('div', { class: 'notes-row' },
        h('button', { onclick: () => restore(version.path) }, 'この版に戻す'),
        h('button', { onclick: () => openNote(version.path, true) }, '戻る')),
      h('pre', { class: 'notes-body' }, version.content),
    );
  } else if (view.kind === 'search') {
    main.append(h('div', { class: 'notes-title' }, `「${view.query}」の検索結果`));
    if (view.matches.length === 0) main.append(h('div', { class: 'notes-empty' }, '見つかりませんでした。'));
    for (const match of view.matches) {
      main.append(h('div', { class: 'notes-match', onclick: () => openNote(match.path) },
        h('div', { class: 'notes-meta' }, `${match.path}:${match.line}`),
        h('div', {}, match.text)));
    }
    if (view.truncated) {
      main.append(h('div', { class: 'notes-meta' }, '件数の上限で切り詰めました。語を増やして絞り込んでください。'));
    }
  }
  return main;
}

function render() {
  if (!root || !state) return;
  root.replaceChildren(h('style', {}, STYLE), h('div', { class: 'notes-page' }, renderSide(), renderMain()));
}

function setError(error) {
  if (!state) return;
  state.error = error ? (error.message || String(error)) : '';
}

/** 編集中・新規作成中に、開いた時点から変わった入力があれば true。 */
function hasUnsavedChanges() {
  const view = state && state.view;
  if (!view || (view.kind !== 'edit' && view.kind !== 'new')) return false;
  return view.path !== view.initialPath || view.content !== view.initialContent;
}

/** 未保存の変更があれば、捨ててよいかを確かめる（無ければ確かめずに true）。 */
function confirmDiscard() {
  if (!hasUnsavedChanges()) return true;
  // eslint-disable-next-line no-alert
  return window.confirm('保存していない変更があります。破棄しますか？');
}

function showEmpty() {
  state.selected = null;
  state.view = { kind: 'empty' };
  setError(null);
  render();
}

async function loadFiles() {
  try {
    const body = await api('/files');
    state.items = body.items;
  } catch (e) {
    state.items = state.items || [];
    setError(e);
  }
  render();
}

async function openNote(path, keepMissing = false) {
  setError(null);
  try {
    const note = await api(`/file?path=${q(path)}`);
    state.selected = path;
    state.view = { kind: 'note', note };
    // 開いたノートの親ディレクトリを開いておく
    const parts = path.split('/');
    for (let i = 1; i < parts.length; i += 1) state.open.add(parts.slice(0, i).join('/'));
  } catch (e) {
    if (!keepMissing) setError(e);
    else state.view = { kind: 'empty' };
  }
  render();
}

function startEdit(note) {
  setError(null);
  state.view = {
    kind: 'edit',
    path: note.path,
    content: note.content,
    initialPath: note.path,
    initialContent: note.content,
  };
  render();
}

function startNew() {
  setError(null);
  state.selected = null;
  state.view = { kind: 'new', path: '', content: '', initialPath: '', initialContent: '' };
  render();
}

async function saveNote(path, content, isNew) {
  setError(null);
  try {
    const result = await api('/file', {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ path, content, mode: isNew ? 'create' : 'overwrite' }),
    });
    await loadFiles();
    await openNote(result.path);
  } catch (e) {
    setError(e);
    state.view = { ...state.view, path, content };
    render();
  }
}

async function deleteNote(path) {
  // eslint-disable-next-line no-alert
  if (!window.confirm(`「${path}」を削除しますか？（直前の版から戻せます）`)) return;
  setError(null);
  try {
    await api(`/file?path=${q(path)}`, { method: 'DELETE' });
    await loadFiles();
    state.selected = null;
    await openHistory(path);
  } catch (e) {
    setError(e);
    render();
  }
}

async function openHistory(path) {
  setError(null);
  try {
    const version = await api(`/history?path=${q(path)}`);
    state.view = { kind: 'history', version };
  } catch (e) {
    setError(e);
  }
  render();
}

async function restore(path) {
  setError(null);
  try {
    await api('/history/restore', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ path }),
    });
    await loadFiles();
    await openNote(path);
  } catch (e) {
    setError(e);
    render();
  }
}

async function runSearch(query) {
  state.query = query;
  setError(null);
  if (!query.trim()) {
    render();
    return;
  }
  try {
    const body = await api(`/search?q=${q(query)}`);
    state.selected = null;
    state.view = { kind: 'search', query, matches: body.matches, truncated: body.truncated };
  } catch (e) {
    setError(e);
  }
  render();
}

/** タブを閉じる・再読み込みするときも、未保存の変更があればブラウザに確かめさせる。 */
function onBeforeUnload(ev) {
  if (!hasUnsavedChanges()) return;
  ev.preventDefault();
  ev.returnValue = '';
}

export function mount(el, ctx) {
  root = el;
  state = {
    ctx,
    items: null,
    open: new Set(),
    selected: null,
    query: '',
    error: '',
    view: { kind: 'empty' },
  };
  window.addEventListener('beforeunload', onBeforeUnload);
  render();
  loadFiles();
}

export function unmount() {
  window.removeEventListener('beforeunload', onBeforeUnload);
  if (root) root.replaceChildren();
  root = null;
  state = null;
}
