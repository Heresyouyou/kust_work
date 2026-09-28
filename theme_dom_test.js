/* ============================================================================
 * theme.js 真实执行测试（自建轻量 DOM 垫片，不引入 jsdom 依赖）
 *
 * 为什么不用 jsdom：本机只装了 node，装 playwright/jsdom 要几百 MB，
 * 而这里要验证的是「脚本逻辑」而非「浏览器渲染」—— 一个够用的 DOM 垫片
 * 反而让断言更精确（能直接读注入的 CSS 字符串、拦截定时器）。
 *
 * 覆盖：日出日落算法（昆明）· 自动/手动模式判定 · 按钮三态与持久化 ·
 *       注入样式内容 · 跨标签同步 · 无 header 页面不崩
 *
 * 运行：node theme_dom_test.js
 * ========================================================================== */
'use strict';
const path = require('path');
const fs = require('fs');

const THEME_JS = path.join(__dirname, 'portal', 'assets', 'theme.js');
const RealDate = Date;

/* ── 断言小工具 ── */
let pass = 0, fail = 0;
const failures = [];
function ok(cond, name, extra) {
  if (cond) { pass++; console.log('  \u2713 ' + name); }
  else {
    fail++; failures.push(name);
    console.log('  \u2717 ' + name + (extra ? '   \u2190 ' + extra : ''));
  }
}
function section(t) { console.log('\n\u3010' + t + '\u3011'); }
function eq(a, b) { return JSON.stringify(a) === JSON.stringify(b); }

/* ── 极简 DOM 垫片 ── */
function El(tag) {
  const el = {
    tagName: String(tag || 'div').toUpperCase(),
    id: '',
    className: '',
    children: [],
    parentNode: null,
    attrs: {},
    style: {},
    _text: '',
    _html: '',
    injected: '',
    _ev: {},
    appendChild(c) { this.children.push(c); c.parentNode = this; return c; },
    insertAdjacentHTML(pos, html) { this._html += html; this.injected += html; },
    setAttribute(k, v) { this.attrs[k] = String(v); if (k === 'id') this.id = String(v); },
    getAttribute(k) { return Object.prototype.hasOwnProperty.call(this.attrs, k) ? this.attrs[k] : null; },
    removeAttribute(k) { delete this.attrs[k]; },
    addEventListener(t, fn) { (this._ev[t] = this._ev[t] || []).push(fn); },
    get textContent() { return this._text; },
    set textContent(v) { this._text = String(v); },
    get innerHTML() { return this._html; },
    set innerHTML(v) { this._html = String(v); },
    querySelector(sel) { return find(this, sel); },
    querySelectorAll(sel) { return findAll(this, sel); },
  };
  return el;
}

function matches(el, sel) {
  if (!sel) return false;
  if (sel[0] === '.') return String(el.className || '').split(/\s+/).indexOf(sel.slice(1)) >= 0;
  if (sel[0] === '#') return el.id === sel.slice(1);
  return String(el.tagName || '').toLowerCase() === sel.toLowerCase();
}
function find(el, sel) {
  for (const c of el.children || []) {
    if (matches(c, sel)) return c;
    const d = find(c, sel);
    if (d) return d;
  }
  return null;
}
function findAll(el, sel) {
  const out = [];
  (function walk(n) {
    for (const c of n.children || []) { if (matches(c, sel)) out.push(c); walk(c); }
  })(el);
  return out;
}

/* ── 全局环境 ── */
let g;   // 本次用例的全局环境

function setupEnv(opts) {
  opts = opts || {};
  const store = Object.create(null);
  if (opts.store) Object.keys(opts.store).forEach(k => { store[k] = opts.store[k]; });

  const docEl = El('html');
  const head = El('head');
  const body = El('body');
  const doc = {
    head, body,
    documentElement: docEl,
    readyState: opts.readyState || 'loading',
    _ev: {},
    getElementById(id) {
      const s = '#' + id;
      if (matches(head, s)) return head;
      if (matches(body, s)) return body;
      return find(body, s) || find(head, s);
    },
    createElement(tag) { return El(tag); },
    querySelector(sel) {
      if (matches(head, sel)) return head;
      if (matches(body, sel)) return body;
      return find(body, sel) || find(head, sel);
    },
    querySelectorAll(sel) { return findAll(body, sel); },
    addEventListener(t, fn) { (this._ev[t] = this._ev[t] || []).push(fn); },
  };
  const winEv = {};
  const win = {
    addEventListener(t, fn) { (winEv[t] = winEv[t] || []).push(fn); },
    _ev: winEv,
  };
  const timers = [];
  const env = {
    doc, head, body, docEl, win, store, winEv, timers,
    fireOn(type, ev) { (doc._ev[type] || []).forEach(fn => fn(ev || {})); },
    fireWin(type, ev) { (winEv[type] || []).forEach(fn => fn(ev || {})); },
  };

  if (opts.withHeader !== false) {
    const header = El('header');
    header.className = '';
    body.appendChild(header);
    env.header = header;
  }

  global.document = doc;
  global.window = win;
  global.localStorage = {
    getItem: k => (k in store ? store[k] : null),
    setItem: (k, v) => { store[k] = String(v); },
    removeItem: k => { delete store[k]; },
  };
  global.setInterval = (fn, ms) => { timers.push({ fn, ms }); return timers.length; };
  global.clearInterval = () => {};
  return env;
}

/** 以指定「昆明本地时刻」加载 theme.js */
function loadTheme(y, m, d, hh, mm, envOpts) {
  const env = setupEnv(envOpts);
  const fixed = new RealDate(y, m - 1, d, hh, mm, 0, 0);
  class FakeDate extends RealDate {
    constructor(...a) { if (a.length === 0) super(fixed.getTime()); else super(...a); }
    static now() { return fixed.getTime(); }
  }
  global.Date = FakeDate;
  delete require.cache[require.resolve(THEME_JS)];
  require(THEME_JS);
  env.now = fixed;
  // 模拟浏览器解析到 </html>：theme.js 在 head 里注册的是 DOMContentLoaded 回调
  if (env.doc.readyState === 'loading') env.fireOn('DOMContentLoaded');
  return env;
}

/** 按「昆明当地分钟数」加载，供日出/日落边界用例使用 */
function loadThemeAtMinute(y, m, d, mins, envOpts) {
  mins = ((Math.round(mins) % 1440) + 1440) % 1440;
  return loadTheme(y, m, d, Math.floor(mins / 60), mins % 60, envOpts);
}

function themeOf(env) { return env.docEl.getAttribute('data-theme'); }
function btnOf(env) { return env.doc.getElementById('kustThemeBtn'); }
function clickBtn(env) {
  const b = btnOf(env);
  (b._ev.click || []).forEach(fn => fn({}));
}

/* ══════════════════════════════════════════════════════════════════
 * A. 日出日落算法（对照昆明公开天文数据）
 * ══════════════════════════════════════════════════════════════════ */
function testSun({ sun }) {
  section('A. 日出日落算法 · 昆明 (25.04N, 102.72E, UTC+8)');
  const at = (y, m, d) => sun(new RealDate(y, m - 1, d, 12, 0, 0));

  const jun = at(2026, 6, 21), dec = at(2026, 12, 21);
  const mar = at(2026, 3, 20), sep = at(2026, 9, 23);
  const today = at(2026, 9, 13);

  const hh = x => Math.floor(x / 60) + ':' + String(Math.round(x % 60)).padStart(2, '0');
  const dayLen = t => (t.set - t.rise) / 60;

  ok(jun && dec && mar && sep, '全年均能解出日出日落（无极昼极夜）');
  ok(jun.rise < dec.rise, '夏至日出早于冬至', hh(jun.rise) + ' vs ' + hh(dec.rise));
  ok(jun.set > dec.set, '夏至日落晚于冬至', hh(jun.set) + ' vs ' + hh(dec.set));
  ok(dayLen(jun) > 13, '夏至日长 > 13h', dayLen(jun).toFixed(2) + 'h');
  ok(dayLen(dec) < 11, '冬至日长 < 11h', dayLen(dec).toFixed(2) + 'h');
  ok(Math.abs(dayLen(mar) - 12) < 0.5, '春分日长 ≈ 12h', dayLen(mar).toFixed(2) + 'h');
  ok(Math.abs(dayLen(sep) - 12) < 0.5, '秋分日长 ≈ 12h', dayLen(sep).toFixed(2) + 'h');

  // 昆明 2026-09-13 公开数据约 06:53 日出 / 19:10 日落
  const near = (v, lo, hi) => v >= lo && v <= hi;
  ok(near(today.rise, 6 * 60 + 40, 7 * 60 + 10), '9/13 日出在 06:40–07:10', hh(today.rise));
  ok(near(today.set, 18 * 60 + 55, 19 * 60 + 30), '9/13 日落在 18:55–19:30', hh(today.set));
  ok(today.rise < today.set, '9/13 日出早于日落（非跨午夜）');

  // 全年扫描：昆明纬度下不应出现极端值
  let minRise = 1e9, maxRise = -1, minSet = 1e9, maxSet = -1, okAll = true;
  for (let i = 0; i < 365; i++) {
    const t = sun(new RealDate(2026, 0, 1 + i, 12, 0, 0));
    if (!t) { okAll = false; break; }
    minRise = Math.min(minRise, t.rise); maxRise = Math.max(maxRise, t.rise);
    minSet = Math.min(minSet, t.set); maxSet = Math.max(maxSet, t.set);
  }
  ok(okAll, '全年 365 天都有解');
  ok(minRise > 5 * 60 + 30 && maxRise < 8 * 60 + 20,
     '全年日出落在 05:30–08:20 内', hh(minRise) + ' ~ ' + hh(maxRise));
  ok(minSet > 17 * 60 + 50 && maxSet < 20 * 60 + 30,
     '全年日落落在 17:50–20:30 内', hh(minSet) + ' ~ ' + hh(maxSet));
}

/* ══════════════════════════════════════════════════════════════════
 * B. 自动模式判定（默认行为）
 * ══════════════════════════════════════════════════════════════════ */
function testAutoMode() {
  section('B. 自动模式 · 日间浅色 / 夜间深色');
  const cases = [
    [2026, 9, 13, 3, 0, 'dark', '凌晨 03:00（夜）'],
    [2026, 9, 13, 6, 0, 'dark', '日出前 06:00'],
    [2026, 9, 13, 8, 30, 'light', '上午 08:30'],
    [2026, 9, 13, 12, 0, 'light', '正午 12:00'],
    [2026, 9, 13, 18, 30, 'light', '日落前 18:30'],
    [2026, 9, 13, 22, 0, 'dark', '夜间 22:00'],
    [2026, 1, 15, 12, 0, 'light', '冬季正午 12:00'],
    [2026, 1, 15, 3, 0, 'dark', '冬季凌晨 03:00'],
  ];
  cases.forEach(([y, m, d, hh, mm, want, label]) => {
    const env = loadTheme(y, m, d, hh, mm, { withHeader: false });
    ok(themeOf(env) === want, label + ' → ' + want, '实际 ' + themeOf(env));
  });

  // 边界：日出后 / 日落前的 1 分钟
  const env = loadTheme(2026, 9, 13, 12, 0, { withHeader: false });
  const t = env.win.kustTheme.sun(new RealDate(2026, 8, 13, 12, 0, 0));
  const mk = (mins) => loadThemeAtMinute(2026, 9, 13, mins, { withHeader: false });
  ok(themeOf(mk(t.rise + 1)) === 'light', '日出后 1 分钟 → 浅色');
  ok(themeOf(mk(t.set - 1)) === 'light', '日落前 1 分钟 → 浅色');
  ok(themeOf(mk(t.set + 1)) === 'dark', '日落后 1 分钟 → 深色');
  // rise/set 带小数，而 minuteOfDay 只精确到分钟，故取整分界断言
  ok(themeOf(mk(Math.ceil(t.rise))) === 'light', '日出后第一个整分钟 → 浅色');
  ok(themeOf(mk(Math.floor(t.set))) === 'light', '日落前最后一个整分钟 → 浅色');
  ok(themeOf(mk(Math.ceil(t.set))) === 'dark', '日落后第一个整分钟 → 深色');
}

/* ══════════════════════════════════════════════════════════════════
 * C. 手动切换与持久化
 * ══════════════════════════════════════════════════════════════════ */
function testManualMode() {
  section('C. 手动切换（右上角按钮，三态循环）');
  // 正午启动 → 自动判定为浅色
  let env = loadTheme(2026, 9, 13, 12, 0, {});
  const KEY = 'kust.theme.mode';
  ok(themeOf(env) === 'light', '初始为自动 → 正午显示浅色');

  const btn = btnOf(env);
  ok(!!btn, '按钮已挂载');
  if (!btn) { console.log('  （按钮缺失，后续按钮相关断言跳过）'); return; }
  ok(env.header.children[env.header.children.length - 1] === btn, '按钮位于顶栏最右侧（最后一个子元素）');
  ok(btn.getAttribute('data-mode') === 'auto', '按钮初始态为 auto');
  const txt = () => btn.querySelector('.tb-txt').textContent;
  const ico = () => btn.querySelector('.tb-ico').textContent;
  ok(txt() === '自动', '按钮显示「自动」', txt());
  ok(ico() === '\u25d0', '自动态图标 ◐', ico());
  ok(/日出.*日落/.test(btn.getAttribute('title') || ''), 'title 含当日日出日落时刻',
     btn.getAttribute('title'));

  clickBtn(env);
  ok(themeOf(env) === 'light' && btn.getAttribute('data-mode') === 'light',
     '点 1 次 → 浅色', themeOf(env) + '/' + btn.getAttribute('data-mode'));
  ok(txt() === '浅色', '按钮文案「浅色」', txt());
  ok(env.store[KEY] === 'light', 'localStorage 记录 light');

  clickBtn(env);
  ok(themeOf(env) === 'dark' && btn.getAttribute('data-mode') === 'dark',
     '点 2 次 → 深色', themeOf(env) + '/' + btn.getAttribute('data-mode'));
  ok(env.store[KEY] === 'dark', 'localStorage 记录 dark');

  clickBtn(env);
  ok(btn.getAttribute('data-mode') === 'auto', '点 3 次 → 回到自动');
  ok(env.store[KEY] === 'auto', 'localStorage 记录 auto');
  ok(themeOf(env) === 'light', '回到自动后按当前时刻（正午）判定为浅色');

  // 持久化：手动选深色后，换一个页面在正午加载，仍应是深色
  let env2 = loadTheme(2026, 9, 13, 12, 0, { store: { [KEY]: 'dark' } });
  ok(themeOf(env2) === 'dark', '手动深色跨页面保持（正午也不变浅）',
     themeOf(env2));
  ok(btnOf(env2).getAttribute('data-mode') === 'dark', '新页面按钮态同步为 dark');

  let env3 = loadTheme(2026, 9, 13, 3, 0, { store: { [KEY]: 'light' } });
  ok(themeOf(env3) === 'light', '手动浅色跨页面保持（凌晨也不变深）',
     themeOf(env3));

  // 脏数据兜底
  let env4 = loadTheme(2026, 9, 13, 12, 0, { store: { [KEY]: 'rainbow' } });
  ok(themeOf(env4) === 'light' && btnOf(env4).getAttribute('data-mode') === 'auto',
     'localStorage 脏值 → 回落 auto');

  // localStorage 不可用（隐私模式）
  const env5 = setupEnv({});
  global.localStorage = { getItem() { throw new Error('blocked'); },
                          setItem() { throw new Error('blocked'); } };
  const fixed = new RealDate(2026, 8, 13, 12, 0, 0);
  global.Date = class extends RealDate {
    constructor(...a) { if (!a.length) super(fixed.getTime()); else super(...a); }
  };
  delete require.cache[require.resolve(THEME_JS)];
  let threw = null;
  try { require(THEME_JS); } catch (e) { threw = e; }
  ok(!threw && env5.docEl.getAttribute('data-theme') === 'light',
     'localStorage 被禁用时不抛错且按自动模式渲染', threw && threw.message);
}

/* ══════════════════════════════════════════════════════════════════
 * D. 注入的样式内容
 * ══════════════════════════════════════════════════════════════════ */
function testInjectedStyle() {
  section('D. 注入样式（浅色变量 + 按钮）');
  const env = loadTheme(2026, 9, 13, 12, 0, {});
  const css = env.head.injected;

  ok(css.indexOf('kust-theme-style') >= 0, '已注入 <style id="kust-theme-style">');
  ok(css.indexOf(':root[data-theme="light"]') >= 0, '含浅色覆盖选择器 :root[data-theme="light"]');
  ok(/\.kust-theme-btn\s*\{/.test(css), '含按钮样式 .kust-theme-btn');
  ok(css.indexOf('@media (max-width:560px)') >= 0, '含移动端收起文案的媒体查询');

  // 浅色必须覆盖深色用到的每一个变量，否则某个组件会「半深半浅」
  const body = fs.readFileSync(path.join(__dirname, 'portal', 'templates', 'portal.html'), 'utf8');
  const rootBlock = body.match(/:root \{([\s\S]*?)\n  \}/);
  // --radius / --topbar-h 是几何量，不随主题变化，不算在内
  const GEOMETRIC = ['--radius', '--topbar-h'];
  const darkVars = [...new Set((rootBlock[1].match(/--[a-z0-9-]+:/g) || [])
    .map(s => s.slice(0, -1))
    .filter(v => GEOMETRIC.indexOf(v) < 0))];
  const lightPart = css.slice(css.indexOf(':root[data-theme="light"]'));
  const missing = darkVars.filter(v => lightPart.indexOf(v + ':') < 0);
  ok(missing.length === 0, '浅色覆盖了深色的全部 ' + darkVars.length + ' 个变量',
     missing.length ? '缺 ' + missing.join(', ') : '');

  ok(env.docEl.style.colorScheme === 'light', '设置了 color-scheme=light（原生控件跟随）');
  const env2 = loadTheme(2026, 9, 13, 3, 0, {});
  ok(env2.docEl.style.colorScheme === 'dark', '夜间 color-scheme=dark');
}

/* ══════════════════════════════════════════════════════════════════
 * E. 定时翻转与跨标签同步
 * ══════════════════════════════════════════════════════════════════ */
function testAutoFlipAndSync() {
  section('E. 自动翻转 · 跨标签同步');
  const env = loadTheme(2026, 9, 13, 12, 0, {});
  ok(env.timers.length === 1, '注册了 1 个定时器（自动模式重判）',
     String(env.timers.length));
  ok(env.timers[0].ms === 60000, '重判周期 60 秒', String(env.timers[0].ms));

  // 把「当前时刻」推到夜里，触发重判
  const night = new RealDate(2026, 8, 13, 22, 0, 0);
  global.Date = class extends RealDate {
    constructor(...a) { if (!a.length) super(night.getTime()); else super(...a); }
    static now() { return night.getTime(); }
  };
  env.timers[0].fn();
  ok(themeOf(env) === 'dark', '定时重判：跨过日落后自动转深色', themeOf(env));
  ok(btnOf(env).querySelector('.tb-txt').textContent === '自动',
     '自动翻转不改动按钮模式（仍是「自动」）');

  // 手动模式不应被定时器翻转
  clickBtn(env);   // auto → light
  clickBtn(env);   // light → dark
  const before = themeOf(env);
  env.timers[0].fn();
  ok(themeOf(env) === before, '手动模式下定时器不越权改动主题', themeOf(env));

  // 跨标签：另一个页面写入 localStorage → storage 事件
  const env2 = loadTheme(2026, 9, 13, 12, 0, {});
  ok(themeOf(env2) === 'light', '标签 A 正午为浅色');
  env2.store['kust.theme.mode'] = 'dark';
  env2.fireWin('storage', { key: 'kust.theme.mode' });
  ok(themeOf(env2) === 'dark', '收到 storage 事件后跟随为深色', themeOf(env2));
  ok(btnOf(env2).getAttribute('data-mode') === 'dark', '按钮态同步');

  // 回到前台时校正
  const env3 = loadTheme(2026, 9, 13, 12, 0, {});
  const night2 = new RealDate(2026, 8, 13, 23, 30, 0);
  global.Date = class extends RealDate {
    constructor(...a) { if (!a.length) super(night2.getTime()); else super(...a); }
    static now() { return night2.getTime(); }
  };
  env3.fireOn('visibilitychange', { hidden: false });
  ok(themeOf(env3) === 'dark', '从后台回到前台时校正主题', themeOf(env3));
}

/* ══════════════════════════════════════════════════════════════════
 * F. 降级与健壮性
 * ══════════════════════════════════════════════════════════════════ */
function testRobustness() {
  section('F. 降级与健壮性');
  let threw = null;
  let env;
  try { env = loadTheme(2026, 9, 13, 12, 0, { withHeader: false }); }
  catch (e) { threw = e; }
  ok(!threw, '页面没有 <header> 时不抛错', threw && threw.message);
  ok(env && themeOf(env) === 'light', '无 header 时主题仍正确落地');
  ok(btnOf(env) === null, '无 header 时不创建按钮');

  // readyState=complete（脚本被延后注入的场景）也要能挂上按钮
  threw = null;
  try { env = loadTheme(2026, 9, 13, 12, 0, { readyState: 'complete' }); }
  catch (e) { threw = e; }
  ok(!threw && !!btnOf(env), 'readyState=complete 时同步挂载按钮', threw && threw.message);

  // 重复执行两次不应产生第二个按钮
  const n1 = env.header.children.length;
  const fixed = new RealDate(2026, 8, 13, 12, 0, 0);
  global.Date = class extends RealDate {
    constructor(...a) { if (!a.length) super(fixed.getTime()); else super(...a); }
  };
  delete require.cache[require.resolve(THEME_JS)];
  require(THEME_JS);
  ok(env.header.children.length === n1, '重复加载不产生重复按钮',
     n1 + ' → ' + env.header.children.length);

  // 公开接口
  const api = env.win.kustTheme;
  ok(api && ['auto', 'light', 'dark'].every(m => api.modes.indexOf(m) >= 0),
     'window.kustTheme 暴露三种模式');
  ok(api.set('dark') === true && themeOf(env) === 'dark', 'kustTheme.set("dark") 生效');
  ok(api.set('nope') === false, 'kustTheme.set 拒绝非法值');
  ok(api.isDaytime(new RealDate(2026, 8, 13, 12, 0, 0)) === true, 'isDaytime 正午为真');
  ok(api.isDaytime(new RealDate(2026, 8, 13, 0, 30, 0)) === false, 'isDaytime 深夜为假');
}

/* ── 主流程 ── */
console.log('══════════════════════════════════════════════════════════');
console.log(' theme.js 测试 · ' + new RealDate().toISOString());
console.log(' 被测文件: ' + THEME_JS);
console.log(' 本机时区偏移: ' + (-new RealDate().getTimezoneOffset() / 60) + 'h');
console.log('══════════════════════════════════════════════════════════');

const probe = loadTheme(2026, 9, 13, 12, 0, { withHeader: false });
testSun(probe.win.kustTheme);
testAutoMode();
testManualMode();
testInjectedStyle();
testAutoFlipAndSync();
testRobustness();

console.log('\n══════════════════════════════════════════════════════════');
console.log(' 通过 ' + pass + ' / ' + (pass + fail));
if (fail) {
  console.log(' 失败项：');
  failures.forEach(f => console.log('   - ' + f));
}
console.log('══════════════════════════════════════════════════════════');
process.exit(fail ? 1 : 0);
