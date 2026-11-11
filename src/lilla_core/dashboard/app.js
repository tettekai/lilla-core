// Lilla ダッシュボード Alpine.js コンポーネント

// 画面の一覧（ナビ・ハッシュ・拡張ページのモジュール URL）はサーバーの
// GET /api/dashboard/nav が正で、組み込みも拡張も同じ形で並ぶ。ハッシュと
// 画面名の対応表はそのカタログから 1 度だけ組み立てる（二重管理しない）。

// カタログを取れなかったときの退避。組み込み 7 画面だけはカタログ無しでも
// 開けるようにして、ナビの取得失敗でダッシュボードごと使えなくならないようにする。
const FALLBACK_PAGES = [
  { name: 'home', label: 'Home', group: 'main', hash: '#/', module: null },
  { name: 'conversations', label: 'Conversations', group: 'main', hash: '#/conversations', module: null },
  { name: 'memos', label: 'Memos', group: 'main', hash: '#/memos', module: null },
  { name: 'skills', label: 'Skills', group: 'main', hash: '#/skills', module: null },
  { name: 'usage', label: 'Usage', group: 'main', hash: '#/usage', module: null },
  { name: 'logs', label: 'Admin', group: 'admin', hash: '#/admin/logs', module: null },
  { name: 'extensions', label: 'Extensions', group: 'admin', hash: '#/admin/extensions', module: null },
];

const DEFAULT_HASH = '#/';

// スキル名の規則（サーバー側の services/skills.py と同じ）。半角英数字・`_`・`-` のみで
// 64 文字以内。大文字小文字は区別せず、サーバーが小文字に正規化して保存する。
const SKILL_NAME_RE = /^[A-Za-z0-9_-]{1,64}$/;
const SKILL_NAME_HINT = '名前は半角英数字・アンダースコア・ハイフンのみ、64 文字以内で入力してください';
const DEFAULT_TAB = 'home';

function dashboardApp() {
  return {
    // 'loading' | 'setup' | 'login' | 'dashboard'
    view: 'loading',
    activeTab: DEFAULT_TAB,
    menuOpen: false,

    // ナビのカタログと、そこから組み立てたハッシュの対応表
    navPages: [],
    routeByHash: {},
    hashByName: {},

    // 拡張ページ用スロットの状態。module を持つページを開いている間だけ name が入る。
    extPage: { name: null, error: '' },
    // 現在マウント中のモジュールの unmount()。Alpine の状態に混ぜる必要はない。
    _extUnmount: null,
    // マウントの世代。タブを素早く切り替えたとき、古い import() の完了で
    // 新しいページを上書きしないための番号。
    _extToken: 0,

    // 認証フォームの入力・エラー表示用
    loginPassword: '',
    loginError: '',
    loginSubmitting: false,
    // 起動ログに出た一度きりのトークン（サーバーは画面へ埋めないので人が貼り付ける）
    setupToken: '',
    setupPassword: '',
    setupPasswordConfirm: '',
    setupError: '',
    setupSubmitting: false,
    minPasswordLength: 12,

    logs: {
      items: [],
      total: 0,
      page: 1,
      page_size: 50,
      total_pages: 1,
      loading: false,
      filters: { level: '', keyword: '', date_from: '', date_to: '' },
      levels: [],
      levelsLoaded: false,
      stats: {},
      selectedLog: null,
    },

    conversations: {
      items: [],
      total: 0,
      page: 1,
      page_size: 50,
      total_pages: 1,
      loading: false,
      filters: { date_from: '', date_to: '' },
    },

    // Home の状態。status は /api/dashboard/status の応答、error は取得自体の失敗。
    home: {
      status: null,
      loading: false,
      error: false,
    },

    // LLM の使用量。data は /api/usage の応答で、days は集計する日数（今日を含む）。
    usage: {
      days: 7,
      data: null,
      loading: false,
      error: false,
    },

    memos: {
      items: [],
      loading: false,
      newContent: '',
      editingId: null,
      editContent: '',
    },

    // 拡張の上書き（extension_overrides）の編集。detail はサーバーの
    // GET /api/admin/extensions/{name} の応答をそのまま持ち、編集中の値は
    // promptDraft / taskDrafts に分けて持つ（保存前の値で表示を壊さないため）。
    exts: {
      items: [],
      loading: false,
      selected: null,
      detail: null,
      detailLoading: false,
      error: '',
      notice: '',
      applyFailed: false,
      promptDraft: '',
      taskDrafts: {},
      // 画面管理の設定（設定の本体）の編集中の値。unset は「未設定（null）」にする項目。
      settingsDraft: {},
      settingsUnset: {},
      settingsErrors: [],
      settingsNotice: '',
    },

    skills: {
      items: [],
      loading: false,
      newName: '',
      newDescription: '',
      newPrompt: '',
      editingId: null,
      editName: '',
      editDescription: '',
      editPrompt: '',
    },

    // ---- 認証 ----

    // setup_required の判定を authenticated より必ず先に行う。
    // DB リセットでパスワードを復旧した直後など、古いセッション Cookie が残って
    // いても /setup を優先表示する必要があるため。
    async init() {
      // ブラウザの戻る／進むに追従する。ログイン画面や初期設定画面では
      // 画面を切り替えず、認証済みのときだけハッシュを解決する。
      window.addEventListener('hashchange', () => {
        if (this.view === 'dashboard') this.resolveRoute();
      });
      try {
        const res = await fetch('/api/auth/status');
        const { setup_required, authenticated } = await res.json();
        if (setup_required) { this.view = 'setup'; return; }
        if (!authenticated) { this.view = 'login'; return; }
        this.startDashboard();
      } catch (e) {
        console.error('Failed to check auth status', e);
        this.view = 'login';  // フェイルセーフ
      }
    },

    // ログイン直後・リロード・戻る/進むのすべてがこの経路を通る。
    // ハッシュはサーバーへ送られずログイン画面を挟んでも残るため、
    // ログイン後の戻り先を別途保存する必要はない。
    // ナビのカタログはここで 1 度だけ取り、画面切り替えでは取り直さない。
    // dashboard へ移すのはカタログが揃ってからにする。先に移すと、ナビが空の
    // 殻が一瞬見えるうえ、その間の hashchange が空の対応表で解決されて
    // ブックマークしたハッシュを #/ に潰してしまう。
    async startDashboard() {
      this.view = 'loading';
      await this.fetchNav();
      // カタログ取得中に 401 で login へ落ちたときは、そのまま戻る。
      if (this.view !== 'loading') return;
      this.view = 'dashboard';
      this.resolveRoute();
    },

    // ---- ナビのカタログ ----

    async fetchNav() {
      try {
        const res = await this.authedFetch('/api/dashboard/nav');
        const data = await res.json();
        if (Array.isArray(data.pages) && data.pages.length > 0) {
          this.setNav(data.pages);
          return;
        }
        console.error('Dashboard nav returned no pages');
      } catch (e) {
        console.error('Failed to load dashboard nav', e);
      }
      this.setNav(FALLBACK_PAGES);
    },

    setNav(pages) {
      this.navPages = pages;
      this.routeByHash = {};
      this.hashByName = {};
      for (const page of pages) {
        this.routeByHash[page.hash] = page.name;
        this.hashByName[page.name] = page.hash;
      }
    },

    get mainNav() {
      return this.navPages.filter((page) => page.group === 'main');
    },

    get adminNav() {
      return this.navPages.filter((page) => page.group === 'admin');
    },

    // ---- ルーティング ----

    // 現在のハッシュから表示するタブを決め、そのタブのデータだけを取得する。
    // 表に無いハッシュは Home へ落とし、URL も #/ に正規化する
    // （replaceState なので履歴は増やさない）。
    resolveRoute() {
      const hash = window.location.hash;
      const tab = this.routeByHash[hash];
      if (!tab && hash) {
        history.replaceState(null, '', this.basePath() + DEFAULT_HASH);
      }
      this.activeTab = tab || DEFAULT_TAB;
      this.loadTabData(this.activeTab);
    },

    // ナビの操作はハッシュを書き換えるだけにして、描画と取得は
    // hashchange 経由の resolveRoute() に一本化する（二重 fetch を避ける）。
    navigate(tab) {
      this.menuOpen = false;
      const hash = this.hashByName[tab] || DEFAULT_HASH;
      if (window.location.hash === hash) {
        // 同じハッシュでは hashchange が起きないので、再読み込みとして直接呼ぶ
        this.resolveRoute();
        return;
      }
      window.location.hash = hash;
    },

    loadTabData(tab) {
      const page = this.navPages.find((p) => p.name === tab);
      if (page && page.module) {
        this.mountExtPage(page);
        return;
      }
      // 組み込み画面へ戻るときは、開いていた拡張ページを必ず片付ける。
      this.unmountExtPage();
      if (tab === 'logs') {
        // レベル一覧はマスタなので一度だけ。初期タブが Logs でなくなった以上、
        // ここで取らないとハッシュ直打ちで開いたとき絞り込みが空になる。
        if (!this.logs.levelsLoaded) this.fetchLogLevels();
        this.fetchLogStats();
        this.fetchLogs(this.logs.page);
      } else if (tab === 'conversations') {
        this.fetchConversations(this.conversations.page);
      } else if (tab === 'memos') {
        this.fetchMemos();
      } else if (tab === 'skills') {
        this.fetchSkills();
      } else if (tab === 'usage') {
        this.fetchUsage();
      } else if (tab === 'extensions') {
        this.fetchExtensions();
      } else if (tab === 'home') {
        this.fetchHomeStatus();
      }
    },

    // ---- 拡張ページのスロット ----

    // 拡張のページは JS モジュール 1 つで、スロットの要素と ctx を受け取る
    // mount(el, ctx) と、後始末の unmount() を export する（unmount は任意）。
    // ctx に渡すのは認証つき fetch と日付整形だけで、Alpine のコンポーネント
    // 内部は渡さない（拡張が殻の状態を直接触れるようにしないため）。
    extPageContext() {
      return {
        authedFetch: (url, options) => this.authedFetch(url, options),
        formatDate: (isoStr) => this.formatDate(isoStr),
      };
    },

    // 世代は「スロットに出すものが変わる」すべての入口（mount / unmount）で進める。
    // 自分の世代を **後始末を待つ前に** 取るのが肝で、待ってから取ると、待っている
    // 間に走った unmount を自分では追い越せず、古い mount が新しい画面を上書きする。
    async mountExtPage(page) {
      // 同じページを開き直すだけなら作り直さない
      if (this.extPage.name === page.name) return;
      const token = ++this._extToken;
      await this._teardownExtPage();
      if (token !== this._extToken) return;

      this.extPage.name = page.name;
      this.extPage.error = '';
      try {
        const mod = await import(page.module);
        const mount = mod.mount || (mod.default && mod.default.mount);
        if (typeof mount !== 'function') {
          throw new Error(`${page.module} does not export mount()`);
        }
        // import() を待つ間に別のタブへ移っていたら、この結果は捨てる
        if (token !== this._extToken) return;
        this._extUnmount = mod.unmount || (mod.default && mod.default.unmount) || null;
        await mount(this.$refs.extSlot, this.extPageContext());
        // mount() 自体が時間を食う場合もあるので、書き終えた時点でも世代を見る
        if (token !== this._extToken) await this._teardownExtPage();
      } catch (e) {
        console.error(`Failed to mount extension page '${page.name}'`, e);
        if (token !== this._extToken) return;
        this._extUnmount = null;
        if (this.$refs.extSlot) this.$refs.extSlot.innerHTML = '';
        this.extPage.error = 'この画面を読み込めませんでした';
      }
    },

    async unmountExtPage() {
      this._extToken++;
      await this._teardownExtPage();
    },

    // 実際の後始末だけを行う（世代は呼び出し側が進める）。
    async _teardownExtPage() {
      const unmount = this._extUnmount;
      this._extUnmount = null;
      if (typeof unmount === 'function') {
        try {
          await unmount();
        } catch (e) {
          console.error('Failed to unmount extension page', e);
        }
      }
      if (this.$refs.extSlot) this.$refs.extSlot.innerHTML = '';
      this.extPage.name = null;
      this.extPage.error = '';
    },

    basePath() {
      return window.location.pathname + window.location.search;
    },

    // 認証必須 API 用の共通 fetch ラッパー。
    // セッション期限切れ後も dashboard 画面のまま各タブが 401 を受け取り続けて
    // 「動いているように見えて何も表示されない」状態になるのを避けるため、
    // 401 を検知したら login 画面へ強制遷移させる。
    async authedFetch(url, options) {
      const res = await fetch(url, options);
      if (res.status === 401) {
        this.view = 'login';
        this.loginPassword = '';
        this.loginError = 'セッションの有効期限が切れました。再度ログインしてください。';
        // 401 のボディはプレーンテキストなので、呼び出し側が続けて res.json() を
        // 呼ぶと JSON パースエラーになる。throw して既存の catch へ流す。
        throw new Error('Unauthorized');
      }
      return res;
    },

    async submitSetup() {
      this.setupError = '';
      const password = this.setupPassword;
      const setupToken = this.setupToken.trim();
      if (!setupToken) {
        this.setupError = 'セットアップトークンを入力してください';
        return;
      }
      if (password.length < this.minPasswordLength) {
        this.setupError = `パスワードは${this.minPasswordLength}文字以上で設定してください`;
        return;
      }
      if (password !== this.setupPasswordConfirm) {
        this.setupError = 'パスワードが一致しません';
        return;
      }
      this.setupSubmitting = true;
      try {
        const res = await fetch('/api/setup', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ password, setup_token: setupToken }),
        });
        if (res.ok) {
          this.setupToken = '';
          this.setupPassword = '';
          this.setupPasswordConfirm = '';
          // 自動ログインさせず、明示的にログインさせる
          this.loginError = 'パスワードを設定しました。ログインしてください。';
          this.view = 'login';
        } else {
          this.setupError = (await res.text()) || 'パスワードの設定に失敗しました';
        }
      } catch (e) {
        console.error('Failed to setup password', e);
        this.setupError = 'パスワードの設定に失敗しました';
      } finally {
        this.setupSubmitting = false;
      }
    },

    async submitLogin() {
      this.loginError = '';
      if (!this.loginPassword) return;
      this.loginSubmitting = true;
      try {
        const res = await fetch('/api/login', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ password: this.loginPassword }),
        });
        if (res.ok) {
          this.loginPassword = '';
          this.startDashboard();
        } else {
          this.loginError = (await res.text()) || 'ログインに失敗しました';
        }
      } catch (e) {
        console.error('Failed to login', e);
        this.loginError = 'ログインに失敗しました';
      } finally {
        this.loginSubmitting = false;
      }
    },

    async logout() {
      try {
        await fetch('/api/logout', { method: 'POST' });
      } catch (e) {
        console.error('Failed to logout', e);
      }
      this.loginPassword = '';
      this.loginError = '';
      this.view = 'login';
      this.menuOpen = false;
      this.unmountExtPage();
      // ハッシュが #/admin/logs のままだと再ログインでそのまま Logs へ
      // 戻ってしまうため、明示的なログアウトのときだけ Home に戻す。
      history.replaceState(null, '', this.basePath() + DEFAULT_HASH);
    },

    // ---- Home ----

    // 生存確認（MongoDB の ping のみ）と直近 24 時間のエラー・警告件数だけを取る。
    // 取得に失敗しても Home の表示に留め、画面全体は止めない。
    async fetchHomeStatus() {
      this.home.loading = true;
      this.home.error = false;
      try {
        const res = await this.authedFetch('/api/dashboard/status');
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        this.home.status = await res.json();
      } catch (e) {
        console.error('Failed to fetch dashboard status', e);
        this.home.status = null;
        this.home.error = true;
      } finally {
        this.home.loading = false;
      }
    },

    // 件数の表示。0 件は「なし」、数えられなかったときは「取得できません」にする。
    homeLogCount(level) {
      const logs = this.home.status && this.home.status.logs;
      if (!logs || !logs.ok || !logs.counts) return '取得できません';
      const count = logs.counts[level] || 0;
      return count === 0 ? 'なし' : `${count} 件`;
    },

    // 直近 24 時間の LLM 使用量の一行表示。数えられなかったときは「取得できません」。
    homeUsageSummary() {
      const usage = this.home.status && this.home.status.llm_usage;
      if (!usage || !usage.ok || !usage.totals) return '取得できません';
      const totals = usage.totals;
      if (!totals.calls) return usage.enabled ? 'なし' : 'なし（記録は無効）';
      let text = `${this.formatCalls(totals)} / ${this.formatNumber(totals.total_tokens)} トークン`;
      if (totals.priced_calls) text += ` / 概算 ${this.formatCost(totals)}`;
      return text;
    },

    // ---- Usage ----

    async fetchUsage() {
      this.usage.loading = true;
      this.usage.error = false;
      try {
        const params = new URLSearchParams({ days: String(this.usage.days) });
        const res = await this.authedFetch('/api/usage?' + params.toString());
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        this.usage.data = await res.json();
      } catch (e) {
        console.error('Failed to fetch LLM usage', e);
        this.usage.data = null;
        this.usage.error = true;
      } finally {
        this.usage.loading = false;
      }
    },

    // 呼び出し件数。トークン数が分からなかった呼び出しがあれば内数で添える。
    formatCalls(row) {
      if (!row) return '';
      const calls = `${this.formatNumber(row.calls)} 回`;
      return row.unknown_calls ? `${calls}（うち不明 ${this.formatNumber(row.unknown_calls)}）` : calls;
    },

    // 単価を設定したプロバイダーの記録が 1 件も無ければ「-」。設定の無い記録が
    // 混ざっているときは、その分を含まない目安であることを添える。
    formatCost(row) {
      if (!row || !row.priced_calls) return '-';
      const cost = Number(row.cost || 0);
      const text = cost.toLocaleString('ja-JP', { maximumFractionDigits: 4 });
      return row.priced_calls < row.calls ? `${text}（単価設定分のみ）` : text;
    },

    formatAvgDuration(row) {
      if (!row || !row.calls) return '-';
      return `${(row.duration_ms / row.calls / 1000).toFixed(1)} 秒`;
    },

    formatNumber(value) {
      return Number(value || 0).toLocaleString('ja-JP');
    },

    // ---- Logs ----

    async fetchLogStats() {
      try {
        const res = await this.authedFetch('/api/admin/logs/stats');
        const data = await res.json();
        this.logs.stats = data.stats || {};
      } catch (e) {
        console.error('Failed to fetch log stats', e);
      }
    },

    async fetchLogLevels() {
      try {
        const res = await this.authedFetch('/api/admin/logs/levels');
        const data = await res.json();
        this.logs.levels = data.levels || [];
        this.logs.levelsLoaded = true;
      } catch (e) {
        console.error('Failed to fetch log levels', e);
      }
    },

    async fetchLogs(page) {
      this.logs.loading = true;
      this.logs.page = page;
      const params = new URLSearchParams();
      if (this.logs.filters.level) params.set('level', this.logs.filters.level);
      if (this.logs.filters.keyword) params.set('keyword', this.logs.filters.keyword);
      if (this.logs.filters.date_from) params.set('date_from', this.logs.filters.date_from);
      if (this.logs.filters.date_to) params.set('date_to', this.logs.filters.date_to);
      params.set('page', page);
      params.set('page_size', this.logs.page_size);
      try {
        const res = await this.authedFetch('/api/admin/logs?' + params.toString());
        const data = await res.json();
        this.logs.items = data.items || [];
        this.logs.total = data.total || 0;
        this.logs.total_pages = data.total_pages || 1;
      } catch (e) {
        console.error('Failed to fetch logs', e);
      } finally {
        this.logs.loading = false;
      }
    },

    resetLogsFilters() {
      this.logs.filters = { level: '', keyword: '', date_from: '', date_to: '' };
      this.fetchLogs(1);
    },

    openLogDetail(log) {
      this.logs.selectedLog = log;
    },

    closeLogDetail() {
      this.logs.selectedLog = null;
    },

    statsEntries() {
      return Object.entries(this.logs.stats);
    },

    // ---- Conversations ----

    async fetchConversations(page) {
      this.conversations.loading = true;
      this.conversations.page = page;
      const params = new URLSearchParams();
      if (this.conversations.filters.date_from) params.set('date_from', this.conversations.filters.date_from);
      if (this.conversations.filters.date_to) params.set('date_to', this.conversations.filters.date_to);
      params.set('page', page);
      params.set('page_size', this.conversations.page_size);
      try {
        const res = await this.authedFetch('/api/conversations?' + params.toString());
        const data = await res.json();
        this.conversations.items = data.items || [];
        this.conversations.total = data.total || 0;
        this.conversations.total_pages = data.total_pages || 1;
      } catch (e) {
        console.error('Failed to fetch conversations', e);
      } finally {
        this.conversations.loading = false;
      }
    },

    resetConvFilters() {
      this.conversations.filters = { date_from: '', date_to: '' };
      this.fetchConversations(1);
    },

    async deleteConversation(item) {
      if (!window.confirm('この会話を削除しますか？')) return;
      try {
        const res = await this.authedFetch('/api/conversations/' + item._id, { method: 'DELETE' });
        if (res.ok) {
          await this.fetchConversations(this.conversations.page);
        } else {
          alert('削除に失敗しました');
        }
      } catch (e) {
        console.error('Failed to delete conversation', e);
        alert('削除に失敗しました');
      }
    },

    // ---- User Memos ----

    async fetchMemos() {
      this.memos.loading = true;
      try {
        const res = await this.authedFetch('/api/user-memos');
        const data = await res.json();
        this.memos.items = data.items || [];
      } catch (e) {
        console.error('Failed to fetch memos', e);
      } finally {
        this.memos.loading = false;
      }
    },

    async addMemo() {
      const content = this.memos.newContent.trim();
      if (!content) return;
      try {
        const res = await this.authedFetch('/api/user-memos', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ content }),
        });
        if (res.ok) {
          this.memos.newContent = '';
          await this.fetchMemos();
        } else {
          alert('追加に失敗しました');
        }
      } catch (e) {
        console.error('Failed to add memo', e);
        alert('追加に失敗しました');
      }
    },

    async toggleMemo(memo) {
      try {
        const res = await this.authedFetch('/api/user-memos/' + memo._id, {
          method: 'PATCH',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ enabled: !memo.enabled }),
        });
        if (res.ok) {
          await this.fetchMemos();
        } else {
          alert('更新に失敗しました');
        }
      } catch (e) {
        console.error('Failed to toggle memo', e);
        alert('更新に失敗しました');
      }
    },

    startEdit(memo) {
      this.memos.editingId = memo._id;
      this.memos.editContent = memo.content;
    },

    cancelEdit() {
      this.memos.editingId = null;
      this.memos.editContent = '';
    },

    async saveEdit(memo) {
      const content = this.memos.editContent.trim();
      if (!content) return;
      try {
        const res = await this.authedFetch('/api/user-memos/' + memo._id, {
          method: 'PATCH',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ content }),
        });
        if (res.ok) {
          this.cancelEdit();
          await this.fetchMemos();
        } else {
          alert('更新に失敗しました');
        }
      } catch (e) {
        console.error('Failed to save edit', e);
        alert('更新に失敗しました');
      }
    },

    async deleteMemo(memo) {
      if (!window.confirm('このメモを削除しますか？')) return;
      try {
        const res = await this.authedFetch('/api/user-memos/' + memo._id, { method: 'DELETE' });
        if (res.ok) {
          await this.fetchMemos();
        } else {
          alert('削除に失敗しました');
        }
      } catch (e) {
        console.error('Failed to delete memo', e);
        alert('削除に失敗しました');
      }
    },

    // ---- Skills ----

    isValidSkillName(name) {
      return SKILL_NAME_RE.test(name);
    },

    // 失敗時はサーバーの応答本文（名前の規則違反・重複など）を添えて知らせる。
    async alertSkillFailure(res, action) {
      let detail = '';
      try {
        detail = await res.text();
      } catch (e) {
        detail = '';
      }
      if (res.status === 409) detail = '同じ名前のスキルが既にあります';
      alert(action + 'に失敗しました' + (detail ? ': ' + detail : ''));
    },

    // ---- 拡張の上書き ----

    editableExtensions() {
      return this.exts.items.filter((item) => item.editable);
    },

    async fetchExtensions() {
      this.exts.loading = true;
      this.exts.error = '';
      try {
        const res = await this.authedFetch('/api/admin/extensions');
        const data = await res.json();
        this.exts.items = data.items || [];
        const editable = this.editableExtensions();
        const keep = editable.find((item) => item.name === this.exts.selected);
        const next = keep ? keep.name : (editable[0] ? editable[0].name : null);
        if (next) {
          await this.selectExtension(next);
        } else {
          this.exts.selected = null;
          this.exts.detail = null;
        }
      } catch (e) {
        console.error('Failed to fetch extensions', e);
        this.exts.error = '拡張の一覧を取得できませんでした';
      } finally {
        this.exts.loading = false;
      }
    },

    async selectExtension(name) {
      if (this.exts.selected !== name) {
        this.exts.notice = '';
        this.exts.applyFailed = false;
      }
      this.exts.selected = name;
      this.exts.detailLoading = true;
      this.exts.error = '';
      try {
        const res = await this.authedFetch('/api/admin/extensions/' + encodeURIComponent(name));
        if (!res.ok) {
          this.exts.detail = null;
          this.exts.error = await this.extErrorText(res, '上書きを取得できませんでした');
          return;
        }
        this.setExtensionDetail(await res.json());
      } catch (e) {
        console.error('Failed to fetch extension detail', e);
        this.exts.detail = null;
        this.exts.error = '上書きを取得できませんでした';
      } finally {
        this.exts.detailLoading = false;
      }
    },

    // 編集欄は「今効いている値」で埋める。タスクの schedule は上書きがあるときだけ
    // 入れ、空欄は「デフォルトを使う」を意味する。
    setExtensionDetail(detail) {
      this.exts.detail = detail;
      this.exts.promptDraft = detail.prompt ? detail.prompt.effective : '';
      const drafts = {};
      for (const task of detail.tasks || []) {
        drafts[task.name] = {
          schedule: task.override.schedule || '',
          enabled: task.effective.enabled,
        };
      }
      this.exts.taskDrafts = drafts;
      this.setSettingsDraft(detail.settings);
    },

    setSettingsDraft(settings) {
      const draft = {};
      const unset = {};
      if (settings && settings.values) {
        for (const field of settings.fields) {
          const value = settings.values[field.name];
          if (field.widget === 'checkbox-group') {
            // 配列の項目は「未設定」を持たない。null も空の選択として扱う
            unset[field.name] = false;
            draft[field.name] = Array.isArray(value) ? [...value] : [];
          } else if (!field.supported) {
            // 汎用フォームで編集できない項目は、保存時にそのまま送り返す
            unset[field.name] = false;
            draft[field.name] = value === undefined ? null : value;
          } else {
            unset[field.name] = field.nullable && (value === null || value === undefined);
            draft[field.name] = value === null || value === undefined ? '' : value;
          }
        }
      }
      this.exts.settingsDraft = draft;
      this.exts.settingsUnset = unset;
      this.exts.settingsErrors =
        settings && settings.error && settings.error.errors ? settings.error.errors : [];
    },

    isPlainSettingField(field) {
      return field.supported && !field.widget;
    },

    settingChoiceChecked(field, value) {
      const selected = this.exts.settingsDraft[field.name];
      return Array.isArray(selected) && selected.some((item) => item === value);
    },

    toggleSettingChoice(field, value, checked) {
      // 値は choices の value（JSON の型のまま）を使い、文字列化しない
      const selected = Array.isArray(this.exts.settingsDraft[field.name])
        ? this.exts.settingsDraft[field.name]
        : [];
      const rest = selected.filter((item) => item !== value);
      this.exts.settingsDraft[field.name] = checked ? [...rest, value] : rest;
    },

    settingUnknownChoices(field) {
      // 保存済みで、今の候補に無い値（落とさずにチェック済みで出す）
      const selected = this.exts.settingsDraft[field.name];
      if (!Array.isArray(selected)) return [];
      const known = (field.choices || []).map((choice) => choice.value);
      return selected.filter((item) => !known.some((value) => value === item));
    },

    settingsFieldErrors(name) {
      return this.exts.settingsErrors.filter((e) => e.field === name).map((e) => e.message);
    },

    async saveSettings() {
      const name = this.exts.selected;
      const settings = this.exts.detail && this.exts.detail.settings;
      if (!name || !settings) return;
      const values = {};
      for (const field of settings.fields) {
        let value = this.exts.settingsDraft[field.name];
        if (field.widget === 'checkbox-group') {
          // 全部オフも [] で明示する（省略すると初期値で保存されるため）
          values[field.name] = Array.isArray(value) ? [...value] : [];
          continue;
        }
        if (!field.supported) {
          values[field.name] = value === undefined ? null : value;
          continue;
        }
        if (field.nullable && this.exts.settingsUnset[field.name]) {
          value = null;
        } else if ((field.type === 'integer' || field.type === 'number') && value !== '') {
          value = Number(value);
        }
        values[field.name] = value;
      }
      this.exts.settingsNotice = '';
      this.exts.settingsErrors = [];
      try {
        const res = await this.authedFetch(
          '/api/admin/extensions/' + encodeURIComponent(name) + '/settings',
          {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ values }),
          },
        );
        const data = await res.json().catch(() => ({}));
        if (!res.ok) {
          this.exts.settingsErrors = data.errors && data.errors.length
            ? data.errors
            : [{ field: '', message: data.error || '保存できませんでした' }];
          return;
        }
        this.exts.detail.settings = data.settings;
        this.setSettingsDraft(data.settings);
        this.exts.settingsNotice = '設定を保存しました。次に始まる処理から使われます';
      } catch (e) {
        console.error('Failed to save extension settings', e);
        this.exts.settingsErrors = [{ field: '', message: '保存できませんでした' }];
      }
    },

    async extErrorText(res, fallback) {
      try {
        const data = await res.json();
        if (data && data.error) return fallback + ': ' + data.error;
      } catch (e) {
        // JSON でない応答は既定の文言にする
      }
      return fallback;
    },

    async saveExtensionOverrides(payload, doneText, failText) {
      const name = this.exts.selected;
      if (!name) return;
      this.exts.notice = '';
      this.exts.error = '';
      try {
        const res = await this.authedFetch(
          '/api/admin/extensions/' + encodeURIComponent(name) + '/overrides',
          {
            method: 'PATCH',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload),
          },
        );
        if (!res.ok) {
          this.exts.error = await this.extErrorText(res, failText + '（保存していません）');
          return;
        }
        const data = await res.json();
        if (data.detail) {
          this.setExtensionDetail(data.detail);
        } else {
          await this.selectExtension(name);
        }
        this.showApplyResult(data.tasks_apply, doneText);
      } catch (e) {
        console.error('Failed to save extension overrides', e);
        this.exts.error = failText;
      }
    },

    // 保存と反映は分けて伝える。保存できても反映に失敗したら成功と言わない。
    showApplyResult(status, savedText) {
      this.exts.applyFailed = status === 'failed';
      if (status === 'failed') {
        this.exts.notice = '';
        this.exts.error = '保存はできましたが、スケジューラへの反映に失敗しました。「再反映」を試すか、再起動してください';
      } else if (status === 'scheduler_disabled') {
        this.exts.notice = savedText + '。内部スケジューラは無効（scheduler.enabled: false）のため、外部のスケジューラが次に GET /api/tasks を取得したときに反映されます';
      } else if (status === 'scheduler_not_running') {
        this.exts.notice = savedText + '。スケジューラがまだ動いていないため、起動時に反映されます';
      } else if (status === 'applied') {
        this.exts.notice = savedText + '。スケジューラへ反映しました';
      } else {
        this.exts.notice = savedText + '。次の会話から使われます';
      }
    },

    async reapplyExtensionTasks() {
      const name = this.exts.selected;
      if (!name) return;
      this.exts.notice = '';
      this.exts.error = '';
      try {
        const res = await this.authedFetch(
          '/api/admin/extensions/' + encodeURIComponent(name) + '/tasks/apply',
          { method: 'POST' },
        );
        const data = await res.json().catch(() => ({}));
        if (!res.ok && data.status !== 'failed') {
          this.exts.error = data.error ? '再反映に失敗しました: ' + data.error : '再反映に失敗しました';
          return;
        }
        await this.selectExtension(name);
        this.showApplyResult(data.status, '保存済みの上書きを再反映しました');
      } catch (e) {
        console.error('Failed to reapply task overrides', e);
        this.exts.error = '再反映に失敗しました';
      }
    },

    savePromptOverride() {
      // 空文字も「何も注入しない」という有効な上書きとして保存する
      this.saveExtensionOverrides(
        { prompt: this.exts.promptDraft },
        'プロンプトの上書きを保存しました',
        'プロンプトの上書きを保存できませんでした',
      );
    },

    resetPromptOverride() {
      if (!confirm('プロンプトの上書きを消して、デフォルトに戻しますか？')) return;
      this.saveExtensionOverrides(
        { prompt: null },
        'プロンプトをデフォルトに戻しました',
        'プロンプトをデフォルトに戻せませんでした',
      );
    },

    saveTaskOverride(task) {
      const draft = this.exts.taskDrafts[task.name];
      const schedule = draft.schedule.trim();
      this.saveExtensionOverrides(
        {
          tasks: {
            [task.name]: {
              // 空欄はデフォルトの crontab を使う（上書きを消す）
              schedule: schedule || null,
              // 有効はデフォルトと同じなので上書きを消す
              enabled: draft.enabled ? null : false,
            },
          },
        },
        task.name + ' の上書きを保存しました',
        task.name + ' の上書きを保存できませんでした',
      );
    },

    resetTaskOverride(task) {
      this.saveExtensionOverrides(
        { tasks: { [task.name]: { schedule: null, enabled: null } } },
        task.name + ' をデフォルトに戻しました',
        task.name + ' をデフォルトに戻せませんでした',
      );
    },

    taskHasOverride(task) {
      return Object.keys(task.override).length > 0 || task.invalid.length > 0;
    },

    async fetchSkills() {
      this.skills.loading = true;
      try {
        const res = await this.authedFetch('/api/skills');
        const data = await res.json();
        this.skills.items = data.items || [];
      } catch (e) {
        console.error('Failed to fetch skills', e);
      } finally {
        this.skills.loading = false;
      }
    },

    async addSkill() {
      const name = this.skills.newName.trim();
      const description = this.skills.newDescription.trim();
      const prompt = this.skills.newPrompt.trim();
      if (!name || !prompt) return;
      if (!this.isValidSkillName(name)) {
        alert(SKILL_NAME_HINT);
        return;
      }
      try {
        const res = await this.authedFetch('/api/skills', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ name, description, prompt }),
        });
        if (res.ok) {
          this.skills.newName = '';
          this.skills.newDescription = '';
          this.skills.newPrompt = '';
          await this.fetchSkills();
        } else {
          await this.alertSkillFailure(res, '追加');
        }
      } catch (e) {
        console.error('Failed to add skill', e);
        alert('追加に失敗しました');
      }
    },

    async toggleSkill(skill) {
      try {
        const res = await this.authedFetch('/api/skills/' + skill._id, {
          method: 'PATCH',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ enabled: !skill.enabled }),
        });
        if (res.ok) {
          await this.fetchSkills();
        } else {
          await this.alertSkillFailure(res, '更新');
        }
      } catch (e) {
        console.error('Failed to toggle skill', e);
        alert('更新に失敗しました');
      }
    },

    startSkillEdit(skill) {
      this.skills.editingId = skill._id;
      this.skills.editName = skill.name;
      this.skills.editDescription = skill.description || '';
      this.skills.editPrompt = skill.prompt;
    },

    cancelSkillEdit() {
      this.skills.editingId = null;
      this.skills.editName = '';
      this.skills.editDescription = '';
      this.skills.editPrompt = '';
    },

    async saveSkillEdit(skill) {
      const name = this.skills.editName.trim();
      const description = this.skills.editDescription.trim();
      const prompt = this.skills.editPrompt.trim();
      if (!name || !prompt) return;
      if (!this.isValidSkillName(name)) {
        alert(SKILL_NAME_HINT);
        return;
      }
      try {
        const res = await this.authedFetch('/api/skills/' + skill._id, {
          method: 'PATCH',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ name, description, prompt }),
        });
        if (res.ok) {
          this.cancelSkillEdit();
          await this.fetchSkills();
        } else {
          await this.alertSkillFailure(res, '更新');
        }
      } catch (e) {
        console.error('Failed to save skill', e);
        alert('更新に失敗しました');
      }
    },

    async deleteSkill(skill) {
      if (!window.confirm('スキル「' + skill.name + '」を削除しますか？')) return;
      try {
        const res = await this.authedFetch('/api/skills/' + skill._id, { method: 'DELETE' });
        if (res.ok) {
          await this.fetchSkills();
        } else {
          alert('削除に失敗しました');
        }
      } catch (e) {
        console.error('Failed to delete skill', e);
        alert('削除に失敗しました');
      }
    },

    // ---- Utilities ----

    levelClass(level) {
      const map = {
        ERROR: 'level-error',
        WARNING: 'level-warning',
        INFO: 'level-info',
        DEBUG: 'level-debug',
        CRITICAL: 'level-critical',
      };
      return map[level] || 'level-default';
    },

    formatDate(isoStr) {
      if (!isoStr) return '';
      return new Date(isoStr).toLocaleString('ja-JP');
    },
  };
}
