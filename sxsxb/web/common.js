/* 实训室选报系统 - 前端公共脚本 */
const TOKEN_KEY = 'sxsxb_token';
const USER_KEY = 'sxsxb_user';

function getToken() { return localStorage.getItem(TOKEN_KEY); }

function getUser() {
  try { return JSON.parse(localStorage.getItem(USER_KEY) || 'null'); }
  catch (e) { return null; }
}

function saveSession(token, user) {
  localStorage.setItem(TOKEN_KEY, token);
  localStorage.setItem(USER_KEY, JSON.stringify(user));
}

function clearSession() {
  localStorage.removeItem(TOKEN_KEY);
  localStorage.removeItem(USER_KEY);
}

async function api(path, options = {}) {
  const method = options.method || 'GET';
  const body = options.body === undefined ? null : options.body;
  const auth = options.auth !== false;
  const headers = { 'Accept': 'application/json' };
  if (body !== null) headers['Content-Type'] = 'application/json; charset=utf-8';
  if (auth) {
    const t = getToken();
    if (t) headers['Authorization'] = 'Bearer ' + t;
  }
  const resp = await fetch(path, {
    method,
    headers,
    body: body === null ? undefined : JSON.stringify(body)
  });
  const text = await resp.text();
  let data = null;
  if (text) {
    try { data = JSON.parse(text); } catch (e) { data = text; }
  }
  if (!resp.ok) {
    let msg = '请求失败(' + resp.status + ')';
    if (data && data.detail) {
      msg = typeof data.detail === 'string' ? data.detail : JSON.stringify(data.detail);
    }
    if (resp.status === 401) {
      clearSession();
      if (!/index\.html$|\/$/.test(location.pathname)) location.href = 'index.html';
    }
    throw new Error(msg);
  }
  return data;
}

function esc(v) {
  if (v === null || v === undefined) return '';
  return String(v)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}

function fmtTime(v) {
  if (!v) return '-';
  const s = String(v).replace('T', ' ');
  return s.length > 16 ? s.slice(0, 16) : s;
}

function toast(msg, type) {
  let box = document.getElementById('toastBox');
  if (!box) {
    box = document.createElement('div');
    box.id = 'toastBox';
    document.body.appendChild(box);
  }
  const el = document.createElement('div');
  el.className = 'toast' + (type ? ' ' + type : '');
  el.textContent = msg;
  box.appendChild(el);
  setTimeout(() => el.remove(), 3200);
}

/* 通用弹窗：替代原生 prompt / confirm（内嵌浏览器不支持原生对话框） */
function openModal(innerHtml, withField) {
  return new Promise(resolve => {
    const mask = document.createElement('div');
    mask.className = 'modal-mask';
    mask.innerHTML = `<div class="modal-box">${innerHtml}
      <div class="modal-actions">
        <button class="btn ghost" data-act="cancel">取消</button>
        <button class="btn" data-act="ok">确定</button>
      </div></div>`;
    document.body.appendChild(mask);
    const field = mask.querySelector('#modalField');
    const close = val => {
      mask.remove();
      document.removeEventListener('keydown', onKey);
      resolve(val);
    };
    const onKey = e => {
      if (e.key === 'Escape') close(null);
      if (e.key === 'Enter' && (!withField || withField !== 'textarea')) {
        e.preventDefault();
        close(field ? field.value : true);
      }
    };
    document.addEventListener('keydown', onKey);
    mask.addEventListener('click', e => { if (e.target === mask) close(null); });
    mask.querySelector('[data-act="cancel"]').addEventListener('click', () => close(null));
    mask.querySelector('[data-act="ok"]').addEventListener('click', () => close(field ? field.value : true));
    if (field) setTimeout(() => field.focus(), 30);
  });
}

function askInput(opts) {
  const o = opts || {};
  const type = o.type || 'text';
  let field;
  if (type === 'select') {
    field = '<select class="modal-input" id="modalField">' + (o.options || []).map(opt => {
      const value = typeof opt === 'string' ? opt : opt.value;
      const label = typeof opt === 'string' ? opt : (opt.label || opt.value);
      return `<option value="${esc(value)}">${esc(label)}</option>`;
    }).join('') + '</select>';
  } else if (type === 'textarea') {
    field = `<textarea class="modal-input" id="modalField" rows="3" placeholder="${esc(o.placeholder || '')}">${esc(o.value || '')}</textarea>`;
  } else {
    field = `<input class="modal-input" id="modalField" type="text" placeholder="${esc(o.placeholder || '')}" value="${esc(o.value || '')}">`;
  }
  const html = `<h4 class="modal-title">${esc(o.title || '请输入')}</h4>` +
    (o.message ? `<div class="modal-msg">${esc(o.message)}</div>` : '') + field;
  return openModal(html, type).then(val => (val === null ? null : String(val)));
}

function askConfirm(message, title) {
  const html = `<h4 class="modal-title">${esc(title || '请确认')}</h4>` +
    `<div class="modal-msg">${esc(message || '')}</div>`;
  return openModal(html, null).then(val => val !== null);
}

function requireUser(type) {
  const user = getUser();
  if (!getToken() || !user) { location.href = 'index.html'; return null; }
  if (type && user.userType !== type) {
    location.href = user.userType === 'TEACHER' ? 'teacher.html' : 'student.html';
    return null;
  }
  return user;
}

function logout() {
  clearSession();
  location.href = 'index.html';
}

function bindTabs(onChange) {
  const buttons = document.querySelectorAll('.tab-btn');
  buttons.forEach(btn => {
    btn.addEventListener('click', () => {
      buttons.forEach(b => b.classList.remove('active'));
      document.querySelectorAll('.panel').forEach(p => p.classList.remove('active'));
      btn.classList.add('active');
      const panel = document.getElementById('panel-' + btn.dataset.tab);
      if (panel) panel.classList.add('active');
      if (onChange) onChange(btn.dataset.tab);
    });
  });
  return function open(tab) {
    const btn = document.querySelector('.tab-btn[data-tab="' + tab + '"]');
    if (btn) btn.click();
  };
}

/* ---------------- 通知中心 ---------------- */
let notifyTimer = null;

async function refreshUnread() {
  try {
    const data = await api('/api/notifications?limit=1');
    const badge = document.getElementById('unreadBadge');
    if (badge) {
      if (data.unreadCount > 0) {
        badge.textContent = data.unreadCount > 99 ? '99+' : data.unreadCount;
        badge.style.display = '';
      } else {
        badge.style.display = 'none';
      }
    }
  } catch (e) { /* 静默 */ }
}

async function openNotices() {
  const mask = document.getElementById('notifyMask');
  const drawer = document.getElementById('notifyDrawer');
  if (!mask || !drawer) return;
  mask.classList.add('open');
  drawer.classList.add('open');
  await renderNotices();
}

function closeNotices() {
  const mask = document.getElementById('notifyMask');
  const drawer = document.getElementById('notifyDrawer');
  if (mask) mask.classList.remove('open');
  if (drawer) drawer.classList.remove('open');
}

async function renderNotices() {
  const body = document.getElementById('notifyBody');
  if (!body) return;
  body.innerHTML = '<div class="empty">加载中…</div>';
  try {
    const data = await api('/api/notifications?limit=100');
    if (!data.items.length) {
      body.innerHTML = '<div class="empty">暂无通知</div>';
      return;
    }
    body.innerHTML = data.items.map(n => `
      <div class="notice ${n.read_flag ? '' : 'unread'}" data-id="${n.notice_id}">
        <div class="t">${esc(n.title)}</div>
        <div class="c">${esc(n.content)}</div>
        <div class="m">${fmtTime(n.sent_at)} ${n.read_flag ? '' : '· 未读'}</div>
      </div>`).join('');
    body.querySelectorAll('.notice.unread').forEach(el => {
      el.addEventListener('click', async () => {
        try {
          await api('/api/notifications/' + el.dataset.id + '/read', { method: 'POST' });
          el.classList.remove('unread');
          el.querySelector('.m').textContent = el.querySelector('.m').textContent.replace('· 未读', '').trim();
          refreshUnread();
        } catch (e) { toast(e.message, 'err'); }
      });
    });
  } catch (e) {
    body.innerHTML = '<div class="empty">' + esc(e.message) + '</div>';
  }
}

function injectNoticeDrawer() {
  if (document.getElementById('notifyDrawer')) return;
  const html = `
    <div class="drawer-mask" id="notifyMask"></div>
    <aside class="drawer" id="notifyDrawer">
      <header>
        <span>通知中心</span>
        <span class="spacer"></span>
        <button class="btn sm ghost" id="readAllBtn">全部已读</button>
        <button class="icon-btn" id="closeNoticeBtn" style="color:#5b6784;background:#eef1f7;margin-left:8px;">×</button>
      </header>
      <div class="body" id="notifyBody"></div>
    </aside>`;
  const holder = document.createElement('div');
  holder.innerHTML = html;
  document.body.appendChild(holder);

  document.getElementById('notifyMask').addEventListener('click', closeNotices);
  document.getElementById('closeNoticeBtn').addEventListener('click', closeNotices);
  document.getElementById('readAllBtn').addEventListener('click', async () => {
    try {
      await api('/api/notifications/read-all', { method: 'POST' });
      await renderNotices();
      refreshUnread();
      toast('已全部标记为已读', 'ok');
    } catch (e) { toast(e.message, 'err'); }
  });
}

/* 顶栏初始化：填充用户信息 + 通知入口 */
function initShell(title, roleLabel) {
  const user = getUser();
  const titleEl = document.querySelector('.brand .txt');
  if (titleEl) titleEl.textContent = title || '实训室选报系统';
  const who = document.getElementById('userBox');
  if (who && user) {
    who.innerHTML = `<span>${esc(user.name)}</span>
      <span class="role-tag">${esc(roleLabel || (user.userType === 'TEACHER' ? '教师' : '学生'))}</span>`;
  }
  const bell = document.getElementById('bellBtn');
  if (bell) {
    injectNoticeDrawer();
    bell.addEventListener('click', openNotices);
    refreshUnread();
    if (notifyTimer) clearInterval(notifyTimer);
    notifyTimer = setInterval(refreshUnread, 30000);
  }
  const logoutBtn = document.getElementById('logoutBtn');
  if (logoutBtn) logoutBtn.addEventListener('click', logout);
}
