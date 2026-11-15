// プロンプトの上書きの共通入力部品（lilla-core#312）。
//
// 「デフォルトプロンプトに追加」か「デフォルトプロンプトを置き換える」を選び、入力本文を
// 書く部品。入力欄は jinja-editor.js の jinjaEditor をそのまま使うため、使う画面では
// jinja-editor.js とこのファイルの両方を <script> で読み込む。
//
// 使い方（index.html の静的 HTML から）:
//
//   <div x-data="promptOverrideEditor({ defaultText: ..., defaultLocale: ..., target: ...,
//                                       variables: ..., fetch: authedFetch })"
//        x-modelable="value" x-model="編集中の値（{ mode, text }）"></div>
//
// - 中身は index.html の <template id="prompt-override-editor-tpl"> を init() で複製して作る
//   （要素は空にしておく）。値は { mode: 'append' | 'replace', text } で、x-modelable /
//   x-model で呼び出し側の値と結ぶ
// - defaultText / defaultLocale: 追加モードのときだけ読み取り専用で見せる既定の本文と言語
// - target / variables / fetch / rows / placeholder: そのまま jinjaEditor へ渡す
//   （プレビューと保存時の検査は入力本文だけが対象。モードは target に含めない）
// - どのオプションも値か、値を返す関数（選択の切り替えに追従させるとき）
//
// モードを切り替えても入力本文は消さず、既定の本文を自動で差し込むこともしない。
// 状態（ラジオの name を含む）はインスタンスごとに持ち、複数並べても混ざらない。
// 待機中のプレビューは中の jinjaEditor が自分の destroy() で止める。

const PROMPT_OVERRIDE_MODES = ['append', 'replace'];
let promptOverrideEditorSeq = 0;

function resolvePromptOverrideOption(value) {
  return typeof value === 'function' ? value() : value;
}

document.addEventListener('alpine:init', () => {
  Alpine.data('promptOverrideEditor', (options = {}) => ({
    value: { mode: 'append', text: '' },
    radioName: 'prompt-override-mode-' + (++promptOverrideEditorSeq),

    init() {
      const tpl = document.getElementById('prompt-override-editor-tpl');
      if (tpl) {
        // jinjaEditor と同じく、複製した要素は自分で初期化する
        const nodes = [...tpl.content.cloneNode(true).children];
        nodes.forEach((node) => this.$el.appendChild(node));
        nodes.forEach((node) => Alpine.initTree(node));
      }
    },

    // 呼び出し側が null や形の崩れた値を入れても表示を壊さないよう、読むときに整える
    mode() {
      const mode = this.value && this.value.mode;
      return PROMPT_OVERRIDE_MODES.includes(mode) ? mode : 'append';
    },

    setMode(mode) {
      if (!PROMPT_OVERRIDE_MODES.includes(mode)) return;
      const text = this.value && typeof this.value.text === 'string' ? this.value.text : '';
      this.value = { mode, text };
    },

    defaultText() {
      return resolvePromptOverrideOption(options.defaultText) || '';
    },

    defaultLocale() {
      return resolvePromptOverrideOption(options.defaultLocale) || '-';
    },

    editorOptions() {
      return {
        target: options.target,
        variables: options.variables,
        fetch: options.fetch,
        rows: options.rows,
        placeholder: options.placeholder,
      };
    },
  }));
});
