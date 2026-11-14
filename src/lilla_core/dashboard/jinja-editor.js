// Jinja2 テンプレートの共通入力部品（lilla-core#352）。
//
// 使い方（index.html の静的 HTML から）:
//
//   <div x-data="jinjaEditor({ target: ..., variables: ..., fetch: authedFetch })"
//        x-modelable="text" x-model="編集中の本文"></div>
//
// - 中身は index.html の <template id="jinja-editor-tpl"> を init() で複製して作る
//   （要素は空にしておく）。本文の読み書きは x-modelable / x-model で呼び出し側の値と結ぶ
// - target: プレビューで展開に使う変数セット。{ type: 'skill' } か
//   { type: 'extension', name }。値か、値を返す関数（選択の切り替えに追従させるとき）
// - variables: 一覧に出す追加の変数名（例: ['settings.day_rollover']）。値か関数。
//   共通の current_hour は部品が先頭に必ず足す
// - fetch: API 呼び出しに使う関数（ダッシュボードの authedFetch を渡す。401 の扱いを揃える）
// - rows / placeholder: textarea の見た目
//
// 状態はインスタンスごとに持ち、複数並べても混ざらない。要素が DOM から外れると
// destroy() が待機中のプレビューを止める。展開はブラウザで再実装せず、保存時の試し展開と
// 同じ処理を POST /api/admin/prompt-preview で呼ぶ。

const JINJA_PREVIEW_URL = '/api/admin/prompt-preview';
const JINJA_PREVIEW_DELAY_MS = 400;
const JINJA_COMMON_VARIABLES = ['current_hour'];

function resolveOption(value) {
  return typeof value === 'function' ? value() : value;
}

document.addEventListener('alpine:init', () => {
  Alpine.data('jinjaEditor', (options = {}) => ({
    text: '',
    rows: options.rows || 8,
    placeholder: options.placeholder || '',
    // 'idle' | 'checking' | 'ok' | 'error' | 'failed'
    status: 'idle',
    error: '',
    rendered: null,
    comments: [],
    settingsUnavailable: false,
    _timer: null,
    _seq: 0,

    init() {
      const tpl = document.getElementById('jinja-editor-tpl');
      if (tpl) {
        // x-data の init() の時点では Alpine が子要素をたどり終えていることがあるため、
        // 複製した要素は自分で初期化する（初期化済みの要素は Alpine が二重に処理しない）
        const nodes = [...tpl.content.cloneNode(true).children];
        nodes.forEach((node) => this.$el.appendChild(node));
        nodes.forEach((node) => Alpine.initTree(node));
      }
      this.$watch('text', () => this.schedulePreview());
      this.$watch(() => JSON.stringify(resolveOption(options.target)), () => this.schedulePreview());
      this.$nextTick(() => this.schedulePreview());
    },

    destroy() {
      clearTimeout(this._timer);
      this._seq += 1;
    },

    variableNames() {
      const extra = resolveOption(options.variables) || [];
      return [...JINJA_COMMON_VARIABLES, ...extra.filter((v) => !JINJA_COMMON_VARIABLES.includes(v))];
    },

    insertVariable(name) {
      const area = this.$refs.input;
      const snippet = '{{ ' + name + ' }}';
      const value = this.text || '';
      const start = area ? area.selectionStart : value.length;
      const end = area ? area.selectionEnd : value.length;
      this.text = value.slice(0, start) + snippet + value.slice(end);
      this.$nextTick(() => {
        if (!area) return;
        area.focus();
        area.selectionStart = area.selectionEnd = start + snippet.length;
      });
    },

    schedulePreview() {
      clearTimeout(this._timer);
      this._seq += 1;
      if (!this.text) {
        this.status = 'idle';
        this.error = '';
        this.rendered = null;
        this.comments = [];
        this.settingsUnavailable = false;
        return;
      }
      this.status = 'checking';
      this._timer = setTimeout(() => this.runPreview(), JINJA_PREVIEW_DELAY_MS);
    },

    async runPreview() {
      const seq = ++this._seq;
      const target = resolveOption(options.target);
      if (!target) return;
      const doFetch = options.fetch || ((url, init) => fetch(url, init));
      try {
        const res = await doFetch(JINJA_PREVIEW_URL, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ text: this.text, target }),
        });
        let data = {};
        try { data = await res.json(); } catch (e) { data = {}; }
        if (seq !== this._seq) return; // 古い応答は捨てる
        if (res.ok) {
          this.status = 'ok';
          this.error = '';
          this.rendered = data.rendered;
          this.comments = data.comments || [];
          this.settingsUnavailable = !!data.settings_unavailable;
        } else if (res.status === 400) {
          this.status = 'error';
          this.error = data.error || 'テンプレートを展開できません';
          this.rendered = null;
          this.comments = [];
          this.settingsUnavailable = false;
        } else {
          this.status = 'failed';
          this.error = data.error || 'プレビューを取得できませんでした';
        }
      } catch (e) {
        if (seq !== this._seq) return;
        console.error('Failed to preview template', e);
        this.status = 'failed';
        this.error = 'プレビューを取得できませんでした';
      }
    },
  }));
});
