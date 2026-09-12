/* ============================================================================
 * kust.work · 主题引擎（浅色 / 深色 · 按昆明日出日落自动切换）
 * ============================================================================
 *
 * 接入方式（每个页面 <head> 里一行，且必须是「同步脚本」）：
 *     <script src="/theme.js"></script>
 * 不要加 defer / async —— 见下方第 1 条。
 *
 * 三条设计约束：
 *
 * 1) 零闪烁（no FOUC）
 *    本文件在 <head> 里同步加载，浏览器解析到它时会暂停渲染，
 *    因此 data-theme 在任何像素画出来之前就已写好。
 *    若改成 defer/async，就会出现「深色闪一下再变浅色」。
 *
 * 2) 降级安全
 *    深色是各页面内联 :root 的默认值 —— 本文件 404 / 被拦截 / JS 被禁用，
 *    页面依旧完整可读，只是没有浅色与切换按钮。
 *    浅色走 :root[data-theme="light"]（权重 0,2,0）覆盖 :root（0,1,0），
 *    所以不依赖 <link>/<style> 的先后顺序，也不怕页面样式表反向覆盖。
 *
 * 3) 模式三态，自动为默认
 *    auto（默认）→ 昆明日出之后、日落之前为浅色，其余为深色
 *    light / dark → 用户手动锁定，直到用户再点回 auto
 *    选择写入 localStorage，同域所有页面、所有标签页共用。
 * ========================================================================== */
(function () {
  'use strict';

  var STORE_KEY = 'kust.theme.mode';   // 存 'auto' | 'light' | 'dark'
  var STYLE_ID = 'kust-theme-style';
  var BTN_ID = 'kustThemeBtn';
  var TICK_MS = 60000;                 // 自动模式下的重判周期（1 分钟足够）

  /* 昆明市（五华区）坐标与时区 —— 用户口径：日间 = 昆明日出后、日落前 */
  var GEO = { lat: 25.0389, lon: 102.7183, tz: 8 };

  var MODES = {
    auto: { ico: '◐', txt: '自动', label: '跟随昆明日出日落' },
    light: { ico: '☀', txt: '浅色', label: '始终浅色' },
    dark: { ico: '☾', txt: '深色', label: '始终深色' }
  };
  var NEXT_MODE = { auto: 'light', light: 'dark', dark: 'auto' };
  var ORDER = ['auto', 'light', 'dark'];

  /* ────────────────────────────────────────────────────────────────
   * 浅色主题：只在这里维护（深色在各页面 :root 里，见文件头第 2 条）
   * 变量全部按语义命名，页面里不出现任何硬编码十六进制值。
   * ──────────────────────────────────────────────────────────────── */
  var LIGHT_VARS = [
    '--bg:#f7f6f3', '--surface:#ffffff', '--surface-2:#f2f0ec', '--surface-3:#e9e7e1',
    '--border:#e2dfd8', '--border-hover:#c9c4b9',
    '--text:#2a2926', '--muted:#6e6a63', '--dim:#918d86',
    '--accent:#2e7d4f', '--accent-dim:#4a9a6d', '--accent-soft:#e9f3ed',
    '--accent-soft-hover:#e3f0e8', '--accent-line:#9ed3b4',
    '--on-accent:#ffffff', '--logo-bg:#2e7d4f', '--logo-fg:#ffffff',
    '--link:#2f4a3b', '--meta-key:#4a7a5c',
    '--warn:#a86d12', '--warn-border:#e4ca93', '--err:#b0433c',
    '--tier-strong-bg:#e2f3e9', '--tier-strong-fg:#1d6b40', '--tier-strong-bd:#b7ddc7',
    '--tier-mid-bg:#e4eef7', '--tier-mid-fg:#24587f', '--tier-mid-bd:#b9d5e9',
    '--tier-low-bg:#f4f1e5', '--tier-low-fg:#77682c', '--tier-low-bd:#dcd4b4',
    '--kw-bg:#eef2ea', '--kw-bd:#d7ded1', '--kw-fg:#5b6a5d',
    '--sw-border:rgba(0,0,0,.12)', '--sw-shadow:0 4px 12px rgba(0,0,0,.16)',
    '--toast-bg:#ffffff', '--placeholder:#a8a49d',
    '--vb-hover-bg:#e7f2ec', '--vb-hover-fg:#256a43',
    '--scroll-bg:#efede8', '--scroll-track:#e4e1da', '--scroll-thumb:#c3beb4',
    '--stage-track:rgba(0,0,0,.08)', '--stage-fill:rgba(0,0,0,.12)',
    '--shimmer:rgba(0,0,0,.05)',
    '--pdf-shadow:0 4px 18px rgba(0,0,0,.18), 0 0 0 1px rgba(0,0,0,.06)',
    '--card-shadow:0 1px 2px rgba(21,20,18,.04), 0 4px 14px rgba(21,20,18,.05)'
  ].join(';');

  /* 切换按钮：HTML 里没有它，由本文件创建，所以样式也在这里，
     避免 6 个页面各写一份（也就不会漂移）。 */
  var BTN_CSS = [
    '.kust-theme-btn{display:inline-flex;align-items:center;gap:5px;flex-shrink:0;',
    'height:26px;padding:0 9px;border:1px solid var(--border,#3a3a3a);border-radius:5px;',
    'background:var(--surface-2,#262626);color:var(--muted,#8a867f);',
    'font:inherit;font-size:11.5px;line-height:1;cursor:pointer;white-space:nowrap;',
    'transition:color .16s,border-color .16s,background .16s;',
    '-webkit-appearance:none;appearance:none;}',
    '.kust-theme-btn:hover{color:var(--accent,#4a9e6a);border-color:var(--accent-dim,#2d6e46);}',
    '.kust-theme-btn:focus-visible{outline:2px solid var(--accent,#4a9e6a);outline-offset:1px;}',
    '.kust-theme-btn .tb-ico{font-size:12.5px;line-height:1;}',
    '.kust-theme-btn[data-mode="light"] .tb-ico{color:#c9962a;}',
    '.kust-theme-btn[data-mode="dark"] .tb-ico{color:#7fa8c9;}',
    '@media (max-width:560px){.kust-theme-btn .tb-txt{display:none;}',
    '.kust-theme-btn{padding:0 7px;}}'
  ].join('');

  function injectStyle() {
    if (document.getElementById(STYLE_ID)) return;
    var head = document.head || document.documentElement;
    if (!head) return;
    head.insertAdjacentHTML(
      'beforeend',
      '<style id="' + STYLE_ID + '">' +
        ':root[data-theme="light"]{' + LIGHT_VARS + ';}' +
        BTN_CSS +
      '</style>'
    );
  }

  /* ────────────────────────────────────────────────────────────────
   * 日出 / 日落（NOAA 简化式，太阳视高度角 -0.833°）
   * 纯本地计算，不联网、不依赖任何外部 API。
   * 返回 {rise, set}：昆明当地时间的「当日分钟数」（0..1439）；极昼/极夜返回 null。
   * ──────────────────────────────────────────────────────────────── */
  var RAD = Math.PI / 180;

  function julianDay(y, m, d) {
    if (m <= 2) { y -= 1; m += 12; }
    var a = Math.floor(y / 100);
    var b = 2 - a + Math.floor(a / 4);
    return Math.floor(365.25 * (y + 4716)) + Math.floor(30.6001 * (m + 1)) + d + b - 1524.5;
  }

  /* 儒略日（UT）→ 昆明当地时间「当日分钟数」 */
  function jdToLocalMinutes(jd) {
    var days = jd - 2440587.5;             // 自 1970-01-01T00:00Z 起的天数
    var frac = days - Math.floor(days);    // 落在 UT 当日的哪个位置（0..1）
    var local = frac * 1440 + GEO.tz * 60;
    return ((local % 1440) + 1440) % 1440;
  }

  function sunMinutes(date) {
    var y = date.getFullYear(), m = date.getMonth() + 1, d = date.getDate();

    // n = 自 J2000（2000-01-01 12:00 UT）起的天数，**必须取整**。
    // 这一步不能省：不取整会把「当地日内的偏移」重复计入 J*，
    // 使 Jtransit 整体偏移与时区相同的时数 —— 昆明会偏 8 小时，
    // 表现为日出算成 22:55、日落算成 11:18（rise/set 颠倒）。
    var n = Math.ceil(julianDay(y, m, d) - 2451545.0 + 0.0008);
    var jStar = n - GEO.lon / 360;

    var M = ((357.5291 + 0.98560028 * jStar) % 360 + 360) % 360;          // 平近点角
    var C = 1.9148 * Math.sin(M * RAD)
          + 0.02 * Math.sin(2 * M * RAD)
          + 0.0003 * Math.sin(3 * M * RAD);                                // 中心差
    var lambda = ((M + C + 180 + 102.9372) % 360 + 360) % 360;             // 黄经
    var jTransit = 2451545.0 + jStar
                 + 0.0053 * Math.sin(M * RAD)
                 - 0.0069 * Math.sin(2 * lambda * RAD);                    // 真太阳时中天

    var sinDec = Math.sin(lambda * RAD) * Math.sin(23.44 * RAD);           // 太阳赤纬
    var cosDec = Math.sqrt(1 - sinDec * sinDec);
    var cosOmega = (Math.sin(-0.833 * RAD) - Math.sin(GEO.lat * RAD) * sinDec)
                 / (Math.cos(GEO.lat * RAD) * cosDec);
    if (cosOmega >= 1 || cosOmega <= -1) return null;                      // 极夜 / 极昼
    var omega = Math.acos(cosOmega) / RAD;                                 // 时角（度）

    return {
      rise: jdToLocalMinutes(jTransit - omega / 360),
      set: jdToLocalMinutes(jTransit + omega / 360)
    };
  }

  function minuteOfDay(d) {
    return d.getHours() * 60 + d.getMinutes() + d.getSeconds() / 60;
  }

  function hhmm(mins) {
    var m = Math.round(mins);
    var h = Math.floor(m / 60) % 24;
    var mi = m % 60;
    return (h < 10 ? '0' : '') + h + ':' + (mi < 10 ? '0' : '') + mi;
  }

  /* 当前是否处于「昆明日间」= 日出之后、日落之前 */
  function isDaytime(now) {
    var t = sunMinutes(now);
    if (!t) return true;              // 理论上昆明永远不会走到这里，兜底给浅色
    var m = minuteOfDay(now);
    if (t.rise <= t.set) return m >= t.rise && m < t.set;
    return m >= t.rise || m < t.set;  // 跨午夜（高纬夏夜），昆明不触发
  }

  /* ────────────────────────────────────────────────────────────────
   * 模式读写与落地
   * ──────────────────────────────────────────────────────────────── */
  function readMode() {
    try {
      var v = localStorage.getItem(STORE_KEY);
      return (v === 'light' || v === 'dark' || v === 'auto') ? v : 'auto';
    } catch (e) { return 'auto'; }     // 隐私模式 / 禁用存储
  }

  function writeMode(v) {
    try { localStorage.setItem(STORE_KEY, v); } catch (e) { /* 忽略 */ }
  }

  function resolve(mode, now) {
    if (mode === 'light' || mode === 'dark') return mode;
    return isDaytime(now) ? 'light' : 'dark';
  }

  function currentTheme() {
    return document.documentElement.getAttribute('data-theme') || 'dark';
  }

  function apply(theme) {
    var root = document.documentElement;
    if (root.getAttribute('data-theme') !== theme) {
      root.setAttribute('data-theme', theme);
    }
    // 让原生控件（滚动条、input、select）跟随，而不是永远浅色
    root.style.colorScheme = theme;
  }

  /* ────────────────────────────────────────────────────────────────
   * 1. 立刻落地主题（在任何渲染之前，避免闪烁）
   * ──────────────────────────────────────────────────────────────── */
  injectStyle();
  var mode = readMode();
  apply(resolve(mode, new Date()));

  /* ────────────────────────────────────────────────────────────────
   * 2. 右上角切换按钮（DOM 就绪后挂到 header 末尾）
   * ──────────────────────────────────────────────────────────────── */
  function titleText() {
    var t = sunMinutes(new Date());
    var cur = currentTheme() === 'light' ? '浅色' : '深色';
    var parts = ['当前 ' + MODES[mode].txt + '（显示为' + cur + '）'];
    if (t) parts.push('今日昆明 ' + hhmm(t.rise) + ' 日出 · ' + hhmm(t.set) + ' 日落');
    parts.push('点击切换：自动 → 浅色 → 深色');
    return parts.join('　|　');
  }

  function syncBtn(btn) {
    if (!btn) return;
    var m = MODES[mode] || MODES.auto;
    var ico = btn.querySelector('.tb-ico');
    var txt = btn.querySelector('.tb-txt');
    if (ico) ico.textContent = m.ico;
    if (txt) txt.textContent = m.txt;
    btn.setAttribute('data-mode', mode);
    btn.setAttribute('title', titleText());
    btn.setAttribute('aria-label', '配色主题：' + m.label + '，点击切换');
  }

  function mountBtn() {
    if (document.getElementById(BTN_ID)) return;
    var header = document.querySelector('header');
    if (!header) return;

    var btn = document.createElement('button');
    btn.type = 'button';
    btn.id = BTN_ID;
    btn.className = 'kust-theme-btn';
    // 用 textContent 逐节点构建，避免把 ◐/☀/☾ 之外的任何东西当 HTML 解析
    var ico = document.createElement('span');
    ico.className = 'tb-ico';
    var txt = document.createElement('span');
    txt.className = 'tb-txt';
    btn.appendChild(ico);
    btn.appendChild(txt);
    btn.addEventListener('click', function () {
      mode = NEXT_MODE[mode] || 'auto';
      writeMode(mode);
      apply(resolve(mode, new Date()));
      syncBtn(btn);
    });
    header.appendChild(btn);
    syncBtn(btn);
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', mountBtn);
  } else {
    mountBtn();
  }

  /* ────────────────────────────────────────────────────────────────
   * 3. 自动模式：跨过日出/日落时自己翻转
   * ──────────────────────────────────────────────────────────────── */
  function reevaluate() {
    if (mode !== 'auto') return;
    var want = resolve('auto', new Date());
    if (want !== currentTheme()) {
      apply(want);
      syncBtn(document.getElementById(BTN_ID));
    }
  }

  setInterval(reevaluate, TICK_MS);

  // 页面在后台挂久了（浏览器会节流定时器），回到前台立刻校正一次
  document.addEventListener('visibilitychange', function () {
    if (!document.hidden) reevaluate();
  });

  /* ────────────────────────────────────────────────────────────────
   * 4. 多标签页同步：另一个页面改了模式，这里跟着改
   * ──────────────────────────────────────────────────────────────── */
  window.addEventListener('storage', function (e) {
    if (e && e.key && e.key !== STORE_KEY) return;
    mode = readMode();
    apply(resolve(mode, new Date()));
    syncBtn(document.getElementById(BTN_ID));
  });

  /* ────────────────────────────────────────────────────────────────
   * 5. 给外部用的只读接口（调试 / 自测用，不影响页面）
   * ──────────────────────────────────────────────────────────────── */
  window.kustTheme = {
    get mode() { return mode; },
    get theme() { return currentTheme(); },
    sun: function (d) { return sunMinutes(d || new Date()); },
    isDaytime: function (d) { return isDaytime(d || new Date()); },
    modes: ORDER,
    set: function (m) {
      if (ORDER.indexOf(m) < 0) return false;
      mode = m; writeMode(m);
      apply(resolve(m, new Date()));
      syncBtn(document.getElementById(BTN_ID));
      return true;
    }
  };
})();
