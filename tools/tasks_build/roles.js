/* ============================================================================
 * kust.work · 任务打卡 —— 角色运行时（多角色 · 联网共享）
 * ============================================================================
 *
 * 职责：角色清单与角色数据存在服务端（见 modules/tasks/routes.py），
 *       浏览器用 cookie 记住「我上次在哪个角色」；本文件负责：
 *
 *   1. 清理上一代「纯本地多角色」遗留数据（kust.tasks.* → 一次性删掉）
 *   2. 拉取角色清单：cookie 指不到任何角色 → 弹「创建角色」命名弹窗，不启动看板
 *      · 服务端常驻一个示例角色（roles[].demo === true）供参考，
 *        **但只列在弹窗里等人自己点进去，绝不会被自动选中或写进 cookie**
 *      · 被删掉/被改乱了可在「⚙ 管理」里用「恢复示例角色」找回来
 *   3. 拉取当前角色数据 → 写进本地缓存（kust.tasks.v2.<roleId>.*）→ 再注入看板脚本
 *   4. 盯着 localStorage 的写入做防抖推送（看板脚本不用知道「有网络」这回事）
 *   5. 定时轮询远端变化，把别人的修改拉回来重绘；离线时降级为纯本地缓存
 *
 * 存储布局：
 *   服务端 modules/tasks/data/store.json
 *       { roles: [{ id, name, avatar, rev, data{nodes,checkins,badges,focus,pet}, tomb }] }
 *   本地缓存（只做加速与离线兜底，真源在服务端）
 *       kust.tasks.v2.roles              角色清单快照
 *       kust.tasks.v2.<roleId>.nodes     该角色的任务节点
 *       kust.tasks.v2.<roleId>.checkins  打卡记录
 *       kust.tasks.v2.<roleId>.badges    已解锁徽章
 *       kust.tasks.v2.<roleId>.focus     番茄专注记录
 *       kust.tasks.v2.<roleId>.pet       桌宠状态
 *       kust.tasks.v2.<roleId>.<tab|view|form_draft>   纯本机视图状态（不参与同步）
 *       kust.tasks.v2.<roleId>.syncrev   上次同步到的版本号
 *       kust.tasks.v2.<roleId>.syncids   上次同步时的 id 快照（用来算「本地删了哪些」）
 *       kust.tasks.v2.<roleId>.dirty     有未推送改动（离线时留着，恢复后自动补传）
 *
 * 注入顺序：本文件在 <head> 里同步加载，但**不立刻**注入看板脚本 ——
 *           要先知道「当前是哪个角色、这个角色是什么数据」，否则看板会先渲染一版错的。
 * ========================================================================== */
(function () {
  'use strict';

  /* ── 常量 ─────────────────────────────────────────────────────────── */
  var API = '/api/tasks';
  var NS = 'kust.tasks.v2.';            // 新版本地缓存命名空间
  var LEGACY = 'kust.tasks.';           // 旧版命名空间（纯本地）→ 启动时一次性清掉
  var K_ROLES = NS + 'roles';
  var COOKIE = 'kust_tasks_role';
  var SYNC_KEYS = ['nodes', 'checkins', 'badges', 'focus', 'pet',
                   'goals', 'goalLogs', 'goalNotes'];   // 后三个属于「学习目标」视图
  var LS_DEFAULT = { nodes: [], checkins: [], badges: {}, focus: [], pet: {},
                     goals: [], goalLogs: [], goalNotes: {} };
  var AVATARS = ['🎓', '🐱', '🐶', '🐼', '🦊', '🐯', '🐰', '🐻', '🦉', '🐧',
                 '🚀', '⚔️', '🔮', '🏆', '🌱', '🍀'];
  var POLL_MS = 15000;                  // 轮询远端变化的间隔
  var ROLES_MS = 40000;                 // 轮询角色清单（看队友有没有新建）
  var PUSH_MS = 700;                    // 本地改动防抖推送

  /* ── 运行态 ───────────────────────────────────────────────────────── */
  var roles = [];             // 服务端角色清单
  var current = null;         // 当前角色
  var rev = 0;                // 上次同步到的版本号
  var syncedIds = { nodes: [], checkins: [] };
  var online = true;
  var dirty = false;
  var pushing = false;
  var applying = false;       // 正在把远端数据写进本地缓存（屏蔽自触发推送）
  var booted = false;
  var pushTimer = null;
  var pollTimer = null;
  var rolesTimer = null;

  /* ── 基础工具 ─────────────────────────────────────────────────────── */
  function lsGet(key) { try { return localStorage.getItem(key); } catch (e) { return null; } }
  function lsSet(key, val) { try { localStorage.setItem(key, val); } catch (e) { /* 隐私模式 / 配额满 */ } }
  function lsDel(key) { try { localStorage.removeItem(key); } catch (e) { } }

  function readJSON(key, fallback) {
    var raw = lsGet(key);
    if (!raw) { return fallback; }
    try {
      var val = JSON.parse(raw);
      return val === null || val === undefined ? fallback : val;
    } catch (e) { return fallback; }
  }

  function writeJSON(key, val) { lsSet(key, JSON.stringify(val)); }

  function uid() { return 'r' + Date.now().toString(36) + Math.random().toString(36).slice(2, 7); }

  function el(tag, cls, txt) {
    var node = document.createElement(tag);
    if (cls) { node.className = cls; }
    if (txt !== undefined && txt !== null) { node.textContent = txt; }
    return node;
  }

  function roleKey(name) { return NS + (current ? current.id : '_') + '.' + name; }

  /* ── 网络 ─────────────────────────────────────────────────────────── */
  function markOnline(flag) {
    if (online === flag) { return; }
    online = flag;
    renderBar();
    if (flag && dirty) { schedulePush(0); }
    if (flag) { refreshRolesSilently(); }
  }

  function api(method, path, body) {
    var opt = { method: method, headers: {}, cache: 'no-store' };
    if (body !== undefined) {
      opt.headers['Content-Type'] = 'application/json; charset=utf-8';
      opt.body = JSON.stringify(body);
    }
    return fetch(API + path, opt).then(function (res) {
      return res.text().then(function (text) {
        var data = {};
        try { data = text ? JSON.parse(text) : {}; } catch (e) { data = {}; }
        if (!res.ok || data.ok === false) {
          var err = new Error(data.error || ('服务端返回 HTTP ' + res.status));
          err.status = res.status;
          err.code = data.code;
          throw err;
        }
        markOnline(true);
        return data;
      });
    }).catch(function (err) {
      if (!err || !err.status) { markOnline(false); }   // 只有网络层失败才算离线
      throw err;
    });
  }

  function cookieValue(name) {
    var parts = String(document.cookie || '').split(';');
    for (var i = 0; i < parts.length; i++) {
      var seg = parts[i].trim();
      if (seg.indexOf(name + '=') === 0) {
        return decodeURIComponent(seg.slice(name.length + 1));
      }
    }
    return '';
  }

  function setCookie(id) {
    if (!id) {
      // 清 cookie 也要写一次（值置空 + max-age=0），否则「退出本机」等于没点。
      // path/SameSite 必须与写入时一致，否则浏览器删的是另一条记录。
      document.cookie = COOKIE + '=;path=/;max-age=0;SameSite=Lax';
      return;
    }
    var base = COOKIE + '=' + encodeURIComponent(id) + ';path=/;max-age=31536000;SameSite=Lax';
    document.cookie = base;
  }

  /* ── 本地缓存读写 ─────────────────────────────────────────────────── */
  function collect() {
    var out = {};
    for (var i = 0; i < SYNC_KEYS.length; i++) {
      var key = SYNC_KEYS[i];
      var val = readJSON(roleKey(key), LS_DEFAULT[key]);
      if (key === 'nodes' || key === 'checkins' || key === 'focus' ||
          key === 'goals' || key === 'goalLogs') {
        out[key] = Object.prototype.toString.call(val) === '[object Array]' ? val : [];
      } else {
        out[key] = (val && typeof val === 'object' && Object.prototype.toString.call(val) !== '[object Array]') ? val : {};
      }
    }
    return out;
  }

  function idSet(data) {
    var out = { nodes: [], checkins: [] };
    var names = ['nodes', 'checkins'];
    for (var i = 0; i < names.length; i++) {
      var arr = data[names[i]] || [];
      for (var j = 0; j < arr.length; j++) {
        if (arr[j] && arr[j]._id) { out[names[i]].push(arr[j]._id); }
      }
    }
    return out;
  }

  function removedSince() {
    var now = idSet(collect());
    var gone = { nodes: [], checkins: [] };
    var names = ['nodes', 'checkins'];
    for (var i = 0; i < names.length; i++) {
      var live = now[names[i]];
      var before = syncedIds[names[i]] || [];
      for (var j = 0; j < before.length; j++) {
        if (live.indexOf(before[j]) < 0) { gone[names[i]].push(before[j]); }
      }
    }
    return gone;
  }

  function applyRemote(data) {
    if (!data) { return; }
    applying = true;
    try {
      for (var i = 0; i < SYNC_KEYS.length; i++) {
        var key = SYNC_KEYS[i];
        if (data[key] !== undefined) { writeJSON(roleKey(key), data[key]); }
      }
      syncedIds = idSet(data);
      writeJSON(roleKey('syncids'), syncedIds);
    } finally {
      applying = false;
    }
  }

  function boardRefresh() {
    var board = window.__TASKS_BOARD__;
    if (board && typeof board.reload === 'function') { board.reload(); }
  }

  /* ── 同步引擎 ─────────────────────────────────────────────────────── */
  function schedulePush(delay) {
    if (pushTimer) { clearTimeout(pushTimer); }
    pushTimer = setTimeout(function () { pushTimer = null; pushNow(); },
                           delay === undefined ? PUSH_MS : delay);
  }

  function pushNow() {
    if (!current) { return Promise.resolve(null); }
    if (pushing) { return Promise.resolve(null); }
    pushing = true;
    statusText('同步中…', 'syncing');
    var payload = {
      role: current.id,
      rev: rev,
      data: collect(),
      removed: removedSince()
    };
    lsSet(roleKey('dirty'), '1');
    return api('PUT', '/state', payload).then(function (res) {
      pushing = false;
      dirty = false;
      lsDel(roleKey('dirty'));
      rev = res.rev || 0;
      applyRemote(res.data);
      statusText('已同步', 'ok');
      if (res.merged) { boardRefresh(); }
      return res;
    }).catch(function (err) {
      pushing = false;
      statusText('离线模式', 'offline');
      throw err;
    });
  }

  function pull(apply) {
    if (!current) { return Promise.resolve(null); }
    var url = '/state?role=' + encodeURIComponent(current.id) + '&rev=' + rev;
    return api('GET', url).then(function (res) {
      if (res.changed === false) {
        statusText('已同步', 'ok');
        return null;
      }
      rev = res.rev || 0;
      writeJSON(roleKey('syncrev'), rev);
      if (apply) {
        applyRemote(res.data);
        boardRefresh();
      } else {
        syncedIds = idSet(res.data);
        writeJSON(roleKey('syncids'), syncedIds);
      }
      statusText('已同步', 'ok');
      return res;
    });
  }

  function poll() {
    if (!current || !booted) { return; }
    if (dirty && online) {
      pushNow().catch(function () { });
      return;
    }
    pull(true).catch(function () { });
  }

  function flushSync() {
    // 关页 / 切角色前尽力而为：keepalive 让浏览器在页面卸载后继续把请求送完
    if (!current || !dirty) { return; }
    try {
      fetch(API + '/state', {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json; charset=utf-8' },
        body: JSON.stringify({ role: current.id, rev: rev, data: collect(), removed: removedSince() }),
        keepalive: true
      });
    } catch (e) { /* 尽力而为 */ }
  }

  /* 拦截 localStorage 写入 —— 看板脚本照旧写本地存储，我们顺手把改动推上去。
     这样看板脚本一行都不用改（这是刻意的：上游页面改版时不需要重做同步层）。 */
  function installHooks() {
    if (installHooks.done) { return; }
    installHooks.done = true;

    var rawSet = localStorage.setItem;
    var rawDel = localStorage.removeItem;
    var prefix = NS + (current ? current.id : '_') + '.';

    localStorage.setItem = function (key, value) {
      var before = lsGet(key);
      rawSet.call(localStorage, key, value);
      if (before === String(value)) { return; }   // 值没变就别推：拦掉「渲染即回写」的无意义抖动
      onLocalWrite(key, prefix);
    };
    localStorage.removeItem = function (key) {
      var before = lsGet(key);
      rawDel.call(localStorage, key);
      if (before === null || before === undefined) { return; }
      onLocalWrite(key, prefix);
    };
  }

  function onLocalWrite(key, prefix) {
    if (applying || !booted || !current) { return; }
    if (String(key).indexOf(prefix) !== 0) { return; }
    var name = String(key).slice(prefix.length);
    if (SYNC_KEYS.indexOf(name) < 0) { return; }   // tab / view / form_draft / dirty 等不参与同步
    dirty = true;
    lsSet(roleKey('dirty'), '1');
    statusText('待同步', 'pending');
    schedulePush();
  }

  /* ── 角色切换 / 新建 / 删除 ───────────────────────────────────────── */
  function gotoRole(id) {
    setCookie(id);
    flushSync();
    if (dirty && online) {
      pushNow().then(function () { location.reload(); },
                     function () { location.reload(); });
      return;
    }
    location.reload();
  }

  function createRole(name, avatar, onError) {
    return api('POST', '/roles', { name: name, avatar: avatar }).then(function (res) {
      roles = res.roles || roles;
      current = res.role;
      setCookie(current.id);
      lsSet(NS + 'active', current.id);
      writeJSON(K_ROLES, roles);
      return res.role;
    }).catch(function (err) {
      onError(err);
      throw err;
    });
  }

  function renameRole(id, name) {
    return api('PATCH', '/roles/' + encodeURIComponent(id), { name: name });
  }

  function avatarRole(id, avatar) {
    return api('PATCH', '/roles/' + encodeURIComponent(id), { avatar: avatar });
  }

  function deleteRole(id) {
    return api('DELETE', '/roles/' + encodeURIComponent(id));
  }

  /* ── 角色清单拉取 ─────────────────────────────────────────────────── */
  function fetchRoles() {
    return api('GET', '/roles').then(function (res) {
      roles = res.roles || [];
      writeJSON(K_ROLES, roles);
      return res;
    });
  }

  function refreshRolesSilently() {
    fetchRoles().then(function (res) {
      renderBar();
      var cur = res.active;
      if (!current && cur) { renderGate(); }
      if (current && res.roles && !res.roles.some(function (r) { return r.id === current.id; })) {
        // 当前角色被队友删掉了：清 cookie 回到创建引导
        setCookie('');
        location.replace('/tasks');
      }
      return null;
    }).catch(function () { });
  }

  /* ── 界面：角色条 ─────────────────────────────────────────────────── */
  function demoRole() {
    for (var i = 0; i < roles.length; i++) {
      if (roles[i].demo) { return roles[i]; }
    }
    return null;
  }

  function statusText(text, cls) {
    var pill = document.getElementById('syncPill');
    if (!pill) { return; }
    pill.textContent = text;
    pill.className = 'sync-pill' + (cls ? ' ' + cls : '');
    pill.title = cls === 'offline'
      ? '服务端暂时连不上：改动只存在本机缓存里，恢复联网后会自动补传'
      : '角色数据已同步到 kust.work，换设备打开链接就是同一份数据';
  }


  function renderBar() {
    var bar = document.getElementById('roleBar');
    if (!bar) { return; }
    bar.innerHTML = '';

    var wrap = el('div', 'rb-in');
    wrap.appendChild(el('span', 'rb-label', '角色'));

    var list = el('div', 'rb-list');
    for (var i = 0; i < roles.length; i++) {
      (function (role) {
        var on = current && role.id === current.id;
        // 示例角色加 demo 标记：虚线弱化，跟「我的角色」拉开区分
        var chip = el('button', 'rolechip' + (on ? ' on' : '') + (role.demo ? ' demo' : ''));
        chip.type = 'button';
        chip.title = role.demo
          ? '参考样例（服务端常驻）：' + role.name + ' —— 点开看看填满的工作台长什么样，不会自动切进来'
          : (on ? '当前角色：' : '切换到「') + role.name + (on ? '' : '」');
        chip.appendChild(el('span', 'rc-av', role.avatar || AVATARS[0]));
        chip.appendChild(el('span', 'rc-nm', role.name));
        chip.addEventListener('click', function () {
          if (on) { return; }
          api('POST', '/roles/' + encodeURIComponent(role.id) + '/active')
            .catch(function () { })
            .then(function () { gotoRole(role.id); });
        });
        list.appendChild(chip);
      })(roles[i]);
    }

    var add = el('button', 'rolechip add', '＋ 新建角色');
    add.type = 'button';
    add.addEventListener('click', function () { openGate('add'); });
    list.appendChild(add);
    wrap.appendChild(list);

    var pill = el('span', 'sync-pill', '已同步');
    pill.id = 'syncPill';
    wrap.appendChild(pill);

    var mng = el('button', 'rb-mng', '⚙ 管理');
    mng.type = 'button';
    mng.addEventListener('click', openManage);
    wrap.appendChild(mng);

    bar.appendChild(wrap);
    statusText(online ? (dirty ? '待同步' : '已同步') : '离线模式',
               online ? (dirty ? 'pending' : 'ok') : 'offline');
  }

  /* ── 界面：创建角色弹窗（首次进入 / 新建角色 共用） ────────────────── */
  var gateMode = 'first';
  var gateAvatar = AVATARS[0];

  function renderAvatarPicker() {
    var box = document.getElementById('gateAvatars');
    if (!box) { return; }
    box.innerHTML = '';
    for (var i = 0; i < AVATARS.length; i++) {
      (function (av) {
        var btn = el('button', 'gate-av' + (av === gateAvatar ? ' on' : ''), av);
        btn.type = 'button';
        btn.addEventListener('click', function () { gateAvatar = av; renderAvatarPicker(); });
        box.appendChild(btn);
      })(AVATARS[i]);
    }
  }

  function renderGate() {
    var wrap = document.getElementById('gateExistingWrap');
    var box = document.getElementById('gateExisting');
    if (!wrap || !box) { return; }
    box.innerHTML = '';
    // 「新建角色」模式下不引导进入别人的角色（关掉弹窗点角色条就行），避免语义混淆
    if (gateMode !== 'first' || !roles.length) { wrap.hidden = true; return; }
    wrap.hidden = false;
    for (var i = 0; i < roles.length; i++) {
      (function (role) {
        var row = el('button', 'gate-row');
        row.type = 'button';
        row.appendChild(el('span', 'gr-av', role.avatar || AVATARS[0]));
        row.appendChild(el('span', 'gr-nm', role.name));
        if (role.demo) { row.appendChild(el('span', 'gr-tag', '示例')); }
        row.appendChild(el('span', 'gr-meta',
          (role.nodes || 0) + ' 任务 · ' + (role.checkins || 0) + ' 打卡'));
        row.appendChild(el('span', 'gr-go', role.demo ? '看样例 →' : '进入 →'));
        row.title = role.demo
          ? '服务端常驻的参考样例：点进去能看到填满的任务轴与打卡记录（不会自动选中它）'
          : '进入这个角色，本机就会记住它';
        row.addEventListener('click', function () {
          api('POST', '/roles/' + encodeURIComponent(role.id) + '/active')
            .catch(function () { })
            .then(function () { gotoRole(role.id); });
        });
        box.appendChild(row);
      })(roles[i]);
    }
  }

  function openGate(mode) {
    gateMode = mode;
    var mask = document.getElementById('gateMask');
    var title = document.getElementById('gateTitle');
    var sub = document.getElementById('gateSub');
    var err = document.getElementById('gateErr');
    var close = document.getElementById('gateClose');
    if (!mask) { return; }
    if (err) { err.textContent = ''; }
    if (mode === 'first') {
      if (title) { title.textContent = '先创建一个角色'; }
      if (sub) {
        var sample = demoRole();
        sub.textContent = '这台浏览器还没绑定角色。每个角色是一整套独立的工作台'
          + '（任务时间轴・阶段打卡・番茄钟・徽章・桌宠），数据会存在 kust.work 上，'
          + '团队内其它人也能切到你的角色查看。'
          + (sample ? '　下方「' + sample.name + '」是已经填好内容的参考样例，想看效果可以直接点进去 —— 但它不会被自动选中。' : '');
      }
      if (close) { close.hidden = true; }
    } else {
      if (title) { title.textContent = '新建角色'; }
      if (sub) { sub.textContent = '新角色是一张空的工作台：从「＋ 接新任务」开始搭你自己的一套。'; }
      if (close) { close.hidden = false; }
    }
    var name = document.getElementById('gateName');
    if (name) { name.value = ''; }
    gateAvatar = AVATARS[Math.floor(Math.random() * AVATARS.length)];
    renderAvatarPicker();
    renderGate();
    mask.classList.add('show');
    if (name) { setTimeout(function () { try { name.focus(); } catch (e) { } }, 60); }
  }

  function closeGate() {
    if (gateMode === 'first') { return; }   // 首次进入不给关：没有角色就没有数据可看
    var mask = document.getElementById('gateMask');
    if (mask) { mask.classList.remove('show'); }
  }

  function submitGate() {
    var nameEl = document.getElementById('gateName');
    var errEl = document.getElementById('gateErr');
    var btn = document.getElementById('gateCreate');
    var name = nameEl ? String(nameEl.value || '').trim() : '';
    if (!name) {
      if (errEl) { errEl.textContent = '请先给角色起个名字（1-16 个字）'; }
      if (nameEl) { try { nameEl.focus(); } catch (e) { } }
      return;
    }
    if (btn) { btn.disabled = true; btn.textContent = '创建中…'; }
    createRole(name.slice(0, 16), gateAvatar, function (err) {
      if (errEl) { errEl.textContent = err && err.message ? err.message : '创建失败，请再试一次'; }
      if (btn) { btn.disabled = false; btn.textContent = '创建并进入'; }
    }).then(function (role) {
      if (!role) { return; }
      var mask = document.getElementById('gateMask');
      if (mask) { mask.classList.remove('show'); }
      location.replace('/tasks');
    }).catch(function () { });
  }

  /* ── 界面：角色管理弹窗 ───────────────────────────────────────────── */
  function renderRows() {
    var box = document.getElementById('roleRows');
    if (!box) { return; }
    box.innerHTML = '';
    for (var i = 0; i < roles.length; i++) {
      (function (role) {
        var on = current && role.id === current.id;
        var row = el('div', 'rrow' + (on ? ' on' : ''));

        var av = el('button', 'rav', role.avatar || AVATARS[0]);
        av.type = 'button';
        av.title = '点击更换头像';
        av.addEventListener('click', function () {
          var idx = AVATARS.indexOf(role.avatar);
          var next = AVATARS[(idx + 1) % AVATARS.length];
          avatarRole(role.id, next).then(function (res) {
            role.avatar = res.role.avatar;
            if (current && current.id === role.id) { current.avatar = res.role.avatar; }
            renderRows(); renderBar();
          }).catch(function (err) { window.alert('换头像失败：' + err.message); });
        });
        row.appendChild(av);

        var nm = document.createElement('input');
        nm.className = 'rnm';
        nm.type = 'text';
        nm.value = role.name;
        nm.maxLength = 16;
        nm.setAttribute('aria-label', '角色名称');
        nm.addEventListener('change', function () {
          var next = String(nm.value || '').trim();
          if (!next || next === role.name) { nm.value = role.name; return; }
          renameRole(role.id, next).then(function (res) {
            role.name = res.role.name;
            if (current && current.id === role.id) { current.name = res.role.name; }
            renderRows(); renderBar();
          }).catch(function (err) {
            nm.value = role.name;
            window.alert('改名失败：' + err.message);
          });
        });
        row.appendChild(nm);

        if (on) { row.appendChild(el('span', 'rflag', '当前')); }
        else {
          var go = el('button', 'rbtn', '进入');
          go.type = 'button';
          go.addEventListener('click', function () {
            api('POST', '/roles/' + encodeURIComponent(role.id) + '/active')
              .catch(function () { })
              .then(function () { gotoRole(role.id); });
          });
          row.appendChild(go);
        }

        var del = el('button', 'rbtn del', '删除');
        del.type = 'button';
        del.addEventListener('click', function () {
          if (!window.confirm('删除角色「' + role.name + '」会同时删除它的全部任务与打卡数据，'
            + '所有成员都看不到它了，且不可恢复。\n\n确定删除吗？')) { return; }
          deleteRole(role.id).then(function () {
            if (current && current.id === role.id) {
              setCookie('');
              location.replace('/tasks');
              return;
            }
            roles = roles.filter(function (r) { return r.id !== role.id; });
            writeJSON(K_ROLES, roles);
            renderRows(); renderBar();
          }).catch(function (err) { window.alert('删除失败：' + err.message); });
        });
        row.appendChild(del);

        box.appendChild(row);
      })(roles[i]);
    }
  }

  function openManage() {
    var mask = document.getElementById('roleMask');
    if (!mask) { return; }
    renderRows();
    mask.classList.add('show');
  }

  function closeManage() {
    var mask = document.getElementById('roleMask');
    if (mask) { mask.classList.remove('show'); }
  }

  /* ── 备份：导出 / 导入当前角色 ────────────────────────────────────── */
  function download(name, text) {
    var blob = new Blob([text], { type: 'application/json;charset=utf-8' });
    var url = URL.createObjectURL(blob);
    var a = document.createElement('a');
    a.href = url;
    a.download = name;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    setTimeout(function () { URL.revokeObjectURL(url); }, 2000);
  }

  function exportRole() {
    var stamp = new Date();
    var pad = function (n) { return (n < 10 ? '0' : '') + n; };
    var data = collect();
    data.role = current ? { id: current.id, name: current.name, avatar: current.avatar } : null;
    data.rev = rev;
    data.exportedAt = new Date().toISOString();
    download('kust-tasks-' + (current ? current.name : 'role') + '-'
      + stamp.getFullYear() + pad(stamp.getMonth() + 1) + pad(stamp.getDate()) + '.json',
      JSON.stringify(data, null, 2));
  }

  function importRole() {
    var input = document.createElement('input');
    input.type = 'file';
    input.accept = '.json,application/json';
    input.addEventListener('change', function () {
      var file = input.files && input.files[0];
      if (!file) { return; }
      var reader = new FileReader();
      reader.onload = function () {
        var obj;
        try { obj = JSON.parse(String(reader.result)); } catch (e) { window.alert('导入失败：不是合法的 JSON 文件'); return; }
        if (!obj || typeof obj !== 'object') { window.alert('导入失败：文件内容不是对象'); return; }
        var picked = 0;
        for (var i = 0; i < SYNC_KEYS.length; i++) {
          if (obj[SYNC_KEYS[i]] !== undefined) { writeJSON(roleKey(SYNC_KEYS[i]), obj[SYNC_KEYS[i]]); picked++; }
        }
        if (!picked) { window.alert('导入失败：文件里没有可识别的数据（nodes / checkins / badges / focus / pet）'); return; }
        dirty = true;
        schedulePush(0);
        window.alert('已导入 ' + picked + ' 项数据，正在同步到服务端…');
        // 重建视图：交给看板自身的重载入口，避免这里直接碰它的内部状态
        boardRefresh();
        renderBar();
      };
      reader.readAsText(file, 'utf-8');
    });
    input.click();
  }

  /* ── 示例角色：恢复 / 重置 ────────────────────────────────────────── */
  /* 示例角色是服务端常驻的参考样例。删掉或被改乱了都能在这里找回来 ——
     恢复完**不会**自动切进去，picky 的用户不会被莫名丢进别人的样例里。 */
  function restoreDemo() {
    var sample = demoRole();
    var ask = sample
      ? '示例角色「' + sample.name + '」已经在库里。\n\n点「确定」把它重置为初始示例数据'
        + '（你在里面试改过的任务与打卡会被覆盖，其它角色不受影响）。'
      : '库里现在没有示例角色。\n\n点「确定」重新建一个填好内容的参考样例 —— '
        + '它不会自动进入，需要你自己在角色条上点它。';
    if (!window.confirm(ask)) { return; }
    api('POST', '/demo', { force: !!sample }).then(function (res) {
      roles = res.roles || roles;
      writeJSON(K_ROLES, roles);
      // 重置的正好是本机当前角色：本地缓存的 rev 已过期，整页重载最稳
      if (current && res.role && current.id === res.role.id) { location.reload(); return; }
      renderRows(); renderBar();
      window.alert(sample ? '示例角色已重置为初始内容。' : '示例角色已重建，可在角色条上点它查看。');
    }).catch(function (err) {
      window.alert('恢复示例角色失败：' + (err && err.message ? err.message : '请稍后再试'));
    });
  }

  /* ── 界面：绑定 ───────────────────────────────────────────────────── */
  function wire() {
    var gate = document.getElementById('gateMask');
    if (gate) {
      gate.addEventListener('click', function (e) { if (e.target === gate) { closeGate(); } });
      var gc = document.getElementById('gateClose');
      if (gc) { gc.addEventListener('click', closeGate); }
      var ok = document.getElementById('gateCreate');
      if (ok) { ok.addEventListener('click', submitGate); }
      var name = document.getElementById('gateName');
      if (name) {
        name.addEventListener('keydown', function (e) { if (e.key === 'Enter') { submitGate(); } });
      }
    }

    var mask = document.getElementById('roleMask');
    if (mask) {
      mask.addEventListener('click', function (e) { if (e.target === mask) { closeManage(); } });
      var close = document.getElementById('roleCloseBtn');
      if (close) { close.addEventListener('click', closeManage); }
      var add = document.getElementById('roleAddBtn');
      if (add) { add.addEventListener('click', function () { closeManage(); openGate('add'); }); }

      var warn = document.getElementById('roleWarn');
      if (warn) {
        warn.textContent = '角色数据存在 kust.work 上，团队内其它人也能切到你的角色查看；'
          + '「退出」本机只是清掉 cookie，不删数据（想彻底删除请点角色行上的「删除」）。';
      }

      var foot = document.getElementById('roleFoot');
      if (foot && !document.getElementById('roleExportBtn')) {
        var box = el('div', 'rfoot-extra');
        var exp = el('button', 'rbtn', '导出本角色备份');
        exp.id = 'roleExportBtn';
        exp.type = 'button';
        exp.addEventListener('click', exportRole);
        var imp = el('button', 'rbtn', '导入恢复');
        imp.id = 'roleImportBtn';
        imp.type = 'button';
        imp.addEventListener('click', importRole);
        var out = el('button', 'rbtn', '退出本机');
        out.id = 'roleLeaveBtn';
        out.type = 'button';
        out.title = '只清掉这台浏览器的角色 cookie，不删除服务端数据';
        out.addEventListener('click', function () {
          if (!window.confirm('「退出本机」只会清掉这台浏览器记住的角色，服务端数据不会删。\n\n继续吗？')) { return; }
          setCookie('');
          location.replace('/tasks');
        });
        var dmo = el('button', 'rbtn', '恢复示例角色');
        dmo.id = 'roleDemoBtn';
        dmo.type = 'button';
        dmo.title = '把「示例角色 · 参考」重建回来；已存在则重置为初始示例数据（不会自动进入它）';
        dmo.addEventListener('click', restoreDemo);
        box.appendChild(exp); box.appendChild(imp); box.appendChild(dmo); box.appendChild(out);
        foot.insertBefore(box, foot.firstChild);
      }
    }

    document.addEventListener('keydown', function (e) { if (e.key === 'Escape') { closeGate(); closeManage(); } });
    window.addEventListener('beforeunload', flushSync);
    document.addEventListener('visibilitychange', function () {
      if (document.visibilityState === 'hidden') { flushSync(); }
      else { poll(); refreshRolesSilently(); }
    });
  }

  /* ── 启动 ─────────────────────────────────────────────────────────── */
  function purgeLegacy() {
    var doomed = [];
    try {
      for (var i = 0; i < localStorage.length; i++) {
        var key = localStorage.key(i);
        if (key && key.indexOf(LEGACY) === 0 && key.indexOf(NS) !== 0) { doomed.push(key); }
      }
    } catch (e) { return 0; }
    for (var j = 0; j < doomed.length; j++) { lsDel(doomed[j]); }
    return doomed.length;
  }

  function injectBoard() {
    var s = document.createElement('script');
    s.src = '/api/tasks/assets/app.js';
    s.async = false;
    document.body.appendChild(s);
    /* 「学习目标」视图：样式 + 脚本。脚本自己会等当前角色就绪后再初始化。 */
    var c = document.createElement('link');
    c.rel = 'stylesheet';
    c.href = '/api/tasks/assets/goals.css';
    document.head.appendChild(c);
    var g = document.createElement('script');
    g.src = '/api/tasks/assets/goals.js';
    g.async = false;
    document.body.appendChild(g);
  }

  function startBoard() {
    if (booted) { return; }
    booted = true;
    injectBoard();
    installHooks();
    if (pollTimer) { clearInterval(pollTimer); }
    pollTimer = setInterval(poll, POLL_MS);
    if (rolesTimer) { clearInterval(rolesTimer); }
    rolesTimer = setInterval(refreshRolesSilently, ROLES_MS);
  }

  function bootstrap() {
    wire();
    purgeLegacy();
    renderBar();

    // 先用本地缓存的清单把角色条画出来，别让首屏空着
    var cached = readJSON(K_ROLES, null);
    if (Object.prototype.toString.call(cached) === '[object Array]') { roles = cached; }

    fetchRoles().then(function (res) {
      // 在线时**只认服务端按 cookie 解析出的 active**：
      // 再叠一层本地缓存会把「退出本机」抵消掉（cookie 清了、缓存还在 → 又自动进回去）
      var activeId = res.active || '';
      var found = null;
      for (var i = 0; i < roles.length; i++) { if (roles[i].id === activeId) { found = roles[i]; } }
      if (!found) {
        // cookie 空 / 指向已删除的角色 → 弹创建角色命名弹窗，看板不启动。
        // 注意：示例角色（roles[].demo）**不算**「找到了角色」—— 参考样例必须用户自己点进去
        renderBar();
        openGate('first');
        return null;
      }
      current = found;
      document.title = current.name + ' · 任务打卡 · kust.work';
      renderBar();
      return bootstrapRole();
    }).catch(function () {
      // 服务端连不上：退回「本机缓存 + cookie」自救，能看能改，恢复后自动补传
      var activeId = cookieValue(COOKIE) || lsGet(NS + 'active') || '';
      var found = null;
      for (var i = 0; i < roles.length; i++) { if (roles[i].id === activeId) { found = roles[i]; } }
      if (!found) {
        // 兜底也**不能**顺手拿示例角色顶上：它是参考样例，不是「我的角色」。
        // 只在用户自己建过的角色里挑一个，挑不到就老实弹创建引导。
        for (var k = 0; k < roles.length; k++) {
          if (!roles[k].demo) { found = roles[k]; break; }
        }
      }
      online = false;
      if (!found) {
        renderBar();
        openGate('first');
        var err = document.getElementById('gateErr');
        if (err) { err.textContent = '服务端暂时连不上，无法创建角色（这一步得联网才行）'; }
        return null;
      }
      current = found;
      renderBar();
      statusText('离线模式', 'offline');
      startBoard();
      return null;
    });
  }

  function bootstrapRole() {
    // 本机没有 syncrev = 这个角色从没在这台机器上同步过 → 用 -1 强制服务端回全量。
    // 不能图省事默认 0：服务端 rev=0 但已有内容（例如示例角色）时会被 changed:false 挡掉，
    // 结果就是「拉到了空的一版」—— 看板全空、还以为数据丢了。
    var cachedRev = readJSON(roleKey('syncrev'), null);
    rev = cachedRev === null ? -1 : (Number(cachedRev) || 0);
    syncedIds = readJSON(roleKey('syncids'), { nodes: [], checkins: [] });
    if (!syncedIds || !syncedIds.nodes || !syncedIds.checkins) {
      syncedIds = { nodes: [], checkins: [] };
    }
    var hadDirty = lsGet(roleKey('dirty')) === '1';

    var chain = hadDirty
      ? pushNow().catch(function () { return null; })
      : Promise.resolve(null);

    return chain.then(function () {
      return pull(true).catch(function () { return null; });   // 拉不到就用本地缓存先渲染
    }).then(function () {
      writeJSON(roleKey('syncrev'), rev);
      renderBar();
      startBoard();
      return null;
    });
  }

  window.__TASKS_ROLES__ = {
    key: function (name) { return roleKey(name); },
    prefix: function () { return NS; },
    list: function () { return roles.slice(); },
    current: function () { return current; },
    avatarPool: function () { return AVATARS.slice(); },
    online: function () { return online; },
    rev: function () { return rev; }
  };

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', bootstrap);
  } else {
    bootstrap();
  }
})();
