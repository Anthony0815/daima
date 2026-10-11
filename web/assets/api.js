/* API 客户端与 SSE 消费：令牌管理、错误码映射、stage/delta/citation/error/done 五类帧解析。 */

const API = (() => {
  const TOKEN_KEY = 'calc_token';
  const USER_KEY = 'calc_user';
  const LOCAL_SESSIONS = 'calc_local_sessions';
  const LOCAL_PAPERS = 'calc_local_papers';

  const ERROR_TEXT = {
    AUTH_INVALID_CREDENTIAL: '账号或密码错误',
    AUTH_REQUIRED: '请先登录',
    AUTH_TOKEN_EXPIRED: '登录已过期，请重新登录',
    AUTH_USERNAME_INVALID: '用户名不合法（3-32字符，仅含字母数字下划线和中文）',
    AUTH_USERNAME_TAKEN: '用户名已被占用',
    AUTH_PASSWORD_WEAK: '密码强度不足（至少6位且含字母和数字）',
    AUTH_ACCOUNT_DISABLED: '账号已被禁用',
    FORBIDDEN_NOT_ADMIN: '该功能仅管理员可用',
    FORBIDDEN_NOT_OWNER: '无权访问他人数据',
    SESSION_NOT_FOUND: '记录不存在',
    SESSION_NOT_OWNED: '无权访问该记录',
    QA_QUESTION_TOO_LONG: '问题过长，请精简后重试',
    QA_NO_RELEVANT_CONTENT: '知识库中未找到相关内容，请尝试其他问法',
    QA_OUT_OF_SCOPE: '该问题超出微积分题库范围',
    LLM_UNAVAILABLE_FALLBACK: '智能回答暂时不可用，以下为知识库原文组织结果',
    EXAM_RULE_INVALID: '组卷规则不合法，请检查题量与题型分布',
    EXAM_INSUFFICIENT_QUESTIONS: '当前可生成题目不足，请放宽抽题范围或减少题量',
    EXAM_TIME_UP: '考试时间已到，已自动交卷',
    PAPER_ALREADY_SUBMITTED: '该试卷已提交，如需重考请重新组卷',
    PAPER_NOT_IN_PROGRESS: '试卷当前不可作答',
    SYS_INTERNAL_ERROR: '服务内部错误，请稍后重试',
    SYS_RATE_LIMITED: '操作过于频繁，请稍后再试',
    SYS_OVERLOADED: '当前访问人数过多，请稍后重试',
    SYS_BODY_TOO_LARGE: '请求内容过大',
    LLM_QUOTA_EXCEEDED_USER: '你今天的额度已用完，已改为给出手册原文依据',
    LLM_QUOTA_EXCEEDED_GLOBAL: '本站今日额度已达上限，已自动切回本地检索模式',
    NOT_FOUND: '接口不存在'
  };

  const getToken = () => localStorage.getItem(TOKEN_KEY) || '';
  const getUser = () => {
    try { return JSON.parse(localStorage.getItem(USER_KEY) || 'null'); } catch (e) { return null; }
  };
  const setSession = (token, user) => {
    localStorage.setItem(TOKEN_KEY, token);
    localStorage.setItem(USER_KEY, JSON.stringify(user));
  };
  const clearSession = () => {
    localStorage.removeItem(TOKEN_KEY);
    localStorage.removeItem(USER_KEY);
  };

  const describe = (code, fallback) => ERROR_TEXT[code] || fallback || '请求失败';

  class ApiError extends Error {
    constructor(code, message, status) {
      super(message || describe(code));
      this.code = code;
      this.status = status;
    }
  }

  async function request(method, path, body) {
    const headers = { 'X-Request-Id': Math.random().toString(36).slice(2) };
    const token = getToken();
    if (token) headers.Authorization = 'Bearer ' + token;
    let payload;
    if (body !== undefined && body !== null) {
      headers['Content-Type'] = 'application/json';
      payload = JSON.stringify(body);
    }
    const res = await fetch(path, { method, headers, body: payload });
    let data = null;
    try { data = await res.json(); } catch (e) { data = null; }
    if (!res.ok) {
      if (res.status === 401) clearSession();
      const code = (data && data.code) || 'SYS_INTERNAL_ERROR';
      throw new ApiError(code, (data && data.message) || describe(code), res.status);
    }
    return data || {};
  }

  const get = (p) => request('GET', p);
  const post = (p, b) => request('POST', p, b);
  const put = (p, b) => request('PUT', p, b);
  const del = (p) => request('DELETE', p);

  /* SSE 流式消费：把响应体解析成帧对象序列 */
  async function* streamAsk(sessionId, question, signal, deepThink = false, workflow = 'auto') {
    const res = await fetch(`/api/v1/qa/sessions/${sessionId}/ask`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        Accept: 'text/event-stream',
        Authorization: 'Bearer ' + getToken()
      },
      body: JSON.stringify({ question, deep_think: deepThink, workflow }),
      signal
    });
    if (!res.ok) {
      let data = null;
      try { data = await res.json(); } catch (e) { data = null; }
      const code = (data && data.code) || 'SYS_INTERNAL_ERROR';
      throw new ApiError(code, (data && data.message) || describe(code), res.status);
    }
    const reader = res.body.getReader();
    const decoder = new TextDecoder('utf-8');
    let buffer = '';
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      let index;
      while ((index = buffer.indexOf('\n\n')) >= 0) {
        const raw = buffer.slice(0, index);
        buffer = buffer.slice(index + 2);
        const event = { event: 'message', data: null };
        raw.split('\n').forEach((line) => {
          if (line.startsWith('event:')) event.event = line.slice(6).trim();
          else if (line.startsWith('data:')) {
            try { event.data = JSON.parse(line.slice(5).trim()); } catch (e) { event.data = null; }
          }
        });
        if (event.data !== null) yield event;
      }
    }
  }

  /* 匿名期间的本地会话与试卷，登录后可通过 migrate 接口导入 */
  const local = {
    sessions() {
      try { return JSON.parse(localStorage.getItem(LOCAL_SESSIONS) || '[]'); } catch (e) { return []; }
    },
    saveSessions(list) { localStorage.setItem(LOCAL_SESSIONS, JSON.stringify(list.slice(0, 50))); },
    papers() {
      try { return JSON.parse(localStorage.getItem(LOCAL_PAPERS) || '[]'); } catch (e) { return []; }
    },
    savePapers(list) { localStorage.setItem(LOCAL_PAPERS, JSON.stringify(list.slice(0, 50))); }
  };

  return {
    getToken, getUser, setSession, clearSession, describe, ApiError, local,
    get, post, put, del, request, streamAsk,

    about: () => get('/api/v1/about'),
    usage: () => get('/api/v1/usage'),
    health: () => get('/api/v1/health'),
    register: (b) => post('/api/v1/auth/register', b),
    login: (b) => post('/api/v1/auth/login', b),
    logout: () => post('/api/v1/auth/logout'),
    getProfile: () => get('/api/v1/auth/profile'),
    updateProfile: (b) => put('/api/v1/auth/profile', b),
    changePassword: (b) => post('/api/v1/auth/password', b),

    kbDocuments: () => get('/api/v1/kb/documents'),
    kbDocument: (id) => get(`/api/v1/kb/documents/${id}`),
    kbSearch: (q) => get(`/api/v1/kb/search?q=${encodeURIComponent(q || '')}`),
    kbPoints: () => get('/api/v1/kb/points'),
    kbStats: () => get('/api/v1/kb/stats'),

    qaCreate: () => post('/api/v1/qa/sessions', {}),
    qaSessions: () => get('/api/v1/qa/sessions'),
    qaMessages: (id) => get(`/api/v1/qa/sessions/${id}/messages`),
    qaDelete: (id) => del(`/api/v1/qa/sessions/${id}`),
    qaMigrate: (b) => post('/api/v1/qa/local-migrate', b),

    examMetadata: () => get('/api/v1/exam/metadata'),
    examCreate: (b) => post('/api/v1/exam/papers', b),
    examPaper: (id) => get(`/api/v1/exam/papers/${id}`),
    examDrafts: (id, b) => put(`/api/v1/exam/papers/${id}/drafts`, b),
    examSubmit: (id, b) => post(`/api/v1/exam/papers/${id}/submit`, b),
    examReport: (id) => get(`/api/v1/exam/papers/${id}/report`),
    examHistory: (p, s) => get(`/api/v1/exam/papers?page=${p || 1}&size=${s || 20}`),
    examWrong: (limit) => get(`/api/v1/exam/wrong-questions?limit=${limit || 50}`),
    examWrongRetry: (b) => post('/api/v1/exam/wrong-questions/retry', b || {}),
    examAnalysis: (qid) => get(`/api/v1/exam/questions/${qid}/analysis`),
    examMigrate: (b) => post('/api/v1/exam/papers/migrate', b),

    adminStats: () => get('/api/v1/admin/stats'),
    metrics: () => get('/api/v1/system/metrics'),
    auditLogs: () => get('/api/v1/system/audit-logs?size=12')
  };
})();
