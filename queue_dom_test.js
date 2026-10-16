/* ============================================================================
 * 翻译队列页（modules/queue/templates/queue.html）渲染测试
 *
 * 自建轻量 DOM 垫片 + fetch 桩，不引入 jsdom —— 与 theme_dom_test.js 同一套路。
 * 重点验证「队列里有任务时」的渲染：
 *   · 五种状态徽章（排队中/翻译中/已完成/失败/已取消）
 *   · 排队位次、进度条宽度、耗时、结果链接
 *   · 统计卡数字、顶部徽章与状态文字
 *   · 空态、队列不可用降级、HTML 转义
 *
 * 运行：node queue_dom_test.js
 * ========================================================================== */
'use strict';
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const HTML = path.join(__dirname, 'modules', 'queue', 'templates', 'queue.html');

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
const has = (s, sub) => String(s).indexOf(sub) >= 0;

/* ── 极简 DOM ── */
function El(tag) {
  return {
    tagName: String(tag || 'div').toUpperCase(),
    id: '', className: '', style: {},
    children: [], _text: '', _html: '',
    setAttribute(k, v) { if (k === 'id') this.id = String(v); },
    get textContent() { return this._text; },
    set textContent(v) { this._text = String(v); },
    get innerHTML() { return this._html; },
    set innerHTML(v) { this._html = String(v); },
  };
}

function makeSandbox(payload) {
  const els = { body: El('div'), badge: El('span'), st: El('span') };
  const timers = [];
  const sandbox = {
    console,
    document: {
      getElementById(id) { return els[id] || null; },
    },
    fetch() {
      return Promise.resolve({ json: () => Promise.resolve(payload) });
    },
    setTimeout(fn, ms) { timers.push({ fn, ms }); return timers.length; },
    clearTimeout() { /* 轮询在测试里不需要真跑 */ },
    Promise, Date, Math, JSON, String, Number, Array, Object, Error,
  };
  return { els, timers, sandbox };
}

function extractScript(html) {
  // theme.js 是 <script src="...">（带属性），只会匹配到无属性的内联块
  const m = html.match(/<script>([\s\S]*?)<\/script>/);
  if (!m) throw new Error('未找到内联脚本');
  return m[1];
}

async function run(payload) {
  const code = extractScript(fs.readFileSync(HTML, 'utf-8'));
  const { els, timers, sandbox } = makeSandbox(payload);
  vm.createContext(sandbox);
  vm.runInContext(code, sandbox);
  // 等 fetch().then() 的微任务链跑完
  await new Promise((r) => setTimeout(r, 20));
  return { els, timers };
}

/* ── 测试数据 ── */
const DOC_WITH_JOBS = {
  ok: true, queue_size: 2,
  stats: { queued: 1, translating: 2, done: 2, failed: 1, cancelled: 1,
           total: 7, active: 3, by_kind: { translate: 5, stitch: 2 } },
  items: [
    { job_id: 'a', kind: 'translate', display: '翻译', name: 'paper_translating.pdf',
      status: 'translating', progress: 42.0, percent: 42.0,
      created_at: 1757724000, elapsed: 12.5, message: '翻译中',
      original_url: '/uploads/a.pdf', mono_url: null, dual_url: null, queue_position: 0 },
    { job_id: 'b', kind: 'translate', display: '翻译', name: 'paper_queued.pdf',
      status: 'queued', progress: 0, percent: 0,
      created_at: 1757724100, elapsed: 0, message: '等待中...', queue_position: 2 },
    { job_id: 'c', kind: 'translate', display: '翻译', name: 'paper_done.pdf',
      status: 'done', progress: 100, percent: 100,
      created_at: 1757720000, ended_at: 1757720300, elapsed: 300,
      original_url: '/uploads/c.pdf', mono_url: '/translated/c_mono.pdf',
      dual_url: '/translated/c_dual.pdf', queue_position: 0 },
    { job_id: 'd', kind: 'translate', display: '翻译', name: 'paper_failed.pdf',
      status: 'failed', progress: 30, percent: 30,
      created_at: 1757721000, elapsed: 20, error: 'BabelDOC 崩溃', queue_position: 0 },
    { job_id: 'e', kind: 'translate', display: '翻译', name: 'paper_cancelled.pdf',
      status: 'cancelled', progress: 10, percent: 10,
      created_at: 1757722000, elapsed: 5, queue_position: 0 },
    // 统一队列新增的第二类任务：图像拼合（吃 GPU，与翻译同一条串行通道）
    { job_id: 'f', kind: 'stitch', display: '拼合', name: '拼合 91 张',
      status: 'translating', progress: 0, percent: 17.0,
      created_at: 1757724200, elapsed: 8, message: '匹配优化中',
      original_url: null, mono_url: null, dual_url: null, queue_position: 0 },
    // ★ 已完成的拼合任务：带 result（成品图指引）—— 队列页要能点进去看成图。
    //   字段与 web_app._queue_snapshot 的白名单一致（filename/url/download_url/
    //   size_kb/width/height），不带 meta（那是模块 tasks 表的完整版）。
    { job_id: 'g', kind: 'stitch', display: '拼合', name: '拼合 T2-C 91 张',
      status: 'done', progress: 0, percent: 100,
      created_at: 1757724300, ended_at: 1757724800, elapsed: 500,
      original_url: null, mono_url: null, dual_url: null, queue_position: 0,
      result: { filename: 'pano_abc123456789.png',
                url: '/api/stitch/result/pano_abc123456789.png',
                download_url: '/api/stitch/result/pano_abc123456789.png/dl',
                public_url: 'http://<nas-host>/<share>',
                public_download_url: 'http://<nas-host>/<share>',
                size_kb: 46592, width: 9876, height: 5432 } },
  ],
};
const TITLE = {
  ok: true, state: 'idle', running: false, coverage_pct: 99.0, cached: 208,
  pending: 2, batch_size: 5, duration_sec: 95, last_finished: '2026-09-13 08:40',
  translated_this_run: 0, failed_this_run: 0, recent_failed: [],
};
const PAYLOAD_BUSY = { ok: true, active: true, doc: DOC_WITH_JOBS, title: TITLE };

/* ── 用例 ── */
(async function main() {
  const html = fs.readFileSync(HTML, 'utf-8');

  section('页面骨架');
  ok(has(html, 'id="body"'), '存在 #body 容器');
  ok(has(html, '<script src="/theme.js"></script>'), '同步引入主题引擎 theme.js');
  ok(!/script[^>]*\bdefer\b/.test(html) && !/script[^>]*\basync\b/.test(html),
    '主题脚本未加 defer/async（避免闪深色）');
  ok(has(html, "fetch('/api/queue'"), '请求统一队列接口 /api/queue');
  ok(has(html, '统一任务队列'), '含「统一任务队列」小节');
  ok(has(html, '标题翻译队列'), '含「标题翻译队列」小节');

  section('有任务时：五种状态渲染');
  const { els, timers } = await run(PAYLOAD_BUSY);
  const out = els.body.innerHTML;
  ok(has(out, 'jbadge translating'), '进行中 → .jbadge.translating');
  ok(has(out, 'jbadge queued'), '排队中 → .jbadge.queued');
  ok(has(out, 'jbadge done'), '已完成 → .jbadge.done');
  ok(has(out, 'jbadge failed'), '失败 → .jbadge.failed');
  ok(has(out, 'jbadge cancelled'), '已取消 → .jbadge.cancelled');
  ok(has(out, '翻译中 42%'), '徽章带实时百分比（翻译中 42%）');
  ok(has(out, 'width:42%'), '进度条宽度 = 42%');
  ok(has(out, 'width:100%'), '已完成的任务进度条 100%');

  section('统一队列：两类任务同列');
  ok(has(out, 'kchip k-trans') || has(out, 'class="kchip k-trans"'), '翻译任务带种类徽标');
  ok(has(out, '拼合中 17%'), '拼合任务显示「拼合中 17%」（措辞随种类变）');
  ok(has(out, '>拼合<'), '拼合任务种类名渲染为「拼合」');
  ok(has(out, '>翻译<'), '翻译任务种类名渲染为「翻译」');

  section('拼合已完成：成品图入口（走 NAS 入口）');
  ok(has(out, '>查看成图<'), '已完成拼合任务带「查看成图」链接');
  ok(has(out, 'href="http://<nas-host>/<share>"'),
    '查看链接用 NAS 对外入口的绝对地址（不是本机相对路径）');
  ok(has(out, 'href="http://<nas-host>/<share>"'),
    '下载链接同样走 NAS 入口（/dl 附件下载）');
  ok(!has(out, 'href="/api/stitch/result/pano_abc123456789.png"'),
    '不再输出相对路径链接（相对路径从别人机器上看就是本机，分享即废）');
  ok(has(out, '9876×5432'), '显示成品图尺寸（9876×5432）');
  ok(has(out, '45.5MB'), '显示成品图体积（46592KB → 45.5MB）');
  // 运行中的拼合任务不该出现成品图入口（后端 result 为 None）
  ok((out.match(/查看成图/g) || []).length === 1,
    '仅已完成任务有「查看成图」（运行中任务不出现）',
    '出现 ' + (out.match(/查看成图/g) || []).length + ' 次');

  // 老快照只有相对 url（没有 public_url）时不能变成死链接
  const legacy = await run({ ok: true, active: false,
    doc: { ok: true, queue_size: 0, stats: {}, items: [
      { job_id: 'y', kind: 'stitch', display: '拼合', name: '拼合 老快照',
        status: 'done', progress: 0, percent: 100,
        result: { filename: 'pano_old.png', url: '/api/stitch/result/pano_old.png',
                  width: 100, height: 50 } } ] },
    title: TITLE });
  ok(has(legacy.els.body.innerHTML, 'href="/api/stitch/result/pano_old.png"'),
    '老快照（只有相对 url）退回相对链接，不裂');

  section('有任务时：位次 / 耗时 / 链接');
  ok(has(out, '第 2 位'), '排队任务显示排队位次（第 2 位）');
  // fmtDur 会取整：12.5s → 13s；300s → 5m
  ok(has(out, '13s') && has(out, '5m'), '显示已用耗时（12.5s→13s，300s→5m）');
  // ★ 链接一律走自研阅读器 /PDFreader?file=…&peer=…（encodeURI 不转义 '/'），
  //   所以断言的是 peer=/file= 参数而不是直链 —— 旧断言写直链，早已失效。
  ok(has(out, 'file=/uploads/a.pdf'), '原文链接（走自研阅读器）');
  ok(has(out, 'peer=/translated/c_mono.pdf'), '已完成带中文版对照链接');
  ok(has(out, 'peer=/translated/c_dual.pdf'), '已完成带双语版对照链接');
  ok(has(out, 'BabelDOC 崩溃'), '失败任务显示错误原因');

  section('统计与顶部状态');
  ok(has(out, '<div class="v">1 <small>个</small></div>'), '统计卡渲染数字（排队中 1 个）');
  ok(has(out, '<div class="v">2 <small>个</small></div>'), '统计卡渲染队列长度（2 个）');
  ok(has(out, '队列长度'), '含队列长度指标');
  ok(els.badge.textContent === '进行中', '顶部徽章 = 进行中', els.badge.textContent);
  ok(els.st.textContent === '3 个任务进行中', '头部状态 = 3 个任务进行中', els.st.textContent);
  ok(timers.length === 1 && timers[0].ms === 2000, '活跃时 2s 轮询', JSON.stringify(timers));

  section('标题队列部分');
  ok(has(out, '99%'), '标题覆盖率 99%');
  ok(has(out, '208'), '累计缓存 208');
  ok(has(out, '每批 5 条'), '批次大小');
  ok(has(out, '术语一致性'), '术语一致性说明');

  section('空态 / 降级');
  const empty = await run({ ok: true, active: false, doc: { ok: true, queue_size: 0, stats: {}, items: [] }, title: TITLE });
  ok(has(empty.els.body.innerHTML, '当前没有任务'), '队列为空时显示空态文案');
  ok(empty.els.badge.textContent === '空闲', '空闲时徽章 = 空闲', empty.els.badge.textContent);

  const noQueue = await run({ ok: true, active: false, doc: { ok: false, unavailable: true, items: [], message: '未接入统一任务队列' }, title: TITLE });
  ok(has(noQueue.els.body.innerHTML, '未接入统一任务队列'), '队列未注入时降级提示（不崩）');

  section('转义');
  const xss = await run({ ok: true, active: false,
    doc: { ok: true, queue_size: 0, stats: {}, items: [{ job_id: 'x', name: '<img src=x onerror=alert(1)>.pdf', status: 'queued', progress: 0 }] },
    title: TITLE });
  const x = xss.els.body.innerHTML;
  ok(!has(x, '<img src=x'), '文件名中的 HTML 被转义（不注入）');
  ok(has(x, '&lt;img'), '转义为 &lt;img');

  console.log('\n' + '\u2500'.repeat(46));
  console.log(`结果：${pass} 通过 / ${fail} 失败`);
  if (fail) { console.log('失败项：\n  - ' + failures.join('\n  - ')); process.exit(1); }
})();
