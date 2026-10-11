/* 微积分题库与考试模拟系统 —— 前端单页应用
   零依赖原生 JS：状态驱动的视图切换、SSE 流式问答、考试计时与自动交卷。 */

(() => {
  'use strict';

  // 第二个参数是可选的查询根节点；弹窗里用 $('...', mask) 把查找限制在弹窗内
  const $ = (sel, root) => (root || document).querySelector(sel);
  const $$ = (sel, root) => Array.prototype.slice.call((root || document).querySelectorAll(sel));

  const GUEST = { user_id: 'guest', username: 'guest', nickname: '访客', role: 'guest' };
  const LEVEL_NAME = { 1: '基础', 2: '进阶', 3: '挑战' };

  const state = {
    user: null,
    view: 'qa',
    params: {},
    meta: null,
    qa: { sessions: [], current: null, messages: [], sending: false, deepThink: false, workflow: 'auto', controller: null },
    exam: { paper: null, answers: {}, deadline: 0, timer: null, dirty: false, report: null },
    kb: { docs: [], current: null, query: '' }
  };

  /* ---------------------------------------------------------- 工具 */

  const esc = (s) => String(s == null ? '' : s)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;').replace(/'/g, '&#39;');

  /* 把 ^指数 与 _下标 转成上标/下标标签。按「花括号 → 圆括号 → 数字 → 单字符」顺序匹配，
     避免 x^2y 被整体吞成上标。 */
  function math(s) {
    return s
      .replace(/\^\{([^}]{1,24})\}/g, '<sup>$1</sup>')
      .replace(/\^\(([^)]{1,24})\)/g, '<sup>$1</sup>')
      .replace(/\^(\d+)/g, '<sup>$1</sup>')
      .replace(/\^([A-Za-z\u0370-\u03ff\u4e00-\u9fff])/g, '<sup>$1</sup>')
      .replace(/_([A-Za-z0-9]{1,4})\b/g, '<sub>$1</sub>');
  }

  const rich = (s) => math(latexLite(esc(s)));

  /* LaTeX → 纯文本兜底转换。
     系统提示词已经要求模型不要用 LaTeX，但各家模型未必听话，这里做一层保险，
     避免页面上直接出现 \(\frac{a}{b}\) 这种源码。只覆盖微积分里常见的记号。 */
  const LATEX_SYMBOLS = [
    [/\\(?:displaystyle|textstyle|scriptstyle|limits|left|right|quad|qquad)\b[ \t]*/g, ''],
    [/\\[,;:!]/g, ''],
    [/\\\[|\\\]|\\\(|\\\)|\$\$/g, ''],
    [/\\(?:d|t)?frac\s*\{([^{}]*)\}\s*\{([^{}]*)\}/g, '($1)/($2)'],
    // 简写形式：\frac16 等价于 \frac{1}{6}，模型经常这么写
    [/\\(?:d|t)?frac\s*([0-9A-Za-z])\s*([0-9A-Za-z])/g, '($1)/($2)'],
    [/\\sqrt\s*\{([^{}]*)\}/g, '√($1)'],
    [/\\boxed\s*\{([^{}]*)\}/g, '$1'],
    [/\\text\s*\{([^{}]*)\}/g, '$1'],
    [/\\mathrm\s*\{([^{}]*)\}/g, '$1'],
    [/\\lim\s*_\{([^{}]*)\}/g, 'lim($1)'],
    [/\\lim\s*_([A-Za-z0-9])/g, 'lim($1)'],
    [/\\lim/g, 'lim'],
    [/\\int\s*_\{([^{}]*)\}\s*\^\{([^{}]*)\}/g, '∫($1→$2)'],
    [/\\int\s*_\{([^{}]*)\}/g, '∫($1)'],
    [/\\int/g, '∫'],
    [/\\sum\s*_\{([^{}]*)\}\s*\^\{([^{}]*)\}/g, 'Σ($1→$2)'],
    [/\\sum/g, 'Σ'],
    [/\\to|\\rightarrow|\\longrightarrow/g, '→'],
    [/\\infty/g, '∞'],
    [/\\cdot/g, '·'],
    [/\\times/g, '×'],
    [/\\div/g, '÷'],
    [/\\pm/g, '±'],
    [/\\leq|\\le\b/g, '≤'],
    [/\\geq|\\ge\b/g, '≥'],
    [/\\neq|\\ne\b/g, '≠'],
    [/\\approx/g, '≈'],
    [/\\equiv/g, '≡'],
    [/\\partial/g, '∂'],
    [/\\in\b/g, '∈'],
    [/\\xi/g, 'ξ'],
    [/\\pi/g, 'π'],
    [/\\alpha/g, 'α'],
    [/\\beta/g, 'β'],
    [/\\gamma/g, 'γ'],
    [/\\theta/g, 'θ'],
    [/\\lambda/g, 'λ'],
    [/\\mu/g, 'μ'],
    [/\\sigma/g, 'σ'],
    [/\\varphi|\\phi/g, 'φ'],
    [/\\psi/g, 'ψ'],
    [/\\omega/g, 'ω'],
    [/\\rho/g, 'ρ'],
    [/\\tau/g, 'τ'],
    [/\\eta/g, 'η'],
    [/\\zeta/g, 'ζ'],
    [/\\varepsilon|\\epsilon/g, 'ε'],
    [/\\Delta/g, 'Δ'],
    [/\\delta/g, 'δ'],
    [/\\sin/g, 'sin'], [/\\cos/g, 'cos'], [/\\tan/g, 'tan'],
    [/\\cot/g, 'cot'], [/\\sec/g, 'sec'], [/\\csc/g, 'csc'],
    [/\\ln/g, 'ln'], [/\\log/g, 'log'], [/\\exp/g, 'exp'],
    [/\\max/g, 'max'], [/\\min/g, 'min'],
    [/\\arcsin/g, 'arcsin'], [/\\arccos/g, 'arccos'], [/\\arctan/g, 'arctan'],
    [/\\prime/g, '′'],
    [/\\ /g, ' '],
    [/\s*→\s*/g, '→']
  ];

  function latexLite(s) {
    if (s.indexOf('\\') < 0 && s.indexOf('$') < 0) return s;
    let out = s;
    // \frac 可能嵌套，多跑两轮把外层剥掉
    for (let pass = 0; pass < 3; pass += 1) {
      const before = out;
      for (const [re, rep] of LATEX_SYMBOLS) out = out.replace(re, rep);
      if (out === before) break;
    }
    // 剩下的未知命令：去掉反斜杠保留命令名。
    // 花括号先转成圆括号，好让后面的 math() 仍能把 e^{3x} 识别成上标。
    out = out.replace(/\^\{([^{}]{1,24})\}/g, '^($1)').replace(/_\{([^{}]{1,24})\}/g, '_($1)');
    out = out.replace(/\\([A-Za-z]+)/g, '$1').replace(/[{}]/g, '');
    return out;
  }

  /* 极简 Markdown：粗体、斜体、行内代码、有序/无序列表、引用块。 */
  function markdown(text) {
    const lines = String(text || '').split('\n');
    const out = [];
    let list = null;
    let quote = null;
    const flush = () => {
      if (list) { out.push(`<${list.tag}>${list.items.join('')}</${list.tag}>`); list = null; }
      if (quote) { out.push(`<blockquote>${quote.join('<br/>')}</blockquote>`); quote = null; }
    };
    for (const raw of lines) {
      const line = raw.trimEnd();
      const inline = (t) => rich(t)
        .replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>')
        .replace(/(^|[^*])\*([^*\n]+)\*/g, '$1<em>$2</em>')
        .replace(/_([^_\n]+)_/g, '<em>$1</em>')
        .replace(/`([^`]+)`/g, '<code>$1</code>');
      if (!line.trim()) { flush(); continue; }
      if (/^\s*>/.test(line)) {
        if (list) { out.push(`<${list.tag}>${list.items.join('')}</${list.tag}>`); list = null; }
        quote = quote || [];
        quote.push(inline(line.replace(/^\s*>\s?/, '')));
        continue;
      }
      if (quote) { out.push(`<blockquote>${quote.join('<br/>')}</blockquote>`); quote = null; }
      const ol = line.match(/^\s*(\d+)\.\s+(.*)$/);
      const ul = line.match(/^\s*[-*]\s+(.*)$/);
      if (ol) {
        if (!list || list.tag !== 'ol') { if (list) out.push(`<${list.tag}>${list.items.join('')}</${list.tag}>`); list = { tag: 'ol', items: [] }; }
        list.items.push(`<li>${inline(ol[2])}</li>`);
        continue;
      }
      if (ul) {
        if (!list || list.tag !== 'ul') { if (list) out.push(`<${list.tag}>${list.items.join('')}</${list.tag}>`); list = { tag: 'ul', items: [] }; }
        list.items.push(`<li>${inline(ul[1])}</li>`);
        continue;
      }
      if (list) { out.push(`<${list.tag}>${list.items.join('')}</${list.tag}>`); list = null; }
      out.push(`<p>${inline(line)}</p>`);
    }
    flush();
    return out.join('');
  }

  function toast(msg, kind) {
    const layer = $('#toast-layer');
    if (!layer) return;
    const el = document.createElement('div');
    el.className = 'toast ' + (kind || '');
    el.textContent = msg;
    layer.appendChild(el);
    setTimeout(() => el.remove(), 3200);
  }

  const errText = (e) => {
    if (e instanceof API.ApiError) return API.describe(e.code, e.message);
    return (e && e.message) || '操作失败';
  };

  const fmtTime = (iso) => {
    if (!iso) return '—';
    const d = new Date(iso);
    if (isNaN(d.getTime())) return iso;
    const p = (n) => String(n).padStart(2, '0');
    return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`;
  };

  const fmtDuration = (sec) => {
    if (sec == null) return '—';
    const m = Math.floor(sec / 60), s = sec % 60;
    return m > 0 ? `${m} 分 ${s} 秒` : `${s} 秒`;
  };

  const isLoggedIn = () => !!(state.user && state.user.user_id !== 'guest');

  /* 需要登录时的统一占位卡片 */
  function needLogin(what) {
    return `<div class="wrap--narrow"><div class="card" style="text-align:center;padding:34px 18px">
      <div style="font-size:34px">🔒</div>
      <h3 style="margin:8px 0">${esc(what)}需要登录</h3>
      <p class="muted">登录后即可保存你的对话记录、试卷与错题本。</p>
      <p class="muted" style="font-size:12px">首次使用可点「注册」创建账号；本机默认管理员账号为 admin / admin123。</p>
      <button class="hm-btn hm-btn--primary" id="need-login">立即登录 / 注册</button>
    </div></div>`;
  }

  /* ---------------------------------------------------------- 主题 */

  function applyTheme(name) {
    document.documentElement.setAttribute('data-theme', name);
    localStorage.setItem('calc_theme', name);
  }
  const currentTheme = () => localStorage.getItem('calc_theme')
    || (window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light');

  /* ---------------------------------------------------------- 顶栏导航 */

  const NAV_USER = [
    { key: 'qa', label: '智能问答' },
    { key: 'exam', label: '考试模拟' },
    { key: 'history', label: '历史成绩' },
    { key: 'wrong', label: '错题本' },
    { key: 'kb', label: '知识库' },
    { key: 'about', label: '关于' }
  ];
  const NAV_ADMIN = NAV_USER.concat([{ key: 'admin', label: '运行监控' }]);

  /* 子页面归属到哪个导航项高亮：答题中算「考试模拟」，报告页算「历史成绩」 */
  const NAV_PARENT = { examrun: 'exam', result: 'history' };
  const navKey = (view) => NAV_PARENT[view] || view;

  function renderShell() {
    const user = state.user || GUEST;
    const nav = user.role === 'admin' ? NAV_ADMIN : NAV_USER;
    const active = navKey(state.view);
    const app = $('#app');
    app.innerHTML = `
      <header class="topbar">
        <div class="brand">
          <span class="brand-mark">∫</span>
          <span>微积分题库<span class="brand-sub"> · 考试模拟系统</span></span>
        </div>
        <nav class="nav" id="main-nav">
          ${nav.map((n) => `<button data-nav="${n.key}" class="${active === n.key ? 'is-active' : ''}">${n.label}</button>`).join('')}
        </nav>
        <div class="topbar-actions">
          <button class="hm-btn hm-btn--text" id="theme-toggle" title="切换主题">${currentTheme() === 'dark' ? '☀️' : '🌙'}</button>
          ${isLoggedIn()
            ? `<button class="hm-btn" id="account-entry">${esc(user.nickname || user.username)}</button>
               <button class="hm-btn hm-btn--text" id="logout">退出</button>`
            : `<button class="hm-btn hm-btn--primary" id="auth-entry">登录 / 注册</button>`}
        </div>
      </header>
      <div class="layout">
        <aside class="sidebar" id="sidebar"></aside>
        <main class="content" id="content"></main>
      </div>
      <div class="nav-mask" id="nav-mask"></div>`;

    $$('#main-nav button').forEach((b) => b.addEventListener('click', () => setView(b.dataset.nav)));
    $('#theme-toggle').addEventListener('click', () => {
      applyTheme(currentTheme() === 'dark' ? 'light' : 'dark');
      renderShell();
    });
    const authEntry = $('#auth-entry');
    if (authEntry) authEntry.addEventListener('click', () => showAuthDialog());
    const accountEntry = $('#account-entry');
    if (accountEntry) accountEntry.addEventListener('click', showAccountDialog);
    const logout = $('#logout');
    if (logout) logout.addEventListener('click', async () => {
      try { await API.logout(); } catch (e) { /* 令牌可能已失效 */ }
      API.clearSession();
      state.user = GUEST;
      state.qa = { sessions: [], current: null, messages: [], sending: false, deepThink: false, workflow: 'auto', controller: null };
      state.exam = { paper: null, answers: {}, deadline: 0, timer: null, dirty: false, report: null };
      setView('qa');
    });
  }

  function setView(view, params) {
    if (state.exam.timer && view !== 'examrun') stopTimer();
    state.view = view;
    state.params = params || {};
    render();
  }

  function render() {
    if (!state.user) state.user = GUEST;
    renderShell();
    const content = $('#content');
    const views = {
      qa: viewQa, exam: viewExam, examrun: viewExamRun, result: viewResult,
      history: viewHistory, wrong: viewWrong, kb: viewKb, about: viewAbout, admin: viewAdmin
    };
    if (state.view === 'examrun' && !state.exam.paper) state.view = 'exam';
    (views[state.view] || viewQa)(content);
  }

  /* ---------------------------------------------------------- 登录 / 注册 */

  function showAuthDialog() {
    const mask = document.createElement('div');
    mask.className = 'mask';
    mask.innerHTML = `
      <div class="dialog">
        <h3>登录 / 注册</h3>
        <div class="tabs">
          <button data-tab="login" class="is-active">登录</button>
          <button data-tab="register">注册</button>
        </div>
        <div id="auth-form"></div>
      </div>`;
    document.body.appendChild(mask);
    const close = () => mask.remove();
    mask.addEventListener('click', (e) => { if (e.target === mask) close(); });

    const renderForm = (tab) => {
      $('#auth-form', mask).innerHTML = tab === 'login' ? `
        <div class="field"><label>账号</label><input class="hm-input" id="f-user" autocomplete="username" /></div>
        <div class="field"><label>密码</label><input class="hm-input" id="f-pass" type="password" autocomplete="current-password" /></div>
        <button class="hm-btn hm-btn--primary block" id="f-submit">登录</button>
        <p class="muted" style="font-size:12px;margin:10px 0 0">本机默认管理员：admin / admin123</p>`
        : `
        <div class="field"><label>账号</label><input class="hm-input" id="f-user" placeholder="3-32字符，字母数字下划线或中文" /></div>
        <div class="field"><label>密码</label><input class="hm-input" id="f-pass" type="password" placeholder="至少6位，需含字母和数字" /></div>
        <div class="field"><label>昵称（可选）</label><input class="hm-input" id="f-nick" /></div>
        <div class="field"><label>联系方式（可选）</label><input class="hm-input" id="f-contact" placeholder="QQ / 微信 / 邮箱" /></div>
        <button class="hm-btn hm-btn--primary block" id="f-submit">注册并登录</button>`;
      $('#f-submit', mask).addEventListener('click', () => submit(tab, close, mask));
      $('#f-pass', mask).addEventListener('keydown', (e) => { if (e.key === 'Enter') submit(tab, close, mask); });
    };

    $$('.tabs button', mask).forEach((b) => b.addEventListener('click', () => {
      $$('.tabs button', mask).forEach((x) => x.classList.toggle('is-active', x === b));
      renderForm(b.dataset.tab);
    }));
    renderForm('login');
  }

  async function submit(tab, close, mask) {
    const username = $('#f-user', mask).value.trim();
    const password = $('#f-pass', mask).value;
    if (!username || !password) { toast('请填写账号与密码'); return; }
    try {
      const body = { username, password };
      if (tab === 'register') {
        body.nickname = $('#f-nick', mask).value.trim();
        body.contact = $('#f-contact', mask).value.trim();
      }
      const res = tab === 'login' ? await API.login(body) : await API.register(body);
      API.setSession(res.token, res.user);
      state.user = res.user;
      close();
      toast(`欢迎，${res.user.nickname || res.user.username}`, 'ok');
      render();
      migrateLocal();
    } catch (e) {
      toast(errText(e), 'error');
    }
  }

  /* 把匿名期间的本地会话/试卷导入账号（对应服务端的 migrate 接口） */
  async function migrateLocal() {
    try {
      const sessions = API.local.sessions();
      const papers = API.local.papers();
      if (sessions.length) {
        await API.qaMigrate({ sessions });
        API.local.saveSessions([]);
      }
      if (papers.length) {
        await API.examMigrate({ papers });
        API.local.savePapers([]);
      }
      if (sessions.length || papers.length) toast('已导入本地记录', 'ok');
    } catch (e) { /* 迁移失败不影响主流程 */ }
  }

  function showAccountDialog() {
    const u = state.user;
    const mask = document.createElement('div');
    mask.className = 'mask';
    mask.innerHTML = `
      <div class="dialog">
        <h3>账号信息</h3>
        <div class="field"><label>账号</label><div>${esc(u.username)}</div></div>
        <div class="field"><label>角色</label><div>${u.role === 'admin' ? '管理员' : '普通用户'}</div></div>
        <div class="field"><label>昵称</label><input class="hm-input" id="a-nick" value="${esc(u.nickname)}" /></div>
        <div class="field"><label>联系方式</label><input class="hm-input" id="a-contact" value="${esc(u.contact || '')}" /></div>
        <div class="row">
          <button class="hm-btn hm-btn--primary" id="a-save">保存</button>
          <button class="hm-btn" id="a-close">关闭</button>
        </div>

        <hr style="border:0;border-top:1px solid var(--line);margin:16px 0" />
        <div class="card-title" style="font-size:14px">修改密码</div>
        ${u.username === 'admin'
          ? '<p class="muted" style="font-size:12px;margin:6px 0 0">⚠️ 你正在使用默认管理员账号。若本机对外网开放（例如手机通过局域网访问），请务必修改密码。</p>'
          : ''}
        <div class="field" style="margin-top:8px"><label>原密码</label>
          <input class="hm-input" id="p-old" type="password" autocomplete="current-password" /></div>
        <div class="field"><label>新密码</label>
          <input class="hm-input" id="p-new" type="password" placeholder="至少6位，需含字母和数字" autocomplete="new-password" /></div>
        <div class="field"><label>确认新密码</label>
          <input class="hm-input" id="p-new2" type="password" autocomplete="new-password" /></div>
        <button class="hm-btn block" id="p-submit">修改密码</button>
      </div>`;
    document.body.appendChild(mask);
    const close = () => mask.remove();
    mask.addEventListener('click', (e) => { if (e.target === mask) close(); });
    $('#a-close', mask).addEventListener('click', close);
    $('#a-save', mask).addEventListener('click', async () => {
      try {
        const res = await API.updateProfile({
          nickname: $('#a-nick', mask).value.trim(),
          contact: $('#a-contact', mask).value.trim()
        });
        state.user = res.user;
        API.setSession(API.getToken(), res.user);
        close();
        toast('已保存', 'ok');
        render();
      } catch (e) { toast(errText(e), 'error'); }
    });
    $('#p-submit', mask).addEventListener('click', async () => {
      const oldPass = $('#p-old', mask).value;
      const newPass = $('#p-new', mask).value;
      const again = $('#p-new2', mask).value;
      if (!oldPass || !newPass) { toast('请填写原密码与新密码'); return; }
      if (newPass !== again) { toast('两次输入的新密码不一致', 'error'); return; }
      try {
        await API.changePassword({ old_password: oldPass, new_password: newPass });
        close();
        API.clearSession();
        state.user = GUEST;
        toast('密码已修改，请用新密码重新登录', 'ok');
        render();
        showAuthDialog();
      } catch (e) { toast(errText(e), 'error'); }
    });
  }

  /* ---------------------------------------------------------- 问答 */

  function viewQa(content) {
    content.innerHTML = `
      <div class="chat">
        <div class="chat-scroll" id="chat-scroll"></div>
        <div class="composer">
          <div class="row" style="margin-bottom:8px">
            <button type="button" class="hm-think-pill" id="deep-think" aria-pressed="${state.qa.deepThink}">
              <span class="hm-think-pill__dot"></span>深度思考
            </button>
            <select class="hm-think-pill" id="workflow-pick" title="自动＝先检索知识库，命中就用原文作答；问答＝强制检索；闲聊＝不检索">
              <option value="auto">工作流 · 自动</option>
              <option value="kb">工作流 · 问答</option>
              <option value="chat">工作流 · 闲聊</option>
            </select>
            <span class="hm-chip" id="quota-chip" style="display:none"></span>
          </div>
          <div class="composer-row">
            <textarea class="hm-input" id="ask-input" rows="1" placeholder="向微积分知识库提问，例如：拉格朗日中值定理的条件是什么？"></textarea>
            <button class="hm-btn hm-btn--primary" id="ask-btn">发送</button>
          </div>
        </div>
      </div>`;
    $('#workflow-pick').value = state.qa.workflow;

    renderSidebar();
    if (!isLoggedIn()) {
      $('#chat-scroll').innerHTML = needLogin('智能问答');
      const btn = $('#need-login');
      if (btn) btn.addEventListener('click', showAuthDialog);
      $('#ask-input').disabled = true;
      $('#ask-btn').disabled = true;
    } else {
      loadSessions();
      renderMessages();
    }

    $('#deep-think').addEventListener('click', () => {
      state.qa.deepThink = !state.qa.deepThink;
      $('#deep-think').setAttribute('aria-pressed', String(state.qa.deepThink));
    });
    $('#workflow-pick').addEventListener('change', (e) => { state.qa.workflow = e.target.value; });
    $('#ask-btn').addEventListener('click', sendQuestion);
    $('#ask-input').addEventListener('keydown', (e) => {
      if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); sendQuestion(); }
    });
    if (isLoggedIn()) refreshQuota();
  }

  /* 今日大模型问答额度：公开部署时每一问都花钱，所以让用户看得见 */
  function renderQuota(q) {
    const el = $('#quota-chip');
    if (!el) return;
    if (!q || !q.user_limit) { el.style.display = 'none'; return; }
    el.style.display = '';
    const left = q.user_remaining;
    el.textContent = `今日大模型额度 剩余 ${left}/${q.user_limit}`;
    el.className = 'hm-chip ' + (left > 5 ? 'hm-tag--info' : (left > 0 ? 'hm-tag--warn' : 'hm-tag--err'));
    el.title = `全局今日已用 ${q.global_used}${q.global_limit ? ' / ' + q.global_limit : ''}（UTC ${q.day} 重置）`;
  }

  function refreshQuota() {
    API.usage().then(renderQuota).catch(() => {});
  }

  function renderSidebar() {
    const sidebar = $('#sidebar');
    if (!sidebar) return;
    if (!isLoggedIn()) { sidebar.innerHTML = ''; sidebar.style.display = 'none'; return; }
    sidebar.style.display = '';
    sidebar.innerHTML = `
      <div class="sidebar-head">
        <button class="hm-btn hm-btn--primary block" id="new-session">＋ 新的对话</button>
      </div>
      <div class="session-list" id="session-list">
        ${state.qa.sessions.length ? state.qa.sessions.map((s) => `
          <div class="session-item ${s.session_id === state.qa.current ? 'active' : ''}" data-id="${esc(s.session_id)}">
            <div class="t">${esc(s.title)}</div>
            <div class="d">${s.message_count} 条 · ${fmtTime(s.updated_at)}</div>
          </div>`).join('') : '<div class="empty">还没有对话<br/>点击上方新建</div>'}
      </div>`;
    $('#new-session').addEventListener('click', createSession);
    $$('.session-item').forEach((n) => n.addEventListener('click', () => openSession(n.dataset.id)));
  }

  async function loadSessions() {
    try {
      const data = await API.qaSessions();
      state.qa.sessions = data.items || [];
      if (!state.qa.current && state.qa.sessions.length) state.qa.current = state.qa.sessions[0].session_id;
      renderSidebar();
      if (state.qa.current) await openSession(state.qa.current, true);
    } catch (e) { toast(errText(e), 'error'); }
  }

  async function createSession() {
    try {
      const s = await API.qaCreate();
      state.qa.current = s.session_id;
      state.qa.messages = [];
      await loadSessions();
      renderMessages();
      const input = $('#ask-input');
      if (input) input.focus();
    } catch (e) { toast(errText(e), 'error'); }
  }

  async function openSession(sessionId, silent) {
    state.qa.current = sessionId;
    try {
      const data = await API.qaMessages(sessionId);
      state.qa.messages = data.items || [];
      renderMessages();
      renderSidebar();
    } catch (e) { if (!silent) toast(errText(e), 'error'); }
  }

  function bubbleHtml(m) {
    if (m.role === 'user') {
      return `<div class="bubble user"><div class="role">我</div><div class="body">${esc(m.content)}</div></div>`;
    }
    const meta = m.meta || {};
    const chips = [];
    if (meta.mode_label) chips.push(`<span class="hm-chip hm-tag--info">${esc(meta.mode_label)}</span>`);
    if (meta.deep_think) chips.push('<span class="hm-chip">深度思考</span>');
    if (meta.workflow) chips.push(`<span class="hm-chip">工作流:${esc(meta.workflow)}</span>`);
    if (meta.contexts) chips.push(`<span class="hm-chip">命中 ${meta.contexts} 个知识块</span>`);
    return `<div class="bubble assistant">
      <div class="role">助教 ${chips.join(' ')}</div>
      <div class="body">${markdown(m.content)}</div>
      ${(m.citations && m.citations.length) ? `<div class="citations">
        <div class="muted" style="margin-bottom:4px">引用来源</div>
        ${m.citations.map((c) => `<div class="c">📖 《${esc(c.doc_title)}》· ${esc(c.section)}（相关度 ${c.score}）</div>`).join('')}
      </div>` : ''}
    </div>`;
  }

  function renderMessages() {
    const box = $('#chat-scroll');
    if (!box) return;
    if (!state.qa.messages.length) {
      box.innerHTML = `<div class="wrap--narrow"><div class="card">
        <div class="card-title">👋 你好，我是微积分助教</div>
        <p class="muted">知识库覆盖 6 章内容，共 101 道题的题库。可以这样问我：</p>
        <ul class="muted">
          <li>拉格朗日中值定理成立需要哪些条件？</li>
          <li>等价无穷小替换为什么不能用在加减里？</li>
          <li>分部积分法怎么选 u？</li>
          <li>反常积分 ∫(1→+∞) dx/x² 收敛吗？</li>
        </ul>
        <p class="muted" style="font-size:12px">回答由知识库检索结果组织而成，不接入大模型也能给出处。</p>
      </div></div>`;
      return;
    }
    box.innerHTML = state.qa.messages.map(bubbleHtml).join('');
    box.scrollTop = box.scrollHeight;
  }

  async function sendQuestion() {
    if (state.qa.sending) return;
    const input = $('#ask-input');
    const question = input.value.trim();
    if (!question) return;
    if (!isLoggedIn()) { showAuthDialog(); return; }
    if (!state.qa.current) { await createSession(); if (!state.qa.current) return; }

    input.value = '';
    state.qa.messages.push({ role: 'user', content: question, citations: [], meta: {} });
    renderMessages();

    state.qa.sending = true;
    const box = $('#chat-scroll');
    const holder = document.createElement('div');
    holder.className = 'bubble assistant';
    holder.innerHTML = `<div class="role">助教</div><div class="body">
      <div class="stage-line" id="stage-line">正在处理…</div><div id="stream-body"></div>
      <div class="citations" id="citations" style="display:none"></div></div>`;
    box.appendChild(holder);
    box.scrollTop = box.scrollHeight;
    const stageLine = $('#stage-line', holder);
    const streamBody = $('#stream-body', holder);
    const cits = $('#citations', holder);
    let acc = '';
    const citations = [];

    try {
      state.qa.controller = new AbortController();
      for await (const frame of API.streamAsk(
        state.qa.current, question, state.qa.controller.signal, state.qa.deepThink, state.qa.workflow
      )) {
        if (frame.event === 'stage') {
          stageLine.textContent = `${frame.data.name}：${frame.data.detail || ''}`;
        } else if (frame.event === 'citation') {
          citations.length = 0;
          (frame.data.items || []).forEach((c) => citations.push(c));
          if (citations.length) {
            cits.style.display = '';
            cits.innerHTML = `<div class="muted" style="margin-bottom:4px">引用来源</div>` +
              citations.map((c) => `<div class="c">📖 《${esc(c.doc_title)}》· ${esc(c.section)}（相关度 ${c.score}）</div>`).join('');
          }
        } else if (frame.event === 'delta') {
          acc += frame.data.text;
          streamBody.innerHTML = markdown(acc);
          box.scrollTop = box.scrollHeight;
        } else if (frame.event === 'error') {
          // 大模型相关的所有降级提示（不可用 / 额度用尽）都告知用户，正文本身仍是有效回答
          if (frame.data.code && frame.data.code.indexOf('LLM_') === 0) {
            toast(frame.data.message, 'error');
          }
          if (frame.data.quota) renderQuota(frame.data.quota);
        } else if (frame.event === 'done') {
          stageLine.textContent = `完成 · ${frame.data.mode_label || ''} · ${frame.data.chars} 字`;
          if (frame.data.quota) renderQuota(frame.data.quota);
        }
      }
      state.qa.messages.push({ role: 'assistant', content: acc, citations, meta: { mode_label: '检索结果' } });
      await loadSessions();
      await openSession(state.qa.current, true);
    } catch (e) {
      if (e.name === 'AbortError') return;
      stageLine.textContent = '生成失败';
      streamBody.innerHTML = `<p style="color:var(--err)">${esc(errText(e))}</p>`;
    } finally {
      state.qa.sending = false;
    }
  }

  /* ---------------------------------------------------------- 考试组卷 */

  async function viewExam(content) {
    if (!isLoggedIn()) {
      content.innerHTML = needLogin('考试模拟');
      const b = $('#need-login');
      if (b) b.addEventListener('click', showAuthDialog);
      return;
    }
    content.innerHTML = '<div class="empty">正在加载题库信息…</div>';
    try {
      state.meta = await API.examMetadata();
    } catch (e) {
      content.innerHTML = `<div class="empty">加载失败：${esc(errText(e))}</div>`;
      return;
    }
    const m = state.meta;
    const levelCount = (lv) => (m.scope_levels.find((x) => x.level === lv) || {}).available_points || 0;
    content.innerHTML = `
      <div class="wrap--narrow">
        <div class="card">
          <div class="card-title">题库概况</div>
          <div class="grid grid-4" style="margin-top:10px">
            <div class="stat"><div class="v">${m.total_points}</div><div class="k">可用题目</div></div>
            <div class="stat"><div class="v">${m.max_questions}</div><div class="k">单卷题量上限</div></div>
            <div class="stat"><div class="v">${m.chapters.length}</div><div class="k">章节</div></div>
            <div class="stat"><div class="v">${m.default_duration_minutes}′</div><div class="k">默认时长</div></div>
          </div>
          <p class="muted" style="font-size:12px;margin:10px 0 0">
            难度分布：基础 ${levelCount(1)} 题 · 进阶 ${levelCount(2)} 题 · 挑战 ${levelCount(3)} 题
          </p>
        </div>

        <div class="card">
          <div class="card-title">组卷规则</div>
          <div class="field" style="margin-top:10px">
            <label>试卷名称</label>
            <input class="hm-input" id="cfg-title" value="微积分模拟测验" maxlength="60" />
          </div>

          <label class="muted" style="font-size:12px">题型配比（单题分值：单选 ${m.type_scores['单选题']} 分 · 多选 ${m.type_scores['多选题']} 分 · 判断 ${m.type_scores['判断题']} 分 · 填空 ${m.type_scores['填空题']} 分）</label>
          <div class="grid grid-4" style="margin:6px 0 12px">
            ${m.question_types.map((t, i) => `
              <div class="field">
                <label>${t}</label>
                <input class="hm-input cfg-type" data-type="${t}" type="number" min="0" max="${m.max_questions}" value="${[4, 2, 2, 2][i] || 0}" />
              </div>`).join('')}
          </div>

          <label class="muted" style="font-size:12px">难度配比（填 0 表示不限难度；三者之和不能超过总题量）</label>
          <div class="grid grid-4" style="margin:6px 0 12px">
            ${[1, 2, 3].map((lv) => `
              <div class="field">
                <label>${LEVEL_NAME[lv]}（${levelCount(lv)} 题）</label>
                <input class="hm-input cfg-level" data-level="${lv}" type="number" min="0" max="${m.max_questions}" value="0" />
              </div>`).join('')}
            <div class="field">
              <label>考试时长（分钟）</label>
              <input class="hm-input" id="cfg-duration" type="number" min="1" max="180" value="${m.default_duration_minutes}" />
            </div>
          </div>

          <label class="muted" style="font-size:12px">抽题章节（不选表示全章节随机）</label>
          <div class="toc" id="cfg-chapters" style="margin:6px 0 14px">
            ${m.chapters.map((c) => `<button type="button" data-chapter="${esc(c.title)}">${esc(c.title)}（${c.question_count}）</button>`).join('')}
          </div>

          <div class="row">
            <button class="hm-btn hm-btn--primary" id="cfg-start">开始考试</button>
            <button class="hm-btn" id="cfg-random">随机配比</button>
            <span class="muted" id="cfg-hint"></span>
          </div>
        </div>
      </div>`;

    $$('#cfg-chapters button').forEach((b) => b.addEventListener('click', () => b.classList.toggle('is-active')));
    $('#cfg-start').addEventListener('click', startExam);
    $('#cfg-random').addEventListener('click', () => {
      $$('.cfg-type').forEach((el) => { el.value = Math.floor(Math.random() * 4) + 2; });
      $$('.cfg-level').forEach((el) => { el.value = 0; });
      toast('已随机生成配比');
    });
  }

  async function startExam() {
    const types = {};
    $$('.cfg-type').forEach((el) => { types[el.dataset.type] = parseInt(el.value || '0', 10); });
    const levels = {};
    $$('.cfg-level').forEach((el) => { levels[el.dataset.level] = parseInt(el.value || '0', 10); });
    const chapters = $$('#cfg-chapters button.is-active').map((b) => b.dataset.chapter);
    const duration = parseInt($('#cfg-duration').value || '10', 10);
    const total = Object.values(types).reduce((a, b) => a + b, 0);
    if (total <= 0) { toast('至少要选 1 道题', 'error'); return; }
    if (total > state.meta.max_questions) { toast(`总题量不能超过 ${state.meta.max_questions}`, 'error'); return; }

    try {
      const paper = await API.examCreate({
        title: $('#cfg-title').value.trim(),
        types, levels, chapters, duration_minutes: duration
      });
      enterExam(paper);
    } catch (e) { toast(errText(e), 'error'); }
  }

  function enterExam(paper) {
    state.exam.paper = paper;
    state.exam.answers = Object.assign({}, paper.drafts || {});
    state.exam.deadline = Date.now() + (paper.remaining_seconds || paper.duration_minutes * 60) * 1000;
    state.exam.dirty = false;
    setView('examrun');
  }

  /* ---------------------------------------------------------- 答题 */

  function questionInputHtml(q, idx) {
    const val = state.exam.answers[q.question_id];
    if (q.type === '填空题') {
      return `<div class="blank-input"><input class="hm-input" data-qid="${esc(q.question_id)}" data-kind="blank"
        placeholder="请输入答案" value="${esc(val || '')}" /></div>`;
    }
    const multi = q.type === '多选题';
    return (q.options || []).map((o) => {
      const picked = multi
        ? String(val || '').indexOf(o.key) >= 0
        : val === o.key;
      return `<label class="opt ${picked ? 'is-picked' : ''}" data-qid="${esc(q.question_id)}" data-key="${o.key}">
        <input type="${multi ? 'checkbox' : 'radio'}" name="q-${esc(q.question_id)}" data-qid="${esc(q.question_id)}"
          data-kind="${multi ? 'multi' : 'single'}" value="${o.key}" ${picked ? 'checked' : ''} />
        <span class="key">${o.key}.</span><span>${rich(o.text)}</span>
      </label>`;
    }).join('');
  }

  function viewExamRun(content) {
    const paper = state.exam.paper;
    if (!paper) { setView('exam'); return; }
    const answered = Object.keys(state.exam.answers).filter((k) => state.exam.answers[k]).length;
    content.innerHTML = `
      <div class="wrap--narrow">
        <div class="exam-timer" id="exam-timer">
          <div class="spread">
            <div>
              <div style="font-weight:700">${esc(paper.title)}</div>
              <div class="muted" style="font-size:12px">${esc(paper.description)}</div>
            </div>
            <div style="text-align:right">
              <div class="timer-num" id="timer-num">--:--</div>
              <div class="muted" style="font-size:12px">已作答 <span id="answered-num">${answered}</span> / ${paper.question_count}</div>
            </div>
          </div>
        </div>

        <div class="card" id="question-card">
          ${paper.questions.map((q, i) => `
            <div class="q-item">
              <div class="q-head">
                <span class="q-no">${i + 1}.</span>
                <span class="q-stem">${rich(q.stem)}</span>
                <span class="hm-chip hm-tag--info">${esc(q.type)}</span>
                <span class="hm-chip">${LEVEL_NAME[q.level] || q.level}</span>
                <span class="hm-chip">${q.score} 分</span>
              </div>
              ${questionInputHtml(q, i)}
            </div>`).join('')}
        </div>

        <div class="card">
          <div class="row">
            <button class="hm-btn hm-btn--primary" id="submit-paper">交卷</button>
            <button class="hm-btn" id="save-draft">保存草稿</button>
            <button class="hm-btn hm-btn--danger" id="abandon">放弃本次考试</button>
            <span class="muted" id="save-hint"></span>
          </div>
        </div>
      </div>`;

    $$('#question-card input').forEach((el) => el.addEventListener('change', onAnswerChange));
    $$('#question-card input[data-kind="blank"]').forEach((el) => el.addEventListener('input', onAnswerChange));
    $('#submit-paper').addEventListener('click', () => submitPaper(false));
    $('#save-draft').addEventListener('click', () => saveDraft(true));
    $('#abandon').addEventListener('click', () => {
      if (!confirm('放弃后本次试卷不再计分，确定继续？')) return;
      stopTimer();
      state.exam.paper = null;
      setView('exam');
    });
    startTimer();
  }

  function onAnswerChange(e) {
    const el = e.target;
    const qid = el.dataset.qid;
    if (el.dataset.kind === 'multi') {
      const picked = $$(`input[data-kind="multi"][data-qid="${qid}"]:checked`).map((x) => x.value).sort().join('');
      state.exam.answers[qid] = picked;
      $$(`.opt[data-qid="${qid}"]`).forEach((o) => o.classList.toggle('is-picked', picked.indexOf(o.dataset.key) >= 0));
    } else {
      state.exam.answers[qid] = el.value;
      if (el.dataset.kind === 'single') {
        $$(`.opt[data-qid="${qid}"]`).forEach((o) => o.classList.toggle('is-picked', o.dataset.key === el.value));
      }
    }
    state.exam.dirty = true;
    const n = Object.keys(state.exam.answers).filter((k) => state.exam.answers[k]).length;
    const box = $('#answered-num');
    if (box) box.textContent = n;
  }

  function startTimer() {
    stopTimer();
    const tick = () => {
      const left = Math.max(0, Math.round((state.exam.deadline - Date.now()) / 1000));
      const num = $('#timer-num');
      const wrap = $('#exam-timer');
      if (num) {
        const m = Math.floor(left / 60), s = left % 60;
        num.textContent = `${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`;
      }
      if (wrap) wrap.classList.toggle('urgent', left <= 60);
      if (left <= 0) { stopTimer(); submitPaper(true); }
    };
    tick();
    state.exam.timer = setInterval(tick, 1000);
  }

  function stopTimer() {
    if (state.exam.timer) { clearInterval(state.exam.timer); state.exam.timer = null; }
  }

  async function saveDraft(notify) {
    if (!state.exam.paper) return;
    const hint = $('#save-hint');
    try {
      await API.examDrafts(state.exam.paper.paper_id, { answers: state.exam.answers });
      state.exam.dirty = false;
      if (hint) hint.textContent = '草稿已保存 ' + new Date().toLocaleTimeString();
      if (notify) toast('草稿已保存', 'ok');
    } catch (e) {
      if (hint) hint.textContent = '';
      if (e.code === 'EXAM_TIME_UP') { submitPaper(true); return; }
      if (notify) toast(errText(e), 'error');
    }
  }

  async function submitPaper(auto) {
    if (!state.exam.paper) return;
    const paper = state.exam.paper;
    const unanswered = paper.question_count - Object.keys(state.exam.answers).filter((k) => state.exam.answers[k]).length;
    if (!auto && unanswered > 0 && !confirm(`还有 ${unanswered} 题未作答，确定交卷？`)) return;
    stopTimer();
    try {
      await API.examSubmit(paper.paper_id, { answers: state.exam.answers, auto: !!auto });
      if (auto) toast('时间到，已自动交卷', 'error');
      state.exam.paper = null;
      state.exam.answers = {};
      setView('result', { paperId: paper.paper_id });
    } catch (e) {
      toast(errText(e), 'error');
      if (e.code === 'PAPER_ALREADY_SUBMITTED') {
        state.exam.paper = null;
        setView('result', { paperId: paper.paper_id });
      }
    }
  }

  /* ---------------------------------------------------------- 成绩报告 */

  async function viewResult(content) {
    if (!isLoggedIn()) { content.innerHTML = needLogin('成绩报告'); const b = $('#need-login'); if (b) b.addEventListener('click', showAuthDialog); return; }
    content.innerHTML = '<div class="empty">正在生成报告…</div>';
    try {
      const rep = await API.examReport(state.params.paperId);
      if (!rep.ready) {
        content.innerHTML = `<div class="wrap--narrow"><div class="card">
          <div class="card-title">试卷尚未交卷</div>
          <p class="muted">该试卷仍在进行中。</p>
          <button class="hm-btn hm-btn--primary" id="go-exam">返回考试</button></div></div>`;
        $('#go-exam').addEventListener('click', () => setView('exam'));
        return;
      }
      state.exam.report = rep;
      const color = rep.ratio >= 0.6 ? 'var(--ok)' : 'var(--err)';
      content.innerHTML = `
        <div class="wrap--narrow">
          <div class="card">
            <div class="score-hero">
              <div class="muted">${esc(rep.title)}</div>
              <div class="big" style="color:${color}">${rep.score}<span style="font-size:22px;color:var(--muted)"> / ${rep.total_score}</span></div>
              <div class="grade" style="color:${color}">${esc(rep.grade)}　正确率 ${(rep.ratio * 100).toFixed(1)}%</div>
              <div class="muted" style="font-size:12px;margin-top:4px">
                共 ${rep.question_count} 题，答对 ${rep.correct_count} 题 · 用时 ${fmtDuration(rep.elapsed_seconds)} · 交卷于 ${fmtTime(rep.submitted_at)}
              </div>
            </div>
          </div>

          <div class="card">
            <div class="card-title">分章节掌握情况</div>
            <table class="hm-table" style="margin-top:8px">
              <thead><tr><th>章节</th><th style="width:110px">得分</th><th style="width:90px">正确率</th><th style="width:130px"></th></tr></thead>
              <tbody>
                ${rep.chapters.map((c) => `<tr>
                  <td>${esc(c.chapter)}</td>
                  <td>${c.score} / ${c.full}</td>
                  <td>${(c.accuracy * 100).toFixed(0)}%</td>
                  <td><div class="bar"><i style="width:${(c.accuracy * 100).toFixed(0)}%;background:${c.accuracy >= 0.6 ? 'var(--ok)' : 'var(--err)'}"></i></div></td>
                </tr>`).join('')}
              </tbody>
            </table>
          </div>

          <div class="card">
            <div class="card-title">学习建议</div>
            <ul style="margin:8px 0 0;padding-left:20px">${rep.suggestions.map((s) => `<li>${esc(s)}</li>`).join('')}</ul>
            ${rep.weak_points.length ? `<div style="margin-top:12px">
              <div class="muted" style="font-size:12px;margin-bottom:6px">需要加强的知识点</div>
              <div class="row">${rep.weak_points.map((w) => `<span class="hm-chip hm-tag--err">${esc(w.point)} ${w.correct}/${w.total}</span>`).join('')}</div>
            </div>` : ''}
            ${rep.strong_points.length ? `<div style="margin-top:12px">
              <div class="muted" style="font-size:12px;margin-bottom:6px">掌握较好的知识点</div>
              <div class="row">${rep.strong_points.map((w) => `<span class="hm-chip hm-tag--ok">${esc(w.point)}</span>`).join('')}</div>
            </div>` : ''}
          </div>

          <div class="card">
            <div class="card-title">逐题解析</div>
            ${rep.questions.map((q, i) => reviewHtml(q, i)).join('')}
          </div>

          <div class="row" style="margin-bottom:20px">
            <button class="hm-btn hm-btn--primary" id="again">再考一次</button>
            <button class="hm-btn" id="to-history">查看历史成绩</button>
            <button class="hm-btn" id="to-wrong">去错题本</button>
          </div>
        </div>`;
      $('#again').addEventListener('click', () => setView('exam'));
      $('#to-history').addEventListener('click', () => setView('history'));
      $('#to-wrong').addEventListener('click', () => setView('wrong'));
    } catch (e) {
      content.innerHTML = `<div class="empty">加载失败：${esc(errText(e))}</div>`;
    }
  }

  function reviewHtml(q, i) {
    const isBlank = q.type === '填空题';
    const your = q.your_answer || '';
    let body;
    if (isBlank) {
      body = `<div class="opt ${q.is_correct ? 'is-right' : 'is-wrong'}">
        <span class="key">你的答案：</span><span>${your ? rich(your) : '<span class="muted">未作答</span>'}</span></div>
        <div class="opt is-right"><span class="key">参考答案：</span><span>${rich((q.accepted || []).join(' 或 '))}</span></div>`;
    } else {
      body = (q.options || []).map((o) => {
        const inAnswer = q.answer.indexOf(o.key) >= 0;
        const picked = your.indexOf(o.key) >= 0;
        let cls = '';
        if (inAnswer) cls = 'is-right';
        else if (picked) cls = 'is-wrong';
        const marks = [];
        if (inAnswer) marks.push('正确答案');
        if (picked) marks.push('你的选择');
        return `<div class="opt ${cls}">
          <span class="key">${o.key}.</span><span style="flex:1">${rich(o.text)}</span>
          ${marks.length ? `<span class="hm-chip ${inAnswer ? 'hm-tag--ok' : 'hm-tag--err'}">${marks.join(' / ')}</span>` : ''}
        </div>`;
      }).join('');
    }
    return `<div class="q-item">
      <div class="q-head">
        <span class="q-no">${i + 1}.</span>
        <span class="q-stem">${rich(q.stem)}</span>
        <span class="hm-chip ${q.is_correct ? 'hm-tag--ok' : 'hm-tag--err'}">${q.is_correct ? '答对' : (q.got_score > 0 ? '部分得分' : '答错')} ${q.got_score}/${q.score}</span>
      </div>
      ${body}
      <div class="analysis"><strong>解析：</strong>${markdown(q.analysis)}
        <div class="muted" style="font-size:12px;margin-top:6px">
          知识点：${(q.knowledge_points || []).map(esc).join('、')}　·　难度：${LEVEL_NAME[q.level] || q.level}　·　章节：${esc(q.chapter)}
        </div>
      </div>
    </div>`;
  }

  /* ---------------------------------------------------------- 历史成绩 */

  async function viewHistory(content) {
    if (!isLoggedIn()) { content.innerHTML = needLogin('历史成绩'); const b = $('#need-login'); if (b) b.addEventListener('click', showAuthDialog); return; }
    content.innerHTML = '<div class="empty">正在加载…</div>';
    try {
      const data = await API.examHistory(1, 50);
      const s = data.stats || {};
      content.innerHTML = `
        <div class="wrap--narrow">
          <div class="card">
            <div class="grid grid-4">
              <div class="stat"><div class="v">${s.papers || 0}</div><div class="k">已完成试卷</div></div>
              <div class="stat"><div class="v">${((s.avg_ratio || 0) * 100).toFixed(0)}%</div><div class="k">平均正确率</div></div>
              <div class="stat"><div class="v">${((s.best_ratio || 0) * 100).toFixed(0)}%</div><div class="k">最好成绩</div></div>
              <div class="stat"><div class="v">${s.wrong_book || 0}</div><div class="k">错题本待复习</div></div>
            </div>
          </div>
          <div class="card">
            <div class="card-title">考试记录</div>
            ${data.items.length ? `<table class="hm-table" style="margin-top:8px">
              <thead><tr><th>试卷</th><th style="width:90px">得分</th><th style="width:80px">正确率</th><th style="width:70px">等级</th><th style="width:150px">时间</th></tr></thead>
              <tbody>${data.items.map((p) => `<tr class="clickable" data-paper="${esc(p.paper_id)}">
                <td>${esc(p.title)}<div class="muted" style="font-size:12px">${p.question_count} 题 · ${p.status}</div></td>
                <td>${p.score == null ? '—' : p.score + ' / ' + p.total_score}</td>
                <td>${(p.ratio * 100).toFixed(0)}%</td>
                <td>${p.grade ? `<span class="hm-chip ${p.ratio >= 0.6 ? 'hm-tag--ok' : 'hm-tag--err'}">${esc(p.grade)}</span>` : '<span class="muted">未交卷</span>'}</td>
                <td class="muted" style="font-size:12px">${fmtTime(p.created_at)}</td>
              </tr>`).join('')}</tbody>
            </table>` : '<div class="empty">还没有考试记录，去「考试模拟」出一套题吧</div>'}
          </div>
        </div>`;
      $$('tr[data-paper]').forEach((tr) => tr.addEventListener('click', () => setView('result', { paperId: tr.dataset.paper })));
    } catch (e) {
      content.innerHTML = `<div class="empty">加载失败：${esc(errText(e))}</div>`;
    }
  }

  /* ---------------------------------------------------------- 错题本 */

  async function viewWrong(content) {
    if (!isLoggedIn()) { content.innerHTML = needLogin('错题本'); const b = $('#need-login'); if (b) b.addEventListener('click', showAuthDialog); return; }
    content.innerHTML = '<div class="empty">正在加载错题…</div>';
    try {
      const data = await API.examWrong(50);
      content.innerHTML = `
        <div class="wrap--narrow">
          <div class="card">
            <div class="spread">
              <div>
                <div class="card-title">错题本（${data.total} 题）</div>
                <div class="muted" style="font-size:12px">连续答对 2 次即自动移出错题本。</div>
              </div>
              <button class="hm-btn hm-btn--primary" id="retry" ${data.total ? '' : 'disabled'}>错题重做</button>
            </div>
          </div>
          ${data.items.length ? data.items.map((q, i) => `
            <div class="card">
              <div class="q-head">
                <span class="q-no">${i + 1}.</span>
                <span class="q-stem">${rich(q.stem)}</span>
                <span class="hm-chip hm-tag--info">${esc(q.type)}</span>
                <span class="hm-chip">${LEVEL_NAME[q.level] || q.level}</span>
                <span class="hm-chip hm-tag--err">错 ${q.wrong_count} 次</span>
              </div>
              ${(q.options || []).map((o) => `<div class="opt ${q.answer.indexOf(o.key) >= 0 ? 'is-right' : ''}">
                <span class="key">${o.key}.</span><span>${rich(o.text)}</span></div>`).join('')}
              ${q.type === '填空题' ? `<div class="opt is-right"><span class="key">参考答案：</span><span>${rich((q.accepted || []).join(' 或 '))}</span></div>` : ''}
              <div class="analysis"><strong>解析：</strong>${markdown(q.analysis)}
                <div class="muted" style="font-size:12px;margin-top:6px">知识点：${(q.knowledge_points || []).map(esc).join('、')}　·　最近错于 ${fmtTime(q.last_wrong_at)}</div>
              </div>
            </div>`).join('') : '<div class="card"><div class="empty">错题本是空的，先去考一次吧</div></div>'}
        </div>`;
      const retry = $('#retry');
      if (retry) retry.addEventListener('click', async () => {
        try {
          const paper = await API.examWrongRetry({ limit: 10 });
          enterExam(paper);
        } catch (e) { toast(errText(e), 'error'); }
      });
    } catch (e) {
      content.innerHTML = `<div class="empty">加载失败：${esc(errText(e))}</div>`;
    }
  }

  /* ---------------------------------------------------------- 知识库 */

  async function viewKb(content) {
    content.innerHTML = '<div class="empty">正在加载知识库…</div>';
    try {
      const data = await API.kbDocuments();
      state.kb.docs = data.items || [];
      content.innerHTML = `
        <div class="wrap">
          <div class="card">
            <div class="card-title">知识库检索</div>
            <div class="row" style="margin-top:8px">
              <input class="hm-input" id="kb-q" style="flex:1;min-width:200px" placeholder="输入关键词，例如：拉格朗日、等价无穷小、反常积分" />
              <button class="hm-btn hm-btn--primary" id="kb-search">检索</button>
            </div>
            <div id="kb-result" style="margin-top:12px"></div>
          </div>
          <div class="card">
            <div class="card-title">章节目录（共 ${state.kb.docs.length} 章）</div>
            <div class="toc" id="kb-toc" style="margin-top:10px">
              ${state.kb.docs.map((d) => `<button data-doc="${esc(d.doc_id)}">${esc(d.name)}（${d.question_count} 题）</button>`).join('')}
            </div>
          </div>
          <div id="kb-doc"></div>
        </div>`;
      $$('#kb-toc button').forEach((b) => b.addEventListener('click', () => {
        $$('#kb-toc button').forEach((x) => x.classList.toggle('is-active', x === b));
        openDoc(b.dataset.doc);
      }));
      $('#kb-search').addEventListener('click', doKbSearch);
      $('#kb-q').addEventListener('keydown', (e) => { if (e.key === 'Enter') doKbSearch(); });
      if (state.kb.docs.length) {
        const first = $('#kb-toc button');
        first.classList.add('is-active');
        openDoc(state.kb.docs[0].doc_id);
      }
    } catch (e) {
      content.innerHTML = `<div class="empty">加载失败：${esc(errText(e))}</div>`;
    }
  }

  async function openDoc(docId) {
    state.kb.current = docId;
    const box = $('#kb-doc');
    box.innerHTML = '<div class="card"><div class="empty">加载中…</div></div>';
    try {
      const doc = await API.kbDocument(docId);
      box.innerHTML = `<div class="card">
        <div class="card-title">${esc(doc.name)}</div>
        <div class="muted" style="font-size:12px;margin-bottom:10px">${esc(doc.source_format)} · ${esc(doc.visibility)} · 第 ${doc.seq} 章</div>
        ${(doc.content_blocks || []).map((b) => b.level >= 2
          ? `<div class="kb-h2">${rich(b.text)}</div>`
          : b.level === 1
            ? `<div class="kb-h1">${rich(b.text)}</div>`
            : `<p class="kb-p">${rich(b.text)}</p>`).join('')}
      </div>`;
      box.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
    } catch (e) {
      box.innerHTML = `<div class="card"><div class="empty">加载失败：${esc(errText(e))}</div></div>`;
    }
  }

  async function doKbSearch() {
    const q = $('#kb-q').value.trim();
    const box = $('#kb-result');
    if (!q) { box.innerHTML = ''; return; }
    box.innerHTML = '<div class="muted">检索中…</div>';
    try {
      const data = await API.kbSearch(q);
      if (!data.items.length) { box.innerHTML = '<div class="muted">没有命中内容，试试更短的术语。</div>'; return; }
      box.innerHTML = data.items.map((h) => `
        <div class="analysis" style="margin-bottom:8px">
          <div class="row" style="justify-content:space-between">
            <strong>《${esc(h.doc_title)}》· ${esc(h.section)}</strong>
            <span class="hm-chip">相关度 ${h.score}</span>
          </div>
          <div style="margin-top:6px">${rich(h.text)}</div>
        </div>`).join('');
    } catch (e) { box.innerHTML = `<div class="muted">检索失败：${esc(errText(e))}</div>`; }
  }

  /* ---------------------------------------------------------- 关于 / 监控 */

  async function viewAbout(content) {
    content.innerHTML = '<div class="empty">正在加载…</div>';
    try {
      const info = await API.about();
      content.innerHTML = `
        <div class="wrap--narrow">
          <div class="card">
            <div class="card-title">关于本系统</div>
            <p class="muted">一个「题库 + 考试模拟」系统：微积分知识库支撑智能问答，题库支撑规则组卷、自动判分、错题本与成绩分析。</p>
            <table class="hm-table" style="margin-top:10px">
              <tbody>
                <tr><th>主题</th><td>${esc(info.topic)}</td></tr>
                <tr><th>版本</th><td>${esc(info.version)}</td></tr>
                <tr><th>知识库</th><td>${info.content.documents} 章节 / ${info.content.blocks} 知识块 / ${info.content.points} 个知识点</td></tr>
                <tr><th>题库</th><td>${info.content.questions} 道题（${Object.entries(info.content.by_type).map(([k, v]) => k + ' ' + v).join(' · ')}）</td></tr>
                <tr><th>难度分布</th><td>${Object.entries(info.content.by_level).map(([k, v]) => (LEVEL_NAME[k] || k) + ' ' + v).join(' · ')}</td></tr>
                <tr><th>回答模式</th><td>${esc(info.llm.label)}${info.llm.model ? '（' + esc(info.llm.model) + '）' : ''}</td></tr>
                ${info.limits ? `<tr><th>今日大模型额度</th><td>
                  ${info.limits.user_limit
                    ? `你已用 <strong>${info.limits.user_used}/${info.limits.user_limit}</strong> 次，剩余 ${info.limits.user_remaining} 次`
                    : '未限制'}
                  ${info.limits.global_limit
                    ? `<span class="muted"> · 全站已用 ${info.limits.global_used}/${info.limits.global_limit} 次</span>`
                    : ''}
                  <div class="muted" style="font-size:12px">额度用完后会自动改为给出手册原文依据，不会报错。每日 00:00（UTC）重置。</div>
                </td></tr>` : ''}
                <tr><th>许可</th><td>${esc(info.license)}</td></tr>
              </tbody>
            </table>
            ${info.llm.hint ? `<p class="muted" style="font-size:12px;margin-top:10px">🔧 ${esc(info.llm.hint)}</p>` : ''}
          </div>
          <div class="card">
            <div class="card-title">更新记录</div>
            <ul style="margin:8px 0 0;padding-left:20px">${(info.changelog || []).map((c) => `<li>${esc(c)}</li>`).join('')}</ul>
          </div>
          <div class="card">
            <div class="card-title">使用提示</div>
            <ul class="muted" style="margin:8px 0 0;padding-left:20px">
              <li>「智能问答」的回答来自知识库检索结果，每条都会标注引用章节。</li>
              <li>「考试模拟」支持单选 / 多选 / 判断 / 填空，多选题漏选得一半分，错选不得分。</li>
              <li>交卷后可在「历史成绩」回看逐题解析，错题会自动进入「错题本」。</li>
              <li>本机默认管理员账号：<code>admin / admin123</code>（部署到公网前请务必修改）。</li>
            </ul>
          </div>
        </div>`;
    } catch (e) {
      content.innerHTML = `<div class="empty">加载失败：${esc(errText(e))}</div>`;
    }
  }

  async function viewAdmin(content) {
    if (!state.user || state.user.role !== 'admin') {
      content.innerHTML = '<div class="empty">该页面仅管理员可见</div>';
      return;
    }
    content.innerHTML = '<div class="empty">正在加载运行数据…</div>';
    try {
      const m = await API.metrics();
      const audit = await API.auditLogs();
      content.innerHTML = `
        <div class="wrap">
          <div class="card">
            <div class="card-title">运行监控</div>
            <div class="grid grid-4" style="margin-top:10px">
              <div class="stat"><div class="v">${m.users}</div><div class="k">注册用户</div></div>
              <div class="stat"><div class="v">${m.sessions}</div><div class="k">问答会话</div></div>
              <div class="stat"><div class="v">${m.messages}</div><div class="k">消息条数</div></div>
              <div class="stat"><div class="v">${m.papers}</div><div class="k">试卷总数</div></div>
              <div class="stat"><div class="v">${m.submitted}</div><div class="k">已交卷</div></div>
              <div class="stat"><div class="v">${(m.avg_ratio * 100).toFixed(0)}%</div><div class="k">平均正确率</div></div>
              <div class="stat"><div class="v">${m.wrong_entries}</div><div class="k">错题条目</div></div>
              <div class="stat"><div class="v">${m.questions}</div><div class="k">题库总量</div></div>
            </div>
          </div>
          <div class="card">
            <div class="card-title">知识库与回答模式</div>
            <table class="hm-table" style="margin-top:8px"><tbody>
              <tr><th>章节 / 知识块</th><td>${m.content.documents} / ${m.content.blocks}</td></tr>
              <tr><th>知识点</th><td>${m.content.points}</td></tr>
              <tr><th>题型分布</th><td>${Object.entries(m.content.by_type).map(([k, v]) => k + ' ' + v).join(' · ')}</td></tr>
              <tr><th>回答模式</th><td>${esc(m.llm.label)}</td></tr>
            </tbody></table>
          </div>
          <div class="card">
            <div class="card-title">最近事件</div>
            ${audit.items.length ? `<table class="hm-table" style="margin-top:8px">
              <thead><tr><th style="width:170px">时间</th><th style="width:150px">类型</th><th>详情</th></tr></thead>
              <tbody>${audit.items.map((e) => `<tr>
                <td class="muted" style="font-size:12px">${fmtTime(e.created_at)}</td>
                <td><span class="hm-chip">${esc(e.kind)}</span></td>
                <td class="muted" style="font-size:12px">${esc(JSON.stringify(e.payload))}</td>
              </tr>`).join('')}</tbody></table>` : '<div class="empty">暂无事件</div>'}
          </div>
        </div>`;
    } catch (e) {
      content.innerHTML = `<div class="empty">加载失败：${esc(errText(e))}</div>`;
    }
  }

  /* ---------------------------------------------------------- 启动 */

  function init() {
    applyTheme(currentTheme());
    state.user = API.getUser() || GUEST;
    render();
    if (!isLoggedIn()) setTimeout(showAuthDialog, 250);
  }

  document.addEventListener('DOMContentLoaded', init);
})();
