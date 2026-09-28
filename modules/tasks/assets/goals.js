/* ==========================================================================
   学习目标 —— 集成进 kust.work「任务打卡 / RPG 任务平台」的第五个视图
   ==========================================================================
   定位：管理「有终点、有总量」的学习目标（背完 2000 个单词 / 读完一本 440 页的书），
         不是无限期习惯打卡。

   数据落在角色命名空间下的三个键里，由 roles.js 的同步引擎自动推送 / 拉回服务端：
       kust.tasks.v2.<roleId>.goals       目标定义（数组）
       kust.tasks.v2.<roleId>.goalLogs    每日打卡记录（数组）
       kust.tasks.v2.<roleId>.goalNotes   每周复盘（对象，键=周一日期）

   删除采用「软删」（removed:1）而不是从数组里摘掉：同步引擎按 _id 求并集，
   真删会被另一端的旧快照复活，软删两端都能收敛。

   本文件是手写源，放在 tools/tasks_build/ 下，由 build_module.py 复制到 assets/。
   ========================================================================== */
(function () {
  'use strict';

  var ROLES = window.__TASKS_ROLES__;
  if (!ROLES) { return; }

  /* ── 常量 ───────────────────────────────────────────────────────────── */
  var K = { goals: 'goals', logs: 'goalLogs', notes: 'goalNotes' };
  var COLORS = ['#4a9e6a', '#f6a201', '#2fd3a4', '#5b8ff9', '#ff5c8a', '#63bb87', '#f0a53c', '#8b5cf6'];
  var SAMPLE_IDS = ['npg-s1', 'npg-s2', 'npg-s3'];
  var SEEDED_KEY = 'goalsSeeded';
  var WEEKDAYS = ['一', '二', '三', '四', '五', '六', '日'];

  /* ── 图标（全部内联 SVG，不用 emoji） ─────────────────────────────────── */
  var ICON = {
    check: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.6" stroke-linecap="round" stroke-linejoin="round"><path d="M20 6 9 17l-5-5"/></svg>',
    flame: '<svg viewBox="0 0 24 24" fill="currentColor"><path d="M13.5.67s.74 2.65.74 4.8c0 2.06-1.35 3.73-3.41 3.73-2.07 0-3.63-1.67-3.63-3.73l.03-.36C5.21 7.51 4 10.62 4 14c0 4.42 3.58 8 8 8s8-3.58 8-8C20 8.61 17.41 3.8 13.5.67zM11.71 19c-1.78 0-3.22-1.4-3.22-3.14 0-1.62 1.05-2.76 2.81-3.12 1.77-.36 3.6-1.21 4.62-2.58.39 1.29.59 2.65.59 4.04 0 2.65-2.15 4.8-4.8 4.8z"/></svg>',
    calendar: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><rect x="3" y="4" width="18" height="18" rx="3"/><path d="M16 2v4M8 2v4M3 10h18"/></svg>',
    trend: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="m22 7-8.5 8.5-5-5L2 17"/><path d="M16 7h6v6"/></svg>',
    target: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="9"/><circle cx="12" cy="12" r="4.5"/><circle cx="12" cy="12" r="1.2" fill="currentColor"/></svg>',
    clock: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 3"/></svg>',
    flag: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M4 22V4c4-2 8 2 12 0v10c-4 2-8-2-12 0"/></svg>',
    alert: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.1" stroke-linecap="round"><path d="M12 9v4"/><path d="M12 17h.01"/><circle cx="12" cy="12" r="9"/></svg>',
    plus: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round"><path d="M12 5v14M5 12h14"/></svg>',
    edit: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M17 3a2.8 2.8 0 1 1 4 4L7.5 20.5 2 22l1.5-5.5Z"/></svg>',
    trash: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 6h18"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6"/><path d="M8 6V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/></svg>',
    refresh: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 12a9 9 0 1 1-3-6.7"/><path d="M21 3v6h-6"/></svg>',
    weight: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 4v16M4 12h16"/></svg>',
    chevL: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"><path d="m15 5-7 7 7 7"/></svg>',
    chevR: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"><path d="m9 5 7 7-7 7"/></svg>',
    up: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 19V6"/><path d="m5 13 7-7 7 7"/></svg>',
    down: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 5v13"/><path d="m19 11-7 7-7-7"/></svg>',
    minus: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round"><path d="M5 12h14"/></svg>',
    layout: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="4" width="18" height="16" rx="3"/><path d="M12 4v16"/></svg>',
    sliders: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M4 6h10M18 6h2M4 12h4M12 12h8M4 18h12M20 18h0"/><circle cx="16" cy="6" r="2"/><circle cx="10" cy="12" r="2"/><circle cx="18" cy="18" r="2"/></svg>'
  };

  /* ── 工具 ───────────────────────────────────────────────────────────── */
  function $(id) { return document.getElementById(id); }
  function esc(s) {
    return String(s === null || s === undefined ? '' : s)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
  }
  function pad(n) { return (n < 10 ? '0' : '') + n; }
  function fmtDate(d) { return d.getFullYear() + '-' + pad(d.getMonth() + 1) + '-' + pad(d.getDate()); }
  function parseDate(s) { var p = String(s).split('-'); return new Date(+p[0], +p[1] - 1, +p[2]); }
  function todayStr() { return fmtDate(new Date()); }
  function addDays(s, n) { var d = parseDate(s); d.setDate(d.getDate() + n); return fmtDate(d); }
  function diffDays(a, b) { return Math.round((parseDate(b) - parseDate(a)) / 86400000); }
  function fmtShort(s) { var p = String(s).split('-'); return (+p[1]) + '/' + (+p[2]); }
  function uid() { return 'npg-' + Date.now().toString(36) + '-' + Math.random().toString(36).slice(2, 6); }
  function trimNum(n) {
    if (Math.abs(n - Math.round(n)) < 1e-9) { return String(Math.round(n)); }
    return (Math.round(n * 10) / 10).toString();
  }
  function isDate(s) { return typeof s === 'string' && /^\d{4}-\d{2}-\d{2}$/.test(s); }
  function fin(v, lo, hi) { return typeof v === 'number' && isFinite(v) && v >= lo && v <= hi; }
  function txt(v, max) { return typeof v === 'string' ? v.trim().slice(0, max) : ''; }
  function mondayOf(s) { var d = parseDate(s); d.setDate(d.getDate() - ((d.getDay() + 6) % 7)); return fmtDate(d); }
  /* 周几索引，0 = 周一（与 WEEKDAYS 对齐，也正好是打卡方块的行号） */
  function wdOf(s) { return (parseDate(s).getDay() + 6) % 7; }

  var toastTimer = null;
  function toast(msg) {
    var el = $('npgToast');
    if (!el) { return; }
    el.textContent = msg;
    el.className = 'npg-toast on';
    clearTimeout(toastTimer);
    toastTimer = setTimeout(function () { el.className = 'npg-toast'; }, 2200);
  }

  /* ── 存储：读写角色命名空间下的三个键 ─────────────────────────────────── */
  function readKey(name, fallback) {
    try {
      var raw = localStorage.getItem(ROLES.key(name));
      if (!raw) { return fallback; }
      var v = JSON.parse(raw);
      return v === null || v === undefined ? fallback : v;
    } catch (e) { return fallback; }
  }
  function writeKey(name, value) {
    try { localStorage.setItem(ROLES.key(name), JSON.stringify(value)); return true; }
    catch (e) { toast('本机存储写入失败，请检查浏览器存储空间'); return false; }
  }

  var st = { goals: [], logs: [], notes: {} };
  var tab = 'home';            /* 默认落在首页仪表盘；卡片布局可自己调 */
  var weekOff = 0;
  var expanded = {};
  var editingId = null;
  var pickedColor = COLORS[0];
  var lastFocus = null;
  var heatGoal = 'all';        /* 打卡方块当前筛选：'all' 或某个目标 id（只作用于看板那张大图） */
  var HEAT_WEEKS = 53;         /* 年视图 53 列 ≈ 一年（看板与首页两处同口径） */
  var HEAT_OP = [0, 0.34, 0.55, 0.76, 1];   /* level → fill-opacity，不另建一套色板 */
  var heatMode = 'year';       /* 打卡方块视图：'year'（53 周）| 'month'（单月，7 列大格） */
  var heatMonth = '';          /* 月视图锚点 'YYYY-MM'；留空 = 今天所在月 */
  var heatPinned = false;      /* 用户手动切过视图就不再按屏幕宽度自动挑 —— 人的选择高于猜测 */

  /* ── 首页「工作仪表盘」：组件注册表 + 布局 ─────────────────────────────────
     每个组件是一个**叶子渲染函数**：自己把 HTML 写进自己的 host，彼此不互相调用，
     由 renderDash() 按布局顺序统一调度 —— 与其余渲染一样保持调用链单向（DAG）。

     布局存本机、不跟角色同步：屏幕不一样，「半宽」在手机上本来就会退化成整宽，
     各设备各摆各的更合理；在手机上收起的卡片也不该连带把电脑上的那张收掉。
     新增组件只要往 DASH_WIDGETS 里加一行 —— 老用户的布局会自动把它接在末尾。 */
  var DASH_KEY = 'kust.tasks.goals.dash';
  var DASH_WIDGETS = [
    { id: 'stats', name: '数据概览', wide: 1, on: 1, render: wStats },
    { id: 'today', name: '今天要处理', wide: 1, on: 1, render: wToday },
    { id: 'goals', name: '目标进度', wide: 0, on: 1, render: wGoals },
    { id: 'chart', name: '近 14 天投入', wide: 0, on: 1, render: wChart },
    { id: 'heat', name: '打卡方块', wide: 1, on: 1, render: wHeat },
    { id: 'weekly', name: '本周概览', wide: 0, on: 1, render: wWeekly },
    { id: 'recent', name: '最近打卡', wide: 0, on: 1, render: wRecent },
    { id: 'guide', name: '规则速查', wide: 0, on: 0, render: wGuide }
  ];
  /* 顺序与宽度是搭配着定的：半宽卡必须**成对相邻**，否则自动流会在它右边留半格空白
     （整宽卡不能塞进剩下的半列）。goals+chart 一对、weekly+recent 一对。 */
  var dash = { order: [], hidden: {}, wide: {} };
  var dashEdit = false;        /* 编辑态：露工具条、屏蔽卡内交互（免得编辑时误点打卡） */

  /* 两种视图的排版参数。min 是「还能看清」的下限，max 是「再大就散了」的上限：
     容器比上限还宽时整张图居中（不硬撑），比下限还窄时才允许横向滚动。 */
  var HEAT_FIT = {
    /* maxGap 只给年视图：格子封顶后把余量还给间隙，图照样铺满，又不会变成几块巨大色块 */
    year: { padL: 18, padT: 15, gap: 3, maxGap: 8, min: 9, max: 17, cols: 53, rows: 7 },
    month: { padL: 18, padT: 6, gap: 4, min: 24, max: 46, cols: 7, rows: 6 }
  };

  function alive(arr) {
    var out = [];
    for (var i = 0; i < arr.length; i++) { if (arr[i] && !arr[i].removed) { out.push(arr[i]); } }
    return out;
  }
  function allRows(arr) { return Object.prototype.toString.call(arr) === '[object Array]' ? arr : []; }

  function load() {
    st.goals = alive(allRows(readKey(K.goals, [])));
    st.logs = alive(allRows(readKey(K.logs, [])));
    var n = readKey(K.notes, {});
    st.notes = (n && typeof n === 'object' && Object.prototype.toString.call(n) !== '[object Array]') ? n : {};
  }
  function persist() {
    // 写回的是「含软删行」的完整数组，否则被摘掉的行会在下一次并集里复活
    var rawGoals = allRows(readKey(K.goals, [])).filter(function (g) { return g && g._id && g.removed !== 1; });
    var rawLogs = allRows(readKey(K.logs, [])).filter(function (l) { return l && l._id && l.removed !== 1; });
    var merged = {};
    for (var i = 0; i < rawGoals.length; i++) { merged[rawGoals[i]._id] = true; }
    for (var j = 0; j < st.goals.length; j++) { if (!merged[st.goals[j]._id]) { rawGoals.push(st.goals[j]); } }

    var m2 = {};
    for (i = 0; i < rawLogs.length; i++) { m2[rawLogs[i]._id] = true; }
    for (j = 0; j < st.logs.length; j++) { if (!m2[st.logs[j]._id]) { rawLogs.push(st.logs[j]); } }

    writeKey(K.goals, rawGoals);
    writeKey(K.logs, rawLogs);
    writeKey(K.notes, st.notes);
  }

  function markRemoved(kind, id) {
    var key = kind === 'goals' ? K.goals : K.logs;
    var rows = allRows(readKey(key, []));
    var hit = false;
    for (var i = 0; i < rows.length; i++) {
      if (rows[i] && rows[i]._id === id) { rows[i].removed = 1; hit = true; }
    }
    if (!hit) { rows.push({ _id: id, removed: 1 }); }
    writeKey(key, rows);
  }

  /* ── 计算 ───────────────────────────────────────────────────────────── */
  function goalById(id) {
    for (var i = 0; i < st.goals.length; i++) { if (st.goals[i]._id === id) { return st.goals[i]; } }
    return null;
  }
  function isSample(g) { return SAMPLE_IDS.indexOf(g._id) >= 0; }
  function doneAmount(id) {
    var s = 0;
    for (var i = 0; i < st.logs.length; i++) { if (st.logs[i].goalId === id) { s += Number(st.logs[i].amount) || 0; } }
    return s;
  }
  function logsOnDate(id, date) {
    var s = 0;
    for (var i = 0; i < st.logs.length; i++) {
      if (st.logs[i].goalId === id && st.logs[i].date === date) { s += Number(st.logs[i].amount) || 0; }
    }
    return s;
  }
  function minutesOnDate(id, date) {
    var s = 0;
    for (var i = 0; i < st.logs.length; i++) {
      if (st.logs[i].goalId === id && st.logs[i].date === date && st.logs[i].minutes) { s += Number(st.logs[i].minutes); }
    }
    return s;
  }
  function hasLog(id, date) {
    for (var i = 0; i < st.logs.length; i++) { if (st.logs[i].goalId === id && st.logs[i].date === date) { return true; } }
    return false;
  }
  function datesOf(id) {
    var o = {};
    for (var i = 0; i < st.logs.length; i++) { if (st.logs[i].goalId === id) { o[st.logs[i].date] = 1; } }
    return o;
  }
  /* 休息日：该周周一（不早于创建日）到 d-1 全部有记录 → d 是本周第一次漏打，自动当休息日 */
  function isRestDay(id, d, dates) {
    var g = goalById(id);
    if (!g) { return false; }
    var monday = mondayOf(d);
    var cur = (monday < g.createdAt) ? g.createdAt : monday;
    while (cur < d) {
      if (!dates[cur]) { return false; }
      cur = addDays(cur, 1);
    }
    return true;
  }
  function streak(id) {
    var g = goalById(id);
    if (!g) { return 0; }
    var dates = datesOf(id);
    var d = todayStr();
    if (!dates[d]) { d = addDays(d, -1); }          /* 今天还没打不算断 */
    var s = 0;
    while (d >= g.createdAt) {
      if (dates[d]) { s++; d = addDays(d, -1); continue; }
      if (isRestDay(id, d, dates)) { d = addDays(d, -1); continue; }
      break;
    }
    return s;
  }
  function last7Sum(id) {
    var t = todayStr(), from = addDays(t, -6), s = 0;
    for (var i = 0; i < st.logs.length; i++) {
      var l = st.logs[i];
      if (l.goalId === id && l.date >= from && l.date <= t) { s += Number(l.amount) || 0; }
    }
    return s;
  }
  /* 预计完成日：按近 7 天平均速度推算；样本不足 → 返回 unknown，不编数字 */
  function eta(g) {
    var done = doneAmount(g._id), remain = Number(g.total) - done;
    if (remain <= 0) { return { status: 'done' }; }
    var speed = last7Sum(g._id) / 7;
    if (!(speed > 0)) { return { status: 'unknown' }; }
    var days = Math.ceil(remain / speed);
    var date = addDays(todayStr(), days);
    return { status: 'ok', date: date, diff: diffDays(date, g.deadline) };
  }
  function dailySuggest(g) {
    var remain = Number(g.total) - doneAmount(g._id);
    if (remain <= 0) { return 0; }
    var left = diffDays(todayStr(), g.deadline);
    if (left < 0) { return remain; }
    return Math.ceil(remain / Math.max(1, left + 1) * 10) / 10;
  }
  function isComplete(g) { return doneAmount(g._id) >= Number(g.total); }
  function isOverdue(g) { return !isComplete(g) && diffDays(todayStr(), g.deadline) < 0; }
  function missedYesterday(g) {
    if (isComplete(g) || hasLog(g._id, todayStr())) { return false; }
    if (g.createdAt > addDays(todayStr(), -1)) { return false; }
    return !hasLog(g._id, addDays(todayStr(), -1));
  }
  function weekStats(start, end) {
    var per = {}, totalMin = 0;
    for (var i = 0; i < st.goals.length; i++) {
      var g = st.goals[i], sum = 0, mins = 0, days = {};
      for (var j = 0; j < st.logs.length; j++) {
        var l = st.logs[j];
        if (l.goalId === g._id && l.date >= start && l.date <= end) {
          sum += Number(l.amount) || 0; days[l.date] = 1;
          if (l.minutes) { mins += Number(l.minutes); }
        }
      }
      per[g._id] = { sum: sum, days: Object.keys(days).length, mins: mins };
      totalMin += mins;
    }
    return { per: per, totalMin: totalMin };
  }

  /* ── 示例数据（只在「本角色第一次打开且**确认线上没有数据**」时生成） ─────
     ★ 判断「线上有没有」靠两件事，缺一不可：
       ① 调用方必须**先 load() 再 seedIfEmpty()** —— st.goals 是缓存，不先读就是空的；
       ② 在线时必须已经跟服务端**对过一次**（roles.js 会写 syncrev）。
     历史事故：原先 boot() 里 seedIfEmpty() 排在 load() 前面，于是它永远认为
     「本地没数据」，在一台**全新设备**上照样播种，随后带着服务端当刻的 rev 整份推上去
     （rev 相同 = 服务端直接采纳），把另一台设备刚写进去的记录顶掉了
     —— 实测服务端 72 条被抹成 71 条。所以这两条都是硬约束，不要调换顺序。 */
  function seedIfEmpty() {
    if (readKey(SEEDED_KEY, null) === 1) { return false; }
    if (st.goals.length > 0) { writeKey(SEEDED_KEY, 1); return false; }
    if (!ROLES.online()) { return false; }        /* 离线时先不生成，避免和服务端数据打架 */
    if (readKey('syncrev', null) === null) { return false; }   /* 还没跟服务端对过 → 「本地空」只代表「还没拉到」 */

    var t = todayStr(), goals = [], logs = [], yMon = mondayOf(addDays(t, -1));
    var yDow = diffDays(yMon, addDays(t, -1));
    var priorEnd = diffDays(t, addDays(yMon, -1));

    function push(id, d, a, m, mk) {
      var o = { _id: 'npg-s-' + id + '-' + addDays(t, d), goalId: id, date: addDays(t, d), amount: a, minutes: m || null, ts: Date.now() + d * 86400000 };
      if (mk) { o.makeup = true; }
      logs.push(o);
    }

    goals.push({ _id: SAMPLE_IDS[0], name: '背英语单词', unit: '个', total: 2000, deadline: addDays(t, 45), color: COLORS[0], createdAt: addDays(t, -30), obstacle: '今天状态差，一个都不想背', plan: '先只背 10 个，打开就算赢' });
    for (var i = -30; i <= -1; i++) {
      if (i === -9) { continue; }
      push(SAMPLE_IDS[0], i, 45 + Math.abs(i) * 7 % 20, 30 + Math.abs(i) * 13 % 15, i === -3);
    }

    goals.push({ _id: SAMPLE_IDS[1], name: '读完《人类简史》', unit: '页', total: 440, deadline: addDays(t, 25), color: COLORS[1], createdAt: addDays(t, -32), obstacle: '睡前一躺下就想刷手机', plan: '把书放在枕头上，先读 2 页再看手机' });
    for (i = -32; i <= priorEnd; i++) { if (Math.abs(i) % 4 !== 1) { push(SAMPLE_IDS[1], i, 14 + Math.abs(i) % 9, 30 + Math.abs(i) * 7 % 20); } }
    for (i = 0; i < yDow; i++) { push(SAMPLE_IDS[1], diffDays(t, addDays(yMon, i)), 14 + (i * 3) % 9, 30 + (i * 7) % 20); }

    goals.push({ _id: SAMPLE_IDS[2], name: 'Python 入门课', unit: '节', total: 40, deadline: addDays(t, 10), color: COLORS[2], createdAt: addDays(t, -26), obstacle: '卡在一个知识点就想放弃', plan: '先跳过学后面的，周末回头补' });
    for (i = -26; i <= priorEnd; i++) { if (Math.abs(i) % 3 !== 0) { push(SAMPLE_IDS[2], i, 1, 55 + Math.abs(i) % 20); } }
    for (i = 0; i < yDow - 2; i++) { push(SAMPLE_IDS[2], diffDays(t, addDays(yMon, i)), 1, 55 + (i * 9) % 25); }

    var notes = {};
    notes[mondayOf(addDays(t, -7))] = { keep: 'Python 一周坚持了 5 天', problem: '周末两天完全没碰书', try: '周末上午先完成一节再出门', next: '如果周末想赖床，就先把电脑放桌上' };
    notes[mondayOf(addDays(t, -14))] = { keep: '单词整周只在休息日断了一天', problem: '阅读有两天差点断掉', try: '把阅读固定到午饭后 20 分钟', next: '如果午饭后又想躺下，我就先把书翻开读 2 页' };

    /* 写回一律与「此刻本地已有的行」求并集，绝不整份覆盖：
       拉取与播种理论上不会撞车了（见上面的两条硬约束），但真要撞上，
       并集至少不会把刚拉下来的线上记录吃掉 —— 这是最后一道保险。 */
    var eg = allRows(readKey(K.goals, [])), el = allRows(readKey(K.logs, []));
    var seen = {}, a;
    for (a = 0; a < eg.length; a++) { seen[eg[a] && eg[a]._id] = 1; }
    for (a = 0; a < goals.length; a++) { if (!seen[goals[a]._id]) { eg.push(goals[a]); } }
    var seen2 = {};
    for (a = 0; a < el.length; a++) { seen2[el[a] && el[a]._id] = 1; }
    for (a = 0; a < logs.length; a++) { if (!seen2[logs[a]._id]) { el.push(logs[a]); } }
    var en = readKey(K.notes, {});
    if (!en || typeof en !== 'object' || Object.prototype.toString.call(en) === '[object Array]') { en = {}; }
    for (var nk in notes) { if (!en[nk]) { en[nk] = notes[nk]; } }

    writeKey(K.goals, eg);
    writeKey(K.logs, el);
    writeKey(K.notes, en);
    writeKey(SEEDED_KEY, 1);
    return true;
  }

  /* ── 渲染：首屏统计 ─────────────────────────────────────────────────── */
  function statCard(cls, icon, num, label) {
    return '<div class="npg-stat"><div class="ico ' + cls + '">' + icon + '</div><div><b class="npg-num">' + num + '</b><span>' + label + '</span></div></div>';
  }
  function renderStats() {
    var t = todayStr(), active = 0, checked = 0, minWeek = 0;
    var mon = mondayOf(t);
    for (var i = 0; i < st.goals.length; i++) {
      var g = st.goals[i];
      if (!isComplete(g)) { active++; if (hasLog(g._id, t)) { checked++; } }
    }
    for (i = 0; i < st.logs.length; i++) {
      var l = st.logs[i];
      if (l.date >= mon && l.date <= t && l.minutes) { minWeek += Number(l.minutes); }
    }
    var sum = 0;
    for (i = 0; i < st.logs.length; i++) { if (st.logs[i].date === t) { sum += Number(st.logs[i].amount) || 0; } }
    var strip = $('npgStats');
    if (!strip) { return; }
    strip.innerHTML =
      statCard('npg-ico-brand', ICON.target, active, '进行中目标') +
      statCard('npg-ico-ok', ICON.check, checked + '/' + active, '今日已打卡') +
      statCard('npg-ico-gold', ICON.clock, minWeek, '本周投入（分钟）') +
      statCard('npg-ico-teal', ICON.trend, trimNum(sum), '今日完成量合计');
    /* 首页仪表盘自带一张「数据概览」卡片 —— 顶部这条在首页收起来，
       否则同一个屏幕里会出现两遍同样的四个数。 */
    strip.style.display = (tab === 'home') ? 'none' : '';
  }

  /* ── 渲染：RPG 时间轴首页里的「学习目标」面板 ─────────────────────────
     首页只承担「一眼看进度 + 一键打卡」，输入型的操作（改数量 / 补记 / 记分钟 /
     复盘 / 图表）都在「学习目标」视图里做，两点之间用一个「去打卡」按钮直达。
     数据源与视图完全同一份（st），所以这里没有任何额外的同步或缓存逻辑。 */
  function homeRank(g) {
    if (isComplete(g)) { return 4; }                 /* 已达成压到最后 */
    if (isOverdue(g)) { return 0; }                  /* 逾期的顶到最前 */
    if (!hasLog(g._id, todayStr())) { return 1; }    /* 今天还没打的其次 */
    return 2;                                        /* 今天打过的再往下 */
  }
  function renderHome() {
    var box = $('npgHomeBody');
    if (!box) { return; }
    if (!st.goals.length) {
      box.innerHTML = '<div class="npg-empty">还没有学习目标。像「背完 2000 个单词」「读完 440 页的书」' +
        '这种有终点、有总量的，放这里最合适。</div>' +
        '<button class="npg-btn primary wide" type="button" data-npg-hadd>' + ICON.plus + '新建学习目标</button>';
      return;
    }
    var t = todayStr();
    var list = st.goals.slice();
    list.sort(function (a, b) { return homeRank(a) - homeRank(b); });

    var active = 0, todayDone = 0, overdue = 0, i;
    for (i = 0; i < st.goals.length; i++) {
      var gg = st.goals[i];
      if (!isComplete(gg)) { active++; }
      if (hasLog(gg._id, t)) { todayDone++; }
      if (isOverdue(gg)) { overdue++; }
    }

    var html = '<div class="npg-hsum">' +
      '<span class="npg-hpill"><b class="npg-num">' + active + '</b>个进行中</span>' +
      '<span class="npg-hpill' + (active && todayDone >= active ? ' ok' : '') + '">今日已打卡 <b class="npg-num">' +
        todayDone + '/' + active + '</b></span>' +
      (overdue ? '<span class="npg-hpill bad">已逾期 <b class="npg-num">' + overdue + '</b> 个</span>' : '') +
      '</div>';

    for (i = 0; i < list.length; i++) {
      var g = list[i];
      var done = doneAmount(g._id), total = Number(g.total) || 1;
      var pct = Math.min(100, Math.round(done / total * 100));
      var comp = isComplete(g), checked = hasLog(g._id, t), od = isOverdue(g);
      var sug = dailySuggest(g), sk = streak(g._id), left = diffDays(t, g.deadline);

      var badge = comp ? '<span class="npg-badge done">已完成</span>'
        : od ? '<span class="npg-badge overdue">已逾期 ' + (-left) + ' 天</span>'
          : checked ? '<span class="npg-badge today">今日已打卡</span>' : '';

      var acts;
      if (comp) {
        acts = '<span class="npg-hdone">' + ICON.check + '达成</span>';
      } else {
        acts = '<button class="npg-btn ' + (checked ? 'ok' : 'primary') + '" type="button" data-npg-quick="' + g._id + '">' +
            ICON.check + (checked ? '再记 +' : '打卡 +') + trimNum(sug) + '</button>' +
          '<button class="npg-btn ghost" type="button" data-npg-open="' + g._id + '" title="去学习目标视图，可改数量、补记、记分钟">去打卡</button>';
      }

      html += '<div class="npg-hrow' + (comp ? ' done' : '') + (od ? ' overdue' : '') + '">' +
        '<span class="npg-hdot" style="background:' + g.color + '"></span>' +
        '<div class="npg-hmain">' +
          '<div class="npg-htop">' +
            '<span class="nm" title="' + esc(g.name) + '">' + esc(g.name) + '</span>' +
            (isSample(g) ? '<span class="npg-badge sample">示例</span>' : '') + badge +
          '</div>' +
          '<div class="npg-hbar"><i style="width:' + pct + '%;background:' + g.color + '"></i>' + barTicks() + '</div>' +
          '<div class="npg-hmeta">' +
            '<b class="npg-num">' + trimNum(done) + '</b>/' + trimNum(total) + ' ' + esc(g.unit) +
            ' · ' + (comp ? '已收尾' : (left >= 0 ? '剩 <b class="npg-num">' + left + '</b> 天' : '<b class="npg-num">' + (-left) + '</b> 天前到期')) +
            (sk > 0 ? ' · 连续 <b class="npg-num">' + sk + '</b> 天' : '') +
            (!comp ? ' · 今日建议 <b class="npg-num">+' + trimNum(sug) + '</b> ' + esc(g.unit) : '') +
          '</div>' +
          /* 需要处理的目标（逾期 / 昨天漏打）把用户自己的预案摆出来 ——
             这正是「障碍出现了怎么办」那一刻最该看见的一句话 */
          (!comp && (od || missedYesterday(g)) ? planLine(g) : '') +
        '</div>' +
        '<div class="npg-hacts">' + acts + '</div>' +
      '</div>';
    }

    /* 首页这张与看板同口径（可切年/月），免得两处天数对不上。
       图只在 paintHeats() 里画 —— 这张卡在 RPG 视图里，而用户可能正停在「学习目标」视图。 */
    html += '<div class="npg-hheat">' +
      '<div class="npg-hheat-hd">' + heatRange(heatMode).label + '打卡节奏' + heatSubHtml('all', heatMode) +
      '</div>' +
      heatCtlHtml() +
      '<div class="npg-heat-scroll" id="npgHHeat"></div>' +
      '</div>';

    html += '<div class="npg-hfoot"><button class="npg-btn ghost wide" type="button" data-npg-openall>' +
      '进入「学习目标」管理 · 看板 / 周报 / 补记 →</button></div>';
    box.innerHTML = html;
  }

  /* 进度条上的 25/50/75 里程碑刻度：不依赖任何数据，只是给「已经走到哪一段」一个心理坐标 */
  function barTicks() {
    return '<em style="left:25%"></em><em style="left:50%"></em><em style="left:75%"></em>';
  }
  /* ── 渲染：今日 ─────────────────────────────────────────────────────── */
  function planLine(g) {
    if (!g.plan && !g.obstacle) { return ''; }
    var body = g.obstacle && g.plan ? ('如果' + esc(g.obstacle) + '，我就' + esc(g.plan)) : esc(g.plan || g.obstacle);
    return '<div class="npg-plan"><b>预案</b>' + body + '</div>';
  }
  function goalCard(g) {
    var t = todayStr();
    var done = doneAmount(g._id), total = Number(g.total) || 1;
    var pct = Math.min(100, Math.round(done / total * 100));
    var checked = hasLog(g._id, t), od = isOverdue(g), sug = dailySuggest(g), sk = streak(g._id);
    var cls = 'npg-goal' + (isComplete(g) ? ' done' : '') + (od ? ' overdue' : '');
    var html = '<div class="' + cls + '" id="npgRow-' + g._id + '">' +
      '<div class="npg-goal-head">' +
        '<span class="npg-color" style="background:' + g.color + '"></span>' +
        '<span class="nm">' + esc(g.name) + '</span>' +
        (isSample(g) ? '<span class="npg-badge sample">示例</span>' : '') +
        (isComplete(g) ? '<span class="npg-badge done">已完成</span>'
          : od ? '<span class="npg-badge overdue">已逾期 ' + (-diffDays(t, g.deadline)) + ' 天</span>'
          : checked ? '<span class="npg-badge today">今日已打卡</span>' : '') +
        '<span class="sp">' +
          '<button class="npg-icon-btn" type="button" data-npg-edit="' + g._id + '" aria-label="编辑目标">' + ICON.edit + '</button>' +
          '<button class="npg-icon-btn danger" type="button" data-npg-del="' + g._id + '" aria-label="删除目标">' + ICON.trash + '</button>' +
        '</span>' +
      '</div>' +
      '<div class="npg-bar"><i style="width:' + pct + '%;background:' + g.color + '"></i>' + barTicks() + '</div>' +
      '<div class="npg-meta">' +
        '<b class="npg-num">' + trimNum(done) + '</b> / ' + trimNum(Number(g.total)) + ' ' + esc(g.unit) +
        ' · 截止 ' + fmtShort(g.deadline) +
        (sk > 0 ? ' · <span class="flame">' + ICON.flame + '<b class="npg-num">' + sk + '</b> 天</span>' : '') +
        (!isComplete(g) && !od ? ' · 今日建议 <b class="npg-num">+' + trimNum(sug) + '</b> ' + esc(g.unit) : '') +
      '</div>' + planLine(g);

    if (isComplete(g)) {
      html += '<div class="npg-note-ok">' + ICON.check + ' 已达成，共投入 ' + trimNum(done) + ' ' + esc(g.unit) + '。</div>';
    } else {
      var opts = '';
      for (var d = 0; d <= 6; d++) {
        var dv = addDays(t, -d);
        opts += '<option value="' + dv + '"' + (d === 0 ? ' selected' : '') + '>' + (d === 0 ? '今天' : (d === 1 ? '昨天 ' + fmtShort(dv) : fmtShort(dv))) + '</option>';
      }
      html += '<div class="npg-form">' +
        '<div class="npg-field"><label for="npgAmt-' + g._id + '">本次完成（' + esc(g.unit) + '）</label>' +
          '<input type="number" step="any" min="0" inputmode="decimal" id="npgAmt-' + g._id + '" value="' + trimNum(sug) + '"></div>' +
        '<div class="npg-field"><label for="npgMin-' + g._id + '">用时（分钟，选填）</label>' +
          '<input type="number" step="1" min="0" max="1440" inputmode="numeric" id="npgMin-' + g._id + '" placeholder="30"></div>' +
        '<div class="npg-field"><label for="npgDate-' + g._id + '">日期（可补记最近 6 天）</label>' +
          '<select id="npgDate-' + g._id + '">' + opts + '</select></div>' +
      '</div>' +
      '<button class="npg-btn ' + (checked ? 'ok' : 'primary') + ' wide" type="button" data-npg-checkin="' + g._id + '" style="margin-top:10px">' +
        (checked ? '再记一笔' : (ICON.plus + '打卡')) + '</button>';
      if (checked) {
        html += '<div class="npg-note-ok">' + ICON.check + ' 今天已 +' + trimNum(logsOnDate(g._id, t)) + ' ' + esc(g.unit) + '，可以继续追加。</div>';
      }
    }
    return html + '</div>';
  }
  function renderToday() {
    var t = todayStr(), y = addDays(t, -1);
    var rest = [], missed = [], pending = [], done = [], active = [];
    for (var i = 0; i < st.goals.length; i++) {
      var g = st.goals[i];
      if (!isComplete(g)) { active.push(g); }
      if (missedYesterday(g)) { (isRestDay(g._id, y, datesOf(g._id)) ? rest : missed).push(g); }
    }
    var zone = '';
    if (active.length === 0 && st.goals.length > 0) {
      zone = '<div class="npg-card"><div class="npg-empty">所有目标都已达成。去「看板」回顾成果，或新建一个目标继续。</div></div>';
    } else if (rest.length || missed.length) {
      zone += '<div class="npg-card"><div class="npg-card-title"><span class="npg-dot red"></span>今天要处理</div>';
      for (i = 0; i < missed.length; i++) {
        var m = missed[i], od = isOverdue(m);
        zone += '<div class="npg-alert red"><span class="ico">' + ICON.alert + '</span><div class="body">' +
          '<b>' + esc(m.name) + '</b>' +
          '<div class="why">' + (od ? '已逾期 ' + (-diffDays(t, m.deadline)) + ' 天，且昨天没学' : '昨天没学 · 已滚入今日') + '</div>' +
          planLine(m) +
          '<div class="acts">' +
            '<button class="npg-btn primary" type="button" data-npg-goto="' + m._id + '">立即打卡</button>' +
            (od ? '<button class="npg-btn ghost" type="button" data-npg-edit="' + m._id + '">调整计划</button>' : '') +
          '</div></div></div>';
      }
      for (i = 0; i < rest.length; i++) {
        var r = rest[i];
        zone += '<div class="npg-alert amber"><span class="ico">' + ICON.calendar + '</span><div class="body">' +
          '<b>' + esc(r.name) + '</b>' +
          '<div class="why">昨天漏打卡 · 已自动使用本周休息日（每周 1 天），连续天数保住了</div>' +
          planLine(r) +
          '<div class="acts"><button class="npg-btn primary" type="button" data-npg-goto="' + r._id + '">立即打卡</button></div>' +
          '</div></div>';
      }
      zone += '</div>';
    }
    $('npgZone').innerHTML = zone;

    var pendHtml = '', doneHtml = '', doneCnt = 0;
    if (st.goals.length === 0) {
      pendHtml = '<div class="npg-card npg-empty">还没有学习目标，点下方「新建目标」开始。</div>';
    } else if (active.length === 0) {
      pendHtml = '<div class="npg-card npg-empty">所有目标都已达成，今天不用打卡。</div>';
    } else {
      for (i = 0; i < active.length; i++) {
        var card = goalCard(active[i]);
        if (hasLog(active[i]._id, t)) { doneHtml += card; doneCnt++; } else { pendHtml += card; }
      }
    }
    $('npgList').innerHTML = pendHtml;
    $('npgDone').innerHTML = doneCnt > 0
      ? '<div class="npg-card-title" style="margin-top:14px"><span class="npg-dot"></span>今日已打卡（' + doneCnt + '）</div><div class="npg-grid">' + doneHtml + '</div>'
      : '';
  }

  /* ── 渲染：看板 ─────────────────────────────────────────────────────── */
  function logsOf(id) {
    var out = [];
    for (var i = 0; i < st.logs.length; i++) { if (st.logs[i].goalId === id) { out.push(st.logs[i]); } }
    out.sort(function (a, b) { return a.date < b.date ? 1 : -1; });
    return out;
  }
  function recentLogs(g) {
    var arr = logsOf(g._id);
    if (!arr.length) { return ''; }
    var open = !!expanded[g._id];
    var html = '<button class="npg-toggle" type="button" data-npg-toggle="' + g._id + '">' + (open ? '收起记录 ▴' : '查看 ' + arr.length + ' 条记录 ▾') + '</button>';
    if (open) {
      html += '<div class="npg-logs">';
      for (var i = 0; i < Math.min(10, arr.length); i++) {
        var l = arr[i];
        html += '<div class="npg-log"><span class="npg-num">' + fmtShort(l.date) + '</span><b class="npg-num">+' + trimNum(Number(l.amount)) + '</b> ' + esc(g.unit) +
          (l.minutes ? ' · ' + l.minutes + ' 分钟' : '') +
          (l.makeup ? ' <span class="npg-badge makeup">补</span>' : '') +
          '<button class="del" type="button" data-npg-dellog="' + l._id + '" title="删除这条记录">×</button></div>';
      }
      if (arr.length > 10) { html += '<div class="npg-muted">仅显示最近 10 条</div>'; }
      html += '</div>';
    }
    return html;
  }
  function boardCard(g) {
    var done = doneAmount(g._id), total = Number(g.total) || 1;
    var pct = Math.min(100, done / total * 100);
    var e = eta(g), C = 2 * Math.PI * 40, off = C * (1 - pct / 100);
    var etaHtml;
    if (e.status === 'done') { etaHtml = '<span class="npg-ahead">已达成</span>'; }
    else if (e.status === 'unknown') { etaHtml = '<span class="npg-muted">近 7 天无记录，暂无推算</span>'; }
    else {
      etaHtml = fmtShort(e.date) + ' · ' + (e.diff >= 0 ? '<span class="npg-ahead">提前 ' + e.diff + ' 天</span>' : '<span class="npg-behind">拖后 ' + (-e.diff) + ' 天</span>');
    }
    return '<div class="npg-goal">' +
      '<div class="npg-goal-head"><span class="npg-color" style="background:' + g.color + '"></span><span class="nm">' + esc(g.name) + '</span>' +
        (isSample(g) ? '<span class="npg-badge sample">示例</span>' : '') + '</div>' +
      '<div class="npg-ring-wrap"><div class="npg-ring">' +
        '<svg width="96" height="96" viewBox="0 0 96 96">' +
          '<circle cx="48" cy="48" r="40" fill="none" stroke="var(--np-brand-soft1)" stroke-width="10"/>' +
          '<circle cx="48" cy="48" r="40" fill="none" stroke="' + g.color + '" stroke-width="10" stroke-linecap="round" stroke-dasharray="' + C.toFixed(1) + '" stroke-dashoffset="' + off.toFixed(1) + '"/>' +
        '</svg><div class="pct"><b class="npg-num">' + Math.round(pct) + '%</b><span>完成率</span></div></div>' +
      '<div class="npg-ring-stats">' +
        '<div class="row">' + ICON.trend + '<b class="npg-num">' + trimNum(done) + '</b> / ' + trimNum(Number(g.total)) + ' ' + esc(g.unit) + '</div>' +
        '<div class="row">' + ICON.flame + '连续 <b class="npg-num">' + streak(g._id) + '</b> 天</div>' +
        '<div class="row">' + ICON.calendar + '预计 ' + etaHtml + '</div>' +
        '<div class="row">' + ICON.clock + '今日建议 <b class="npg-num">+' + trimNum(dailySuggest(g)) + '</b> ' + esc(g.unit) + '</div>' +
      '</div></div>' +
      planLine(g) + recentLogs(g) + '</div>';
  }
  function renderBoard() {
    var html = '';
    for (var i = 0; i < st.goals.length; i++) { html += boardCard(st.goals[i]); }
    $('npgBoardGrid').innerHTML = html || '<div class="npg-card npg-empty">还没有目标。</div>';
    renderHeat();
    paintChart($('npgChart'), $('npgLegend'));
  }
  /* 近 14 天投入分钟 · 分目标堆叠柱状图（手写 SVG；未填分钟的记录不参与）
     宽度与方块图同一套规矩：按容器实测宽度算 —— 面板藏着时量到 0，就先放骨架，
     等显隐定下来由 paintHeats() 补画。窄屏按容器收窄（自己填满，不留横向滚动），
     宽屏封顶后居中（柱子不被拉成三倍宽的空壳）。不引入 viewBox 缩放：那会把柱子一起缩。 */
  var CHART_W_MIN = 300, CHART_W_MAX = 900;
  /* host / lg 由调用方给：看板那张与首页仪表盘那张共用同一套绘制（叶子函数，
     自己量容器宽度、自己画，不认识调用者是谁） */
  function paintChart(host, lg) {
    if (!host) { return; }
    var days = [], i, t = todayStr(), j;
    for (i = 13; i >= 0; i--) { days.push(addDays(t, -i)); }
    var avail = availOf(host);
    var W = Math.max(CHART_W_MIN, Math.min(CHART_W_MAX, avail));
    var H = 210, padL = 42, padR = 8, padB = 26, padT = 12;
    var plotW = W - padL - padR, plotH = H - padT - padB;

    var byDay = {}, max = 0;
    for (i = 0; i < days.length; i++) { byDay[days[i]] = {}; }
    for (i = 0; i < st.logs.length; i++) {
      var l = st.logs[i];
      if (!l.minutes || !byDay[l.date]) { continue; }
      byDay[l.date][l.goalId] = (byDay[l.date][l.goalId] || 0) + Number(l.minutes);
    }
    for (i = 0; i < days.length; i++) {
      var sum = 0, d = byDay[days[i]];
      for (var k in d) { if (Object.prototype.hasOwnProperty.call(d, k)) { sum += d[k]; } }
      if (sum > max) { max = sum; }
    }
    /* 没数据与宽度无关，先判它 —— 面板藏着时也该看到「还没填用时」而不是一块灰条 */
    if (max <= 0) {
      host.innerHTML = '<div class="npg-empty">最近 14 天还没有填写用时的记录。打卡时填上「用时（分钟）」就会出现在这里。</div>';
      if (lg) { lg.innerHTML = ''; }
      return;
    }
    if (avail <= 0) {                       /* 量不到宽度 → 骨架，等可见后补画 */
      host.innerHTML = '<div class="npg-chart-skel" aria-hidden="true"></div>';
      if (lg) { lg.innerHTML = ''; }
      return;
    }
    var step = plotW / days.length, bw = Math.max(9, Math.min(30, step - 8));
    /* 日期标签隔几根柱子标一个，跟着柱子宽度走 —— 否则窄栏里「9/21」会叠成一片。
       从「今天」往回数着标（而不是从 14 天前数起）：今天这一格必须有标签，
       那是这张图唯一的参照点；否则最后一根柱子常常是个没名字的棒子。 */
    var lblStep = step >= 44 ? 1 : (step >= 26 ? 2 : (step >= 18 ? 3 : 4));
    var lastI = days.length - 1;
    var ticks = 4, svg = '<svg width="' + W + '" height="' + H + '" viewBox="0 0 ' + W + ' ' + H + '" role="img" aria-label="近 14 天各目标投入分钟堆叠柱状图">';
    for (i = 0; i <= ticks; i++) {
      var vy = padT + plotH - plotH * i / ticks;
      svg += '<line x1="' + padL + '" y1="' + vy.toFixed(1) + '" x2="' + (W - padR) + '" y2="' + vy.toFixed(1) + '" stroke="var(--np-brand-line1)" stroke-width="1"/>';
      svg += '<text x="' + (padL - 6) + '" y="' + (vy + 4).toFixed(1) + '" text-anchor="end" font-size="10" fill="var(--np-ink-4)">' + Math.round(max * i / ticks) + '</text>';
    }
    for (i = 0; i < days.length; i++) {
      var x = padL + step * i + (step - bw) / 2, acc = 0;
      for (j = 0; j < st.goals.length; j++) {
        var g = st.goals[j];
        var v = byDay[days[i]][g._id] || 0;
        if (v <= 0) { continue; }
        var h = plotH * v / max;
        var y = padT + plotH - h - acc;
        svg += '<rect x="' + x.toFixed(1) + '" y="' + y.toFixed(1) + '" width="' + bw.toFixed(1) + '" height="' + h.toFixed(1) + '" rx="2" fill="' + g.color + '"/>';
        acc += h;
      }
      if ((lastI - i) % lblStep === 0) {
        svg += '<text x="' + (x + bw / 2).toFixed(1) + '" y="' + (H - 8) + '" text-anchor="middle" font-size="10" fill="var(--np-ink-4)">' + fmtShort(days[i]) + '</text>';
      }
    }
    svg += '</svg>';
    host.innerHTML = svg;
    var legend = '';
    for (i = 0; i < st.goals.length; i++) {
      legend += '<span><i style="background:' + st.goals[i].color + '"></i>' + esc(st.goals[i].name) + '</span>';
    }
    if (lg) { lg.innerHTML = legend; }
  }

  /* ── 渲染：打卡方块（GitHub 贡献图风格） ────────────────────────────────
     为什么按「分钟」分档：跨目标的单位不可比（个 / 页 / 节 / 章），分钟是唯一的共同尺度。
     但分钟是选填的，所以「有打卡却没填分钟」至少给 1 档 —— 保证「打过卡就有颜色」，
     这是这张图最要紧的可读性（否则一片空白，看着像根本没坚持）。
     颜色不另建一套色板：直接用目标色 + fill-opacity 分档，深浅主题都自适应。
     下面全是叶子函数，彼此不调用，由 renderBoard / renderHome 统一调度（铁律 9）。 */
  /* ── 月视图的日历坐标（纯计算，叶子函数） ──────────────────────────────
     为什么月视图不是「把年视图横向切成 12 段」：那样每段只有 3~5 列，格子还是小的，
     手机上照样看不清。月视图要的是「7 列 × 最多 6 行」的日历网格 —— 格子大、能写日号、
     不用横向滚，这才是手机端真正能用的一张图。 */
  function thisMonth() { return todayStr().slice(0, 7); }
  function monthKey() {
    var m = /^\d{4}-\d{2}$/.test(heatMonth) ? heatMonth : thisMonth();
    return m > thisMonth() ? thisMonth() : m;      /* 不允许翻到未来 */
  }
  function monthInfo(ym) {
    var y = +ym.slice(0, 4), mo = +ym.slice(5, 7);
    return {
      ym: ym, y: y, mo: mo,
      days: new Date(y, mo, 0).getDate(),          /* 当月天数（第 0 天 = 上月末） */
      lead: wdOf(ym + '-01'),                      /* 1 号落在第几列（0 = 周一） */
      label: y + '年' + mo + '月'
    };
  }
  function shiftMonth(ym, d) {
    var y = +ym.slice(0, 4), m = +ym.slice(5, 7) + d;
    y += Math.floor((m - 1) / 12);
    m = ((m - 1) % 12 + 12) % 12 + 1;
    return y + '-' + pad(m);
  }
  /* 当前视图覆盖的日期区间 —— 数据筛选与统计都以它为准，两种视图共用 */
  function heatRange(mode) {
    if (mode === 'month') {
      var mi = monthInfo(monthKey());
      return { start: mi.ym + '-01', end: mi.ym + '-' + pad(mi.days), label: mi.label, short: mi.mo + '月' };
    }
    return { start: heatWindow(HEAT_WEEKS).start, end: heatWindow(HEAT_WEEKS).end, label: '近一年', short: '近一年' };
  }
  function heatWindow(weeks) {
    var end = addDays(mondayOf(todayStr()), 6);      /* 最后一列 = 本周（周一…周日） */
    return { start: addDays(end, -(weeks * 7 - 1)), end: end };
  }
  function heatGoalsOn(date) {
    var out = [], i;
    for (i = 0; i < st.logs.length; i++) {
      if (st.logs[i].date === date && out.indexOf(st.logs[i].goalId) < 0) { out.push(st.logs[i].goalId); }
    }
    return out;
  }
  /* 一格的深浅档位：0 = 没打卡。忙到忘了填分钟也记 1 档 ——
     「打过卡就有颜色」是这张图的底线，否则一片空白看着像根本没坚持。 */
  function heatLevel(rec, th) {
    if (!rec) { return 0; }
    if (!th || !rec.minutes) { return 1; }
    if (rec.minutes <= th.t1) { return 2; }
    if (rec.minutes <= th.t2) { return 3; }
    return 4;
  }
  /* mode: 'year' → 53 列 × 7 行；'month' → 7 列 × 6 行的日历网格 */
  function heatData(goalId, mode) {
    var rg = heatRange(mode), byDate = {}, i, j, k;
    for (i = 0; i < st.logs.length; i++) {
      var l = st.logs[i];
      if (goalId !== 'all' && l.goalId !== goalId) { continue; }
      if (!isDate(l.date) || l.date < rg.start || l.date > rg.end) { continue; }
      var d = byDate[l.date] || (byDate[l.date] = { n: 0, amount: 0, minutes: 0 });
      d.n++; d.amount += Number(l.amount) || 0; d.minutes += Number(l.minutes) || 0;
    }
    var mins = [];
    for (k in byDate) {
      if (Object.prototype.hasOwnProperty.call(byDate, k) && byDate[k].minutes > 0) { mins.push(byDate[k].minutes); }
    }
    mins.sort(function (a, b) { return a - b; });
    /* 样本够 5 天才分 2/3/4 三档深浅，否则「打卡即最深」—— 免得只有两三条记录时全糊成一色 */
    var th = mins.length >= 5
      ? { t1: mins[Math.floor(mins.length * 0.34)], t2: mins[Math.floor(mins.length * 0.67)] } : null;
    var today = todayStr(), cells = [];
    if (mode === 'month') {
      var mi = monthInfo(monthKey());
      for (i = 0; i < mi.days; i++) {
        var dt = mi.ym + '-' + pad(i + 1), pos = mi.lead + i, mr = byDate[dt];
        cells.push({
          date: dt, col: pos % 7, row: Math.floor(pos / 7), day: i + 1,
          level: heatLevel(mr, th), future: dt > today,
          count: mr ? mr.n : 0, amount: mr ? mr.amount : 0, minutes: mr ? mr.minutes : 0
        });
      }
      return { cells: cells, cols: 7, rows: HEAT_FIT.month.rows, range: rg };
    }
    var win = heatWindow(HEAT_WEEKS);
    for (i = 0; i < HEAT_WEEKS; i++) {
      for (j = 0; j < 7; j++) {
        var date = addDays(win.start, i * 7 + j), rec = byDate[date];
        cells.push({
          date: date, col: i, row: j, day: +date.slice(8, 10),
          level: heatLevel(rec, th), future: date > today,
          count: rec ? rec.n : 0, amount: rec ? rec.amount : 0, minutes: rec ? rec.minutes : 0
        });
      }
    }
    return { cells: cells, cols: HEAT_WEEKS, rows: 7, range: rg };
  }
  function heatStats(goalId, mode) {
    var rg = heatRange(mode), seen = {}, ever = {}, minutes = 0, i, t = todayStr();
    for (i = 0; i < st.logs.length; i++) {
      var l = st.logs[i];
      if (goalId !== 'all' && l.goalId !== goalId) { continue; }
      if (!isDate(l.date)) { continue; }
      ever[l.date] = 1;                          /* 当前连续要跨出窗口去数，否则切到月视图会凭空少几天 */
      if (l.date < rg.start || l.date > rg.end) { continue; }
      seen[l.date] = 1;
      minutes += Number(l.minutes) || 0;
    }
    var list = [];
    for (var k in seen) { if (Object.prototype.hasOwnProperty.call(seen, k)) { list.push(k); } }
    list.sort();
    var longest = 0, run = 0, prev = null;
    for (i = 0; i < list.length; i++) {
      run = (prev && diffDays(prev, list[i]) === 1) ? run + 1 : 1;
      if (run > longest) { longest = run; }
      prev = list[i];
    }
    var cur = 0;
    if (ever[t]) { cur = 1; var p = t; while (ever[addDays(p, -1)]) { p = addDays(p, -1); cur++; } }
    return { days: list.length, longest: longest, current: cur, minutes: minutes };
  }
  /* 一格的两行提示文案。返回纯文本写进 data 属性，tooltip 用 textContent 渲染 ——
     不拼 HTML 就没有注入面，也省掉「属性里再嵌一层转义」的麻烦。 */
  function heatTip(goalId, c) {
    var head = c.date + ' 周' + WEEKDAYS[c.row];
    if (c.future) { return { a: head, b: '还没到' }; }
    if (!c.count) { return { a: head, b: '这天没有打卡 · 点开可查看/补记' }; }
    var b = c.count + ' 次打卡';
    if (goalId === 'all') { b += ' · ' + heatGoalsOn(c.date).length + ' 个目标'; }
    else {
      var g = goalById(goalId);
      b += ' · +' + trimNum(c.amount) + ' ' + (g ? g.unit : '');
    }
    if (c.minutes) { b += ' · ' + c.minutes + ' 分钟'; }
    return { a: head, b: b + ' · 点开看当天明细' };
  }
  function heatColor(goalId) {
    var g = goalId === 'all' ? null : goalById(goalId);
    return g && g.color ? g.color : 'var(--np-brand)';
  }
  /* ── 尺寸自适应：铺满 / 居中 / 才滚动 ────────────────────────────────────
     为什么不用 SVG viewBox 整体缩放：缩放会把格子一起缩，手机上 53 列缩完每格只剩 5px，
     反而更看不清。所以按容器实测宽度「重算格子边长」，缩的只有留白，格子守住可读下限。 */
  function heatMetrics(mode, avail) {
    var F = HEAT_FIT[mode] || HEAT_FIT.year;
    var gap = F.gap, cell = 0, gaps = [F.gap, 2], g;
    /* 挤不下时先收紧格子间距（3 → 2），再考虑缩格子 —— 宁可看着密一点，
       也别把方块缩到点不准。并排后左列本来就比整页窄一档，这一步是它的安全垫。 */
    for (g = 0; g < gaps.length; g++) {
      gap = gaps[g];
      cell = Math.floor((avail - F.padL - gap * (F.cols - 1)) / F.cols);
      if (isFinite(cell) && cell >= F.min) { break; }
    }
    if (!isFinite(cell) || cell < F.min) { cell = F.min; gap = F.gap; }   /* 真放不下 → 守下限，交给横向滚动 */
    if (cell > F.max) {
      cell = F.max;                                          /* 太宽 → 格子封顶 */
      if (F.maxGap) {                                        /* 余量改喂间隙，图仍然铺满 */
        var rest = Math.floor((avail - F.padL - F.cols * cell) / (F.cols - 1));
        gap = Math.min(F.maxGap, Math.max(gap, rest));
      }
    }
    return {
      cell: cell, gap: gap, padL: F.padL, padT: F.padT,
      W: F.padL + F.cols * (cell + gap) - gap
    };
  }
  /* 月视图格子够大时把日号写进去 —— 「这是 8 月 17 号」比「这是第 5 行第 3 格」有用得多 */
  function heatDayNum(x, y, cell, fs, n, col) {
    return '<text x="' + (x + cell / 2).toFixed(1) + '" y="' + (y + cell / 2 + fs * 0.36).toFixed(1) +
      '" text-anchor="middle" font-size="' + fs + '" font-weight="600" fill="' + col + '">' + n + '</text>';
  }
  function heatSvg(goalId, mode, avail) {
    var m = heatMetrics(mode, avail);
    var data = heatData(goalId, mode), cells = data.cells;
    var cell = m.cell, gap = m.gap, padL = m.padL, padT = m.padT;
    var H = padT + data.rows * (cell + gap) - gap;
    var color = heatColor(goalId), s = heatStats(goalId, mode), today = todayStr(), i, j;
    var showDay = (mode === 'month' && cell >= 26);
    var fDay = Math.max(10, Math.min(15, Math.round(cell * 0.37)));
    var aria = data.range.label + '打卡方块图：共 ' + s.days + ' 天有打卡，当前连续 ' + s.current +
      ' 天，最长连续 ' + s.longest + ' 天' + (s.minutes ? '，累计 ' + s.minutes + ' 分钟' : '');
    var out = '<svg width="' + m.W + '" height="' + H + '" viewBox="0 0 ' + m.W + ' ' + H +
      '" role="img" aria-label="' + esc(aria) + '">';
    for (j = 0; j < 7; j += 2) {          /* 隔行标星期，和 GitHub 一致 */
      out += '<text x="' + (padL - 5) + '" y="' + (padT + j * (cell + gap) + cell / 2 + 3.5).toFixed(1) +
        '" text-anchor="end" font-size="9" fill="var(--np-ink-5)">' + WEEKDAYS[j] + '</text>';
    }
    if (mode === 'year') {                /* 月份标签只在年视图画；月视图的月份写在导航条上 */
      var lastMo = -1;
      for (i = 0; i < data.cols; i++) {
        var mo = parseDate(addDays(data.range.start, i * 7)).getMonth() + 1;
        if (mo !== lastMo) {
          lastMo = mo;
          if (i > 0) {                    /* 第一列不标，否则紧贴左缘被裁 */
            out += '<text x="' + (padL + i * (cell + gap)) + '" y="' + (padT - 5) +
              '" font-size="9" fill="var(--np-ink-5)">' + mo + '月</text>';
          }
        }
      }
    }
    for (i = 0; i < cells.length; i++) {
      var c = cells[i];
      var x = padL + c.col * (cell + gap), y = padT + c.row * (cell + gap);
      var box = 'x="' + x + '" y="' + y + '" width="' + cell + '" height="' + cell +
        '" rx="' + (cell >= 26 ? 4 : 2) + '"';
      if (c.future) {                     /* 还没到的日子用虚线框，避免被误读成「漏打」 */
        out += '<rect ' + box + ' fill="none" stroke="var(--np-brand-line1)" stroke-width="1" stroke-dasharray="2 2"/>';
        if (showDay) { out += heatDayNum(x, y, cell, fDay, c.day, 'var(--np-ink-6)'); }
        continue;
      }
      var tip = heatTip(goalId, c);
      out += '<rect ' + box +
        ' fill="' + (c.level > 0 ? color : 'var(--np-brand-soft1)') + '"' +
        (c.level > 0 ? ' fill-opacity="' + HEAT_OP[c.level] + '"' : '') +
        (c.date === today ? ' stroke="var(--np-ink-3)" stroke-width="1.5"' : '') +
        ' data-npg-heat="' + c.date + '" data-npg-t1="' + esc(tip.a) + '" data-npg-t2="' + esc(tip.b) + '"/>';
      if (showDay) {
        /* 深色底（3 档以上）用品牌前景色，浅色底用正文色 —— 跟随主题自动成立，不新开色板 */
        out += heatDayNum(x, y, cell, fDay, c.day,
          c.level >= 3 ? 'var(--np-on-brand)' : (c.level > 0 ? 'var(--np-ink)' : 'var(--np-ink-4)'));
      }
    }
    return out + '</svg>';
  }
  /* 看板那张大图（可切年/月）：这里只出筛选项、统计数字与图例。
     SVG 交给 paintHeats() 在「面板显隐已经定下来」之后统一画 —— 面板藏着时量到的宽度是 0。 */
  function renderHeat() {
    var host = $('npgHeat');
    if (!host) { return; }
    var filt = $('npgHeatFilt'), stt = $('npgHeatStats'), lg = $('npgHeatLegend');
    if (!st.goals.length) {
      host.innerHTML = '<div class="npg-empty">还没有学习目标。建一个之后，这里会长出你的打卡方块。</div>';
      if (filt) { filt.innerHTML = ''; }
      if (stt) { stt.innerHTML = ''; }
      if (lg) { lg.innerHTML = ''; }
      return;
    }
    if (heatGoal !== 'all' && !goalById(heatGoal)) { heatGoal = 'all'; }
    host.innerHTML = '';
    if (filt) {
      var f = '<button class="npg-hfilt' + (heatGoal === 'all' ? ' on' : '') + '" type="button" data-npg-hfilt="all">全部目标</button>';
      for (var i = 0; i < st.goals.length; i++) {
        var g = st.goals[i];
        f += '<button class="npg-hfilt' + (heatGoal === g._id ? ' on' : '') + '" type="button" data-npg-hfilt="' + g._id + '">' +
          '<i style="background:' + g.color + '"></i>' + esc(g.name) + '</button>';
      }
      filt.innerHTML = f;
    }
    if (stt) { stt.innerHTML = heatStatsHtml(heatGoal, heatMode); }
    if (lg) { lg.innerHTML = heatLegendHtml(heatGoal); }
  }

  /* ── 视图切换 / 月份导航 / 统一绘制（全是叶子函数，彼此不反向调用） ──────
     为什么要「先出骨架、再量宽度、最后画图」：方块图是像素级的，格子大小取决于容器实测宽度，
     而容器藏在未激活的面板里时 clientWidth 是 0。硬画就会得到一张尺寸错误的图，
     切回来又没人重画。所以绘制统一收口到 paintHeats()，由 render 末尾 / resize / 视图切换触发。 */
  function availOf(el) {
    if (!el) { return 0; }
    var w = el.clientWidth;
    return w > 20 ? w - 2 : 0;          /* 减 2 给滚动条留点余量 */
  }
  function paintHeat(host, goalId, mode) {
    if (!host) { return; }
    var avail = availOf(host), keep = host.scrollLeft || 0;
    if (avail <= 0) {                   /* 量不到宽度就先放骨架，等可见了再补画 */
      host.innerHTML = '<div class="npg-heat-skel" aria-hidden="true"></div>';
      return;
    }
    host.innerHTML = heatSvg(goalId, mode, avail);
    /* 年视图在窄屏放不下要横向滚：首次进来直接停在最右 —— 最近的日期才是要看的 */
    host.scrollLeft = (mode === 'year' && keep <= 0) ? host.scrollWidth : keep;
  }
  function heatSegHtml() {
    return '<div class="npg-hseg" role="group" aria-label="打卡方块视图">' +
      '<button class="npg-hseg-btn' + (heatMode === 'year' ? ' on' : '') + '" type="button" data-npg-hmode="year">年视图</button>' +
      '<button class="npg-hseg-btn' + (heatMode === 'month' ? ' on' : '') + '" type="button" data-npg-hmode="month">月视图</button>' +
      '</div>';
  }
  function heatNavHtml() {
    if (heatMode !== 'month') { return ''; }
    var ym = monthKey(), t = thisMonth();
    return '<div class="npg-hnav">' +
      '<button class="npg-hnav-btn" type="button" data-npg-hprev aria-label="上一月">' + ICON.chevL + '</button>' +
      '<span class="npg-hnav-lbl">' + monthInfo(ym).label + '</span>' +
      '<button class="npg-hnav-btn" type="button" data-npg-hnext' + (ym >= t ? ' disabled' : '') +
      ' aria-label="下一月">' + ICON.chevR + '</button>' +
      (ym !== t ? '<button class="npg-hnav-now" type="button" data-npg-hnow>回到本月</button>' : '') +
      '</div>';
  }
  /* 切换胶囊 + 月份导航合成一条。月视图的网格是居中的，控件也跟着居中，
     否则宽屏上一头一尾、中间空一大片，眼睛得横跨整张卡才能把两者对上。 */
  function heatCtlHtml() {
    return '<div class="npg-hctl' + (heatMode === 'month' ? ' mid' : '') + '">' +
      heatSegHtml() + heatNavHtml() + '</div>';
  }
  /* 首页那颗「多少天有打卡」的小统计，跟着视图口径走（口径已写在标题里，这里不再重复） */
  function heatSubHtml(goalId, mode) {
    var s = heatStats(goalId, mode);
    return '<span class="npg-hheat-sub"><b class="npg-num">' + s.days + '</b> 天有打卡' +
      ' · 当前连续 <b class="npg-num">' + s.current + '</b> 天</span>';
  }
  /* 方块图下面那行统计与色阶图例：看板与首页仪表盘两处共用（叶子函数，只拼 HTML） */
  function heatStatsHtml(goalId, mode) {
    var s = heatStats(goalId, mode), rg = heatRange(mode);
    return '<span>' + rg.short + '打卡 <b>' + s.days + '</b> 天</span>' +
      '<span>当前连续 <b>' + s.current + '</b> 天</span>' +
      '<span>最长连续 <b>' + s.longest + '</b> 天</span>' +
      (s.minutes ? '<span>累计投入 <b>' + s.minutes + '</b> 分钟</span>' : '');
  }
  function heatLegendHtml(goalId) {
    var lc = heatColor(goalId), sw = '', k;
    for (k = 0; k < 5; k++) {
      sw += '<i style="' + (k === 0
        ? 'background:var(--np-brand-soft1)'
        : 'background:' + lc + ';opacity:' + HEAT_OP[k]) + '"></i>';
    }
    return '<span>少</span>' + sw + '<span>多</span>';
  }
  function paintHeats() {
    paintHeat($('npgHHeat'), 'all', heatMode);
    paintHeat($('npgHeat'), heatGoal, heatMode);
    paintHeat($('npgDashHeat'), 'all', heatMode);     /* 首页仪表盘那张，与看板同口径 */
    paintChart($('npgChart'), $('npgLegend'));
    paintChart($('npgDashChart'), $('npgDashLegend'));
    var ctl = $('npgHeatCtl');
    if (ctl) { ctl.innerHTML = heatCtlHtml(); }
    var dctl = $('npgDashHCtl');
    if (dctl) { dctl.innerHTML = heatCtlHtml(); }
    /* 统计与图例跟着年/月口径走：仪表盘那张不经过 renderHeat，就在这里一并刷新 */
    var dstt = $('npgDashHeatStats'), dlg = $('npgDashHeatLegend');
    if (dstt) { dstt.innerHTML = heatStatsHtml('all', heatMode); }
    if (dlg) { dlg.innerHTML = heatLegendHtml('all'); }
  }
  /* 只重画方块图那几块。不整个 render()：那会把「今日」里的输入框一起重建，
     用户在填写时切视图就会被吞掉半行字。 */
  function refreshHeatViews() {
    renderHome();
    renderHeat();
    paintHeats();
  }

  /* ── 点方块 → 当天明细（可查看 / 删除 / 直接补记） ────────────────────────
     这是这张图最实用的一步：看见哪天空，点一下就补上，不用先想「那天是几号」。
     补记只对最近 6 天开放（与 doCheckin 的日期口径一致），更早的日期如实说明。 */
  function dayWindowOk(date) { return date <= todayStr() && date >= addDays(todayStr(), -6); }
  function renderDay(date) {
    var box = $('npgDayBody'), title = $('npgDayTitle'), note = $('npgDayNote');
    if (!box || !title) { return; }
    title.textContent = date + ' · 周' + WEEKDAYS[wdOf(date)];
    var ok = dayWindowOk(date), html = '', i, j, k;
    for (i = 0; i < st.goals.length; i++) {
      var g = st.goals[i], recs = [];
      for (j = 0; j < st.logs.length; j++) {
        if (st.logs[j].goalId === g._id && st.logs[j].date === date && st.logs[j].removed !== 1) { recs.push(st.logs[j]); }
      }
      var amt = 0, mins = 0;
      for (k = 0; k < recs.length; k++) { amt += Number(recs[k].amount) || 0; mins += Number(recs[k].minutes) || 0; }
      html += '<div class="npg-day-row">' +
        '<span class="npg-hdot" style="background:' + g.color + '"></span>' +
        '<span class="nm">' + esc(g.name) + '</span>' +
        (recs.length
          ? '<span class="val">+' + trimNum(amt) + ' ' + esc(g.unit) + (mins ? ' · ' + mins + ' 分钟' : '') + '</span>' +
            '<button class="npg-icon-btn danger" type="button" data-npg-daydel="' + date + '|' + g._id +
            '" aria-label="删除这天该目标的记录">' + ICON.trash + '</button>'
          : (ok
            ? '<button class="npg-btn ghost" type="button" data-npg-makeup="' + date + '|' + g._id + '">补记这天</button>'
            : '<span class="val npg-muted">没打卡</span>')) +
        '</div>';
    }
    if (!st.goals.length) { html = '<div class="npg-empty">还没有学习目标。</div>'; }
    box.innerHTML = html;
    if (note) {
      note.textContent = ok
        ? '这天在补记窗口内（可补最近 6 天）。点「补记这天」会带着日期跳到「今日」，填数量回车即可。'
        : '这天已超出 6 天补记窗口，只能查看与删除记录。';
    }
  }
  function openDay(date) {
    hideTip();
    var m = $('npgDay');
    if (!m) { return; }
    renderDay(date);
    m.className = 'npg-mask on';
  }
  function closeDay() {
    var m = $('npgDay');
    if (m) { m.className = 'npg-mask'; }
  }
  /* 带着日期跳进「今日」并聚焦到该目标的输入框 —— 从「看见漏打」到「补上」只差两步 */
  function goMakeup(date, goalId) {
    closeDay();
    showGoals();
    switchTab('today');
    var g = goalById(goalId);
    var sel = $('npgDate-' + goalId);
    if (sel) { sel.value = date; }
    var row = $('npgRow-' + goalId);
    if (row) {
      row.scrollIntoView({ block: 'center' });
      row.className += ' npg-flash';
      (function (el) {
        setTimeout(function () { el.className = el.className.replace(' npg-flash', ''); }, 1500);
      })(row);
    }
    var amt = $('npgAmt-' + goalId);
    if (amt && amt.focus) { amt.focus(); }
    toast((date === todayStr() ? '填数量后回车即可打卡' : '已选 ' + fmtShort(date) + '，填数量后回车即可补记') +
      (g ? '（' + g.name + '）' : ''));
  }

  /* ── 悬浮提示（跟随鼠标，边界自动翻转） ───────────────────────────────── */
  function hideTip() {
    var t = $('npgTip');
    if (t) { t.className = 'npg-tip'; }
  }
  function moveTip(e) {
    var box = $('npgTip');
    if (!box) { return; }
    var w = box.offsetWidth || 180, h = box.offsetHeight || 40;
    var x = e.clientX + 14, y = e.clientY + 16;
    if (x + w > window.innerWidth - 8) { x = e.clientX - w - 14; }
    if (y + h > window.innerHeight - 8) { y = e.clientY - h - 12; }
    box.style.left = Math.max(8, x) + 'px';
    box.style.top = Math.max(8, y) + 'px';
  }

  /* ── 渲染：周报 ─────────────────────────────────────────────────────── */
  function weekRange() {
    var start = addDays(mondayOf(todayStr()), weekOff * 7);
    return { start: start, end: addDays(start, 6) };
  }
  function renderWeekly() {
    var r = weekRange(), isCur = weekOff === 0;
    $('npgWeekLabel').textContent = fmtShort(r.start) + ' - ' + fmtShort(r.end) + '（周' + WEEKDAYS[0] + ' 至 周日' + (isCur ? ' · 本周' : '') + '）';
    $('npgWeekNext').style.visibility = weekOff < 0 ? 'visible' : 'hidden';

    var cur = weekStats(r.start, r.end), prev = weekStats(addDays(r.start, -7), addDays(r.start, -1));
    var html = '', any = false;
    for (var i = 0; i < st.goals.length; i++) {
      var g = st.goals[i], c = cur.per[g._id] || { sum: 0, days: 0, mins: 0 }, p = prev.per[g._id] || { sum: 0, days: 0, mins: 0 };
      if (c.sum > 0 || c.days > 0) { any = true; }
      if (isComplete(g) && c.days === 0) { continue; }
      var cmp = (p.sum > 0 || p.days > 0)
        ? '<span class="cmp">｜上周 ' + (p.sum > 0 ? '+' + trimNum(p.sum) + ' ' + esc(g.unit) + ' · ' : '') + '打卡 ' + p.days + ' 天' + (p.mins > 0 ? ' · ' + p.mins + ' 分钟' : '') + '</span>'
        : '<span class="cmp">｜上周无记录</span>';
      html += '<div class="npg-wrow"><span class="nm">' + esc(g.name) + '</span><span class="vals">' +
        (c.sum > 0 ? '<b class="npg-num">+' + trimNum(c.sum) + '</b> ' + esc(g.unit) + ' · ' : '') +
        '打卡 <b class="npg-num">' + c.days + '</b> 天' + (c.mins > 0 ? ' · <b class="npg-num">' + c.mins + '</b> 分钟' : '') +
        '</span>' + cmp + '</div>';
    }
    if (!any) {
      html = '<div class="npg-empty">这一周还没有打卡记录</div>';
    } else {
      var dm = cur.totalMin - prev.totalMin;
      html += '<div class="npg-wrow"><b>合计投入</b><span class="vals"><b class="npg-num">' + cur.totalMin + '</b> 分钟</span>' +
        (prev.totalMin > 0 ? '<span class="cmp">｜上周 ' + prev.totalMin + ' 分钟' + (dm !== 0 ? '（' + (dm > 0 ? '+' : '') + dm + '）' : '') + '</span>' : '') + '</div>';
    }
    $('npgWeekSummary').innerHTML = html;

    var n = st.notes[r.start] || {};
    $('npgKeep').value = n.keep || '';
    $('npgProblem').value = n.problem || '';
    $('npgTry').value = n.try || '';
    $('npgNext').value = n.next || '';
    $('npgReportBox').style.display = 'none';
  }
  function buildReport() {
    var r = weekRange(), cur = weekStats(r.start, r.end), prev = weekStats(addDays(r.start, -7), addDays(r.start, -1));
    var lines = ['【学习周报】' + fmtShort(r.start) + ' - ' + fmtShort(r.end)];
    for (var i = 0; i < st.goals.length; i++) {
      var g = st.goals[i], c = cur.per[g._id] || { sum: 0, days: 0, mins: 0 }, p = prev.per[g._id] || { sum: 0 };
      if (c.sum <= 0 && c.days <= 0) { continue; }
      var pct = Math.min(100, Math.round(doneAmount(g._id) / (Number(g.total) || 1) * 100));
      var line = '- ' + g.name + '：+' + trimNum(c.sum) + ' ' + g.unit + '，打卡 ' + c.days + ' 天（累计 ' + pct + '%）';
      if (p.sum > 0) { line += '｜上周 +' + trimNum(p.sum) + ' ' + g.unit; }
      lines.push(line);
    }
    if (cur.totalMin > 0) {
      lines.push('本周总投入：' + cur.totalMin + ' 分钟' + (prev.totalMin > 0 ? '（上周 ' + prev.totalMin + ' 分钟）' : ''));
    }
    var n = st.notes[r.start] || {};
    if (n.keep) { lines.push('保持：' + n.keep); }
    if (n.problem) { lines.push('问题：' + n.problem); }
    if (n.try) { lines.push('尝试：' + n.try); }
    if (n.next) { lines.push('下周预案：' + n.next); }
    return lines.join('\n');
  }
  function copyText(text) {
    function fallback() {
      var ta = document.createElement('textarea');
      ta.value = text;
      ta.style.position = 'fixed';
      ta.style.opacity = '0';
      document.body.appendChild(ta);
      ta.select();
      try { document.execCommand('copy'); toast('周报已复制'); } catch (e) { toast('复制失败，请手动选择文本'); }
      ta.parentNode.removeChild(ta);
    }
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(text).then(function () { toast('周报已复制'); }, fallback);
    } else { fallback(); }
  }

  /* ── 渲染：我的 ─────────────────────────────────────────────────────── */
  function renderMine() {
    var online = ROLES.online();
    var role = ROLES.current();
    $('npgSyncBox').innerHTML =
      '<div class="npg-card-title"><span class="npg-dot' + (online ? '' : ' amber') + '"></span>在线同步</div>' +
      '<div class="npg-wrow"><span class="vals">' +
        '<span class="npg-badge ' + (online ? 'done' : 'overdue') + '">' + (online ? '已连上服务端' : '离线模式') + '</span>' +
        ' 当前角色：<b>' + esc(role ? role.name : '—') + '</b> · 数据版本 <b class="npg-num">' + ROLES.rev() + '</b>' +
      '</span></div>' +
      '<div class="npg-muted" style="margin-top:6px">目标、打卡记录、每周复盘都存在这个角色名下，改动会自动推送；' +
      '换设备或换浏览器打开同一角色，数据都在。' + (online ? '' : '当前是离线模式，改动暂存本机，恢复后会自动补传。') + '</div>' +
      '<div class="npg-actions"><button class="npg-btn ghost" type="button" id="npgResync">' + ICON.refresh + '手动重新同步</button></div>';
  }

  /* ══ 首页仪表盘：布局状态（读写 / 归一化 / 增删改） ══════════════════════
     坏数据一律在入口挡掉：读进来先跟注册表对齐（未知 id 丢掉、缺的补到末尾），
     写出去只用 id 数组，任何一步失败都不会让页面崩成空白。 */
  function dashMeta(id) {
    for (var i = 0; i < DASH_WIDGETS.length; i++) {
      if (DASH_WIDGETS[i].id === id) { return DASH_WIDGETS[i]; }
    }
    return null;
  }
  function dashLoad() {
    var raw = null;
    try { raw = JSON.parse(localStorage.getItem(DASH_KEY) || 'null'); } catch (e) { raw = null; }
    if (!raw || typeof raw !== 'object' || Object.prototype.toString.call(raw) === '[object Array]') { raw = {}; }
    var ro = allRows(raw.order), fresh = ro.length === 0;
    var order = [], seen = {}, i, id;
    for (i = 0; i < ro.length; i++) {
      id = ro[i];
      if (typeof id === 'string' && dashMeta(id) && !seen[id]) { seen[id] = 1; order.push(id); }
    }
    for (i = 0; i < DASH_WIDGETS.length; i++) {          /* 新版新增的组件自动接在末尾 */
      if (!seen[DASH_WIDGETS[i].id]) { order.push(DASH_WIDGETS[i].id); }
    }
    dash.order = order;
    dash.hidden = {};
    dash.wide = {};
    var rh = allRows(raw.hidden), rw = allRows(raw.wide);
    for (i = 0; i < rh.length; i++) { if (dashMeta(rh[i])) { dash.hidden[rh[i]] = 1; } }
    for (i = 0; i < rw.length; i++) { if (dashMeta(rw[i])) { dash.wide[rw[i]] = 1; } }
    if (fresh) {                                         /* 第一次用：按注册表默认铺一遍 */
      for (i = 0; i < DASH_WIDGETS.length; i++) {
        if (!DASH_WIDGETS[i].on) { dash.hidden[DASH_WIDGETS[i].id] = 1; }
        if (DASH_WIDGETS[i].wide) { dash.wide[DASH_WIDGETS[i].id] = 1; }
      }
    }
  }
  function dashSave() {
    var hid = [], wide = [], i;
    for (i = 0; i < dash.order.length; i++) {
      if (dash.hidden[dash.order[i]]) { hid.push(dash.order[i]); }
      if (dash.wide[dash.order[i]]) { wide.push(dash.order[i]); }
    }
    try {
      localStorage.setItem(DASH_KEY, JSON.stringify({ order: dash.order, hidden: hid, wide: wide }));
    } catch (e) { toast('布局只存在这台设备上，但本机存储当前不可用'); }
  }
  function dashVisible() {
    var out = [], i;
    for (i = 0; i < dash.order.length; i++) {
      if (!dash.hidden[dash.order[i]]) { out.push(dash.order[i]); }
    }
    return out;
  }
  /* 上移 / 下移只在「看得见的卡片」之间换位 —— 中间夹着收起的卡片时，
     直接对 order 换位会看着毫无反应（真换了，但视线里两张都没动）。 */
  function dashMove(id, dir) {
    var vis = dashVisible(), i = vis.indexOf(id), j = i + dir;
    if (i < 0 || j < 0 || j >= vis.length) { return; }
    var t = vis[i]; vis[i] = vis[j]; vis[j] = t;
    var hid = [], k;
    for (k = 0; k < dash.order.length; k++) {
      if (dash.hidden[dash.order[k]]) { hid.push(dash.order[k]); }
    }
    dash.order = vis.concat(hid);
    dashSave();
  }
  function dashToggleWide(id) {
    if (dash.wide[id]) { delete dash.wide[id]; } else { dash.wide[id] = 1; }
    dashSave();
  }
  function dashHide(id) { dash.hidden[id] = 1; dashSave(); }
  function dashShow(id) { delete dash.hidden[id]; dashSave(); }
  function dashReset() {
    try { localStorage.removeItem(DASH_KEY); } catch (e) { /* 清不掉就整个覆盖一次 */ }
    dashLoad();
    dashSave();
  }

  /* ── 仪表盘：渲染（外壳 + 工具条 + 货架，组件内容由 dashFill 逐个填） ──── */
  function dashBarHtml(id) {
    var m = dashMeta(id), vis = dashVisible(), i = vis.indexOf(id);
    return '<div class="npg-w-bar">' +
      '<span class="nm">' + esc(m.name) + '</span>' +
      '<span class="acts">' +
        '<button class="npg-w-btn" type="button" data-npg-wup="' + id + '"' + (i <= 0 ? ' disabled' : '') +
          ' aria-label="把「' + esc(m.name) + '」上移">' + ICON.up + '</button>' +
        '<button class="npg-w-btn" type="button" data-npg-wdown="' + id + '"' + (i >= vis.length - 1 ? ' disabled' : '') +
          ' aria-label="把「' + esc(m.name) + '」下移">' + ICON.down + '</button>' +
        '<button class="npg-w-btn' + (dash.wide[id] ? ' on' : '') + '" type="button" data-npg-wwide="' + id +
          '" aria-label="切换「' + esc(m.name) + '」宽度" title="' + (dash.wide[id] ? '改成半宽' : '改成整宽') + '">' +
          ICON.layout + '</button>' +
        '<button class="npg-w-btn danger" type="button" data-npg-whide="' + id +
          '" aria-label="收起「' + esc(m.name) + '」" title="收起这张卡片">' + ICON.minus + '</button>' +
      '</span></div>';
  }
  function renderDash() {
    var grid = $('npgDashGrid');
    if (!grid) { return; }
    dashLoad();                    /* 每次重绘都归一化：坏数据永远进不了 DOM */
    var vis = dashVisible(), html = '', i, id, m;
    for (i = 0; i < vis.length; i++) {
      id = vis[i]; m = dashMeta(id);
      html += '<section class="npg-w' + (dash.wide[id] ? ' wide' : '') + '" data-npg-w="' + id + '">' +
        (dashEdit ? dashBarHtml(id) : '') +
        '<div class="npg-w-body" id="npgW-' + id + '"></div></section>';
    }
    if (!vis.length) {
      html = '<div class="npg-card"><div class="npg-empty">卡片都收起来了。点「恢复默认布局」把它们放回来。</div></div>';
    }
    grid.innerHTML = html;
    for (i = 0; i < vis.length; i++) { dashFill(vis[i]); }   /* 单向：只在这里调各叶子组件 */
    renderDashShelf();
    var wrap = $('npgDash'), btn = $('npgDashBtn'), hint = $('npgDashHint');
    if (wrap) { wrap.className = 'npg-dash' + (dashEdit ? ' editing' : ''); }
    if (hint) { hint.style.display = dashEdit ? '' : 'none'; }
    if (btn) {
      btn.className = 'npg-btn' + (dashEdit ? ' ghost' : '');
      btn.innerHTML = (dashEdit ? '' : ICON.sliders) + (dashEdit ? '完成' : '自定义卡片');
    }
  }
  function dashFill(id) {
    var host = $('npgW-' + id), m = dashMeta(id);
    if (host && m && m.render) { m.render(host); }
  }
  /* 改完布局要整体重绘：卡片外壳重建了，方块图 / 柱状图的容器**也是新的空 div** ——
     不跟着补画，用户会看到两张空卡片。所以布局类操作一律走这里。 */
  function dashRefresh() { renderDash(); paintHeats(); }
  function renderDashShelf() {
    var shelf = $('npgDashShelf');
    if (!shelf) { return; }
    if (!dashEdit) { shelf.style.display = 'none'; shelf.innerHTML = ''; return; }
    shelf.style.display = '';
    var hid = [], i;
    for (i = 0; i < dash.order.length; i++) {
      if (dash.hidden[dash.order[i]]) { hid.push(dash.order[i]); }
    }
    var html = '<div class="npg-shelf-hd">收起的卡片 · ' + hid.length + ' 个</div>';
    if (!hid.length) {
      html += '<div class="npg-muted">八张卡片都在上面了。用不上的可以点卡片右上角的减号收起来。</div>';
    } else {
      html += '<div class="npg-shelf-list">';
      for (i = 0; i < hid.length; i++) {
        var m = dashMeta(hid[i]);
        html += '<button class="npg-shelf-btn" type="button" data-npg-wshow="' + hid[i] + '">' +
          ICON.plus + esc(m.name) + '</button>';
      }
      html += '</div>';
    }
    html += '<div class="npg-shelf-note">布局只存在这台设备上 —— 手机和电脑各摆各的，不会互相覆盖。</div>' +
      '<button class="npg-btn ghost" type="button" data-npg-wreset>恢复默认布局</button>';
    shelf.innerHTML = html;
  }

  /* ══ 仪表盘组件（叶子渲染函数：各自只管把 HTML 写进自己的 host） ═══════
     复用底层计算与图表生成函数（dailySuggest / streak / heatCtlHtml / statCard …），
     不复制一份算法 —— 否则两处的「建议量」会各算各的，早晚对不上。 */

  /* 组件 · 数据概览 */
  function wStats(host) {
    var t = todayStr(), mon = mondayOf(t), active = 0, checked = 0, minWeek = 0, sum = 0, i, l;
    for (i = 0; i < st.goals.length; i++) {
      var g = st.goals[i];
      if (!isComplete(g)) { active++; if (hasLog(g._id, t)) { checked++; } }
    }
    for (i = 0; i < st.logs.length; i++) {
      l = st.logs[i];
      if (l.date >= mon && l.date <= t && l.minutes) { minWeek += Number(l.minutes); }
      if (l.date === t) { sum += Number(l.amount) || 0; }
    }
    host.innerHTML = '<div class="npg-wstats">' +
      statCard('npg-ico-brand', ICON.target, active, '进行中目标') +
      statCard('npg-ico-ok', ICON.check, checked + '/' + active, '今日已打卡') +
      statCard('npg-ico-gold', ICON.clock, minWeek, '本周投入（分钟）') +
      statCard('npg-ico-teal', ICON.trend, trimNum(sum), '今日完成量合计') +
      '</div>';
  }

  /* 组件 · 今天要处理（逾期 / 昨天漏打 → 其余待办；每条一个一键打卡） */
  function wTodayRow(g, kind) {
    var sug = dailySuggest(g), sk = streak(g._id), left = diffDays(todayStr(), g.deadline);
    var tag = kind === 'od' ? '<span class="npg-badge overdue">已逾期 ' + (-left) + ' 天</span>'
      : kind === 'miss' ? '<span class="npg-badge today">昨天没打</span>' : '';
    return '<div class="npg-wrow2">' +
      '<span class="npg-hdot" style="background:' + g.color + '"></span>' +
      '<div class="npg-wmain">' +
        '<div class="npg-wname"><span class="nm">' + esc(g.name) + '</span>' + tag + '</div>' +
        '<div class="npg-wmeta">建议 <b class="npg-num">+' + trimNum(sug) + '</b> ' + esc(g.unit) +
          (left >= 0 ? ' · 剩 <b class="npg-num">' + left + '</b> 天' : '') +
          (sk > 0 ? ' · 连续 <b class="npg-num">' + sk + '</b> 天' : '') + '</div>' +
        /* 需要处理的目标（逾期 / 昨天漏打）把用户自己写的预案摆出来 ——
           那正是「障碍出现了怎么办」这一刻最该看见的一句话 */
        (kind ? planLine(g) : '') +
      '</div>' +
      '<div class="npg-wacts">' +
        '<button class="npg-btn primary" type="button" data-npg-quick="' + g._id + '">' +
          ICON.check + '打卡 +' + trimNum(sug) + '</button>' +
        '<button class="npg-btn ghost" type="button" data-npg-open="' + g._id +
          '" title="去今日页签，可改数量、补记、记分钟">细记</button>' +
      '</div></div>';
  }
  function wToday(host) {
    if (!st.goals.length) {
      host.innerHTML = '<div class="npg-whd">今天要处理</div>' +
        '<div class="npg-empty">还没有学习目标。像「背完 2000 个单词」「读完 440 页的书」' +
        '这种有终点、有总量的，放这里最合适。</div>' +
        '<button class="npg-btn primary wide" type="button" data-npg-hadd style="margin-top:10px">' +
        ICON.plus + '新建学习目标</button>';
      return;
    }
    var t = todayStr(), y = addDays(t, -1), rush = [], todo = [], doneN = 0, i;
    for (i = 0; i < st.goals.length; i++) {
      var g = st.goals[i];
      if (isComplete(g)) { continue; }
      if (hasLog(g._id, t)) { doneN++; continue; }
      var od = isOverdue(g);
      /* 昨天没打，但按休息日规则不算断档 → 不催，也不标红 */
      var miss = missedYesterday(g) && !isRestDay(g._id, y, datesOf(g._id));
      if (od || miss) { rush.push({ g: g, kind: od ? 'od' : 'miss' }); } else { todo.push(g); }
    }
    var html = '<div class="npg-whd">今天要处理' +
      '<span class="npg-hint-inline">逾期的先补 · 没打的按建议量走</span></div>';
    if (!rush.length && !todo.length) {
      html += '<div class="npg-wok">' + ICON.check + '今天的都打完了' +
        (doneN ? '（' + doneN + ' 个）' : '') + '，明天见。</div>';
    }
    for (i = 0; i < rush.length; i++) { html += wTodayRow(rush[i].g, rush[i].kind); }
    for (i = 0; i < todo.length; i++) { html += wTodayRow(todo[i], ''); }
    host.innerHTML = html;
  }

  /* 组件 · 目标进度（只读一眼看完整盘；打卡等操作在「今天要处理」和页签里） */
  function wGoals(host) {
    var list = st.goals.slice(), t = todayStr(), i;
    if (!list.length) {
      host.innerHTML = '<div class="npg-whd">目标进度</div><div class="npg-empty">还没有目标。</div>';
      return;
    }
    list.sort(function (a, b) { return homeRank(a) - homeRank(b); });
    var html = '<div class="npg-whd">目标进度' +
      '<span class="npg-hint-inline">按「逾期 → 今天没打 → 今天打过 → 已完成」排</span></div>';
    for (i = 0; i < list.length; i++) {
      var g = list[i], done = doneAmount(g._id), total = Number(g.total) || 1;
      var pct = Math.min(100, Math.round(done / total * 100));
      var comp = isComplete(g), left = diffDays(t, g.deadline);
      html += '<div class="npg-wgoal' + (comp ? ' done' : '') + '">' +
        '<div class="npg-wgoal-hd">' +
          '<span class="npg-hdot" style="background:' + g.color + '"></span>' +
          '<span class="nm">' + esc(g.name) + '</span>' +
          '<span class="pct npg-num">' + pct + '%</span>' +
        '</div>' +
        '<div class="npg-hbar"><i style="width:' + pct + '%;background:' + g.color + '"></i>' + barTicks() + '</div>' +
        '<div class="npg-wmeta"><b class="npg-num">' + trimNum(done) + '</b>/' + trimNum(total) + ' ' + esc(g.unit) +
          ' · ' + (comp ? '已收尾'
            : (left >= 0 ? '剩 <b class="npg-num">' + left + '</b> 天'
              : '<b class="npg-num">' + (-left) + '</b> 天前到期')) +
        '</div></div>';
    }
    host.innerHTML = html;
  }

  /* 组件 · 打卡方块（与看板共用 heatSvg / 控件生成；内容由 paintHeats 补画） */
  function wHeat(host) {
    if (!st.goals.length) {
      host.innerHTML = '<div class="npg-whd">打卡方块</div>' +
        '<div class="npg-empty">建了目标之后，这里会长出你的打卡方块。</div>';
      return;
    }
    host.innerHTML = '<div class="npg-whd">打卡方块' +
      '<span class="npg-hint-inline">按当天投入时长分档 · 没填分钟记 1 档 · 点方块看当天明细</span></div>' +
      '<div id="npgDashHCtl">' + heatCtlHtml() + '</div>' +
      '<div class="npg-heat-scroll" id="npgDashHeat"></div>' +
      '<div class="npg-heat-foot">' +
        '<div class="npg-heat-stats" id="npgDashHeatStats">' + heatStatsHtml('all', heatMode) + '</div>' +
        '<div class="npg-heat-legend" id="npgDashHeatLegend">' + heatLegendHtml('all') + '</div>' +
      '</div>';
  }

  /* 组件 · 近 14 天投入（柱状图内容由 paintChart 在显隐确定后补画） */
  function wChart(host) {
    if (!st.goals.length) {
      host.innerHTML = '<div class="npg-whd">近 14 天投入时长</div><div class="npg-empty">先建一个目标。</div>';
      return;
    }
    host.innerHTML = '<div class="npg-whd">近 14 天投入时长' +
      '<span class="npg-hint-inline">分钟 · 分目标堆叠 · 只统计填了用时的打卡</span></div>' +
      '<div class="npg-chart-scroll" id="npgDashChart"></div>' +
      '<div class="npg-legend" id="npgDashLegend"></div>';
  }

  /* 组件 · 本周概览（与「周报」页签同源，这里只做只读摘要） */
  function wWeekly(host) {
    var r = weekRange(), cur = weekStats(r.start, r.end), prev = weekStats(addDays(r.start, -7), addDays(r.start, -1));
    var i, any = false;
    var html = '<div class="npg-whd">本周概览' +
      '<span class="npg-hint-inline">' + fmtShort(r.start) + ' - ' + fmtShort(r.end) + ' · 与上周对比</span></div>';
    for (i = 0; i < st.goals.length; i++) {
      var g = st.goals[i];
      var c = cur.per[g._id] || { sum: 0, days: 0, mins: 0 }, p = prev.per[g._id] || { sum: 0, days: 0, mins: 0 };
      if (c.sum > 0 || c.days > 0) { any = true; }
      if (isComplete(g) && c.days === 0) { continue; }
      html += '<div class="npg-wrow2"><span class="npg-hdot" style="background:' + g.color + '"></span>' +
        '<div class="npg-wmain"><div class="npg-wname"><span class="nm">' + esc(g.name) + '</span></div>' +
        '<div class="npg-wmeta">' + (c.sum > 0 ? '+' + trimNum(c.sum) + ' ' + esc(g.unit) + ' · ' : '') +
          '打卡 <b class="npg-num">' + c.days + '</b> 天' + (c.mins > 0 ? ' · ' + c.mins + ' 分钟' : '') +
          ' · ' + ((p.sum > 0 || p.days > 0) ? '上周 ' + p.days + ' 天' : '上周无记录') +
        '</div></div></div>';
    }
    if (!any) {
      html += '<div class="npg-empty">这一周还没有打卡记录</div>';
    } else {
      var dm = cur.totalMin - prev.totalMin;
      html += '<div class="npg-wtotal">合计投入 <b class="npg-num">' + cur.totalMin + '</b> 分钟' +
        (prev.totalMin > 0 ? ' · 上周 ' + prev.totalMin + ' 分钟' + (dm !== 0 ? '（' + (dm > 0 ? '+' : '') + dm + '）' : '') : '') +
        '</div>';
    }
    html += '<div class="npg-wfoot"><button class="npg-btn ghost wide" type="button" data-npg-tab="weekly">' +
      '去写本周复盘 →</button></div>';
    host.innerHTML = html;
  }

  /* 组件 · 最近打卡（按录入时间倒序，最多 8 条 —— 用来确认「刚才那一笔记上了没」） */
  function wRecent(host) {
    var rows = [], i, l;
    for (i = 0; i < st.logs.length; i++) {
      l = st.logs[i];
      if (l && l.removed !== 1 && isDate(l.date)) { rows.push(l); }
    }
    rows.sort(function (a, b) {
      var ta = Number(a.ts) || 0, tb = Number(b.ts) || 0;
      if (ta !== tb) { return tb - ta; }
      return a.date < b.date ? 1 : (a.date > b.date ? -1 : 0);
    });
    if (!rows.length) {
      host.innerHTML = '<div class="npg-whd">最近打卡</div><div class="npg-empty">还没有打卡记录。</div>';
      return;
    }
    var html = '<div class="npg-whd">最近打卡' +
      '<span class="npg-hint-inline">按录入时间倒序 · 最多 8 条</span></div>', shown = 0;
    for (i = 0; i < rows.length && shown < 8; i++) {
      l = rows[i];
      var g = goalById(l.goalId);
      if (!g) { continue; }
      shown++;
      html += '<div class="npg-wlog">' +
        '<span class="dt npg-num">' + fmtShort(l.date) + '</span>' +
        '<span class="npg-hdot" style="background:' + g.color + '"></span>' +
        '<span class="nm">' + esc(g.name) + '</span>' +
        '<span class="vals">+' + trimNum(Number(l.amount) || 0) + ' ' + esc(g.unit) +
          (l.minutes ? ' · ' + Number(l.minutes) + ' 分钟' : '') + '</span>' +
        (l.makeup ? '<span class="npg-badge makeup">补</span>' : '') +
      '</div>';
    }
    host.innerHTML = html;
  }

  /* 组件 · 规则速查（把散在各处的口径写在一处，省得用户自己猜） */
  function wGuide(host) {
    host.innerHTML = '<div class="npg-whd">规则速查' +
      '<span class="npg-hint-inline">这套算法怎么算的，写一次就不用再猜</span></div>' +
      '<ul class="npg-wguide">' +
        '<li><b>今日建议量</b> = 剩余量 ÷ 剩余天数（含今天）；已逾期就按剩余全部算</li>' +
        '<li><b>休息日</b>：本周内第一次断档自动算休息日，不扣连续天数（之后仍可补记）</li>' +
        '<li><b>补记窗口</b>：最近 6 天，与打卡校验同一口径，更早的日期不再开放</li>' +
        '<li><b>方块分档</b>：按当天投入分钟分 4 档；没填分钟的记 1 档 —— 打过卡就有颜色</li>' +
        '<li><b>在线同步</b>：目标 / 打卡 / 周复盘存在角色名下自动推送；卡片布局只存在本机</li>' +
      '</ul>';
  }

  /* 面板 / 页签的显隐先定下来 —— 后面各渲染函数要按容器实测宽度排版，藏着时量不准 */
  function syncPanels() {
    var panels = document.querySelectorAll('.npg-panel'), i;
    for (i = 0; i < panels.length; i++) {
      panels[i].className = 'npg-panel' + (panels[i].id === 'npgPanel-' + tab ? ' on' : '');
    }
    var tabs = document.querySelectorAll('.npg-subtab');
    for (i = 0; i < tabs.length; i++) {
      tabs[i].className = 'npg-subtab' + (tabs[i].getAttribute('data-npg-tab') === tab ? ' on' : '');
    }
  }
  /* ── 渲染总入口（各渲染函数之间不互相调用，统一由这里按固定顺序调度） ── */
  function render() {
    syncPanels();
    renderDash();          /* 首页仪表盘：卡片外壳 + 各组件内容（必须在 syncPanels 之后量宽度） */
    renderHome();
    renderStats();
    renderToday();
    renderBoard();
    renderWeekly();
    renderMine();
    paintHeats();          /* 收口：三处方块图 + 两处柱状图，等显隐确定之后再画 */
  }

  /* ── 彩带 ───────────────────────────────────────────────────────────── */
  function celebrate() {
    try {
      if (window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches) { return; }
    } catch (e) { /* 不支持就照常放 */ }
    var colors = COLORS.concat(['#ffd166', '#2fd3a4']);
    for (var i = 0; i < 64; i++) {
      var b = document.createElement('div');
      b.className = 'npg-confetti';
      var size = 6 + Math.random() * 8;
      b.style.left = (Math.random() * 100) + 'vw';
      b.style.width = size + 'px';
      b.style.height = (size * 0.5) + 'px';
      b.style.background = colors[i % colors.length];
      b.style.animationDuration = (1.4 + Math.random() * 1.4) + 's';
      b.style.animationDelay = (Math.random() * 0.4) + 's';
      document.body.appendChild(b);
      (function (el) { setTimeout(function () { if (el.parentNode) { el.parentNode.removeChild(el); } }, 3200); })(b);
    }
  }

  /* ── 行为 ───────────────────────────────────────────────────────────── */
  function doCheckin(id) {
    var g = goalById(id);
    if (!g) { return; }
    var amtEl = $('npgAmt-' + id), minEl = $('npgMin-' + id), dateEl = $('npgDate-' + id);
    var amt = Number(amtEl ? amtEl.value : NaN);
    if (!fin(amt, 0.000000001, 1000000000000)) { toast('完成数量需为有效正数'); if (amtEl) { amtEl.focus(); } return; }
    var mins = (minEl && minEl.value !== '') ? Number(minEl.value) : null;
    if (mins !== null && !fin(mins, 0, 1440)) { toast('用时需为 0–1440 分钟'); minEl.focus(); return; }
    var date = (dateEl && dateEl.value) ? dateEl.value : todayStr();
    if (date < addDays(todayStr(), -6) || date > todayStr()) { date = todayStr(); }
    var wasDone = isComplete(g);
    var rec = {
      _id: uid(), goalId: id, date: date, amount: amt,
      minutes: (mins === null ? null : mins), ts: Date.now(), makeup: date < todayStr()
    };
    st.logs.push(rec);
    persist();
    render();
    if (!wasDone && isComplete(g)) {
      celebrate();
      toast('目标「' + g.name + '」达成，恭喜！');
    } else if (rec.makeup) {
      toast('已补记 ' + fmtShort(date) + '：+' + trimNum(amt) + ' ' + g.unit);
    } else {
      toast('已打卡 +' + trimNum(amt) + ' ' + g.unit);
    }
  }
  /* 首页一键打卡：直接用页面算好的「今日建议量」，不让用户再填表。
     要改数量 / 补记 / 记分钟，点旁边的「去打卡」进视图里打 —— 两处写的是同一份数据。 */
  function quickCheckin(id) {
    var g = goalById(id);
    if (!g) { return; }
    var amt = dailySuggest(g);
    if (!(amt > 0)) { toast('该目标今天没有待完成的剩余量'); return; }
    var wasDone = isComplete(g);
    st.logs.push({
      _id: uid(), goalId: id, date: todayStr(), amount: amt,
      minutes: null, ts: Date.now(), makeup: false
    });
    persist();
    render();
    if (!wasDone && isComplete(g)) {
      celebrate();
      toast('目标「' + g.name + '」达成，恭喜！');
    } else {
      toast('已打卡 +' + trimNum(amt) + ' ' + g.unit);
    }
  }

  /* 从首页跳到「学习目标」视图并定位到某个目标 */
  function openGoalView(id) {
    showGoals(true);        /* 只切视图，渲染交给下面的 switchTab 统一做，避免重绘两次 */
    switchTab('today');
    var row = $('npgRow-' + id);
    if (row && row.scrollIntoView) {
      try { row.scrollIntoView({ block: 'center' }); } catch (e) { row.scrollIntoView(); }
    }
  }

  function armConfirm(btn, label, action) {
    if (btn.getAttribute('data-armed') === '1') { btn.removeAttribute('data-armed'); action(); return; }
    btn.setAttribute('data-armed', '1');
    var old = btn.innerHTML;
    btn.innerHTML = label;
    btn.className = btn.className + ' confirming';
    setTimeout(function () {
      if (btn.getAttribute('data-armed')) {
        btn.removeAttribute('data-armed');
        btn.innerHTML = old;
        btn.className = btn.className.replace(' confirming', '');
      }
    }, 3000);
  }
  function openModal(g) {
    lastFocus = document.activeElement;
    editingId = g ? g._id : null;
    $('npgModalTitle').textContent = g ? '编辑学习目标' : '新建学习目标';
    $('npgFName').value = g ? g.name : '';
    $('npgFUnit').value = g ? g.unit : '';
    $('npgFTotal').value = g ? g.total : '';
    $('npgFDeadline').value = g ? g.deadline : addDays(todayStr(), 30);
    $('npgFObstacle').value = g ? (g.obstacle || '') : '';
    $('npgFPlan').value = g ? (g.plan || '') : '';
    pickedColor = g ? g.color : COLORS[st.goals.length % COLORS.length];
    var cp = '';
    for (var i = 0; i < COLORS.length; i++) {
      cp += '<button type="button" data-npg-color="' + COLORS[i] + '" style="background:' + COLORS[i] + '" class="' + (COLORS[i] === pickedColor ? 'sel' : '') + '" aria-label="选择配色"></button>';
    }
    $('npgColors').innerHTML = cp;
    $('npgModal').className = 'npg-mask on';
    setTimeout(function () { $('npgFName').focus(); }, 0);
  }
  function closeModal() {
    $('npgModal').className = 'npg-mask';
    if (lastFocus && document.contains(lastFocus)) { lastFocus.focus(); }
    lastFocus = null;
  }
  function saveModal() {
    var name = $('npgFName').value.trim(), unit = $('npgFUnit').value.trim() || '个';
    var total = Number($('npgFTotal').value), dl = $('npgFDeadline').value;
    if (!name) { toast('请填写目标名称'); $('npgFName').focus(); return; }
    if (!fin(total, 0.000000001, 1000000000000)) { toast('总量需为有效正数'); $('npgFTotal').focus(); return; }
    if (!isDate(dl)) { toast('请选择有效截止日'); $('npgFDeadline').focus(); return; }
    if (!editingId && dl < todayStr()) { toast('截止日不能早于今天'); return; }
    var obstacle = $('npgFObstacle').value.trim(), plan = $('npgFPlan').value.trim();
    if (editingId) {
      var g = goalById(editingId);
      g.name = name; g.unit = unit; g.total = total; g.deadline = dl; g.color = pickedColor;
      g.obstacle = obstacle; g.plan = plan;
      toast('目标已更新');
    } else {
      st.goals.push({ _id: uid(), name: name, unit: unit, total: total, deadline: dl, color: pickedColor, createdAt: todayStr(), obstacle: obstacle, plan: plan });
      toast('已创建目标「' + name + '」');
    }
    persist();
    closeModal();
    render();
  }
  function clearSamples() {
    var had = 0;
    for (var i = 0; i < st.goals.length; i++) {
      if (isSample(st.goals[i])) { had++; markRemoved('goals', st.goals[i]._id); }
    }
    for (i = 0; i < st.logs.length; i++) {
      if (SAMPLE_IDS.indexOf(st.logs[i].goalId) >= 0) { markRemoved('logs', st.logs[i]._id); }
    }
    load();
    render();
    toast(had > 0 ? '已清空示例数据' : '没有示例数据需要清理');
  }
  function clearAll() {
    var v = window.prompt('将清空这个角色下的全部学习目标、打卡记录与周复盘，且不可恢复。\n请输入「清空」二字确认：');
    if (v === null) { return; }
    if (v !== '清空') { toast('未输入「清空」，已取消'); return; }
    for (var i = 0; i < st.goals.length; i++) { markRemoved('goals', st.goals[i]._id); }
    for (i = 0; i < st.logs.length; i++) { markRemoved('logs', st.logs[i]._id); }
    st.notes = {};
    writeKey(K.notes, {});
    writeKey(SEEDED_KEY, 1);
    load();
    render();
    toast('已清空全部学习目标数据');
  }

  /* ── 视图切换：自给自足 ────────────────────────────────────────────────
     看板 app.js 的导航点击是「初始化时一次性绑定」的（见 app.js 末尾那段
     querySelectorAll('.nav-item') 循环），而本视图的导航按钮是后注入的，
     拿不到它的 onclick；app.js 的 VIEWS 白名单也不含 'goals'，刷新后不会
     帮我们恢复。所以这里自己绑、自己切、自己恢复，不依赖看板内部实现。 */
  function viewKey() {
    try { return ROLES.key ? ROLES.key('view') : 'kust.tasks.v2.view'; }
    catch (e) { return 'kust.tasks.v2.view'; }
  }

  function showGoals(skipRender) {
    var views = document.querySelectorAll('.view');
    for (var i = 0; i < views.length; i++) {
      views[i].className = (views[i].id === 'view-goals') ? 'view active' : 'view';
    }
    var navs = document.querySelectorAll('nav .nav-item');
    for (var j = 0; j < navs.length; j++) {
      navs[j].className = (navs[j].getAttribute('data-view') === 'goals') ? 'nav-item active' : 'nav-item';
    }
    try { localStorage.setItem(viewKey(), 'goals'); } catch (e) { }
    if (!skipRender) { load(); render(); }
    try { window.scrollTo(0, 0); } catch (e) { }
  }

  function restoreView() {
    var saved = '';
    try { saved = localStorage.getItem(viewKey()) || ''; } catch (e) { }
    if (saved === 'goals') { showGoals(true); }
  }

  function isGoalsVisible() {
    var v = $('view-goals');
    return !!(v && v.className.indexOf('active') >= 0);
  }

  /* ── 骨架注入 ───────────────────────────────────────────────────────── */
  function injectUi() {
    /* 侧栏顺序：学习目标排在「RPG 时间轴」之后，即第二位。
       不写死 appendChild —— 看板以后再加视图也不会把它挤到末尾；
       拿不到第一个 nav-item 才退回追加。 */
    var nav = document.querySelector('nav');
    if (nav && !document.querySelector('[data-view="goals"]')) {
      var b = document.createElement('button');
      b.className = 'nav-item';
      b.type = 'button';
      b.setAttribute('data-view', 'goals');
      b.innerHTML = ICON.target + '学习目标';
      b.onclick = function (e) { if (e && e.preventDefault) { e.preventDefault(); } showGoals(); };
      var first = nav.querySelector('.nav-item');
      nav.insertBefore(b, first ? first.nextSibling : null);
    }
    var main = document.querySelector('main.main') || document.querySelector('main');
    if (main && !$('view-goals')) {
      var v = document.createElement('div');
      v.className = 'view';
      v.id = 'view-goals';
      v.innerHTML =
        '<div class="view-head"><h2>学习目标</h2><p class="view-sub">有终点、有总量的目标进度追踪 · 今日建议量 = 剩余量 ÷ 剩余天数</p></div>' +
        '<div class="npg-strip" id="npgStats"></div>' +
        '<div class="npg-subtabs" role="tablist">' +
          '<button class="npg-subtab" type="button" role="tab" data-npg-tab="home">首页</button>' +
          '<button class="npg-subtab" type="button" role="tab" data-npg-tab="today">今日</button>' +
          '<button class="npg-subtab" type="button" role="tab" data-npg-tab="board">看板</button>' +
          '<button class="npg-subtab" type="button" role="tab" data-npg-tab="weekly">周报</button>' +
          '<button class="npg-subtab" type="button" role="tab" data-npg-tab="mine">我的</button>' +
        '</div>' +
        /* 首页：可自定义的工作仪表盘。卡片由 renderDash() 按本机布局拼装，
           组件清单在 DASH_WIDGETS —— 每个组件都是一个独立的叶子渲染函数。 */
        '<div class="npg-panel" id="npgPanel-home">' +
          '<div class="npg-dash" id="npgDash">' +
            '<div class="npg-dash-head">' +
              '<div class="npg-dash-lead"><b>工作仪表盘</b>' +
                '<span class="npg-muted">卡片顺序、宽度、收不收都由你定 —— 改完就记住（存在这台设备上）</span></div>' +
              '<button class="npg-btn" type="button" id="npgDashBtn">' + ICON.sliders + '自定义卡片</button>' +
            '</div>' +
            '<div class="npg-dash-tip" id="npgDashHint" style="display:none">编辑中：' +
              '↑ ↓ 调顺序 · 方框按钮切整宽/半宽 · 减号收起卡片。此时卡内按钮暂时不可点，改完点「完成」。</div>' +
            '<div class="npg-dash-grid" id="npgDashGrid"></div>' +
            '<div class="npg-shelf" id="npgDashShelf" style="display:none"></div>' +
          '</div>' +
        '</div>' +
        '<div class="npg-panel" id="npgPanel-today">' +
          '<div class="npg-card"><button class="npg-btn primary wide" type="button" id="npgAddTop">' + ICON.plus + '新建学习目标</button>' +
            '<div class="npg-muted" style="margin-top:8px">有终点、有总量才算目标（背完 2000 个单词 / 读完 440 页的书）；' +
            '每天喝水这类无限期习惯不在这个视图里。</div></div>' +
          '<div id="npgZone"></div>' +
          '<div id="npgList" class="npg-grid"></div>' +
          '<div id="npgDone"></div>' +
          '<button class="npg-btn ghost wide" type="button" id="npgAddBottom" style="margin-top:14px">' + ICON.plus + '新建学习目标</button>' +
        '</div>' +
        '<div class="npg-panel" id="npgPanel-board">' +
          '<div class="npg-grid" id="npgBoardGrid"></div>' +
          /* 宽屏这两张并排（否则各自独立居中，一张 1335 一张 680，中间空一大片）；
             窄屏照旧上下堆叠。并排与否由容器宽度决定（container query），
             不认视口宽度 —— 同一个视口，门户里外可用的宽度能差一倍。 */
          '<div class="npg-duo">' +
            '<div class="npg-card" id="npgDuoHeat"><div class="npg-card-title"><span class="npg-dot"></span>每日打卡方块' +
              '<span class="npg-hint-inline">按当天投入时长分档 · 没填分钟记 1 档 · 点方块看当天明细</span></div>' +
              '<div class="npg-hfilts" id="npgHeatFilt"></div>' +
              '<div id="npgHeatCtl"></div>' +
              '<div class="npg-heat-scroll" id="npgHeat"></div>' +
              '<div class="npg-heat-foot">' +
                '<div class="npg-heat-stats" id="npgHeatStats"></div>' +
                '<div class="npg-heat-legend" id="npgHeatLegend"></div>' +
              '</div></div>' +
            '<div class="npg-card" id="npgDuoChart"><div class="npg-card-title"><span class="npg-dot"></span>近 14 天投入时长' +
              '<span class="npg-hint-inline">分钟 · 分目标堆叠 · 只统计填了用时的打卡</span></div>' +
              '<div class="npg-chart-scroll" id="npgChart"></div><div class="npg-legend" id="npgLegend"></div></div>' +
          '</div>' +
        '</div>' +
        '<div class="npg-panel" id="npgPanel-weekly">' +
          '<div class="npg-card">' +
            '<div class="npg-weeknav">' +
              '<button class="npg-btn ghost" type="button" id="npgWeekPrev">上一周</button>' +
              '<span class="lbl" id="npgWeekLabel"></span>' +
              '<button class="npg-btn ghost" type="button" id="npgWeekNext">下一周</button>' +
            '</div><div id="npgWeekSummary"></div>' +
          '</div>' +
          '<div class="npg-card"><div class="npg-card-title"><span class="npg-dot amber"></span>本周复盘</div>' +
            '<div class="npg-row2">' +
              '<div class="npg-field"><label for="npgKeep">保持 Keep</label><input type="text" maxlength="100" id="npgKeep" placeholder="做得好的，下周继续"></div>' +
              '<div class="npg-field"><label for="npgProblem">问题 Problem</label><input type="text" maxlength="100" id="npgProblem" placeholder="卡在哪了"></div>' +
              '<div class="npg-field"><label for="npgTry">尝试 Try</label><input type="text" maxlength="100" id="npgTry" placeholder="下周试试这样改"></div>' +
              '<div class="npg-field"><label for="npgNext">下周预案</label><input type="text" maxlength="100" id="npgNext" placeholder="如果…我就…"></div>' +
            '</div>' +
            '<div class="npg-actions">' +
              '<button class="npg-btn ghost" type="button" id="npgSaveNote">保存复盘</button>' +
              '<button class="npg-btn primary" type="button" id="npgGenReport">生成周报文本</button></div>' +
            '<div id="npgReportBox" style="display:none"><div class="npg-report" id="npgReport"></div>' +
              '<button class="npg-btn ok wide" type="button" id="npgCopyReport" style="margin-top:10px">一键复制周报</button></div>' +
          '</div>' +
        '</div>' +
        '<div class="npg-panel" id="npgPanel-mine">' +
          '<div class="npg-card" id="npgSyncBox"></div>' +
          '<div class="npg-card"><div class="npg-card-title"><span class="npg-dot"></span>数据管理</div>' +
            '<div class="npg-muted" style="margin-bottom:10px">示例数据是首次打开时预置的 3 个目标与记录，覆盖补记 / 休息日 / 连续漏打 / 预案四种状态，可直接删。</div>' +
            '<button class="npg-btn wide" type="button" id="npgClearSamples">清空示例数据</button>' +
            '<button class="npg-btn danger wide" type="button" id="npgClearAll" style="margin-top:10px">清空全部数据</button></div>' +
          '<div class="npg-card"><div class="npg-card-title"><span class="npg-dot"></span>装到手机桌面，天天用</div>' +
            '<div class="npg-muted">1. 用手机浏览器打开本页<br>2. 点浏览器「分享」<br>3. 选「添加到主屏幕」，桌面出现图标，点开即用</div></div>' +
        '</div>';
      main.appendChild(v);
    }
    if (!document.querySelector('.npg-mask')) {
      var m = document.createElement('div');
      m.className = 'npg-mask';
      m.id = 'npgModal';
      m.innerHTML = '<div class="npg-modal" role="dialog" aria-modal="true" aria-labelledby="npgModalTitle">' +
        '<h3 id="npgModalTitle">新建学习目标</h3>' +
        '<div class="npg-field"><label for="npgFName">目标名称</label><input type="text" maxlength="30" id="npgFName" placeholder="例：背英语单词 / 读完《人类简史》"></div>' +
        '<div class="npg-row2">' +
          '<div class="npg-field"><label for="npgFUnit">计量单位</label><input type="text" maxlength="6" id="npgFUnit" placeholder="个 / 页 / 节 / 章"></div>' +
          '<div class="npg-field"><label for="npgFTotal">总量</label><input type="number" step="any" min="0" inputmode="decimal" id="npgFTotal" placeholder="例：2000"></div>' +
        '</div>' +
        '<div class="npg-field"><label for="npgFDeadline">截止日</label><input type="date" id="npgFDeadline"></div>' +
        '<div class="npg-field"><label for="npgFObstacle">最容易拦住你的障碍（选填）</label><input type="text" maxlength="60" id="npgFObstacle" placeholder="例：睡前一躺下就想刷手机"></div>' +
        '<div class="npg-field"><label for="npgFPlan">如果它出现，我就（选填）</label><input type="text" maxlength="60" id="npgFPlan" placeholder="例：把书放在枕头上，先读 2 页再看手机"></div>' +
        '<div class="npg-field"><label>配色</label><div class="npg-colors" id="npgColors"></div></div>' +
        '<div class="npg-actions"><button class="npg-btn ghost" type="button" id="npgModalCancel">取消</button>' +
        '<button class="npg-btn primary" type="button" id="npgModalSave">保存目标</button></div></div>';
      document.body.appendChild(m);
    }
    if (!$('npgToast')) {
      var t = document.createElement('div');
      t.className = 'npg-toast';
      t.id = 'npgToast';
      t.setAttribute('role', 'status');
      document.body.appendChild(t);
    }
    if (!$('npgTip')) {
      var tp = document.createElement('div');
      tp.className = 'npg-tip';
      tp.id = 'npgTip';
      tp.setAttribute('role', 'tooltip');
      tp.innerHTML = '<b id="npgTipA"></b><span id="npgTipB"></span>';
      document.body.appendChild(tp);
    }
    if (!$('npgDay')) {
      var dm = document.createElement('div');
      dm.className = 'npg-mask';
      dm.id = 'npgDay';
      dm.innerHTML = '<div class="npg-modal npg-day" role="dialog" aria-modal="true" aria-labelledby="npgDayTitle">' +
        '<h3 id="npgDayTitle">这天的打卡</h3>' +
        '<div class="npg-day-list" id="npgDayBody"></div>' +
        '<div class="npg-day-note" id="npgDayNote"></div>' +
        '<div class="npg-actions"><button class="npg-btn ghost wide" type="button" id="npgDayClose">关闭</button></div>' +
        '</div>';
      document.body.appendChild(dm);
    }
    injectHome();
  }

  /* ── 首页面板：挂到 RPG 时间轴视图里「任务时间轴」卡片的下方 ─────────────
     为什么插在卡片之间而不是视图末尾：用户要的就是「时间轴首页也能看到学习目标」，
     放在时间轴正下方一眼能看见，不用往下滚。
     为什么不会被看板冲掉：#timeline 内部由 app.js 的 renderTimeline() 重写，
     而这张 <section class="card"> 本身是静态 HTML，app.js 不碰它。 */
  function injectHome() {
    var view = $('view-rpg');
    if (!view || $('npgHome')) { return; }
    var tl = $('timeline');
    var anchor = null;
    if (tl && tl.closest) {
      anchor = tl.closest('section.card') || tl.closest('section');
    }
    var sec = document.createElement('section');
    sec.className = 'card npg-home';
    sec.id = 'npgHome';
    sec.innerHTML =
      '<div class="card-title"><span class="emoji">🎯</span>学习目标' +
      '<span class="hint">与侧栏「学习目标」同一份数据 · 今日建议量 = 剩余量 ÷ 剩余天数</span></div>' +
      '<div id="npgHomeBody"></div>';
    if (anchor && anchor.parentNode === view) {
      view.insertBefore(sec, anchor.nextSibling);
    } else if (view.firstChild) {
      view.insertBefore(sec, view.firstChild);
    } else {
      view.appendChild(sec);
    }
  }

  /* ── 事件 ───────────────────────────────────────────────────────────── */
  function switchTab(name) { tab = name; render(); }
  function bind() {
    document.addEventListener('click', function (e) {
      /* 编辑态下卡内一律不响应 —— CSS 已经挡了 pointer-events，这里再挡一道：
         免得将来某处给组件加了内联 pointer-events 就漏进一次误打卡。 */
      if (dashEdit && e.target.closest && e.target.closest('.npg-w-body')) { return; }
      var t = e.target.closest ? e.target.closest('[data-npg-tab]') : null;
      if (t) { switchTab(t.getAttribute('data-npg-tab')); return; }

      /* 仪表盘：编辑开关 / 上下移 / 整宽-半宽 / 收起 / 放回 / 恢复默认 */
      if (e.target.closest && e.target.closest('#npgDashBtn')) { dashEdit = !dashEdit; dashRefresh(); return; }
      var wb = e.target.closest ? e.target.closest('[data-npg-wup]') : null;
      if (wb) { dashMove(wb.getAttribute('data-npg-wup'), -1); dashRefresh(); return; }
      wb = e.target.closest ? e.target.closest('[data-npg-wdown]') : null;
      if (wb) { dashMove(wb.getAttribute('data-npg-wdown'), 1); dashRefresh(); return; }
      wb = e.target.closest ? e.target.closest('[data-npg-wwide]') : null;
      if (wb) { dashToggleWide(wb.getAttribute('data-npg-wwide')); dashRefresh(); return; }
      wb = e.target.closest ? e.target.closest('[data-npg-whide]') : null;
      if (wb) {
        var hname = dashMeta(wb.getAttribute('data-npg-whide'));
        dashHide(wb.getAttribute('data-npg-whide'));
        dashRefresh();
        toast('已收起「' + (hname ? hname.name : '') + '」，可在下方放回来');
        return;
      }
      wb = e.target.closest ? e.target.closest('[data-npg-wshow]') : null;
      if (wb) { dashShow(wb.getAttribute('data-npg-wshow')); dashRefresh(); return; }
      if (e.target.closest && e.target.closest('[data-npg-wreset]')) {
        dashReset(); dashRefresh(); toast('已恢复默认布局'); return;
      }
      if (e.target.closest && e.target.closest('#npgAddTop')) { openModal(null); return; }
      if (e.target.closest && e.target.closest('#npgAddBottom')) { openModal(null); return; }
      var el = e.target.closest ? e.target.closest('[data-npg-checkin]') : null;
      if (el) { doCheckin(el.getAttribute('data-npg-checkin')); return; }
      el = e.target.closest ? e.target.closest('[data-npg-goto]') : null;
      if (el) {
        switchTab('today');
        var row = $('npgRow-' + el.getAttribute('data-npg-goto'));
        if (row) { row.scrollIntoView({ block: 'center' }); }
        return;
      }
      /* 首页面板（RPG 时间轴下方那张卡）的四个入口 */
      el = e.target.closest ? e.target.closest('[data-npg-quick]') : null;
      if (el) { quickCheckin(el.getAttribute('data-npg-quick')); return; }
      el = e.target.closest ? e.target.closest('[data-npg-open]') : null;
      if (el) { openGoalView(el.getAttribute('data-npg-open')); return; }
      if (e.target.closest && e.target.closest('[data-npg-openall]')) { showGoals(); return; }
      if (e.target.closest && e.target.closest('[data-npg-hadd]')) { openModal(null); return; }

      /* 打卡方块：切年/月视图、翻月份、筛选目标 / 点一格看当天明细 / 从明细里补记或删记录 */
      el = e.target.closest ? e.target.closest('[data-npg-hmode]') : null;
      if (el) {
        var hm = el.getAttribute('data-npg-hmode');
        if (hm !== heatMode) { heatMode = hm; heatPinned = true; refreshHeatViews(); }
        return;
      }
      if (e.target.closest && e.target.closest('[data-npg-hprev]')) {
        heatMonth = shiftMonth(monthKey(), -1); refreshHeatViews(); return;
      }
      if (e.target.closest && e.target.closest('[data-npg-hnext]')) {
        var hnx = shiftMonth(monthKey(), 1);
        if (hnx <= thisMonth()) { heatMonth = hnx; refreshHeatViews(); }   /* 不许翻到未来 */
        return;
      }
      if (e.target.closest && e.target.closest('[data-npg-hnow]')) {
        heatMonth = thisMonth(); refreshHeatViews(); return;
      }
      el = e.target.closest ? e.target.closest('[data-npg-hfilt]') : null;
      if (el) { heatGoal = el.getAttribute('data-npg-hfilt'); renderHeat(); paintHeats(); return; }
      el = e.target.closest ? e.target.closest('[data-npg-heat]') : null;
      if (el) { openDay(el.getAttribute('data-npg-heat')); return; }
      el = e.target.closest ? e.target.closest('[data-npg-makeup]') : null;
      if (el) {
        var mp = (el.getAttribute('data-npg-makeup') || '').split('|');
        goMakeup(mp[0], mp[1]);
        return;
      }
      el = e.target.closest ? e.target.closest('[data-npg-daydel]') : null;
      if (el) {
        var dp = (el.getAttribute('data-npg-daydel') || '').split('|');
        var ids = [], q;
        for (q = 0; q < st.logs.length; q++) {
          if (st.logs[q].goalId === dp[1] && st.logs[q].date === dp[0] && st.logs[q].removed !== 1) { ids.push(st.logs[q]._id); }
        }
        if (!ids.length) { toast('这天的记录已经不在了'); renderDay(dp[0]); return; }
        armConfirm(el, '确认删除？', function () {
          for (var w = 0; w < ids.length; w++) { markRemoved('logs', ids[w]); }
          load();
          render();
          renderDay(dp[0]);
          toast('已删除 ' + fmtShort(dp[0]) + ' 的 ' + ids.length + ' 条记录');
        });
        return;
      }
      if (e.target.closest && e.target.closest('#npgDayClose')) { closeDay(); return; }
      if (e.target === $('npgDay')) { closeDay(); return; }

      el = e.target.closest ? e.target.closest('[data-npg-toggle]') : null;
      if (el) {
        var gid = el.getAttribute('data-npg-toggle');
        expanded[gid] = !expanded[gid];
        render();
        return;
      }
      el = e.target.closest ? e.target.closest('[data-npg-dellog]') : null;
      if (el) {
        var lid = el.getAttribute('data-npg-dellog');
        armConfirm(el, '确认？', function () {
          markRemoved('logs', lid);
          load(); render();
          toast('已删除该条记录');
        });
        return;
      }
      el = e.target.closest ? e.target.closest('[data-npg-edit]') : null;
      if (el) { openModal(goalById(el.getAttribute('data-npg-edit'))); return; }
      el = e.target.closest ? e.target.closest('[data-npg-del]') : null;
      if (el) {
        var did = el.getAttribute('data-npg-del');
        armConfirm(el, '确认删除？', function () {
          var g = goalById(did);
          markRemoved('goals', did);
          for (var i = 0; i < st.logs.length; i++) { if (st.logs[i].goalId === did) { markRemoved('logs', st.logs[i]._id); } }
          load(); render();
          toast('已删除「' + (g ? g.name : '') + '」及其全部记录');
        });
        return;
      }
      el = e.target.closest ? e.target.closest('[data-npg-color]') : null;
      if (el) {
        pickedColor = el.getAttribute('data-npg-color');
        var bs = $('npgColors').querySelectorAll('button');
        for (var i = 0; i < bs.length; i++) {
          bs[i].className = bs[i].getAttribute('data-npg-color') === pickedColor ? 'sel' : '';
        }
        return;
      }
      if (e.target.closest && e.target.closest('#npgModalCancel')) { closeModal(); return; }
      if (e.target.closest && e.target.closest('#npgModalSave')) { saveModal(); return; }
      if (e.target.closest && e.target.closest('#npgWeekPrev')) { weekOff--; renderWeekly(); return; }
      if (e.target.closest && e.target.closest('#npgWeekNext')) { if (weekOff < 0) { weekOff++; renderWeekly(); } return; }
      if (e.target.closest && e.target.closest('#npgSaveNote')) {
        var r = weekRange();
        st.notes[r.start] = { keep: $('npgKeep').value.trim(), problem: $('npgProblem').value.trim(), try: $('npgTry').value.trim(), next: $('npgNext').value.trim() };
        persist();
        toast('复盘已保存');
        return;
      }
      if (e.target.closest && e.target.closest('#npgGenReport')) {
        var r2 = weekRange();
        st.notes[r2.start] = { keep: $('npgKeep').value.trim(), problem: $('npgProblem').value.trim(), try: $('npgTry').value.trim(), next: $('npgNext').value.trim() };
        persist();
        $('npgReport').textContent = buildReport();
        $('npgReportBox').style.display = 'block';
        return;
      }
      if (e.target.closest && e.target.closest('#npgCopyReport')) { copyText($('npgReport').textContent); return; }
      if (e.target.closest && e.target.closest('#npgClearSamples')) {
        armConfirm(e.target.closest('#npgClearSamples'), '确认清空示例？', clearSamples);
        return;
      }
      if (e.target.closest && e.target.closest('#npgClearAll')) { clearAll(); return; }
      if (e.target.closest && e.target.closest('#npgResync')) {
        toast('正在与角色数据同步…');
        setTimeout(function () { location.reload(); }, 350);
        return;
      }
      if (e.target === $('npgModal')) { closeModal(); return; }
    });

    document.addEventListener('keydown', function (e) {
      if (e.key === 'Escape') {
        if ($('npgModal') && $('npgModal').className.indexOf('on') >= 0) { closeModal(); return; }
        if ($('npgDay') && $('npgDay').className.indexOf('on') >= 0) { closeDay(); return; }
      }
      if (e.key === 'Enter' && e.target && e.target.id && e.target.id.indexOf('npgAmt-') === 0) {
        doCheckin(e.target.id.slice(7));
      }
    });

    /* 打卡方块的悬浮提示：mouseover 显示 / mousemove 跟随 / mouseout 与页面滚动时收起
       （滚动不收起的话，提示会停在原地，看着像「卡住了」） */
    document.addEventListener('mouseover', function (e) {
      var el = e.target.closest ? e.target.closest('[data-npg-heat]') : null;
      var box = $('npgTip');
      if (!el || !box) { return; }
      $('npgTipA').textContent = el.getAttribute('data-npg-t1') || '';
      $('npgTipB').textContent = el.getAttribute('data-npg-t2') || '';
      box.className = 'npg-tip on';
      moveTip(e);
    });
    document.addEventListener('mousemove', function (e) {
      var box = $('npgTip');
      if (box && box.className.indexOf(' on') >= 0) { moveTip(e); }
    });
    document.addEventListener('mouseout', function (e) {
      if (e.target.closest && e.target.closest('[data-npg-heat]')) { hideTip(); }
    });
    window.addEventListener('scroll', hideTip, true);

    /* 转屏 / 拖窗口：方块图是按容器实测宽度排的，宽度一变必须重画（防抖，免得拖拽时连着重绘） */
    var rzTimer = null;
    window.addEventListener('resize', function () {
      if (rzTimer) { clearTimeout(rzTimer); }
      rzTimer = setTimeout(function () { rzTimer = null; paintHeats(); }, 160);
    });
  }

  /* RPG 视图显隐一变就补画首页那张图 —— 它藏在 #view-rpg 里，
     用户停在「学习目标」视图时量不到宽度（见 paintHeat 的骨架分支）。
     只盯 class/style 的变化，不依赖看板脚本的内部实现，谁改的都认。 */
  function watchView() {
    var v = $('view-rpg');
    if (!v || !window.MutationObserver) { return; }
    var t = null;
    var mo = new MutationObserver(function () {
      if (t) { return; }
      t = setTimeout(function () { t = null; paintHeats(); }, 80);
    });
    mo.observe(v, { attributes: true, attributeFilter: ['class', 'style'] });
  }

  /* 首屏默认视图：窄屏（手机）默认月视图 —— 年视图 53 列在 360px 上必然横向滚，
     一进来先给一张看得清的。只在启动时挑一次；用户手动切过（heatPinned）就再不擅自改动。 */
  function pickHeatMode() {
    if (heatPinned) { return; }
    var w = document.documentElement.clientWidth || window.innerWidth || 1024;
    heatMode = w < 720 ? 'month' : 'year';
  }

  /* ── 与看板同步：远端拉回后 roles.js 会调 __TASKS_BOARD__.reload ──────── */
  function hookBoard() {
    var b = window.__TASKS_BOARD__;
    if (!b || b.__npgHooked) { return !!b; }
    var orig = b.reload;
    b.reload = function () {
      if (typeof orig === 'function') { orig.apply(this, arguments); }
      load();
      if (seedIfEmpty()) { load(); }        /* 首拉落地后才知道线上到底有没有内容 → 这里再判一次 */
      /* 用户正在本视图里输入（打卡量 / 复盘四栏）时不要重绘，否则输入被吞；
         数据已经进内存了，下一次交互或切页签自然会渲染出来。 */
      var a = document.activeElement;
      var host = $('view-goals');
      var typing = !!(a && a.tagName === 'INPUT' && host && host.contains(a));
      if (!typing) { render(); } else { renderMine(); }
    };
    b.__npgHooked = true;
    return true;
  }

  /* ── 启动：等角色定下来再初始化（roles.js 拉完远端数据才注入脚本） ────
     注意：boot 只跑一次；渲染只在数据真的变了的时候做，避免反复重建 DOM
     把用户正在点的按钮 detach 掉（点了没反应 / 报 detached）。 */
  var booted = false;

  function boot() {
    if (booted) { return true; }
    if (!ROLES.current()) { return false; }
    booted = true;
    injectUi();
    pickHeatMode();
    bind();
    watchView();
    load();                                  /* ★ 必须先 load 再判空，否则 st 还是初始空数组 */
    if (seedIfEmpty()) { load(); }
    render();
    restoreView();
    /* 看板脚本（app.js）在 goals.js 之前注入，正常情况下这里一次就 hook 上；
       拿不到才轮询等待，等不到就安静收摊——绝不为了等它而反复重绘。 */
    if (!hookBoard()) {
      var tries = 0;
      var timer = setInterval(function () {
        if (hookBoard() || ++tries > 40) {
          clearInterval(timer);
          if (window.__TASKS_BOARD__) { load(); render(); }
        }
      }, 250);
    }
    return true;
  }

  var waited = 0;
  var wait = setInterval(function () {
    var ok = false;
    try { ok = boot(); } catch (err) { ok = true; }
    if (ok || (waited += 200) > 30000) { clearInterval(wait); }
  }, 200);
})();
