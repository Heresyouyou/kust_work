/**
 * 门户「期刊列表」双视图 DOM 冒烟测试（纯 Node，无浏览器）
 *
 * 思路：用最小 DOM 垫片执行页面里的真实 <script>，喂入真实 /api/journals/list
 * 载荷，断言渲染结果符合预期。避免安装 500MB 级 Chromium。
 *
 * 本版重点验证「近 60 天获取窗口」：
 *   · 徽章 / 汇总行 / 窗口元信息文案
 *   · 每卡默认只展开 6 条，其余折叠
 *   · 「展开其余 N 篇 / 收起」交互（事件委托 + 重渲染）
 *   · 条目携带本地获取日期
 *   · 空卡 / 超出单卡上限等边界分支（用合成载荷，因真实数据当前无空刊）
 *
 * 用法：
 *   node portal_journals_dom_test.js /tmp/journals_page.html /tmp/journals_api.json
 */
const fs = require('fs');
const vm = require('vm');

const [pagePath, apiPath] = process.argv.slice(2);
const html = fs.readFileSync(pagePath, 'utf8');
const api = JSON.parse(fs.readFileSync(apiPath, 'utf8'));

// 找出真正的「期刊页脚本」。不能盲取第一个 <script>：
// 离线快照会把 theme.js 内联在页面最前面，而 theme.js 的头部注释里
// 含**字面量** `</script>`，非贪婪匹配会在那里提前截断 → 拿到半截 JS 报
// "Invalid or unexpected token"。改为遍历所有内联块，选含 `getElementById('body')` 的那段。
const _blocks = [];
const _re = /<script>([\s\S]*?)<\/script>/g;
let _m;
while ((_m = _re.exec(html))) _blocks.push(_m[1]);
const scriptSrc = _blocks.find(s => s.includes("getElementById('body')"));
if (!scriptSrc) {
  console.error('FAIL: 页面里找不到期刊页内联脚本（共 ' + _blocks.length + ' 段 <script>）');
  process.exit(1);
}
console.log('  · 内联脚本 ' + _blocks.length + ' 段，已选中期刊页那段（' + scriptSrc.length + " 字符）");

// 与页面同款的转义（断言标题/文案时要先过一遍）
function esc(s) {
  return String(s == null ? '' : s).replace(/[&<>"']/g, c =>
    ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}

// ── 最小 DOM 垫片 ──
function makeEl(id) {
  return {
    id, _html: '', _text: '', attrs: {}, classes: new Set(), listeners: {},
    get innerHTML() { return this._html; },
    set innerHTML(v) { this._html = String(v); },
    get textContent() { return this._text; },
    set textContent(v) { this._text = String(v); },
    getAttribute(k) { return this.attrs[k] === undefined ? null : this.attrs[k]; },
    setAttribute(k, v) { this.attrs[k] = String(v); },
    addEventListener(t, fn) { (this.listeners[t] = this.listeners[t] || []).push(fn); },
    querySelectorAll() { return []; },
    classList: { add: c => { }, remove: c => { }, contains: c => false },
    matches(sel) { return this.classes.has(sel.replace(/^\./, '')); },
    closest(sel) { return this.matches(sel) ? this : null; },
  };
}

function withClassList(el) {
  el.classList = {
    add: c => el.classes.add(c),
    remove: c => el.classes.delete(c),
    contains: c => el.classes.has(c),
  };
  return el;
}

/** 用指定的 API 载荷，在全新沙箱里跑一遍真实页面脚本，返回可断言的元素句柄 */
function boot(payload) {
  const els = {
    body: makeEl('body'), sum: makeEl('sum'), st: makeEl('st'),
    bar: makeEl('bar'), vnote: makeEl('vnote'), legend: makeEl('legend'),
    allbtn: makeEl('allbtn'),
  };
  const regionTabs = [
    { region: 'all', on: true }, { region: '国际' }, { region: '国内' },
  ].map(t => {
    const e = withClassList(makeEl('tab-' + t.region));
    e.attrs['data-region'] = t.region;
    e.classes.add('tab');
    if (t.on) e.classes.add('on');
    return e;
  });
  const viewTabs = [
    { view: 'latest', on: true }, { view: 'topic' },
  ].map(t => {
    const e = withClassList(makeEl('vtab-' + t.view));
    e.attrs['data-view'] = t.view;
    e.classes.add('vtab');
    if (t.on) e.classes.add('on');
    return e;
  });
  const vbar = withClassList(makeEl('vbar'));
  vbar.querySelectorAll = () => viewTabs;
  els.bar.querySelectorAll = () => regionTabs;
  ['body', 'sum', 'st', 'vnote', 'legend', 'allbtn'].forEach(k => withClassList(els[k]));

  const document = {
    getElementById: id => els[id] || makeEl(id),
    querySelector: sel => (sel === '.vbar' ? vbar : makeEl(sel)),
    querySelectorAll: sel => (sel === '.vtab' ? viewTabs : []),
  };
  const sandbox = {
    document,
    window: { scrollTo() {} },
    fetch: () => Promise.resolve({ json: () => Promise.resolve(payload) }),
    console, setTimeout,
    Array, Promise, String, Number, Math, JSON, RegExp, Date, Object,
  };
  vm.createContext(sandbox);
  vm.runInContext(scriptSrc, sandbox, { filename: 'journals-inline.js' });
  return { els, vbar, viewTabs, regionTabs };
}

// ── 断言 ──
let pass = 0, fail = 0;
function ok(cond, label, extra) {
  if (cond) { pass++; console.log('  ✔ ' + label); }
  else { fail++; console.log('  ✘ ' + label + (extra ? '  → ' + extra : '')); }
}
function tick() { return new Promise(r => setTimeout(r, 0)); }
const clickEvt = e => ({ target: e });
const flat = p => p.groups.flatMap(g => g.journals);
const countArt = h => (h.match(/class="art"/g) || []).length;

(async () => {
  const W = api.window || {};
  const env = boot(api);
  const els = env.els;
  await tick(); await tick();

  console.log('\n【视图一 · 近 60 天获取】');
  let h = els.body._html;
  ok(h.length > 1000, '渲染出内容', 'len=' + h.length);
  ok(!/undefined|NaN/.test(h), '无 undefined / NaN 泄漏');

  const cards = (h.match(/class="card/g) || []).length;
  ok(cards === api.total_journals, `卡片数 = ${api.total_journals}`, '实际 ' + cards);

  ok(new RegExp(W.days + ' 天内 \\d+ 篇').test(h), `含「${W.days} 天内 N 篇」徽章`);

  const expectRows = flat(api).reduce(
    (n, j) => n + Math.min(api.max_per_card, j.article_total), 0);
  ok(countArt(h) === expectRows, `默认渲染条目数 = ${expectRows}（每卡≤${api.max_per_card}）`,
     '实际 ' + countArt(h));

  ok(els.sum._html.includes('天有获取'), '汇总行切换为 60 天口径', els.sum._html);
  ok(/\d+<\/b> 条/.test(els.sum._html), '汇总行含链接条数');
  ok(els.vnote._html.includes(W.from) && els.vnote._html.includes(W.to),
     '窗口边界已展示', els.vnote._html.replace(/<br>/g, ' '));
  ok(els.vnote._html.includes('本地时间'), '明示「本地时间」口径');
  ok(els.vnote._html.includes(W.ledger_available ? '获取台账已建立' : '获取台账缺失'),
     '明示获取台账状态');

  const withArts = flat(api).find(j => j.article_total);
  const a0 = withArts.articles[0];
  ok(h.includes(esc(a0.title).slice(0, 30)), '条目标题已渲染');
  ok(a0.obtained_label && h.includes(a0.obtained_label), '条目携带获取日期', a0.obtained_label);
  ok(/class="ob" title="本地获取时间/.test(h), '获取日期带悬浮完整时间');
  ok(h.includes('href="https://') && h.includes('target="_blank"'), '外链均新窗口打开');

  const bigJ = flat(api).find(j => j.article_total > api.max_per_card);
  ok(!!bigJ, '存在需折叠的卡片', bigJ && bigJ.name);
  ok(h.includes('展开其余 ' + (bigJ.article_total - api.max_per_card) + ' 篇'),
     `卡片提供「展开其余 ${bigJ.article_total - api.max_per_card} 篇」`);
  ok(!h.includes(esc(bigJ.articles[api.max_per_card].title).slice(0, 30)),
     '被折叠的条目默认不渲染');

  console.log('\n【展开 / 收起 交互】');
  const moreEl = withClassList(makeEl('more-' + bigJ.id));
  moreEl.classes.add('more');
  moreEl.attrs['data-more'] = bigJ.id;
  const bodyClick = els.body.listeners.click && els.body.listeners.click[0];
  ok(!!bodyClick, '卡片区已挂载点击委托');

  let err1 = null;
  try { bodyClick(clickEvt(moreEl)); } catch (e) { err1 = e; }
  ok(!err1, '展开操作无 JS 异常', err1 && err1.message);
  h = els.body._html;
  ok(h.includes(esc(bigJ.articles[api.max_per_card].title).slice(0, 30)),
     '展开后折叠条目已出现');
  ok(h.includes('收起 ↑'), '展开后出现「收起」');
  const openedExp = expectRows - api.max_per_card + bigJ.articles.length;
  ok(countArt(h) === openedExp, `展开后条目数 = ${openedExp}`, '实际 ' + countArt(h));

  try { bodyClick(clickEvt(moreEl)); } catch (e) { ok(false, '收起无 JS 异常', e.message); }
  h = els.body._html;
  ok(countArt(h) === expectRows, '收起后恢复默认折叠', '实际 ' + countArt(h));
  ok(!h.includes(esc(bigJ.articles[api.max_per_card].title).slice(0, 30)),
     '收起后被折叠条目不再渲染');

  console.log('\n【全部展开 / 收起全部 总开关】');
  const ab = els.allbtn;
  const abClick = ab.listeners.click && ab.listeners.click[0];
  ok(!!abClick, '总开关已挂载点击事件');
  ok(!ab.hidden, '存在折叠卡片时总开关可见');
  ok(/全部展开/.test(ab.textContent), '初始文案为「全部展开」', ab.textContent);

  let err2 = null;
  try { abClick(); } catch (e) { err2 = e; }
  ok(!err2, '全部展开无 JS 异常', err2 && err2.message);
  h = els.body._html;
  // 全部展开 = 每卡下发多少就渲染多少（受服务端单卡上限保护）
  const allExp = flat(api).reduce(
    (n, j) => n + Math.min(j.articles.length, api.max_expand_per_card), 0);
  ok(countArt(h) === allExp, `全部展开后条目数 = ${allExp}`, '实际 ' + countArt(h));
  ok(countArt(h) === api.total_articles, `全部展开后已铺满 ${api.total_articles} 条`);
  ok(!h.includes('展开其余'), '全部展开后不再出现「展开其余」');
  ok(/收起全部/.test(ab.textContent), '文案切换为「收起全部」', ab.textContent);
  ok(!/undefined|NaN/.test(h), '全部展开后无 undefined / NaN 泄漏');

  // 逐卡收起一盏 → 总开关状态应复位为「未全开」
  const stillBig = flat(api).find(j => j.article_total > api.max_per_card);
  const m2 = withClassList(makeEl('more-' + stillBig.id));
  m2.classes.add('more');
  m2.attrs['data-more'] = stillBig.id;
  let err3 = null;
  try { bodyClick(clickEvt(m2)); } catch (e) { err3 = e; }
  ok(!err3, '逐卡收起无 JS 异常', err3 && err3.message);
  h = els.body._html;
  const afterOne = allExp - (stillBig.articles.length - api.max_per_card);
  ok(countArt(h) === afterOne, `逐卡收起后条目数 = ${afterOne}`, '实际 ' + countArt(h));
  ok(/全部展开/.test(ab.textContent), '逐卡操作后总开关复位为「全部展开」', ab.textContent);
  ok(h.includes('展开其余 ' + (stillBig.article_total - api.max_per_card) + ' 篇'),
     '逐卡收起的那张卡恢复折叠');

  console.log('\n【视图二 · 主题归档】');
  const topicTab = env.viewTabs[1];
  let clickErr = null;
  try { env.vbar.listeners.click[0](clickEvt(topicTab)); } catch (e) { clickErr = e; }
  ok(!clickErr, '切换视图无 JS 异常', clickErr && clickErr.message);
  await tick(); await tick();
  h = els.body._html;
  ok(topicTab.classes.has('on'), '视图切换按钮置为选中');
  ok(!/undefined|NaN/.test(h), '无 undefined / NaN 泄漏');
  const expectTopic = flat(api).filter(j => j.topic_total > 0).length;
  ok((h.match(/篇命中/g) || []).length === expectTopic,
     `有命中卡片数 = ${expectTopic}`);
  ok(!h.includes('无命中'), '无命中的刊在归档视图被隐藏');
  ok(h.includes('t-badge t-strong'), '含「强相关」分档徽章');
  ok(h.includes('t-badge t-mid') && h.includes('t-badge t-low'), '含「相关 / 一般」分档徽章');
  ok(els.legend._html.includes('强相关 ≥19') && els.legend._html.includes('一般 8–11'),
     '图例展示分档阈值');
  ok(els.sum._html.includes('归档命中'), '汇总行切换为归档口径');
  ok(els.vnote._html.includes(api.topic.window_from), '归档时间窗展示');
  ok(els.allbtn.hidden, '主题归档视图隐藏总开关（服务端已裁剪，铺不开）');

  const t0 = flat(api).find(j => j.topic_total).topic_articles[0];
  ok(h.includes(String(t0.score)), '归档条目显示相关度得分', 'score=' + t0.score);
  ok(h.includes(esc(t0.title).slice(0, 30)), '归档条目标题已渲染');
  ok(h.includes(t0.date_label), '归档条目日期已渲染', t0.date_label);
  const strongExp = flat(api).reduce((n, j) =>
    n + (j.topic_articles || []).filter(a => a.tier === 'strong').length, 0);
  ok((h.match(/t-badge t-strong">强相关/g) || []).length === strongExp,
     `强相关条目数 = ${strongExp}`);

  console.log('\n【区域筛选 × 归档视图 组合】');
  const intlTab = env.regionTabs.find(t => t.attrs['data-region'] === '国际');
  try { els.bar.listeners.click[0](clickEvt(intlTab)); }
  catch (e) { ok(false, '区域切换无 JS 异常', e.message); }
  await tick();
  h = els.body._html;
  ok(h.includes('国际期刊 · 有主题命中'), '区域 + 归档标签正确');
  ok(!h.includes('国内期刊'), '国内分组被正确过滤');

  // ── 合成载荷：覆盖真实数据里暂时不存在的边界分支 ──
  // 离线预览页把数据内嵌成了 PREVIEW_PAYLOAD（忽略 fetch），合成载荷对它无效，故跳过。
  console.log('\n【边界分支（合成载荷）】');
  if (scriptSrc.includes('PREVIEW_PAYLOAD')) {
    console.log('  — 跳过：本页为离线快照，数据已内嵌，合成载荷无法生效');
  } else {
    const synth = JSON.parse(JSON.stringify(api));
    const sf = flat(synth);
    sf[0].article_total = 0; sf[0].articles = []; sf[0].topic_total = 0;
    sf[1].article_total = 200;                     // 超出单卡上限
    const env2 = boot(synth);
    await tick(); await tick();
    const h2 = env2.els.body._html;
    ok(h2.includes(W.days + ' 天内无'), `空卡显示「${W.days} 天内无」徽章`);
    ok(new RegExp('近 ' + W.days + ' 天未获取到该刊文章').test(h2), '空卡有引导文案');
    ok(!h2.includes('展开其余 0 篇'), '空卡不出现「展开其余 0 篇」');
    const over = 200 - sf[1].articles.length;      // 服务端下发量之外的部分
    ok(h2.includes('另有 ' + over + ' 篇超出单卡展示上限'),
       '超上限卡有提示', '另有 ' + over + ' 篇');
    ok(h2.includes('展开其余 ' + (200 - 6) + ' 篇'), '超上限卡仍先折叠到 6 条');
  }

  console.log('\n' + '='.repeat(58));
  console.log(`结果：${pass} 通过 / ${fail} 失败`);
  process.exit(fail ? 1 : 0);
})();
