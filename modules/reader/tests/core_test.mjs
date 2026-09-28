// ═══════════════════════════════════════════════════════════════
//  reader 核心逻辑回归测试（纯 Node，无浏览器、无 DOM）
//
//  跑： node modules/reader/tests/core_test.mjs
//
//  能这么测，是因为框架刻意把「算位置」和「画像素」分开了：
//    layout.js / sync.js 只做数学，不碰 DOM → 可直接断言
//    viewport.js / renderer.js 才需要浏览器 → 由浏览器冒烟测试覆盖
//
//  重点验证的是**左右同步的语义**：页数不同的两份文档（22 vs 10），
//  按归一化锚点投影后，是否真能停在同一段内容上。
// ═══════════════════════════════════════════════════════════════
import {
  computeLayout, fitScale, anchorAt, posOfAnchor, mapPageIndex, rotateSizes, axisOf,
} from '../assets/kpdf/layout.js';
import { SyncGroup, projectAnchor, anchorSkew, SYNC_MODE, SCALE_MODE } from '../assets/kpdf/sync.js';

// Node 里补一个 rAF（sync.js 用它合并同帧请求）
globalThis.requestAnimationFrame = (cb) => setTimeout(() => cb(performance.now()), 0);

let pass = 0, fail = 0;
const ok = (cond, msg) => {
  if (cond) { pass++; console.log('  \u2713 ' + msg); }
  else { fail++; console.log('  \u2717 ' + msg); }
};
const near = (a, b, eps = 1e-6) => Math.abs(a - b) <= eps;
/**
 * 等待同步链跑完。
 * ⚠️ 不能用固定的 sleep(5ms)：sync.js 的同步是 rAF 批处理的，
 *    定时器与 rAF 的先后顺序在 Node 里不稳定 —— 实测同一份代码会时过时不过。
 *    这里显式等「若干轮 rAF + 宏任务」，把抖动消掉。
 */
const frame = async () => {
  for (let i = 0; i < 3; i++) {
    await new Promise((r) => requestAnimationFrame(() => r()));
    await new Promise((r) => setTimeout(r, 0));
  }
};

/** A4 页面在 scale=1 下的 CSS 尺寸（595.28pt × 96/72） */
const A4 = { width: 793.7, height: 1058.3 };
const WIDE = { width: 1587.4, height: 1058.3 };   // 双语版：双栏拼接，宽一倍

const mkSizes = (n, size = A4) => Array.from({ length: n }, () => ({ ...size }));

// ── 1. 布局引擎 ────────────────────────────────────────────
console.log('\n[1] 布局引擎（computeLayout）');
{
  const L = computeLayout({ sizes: mkSizes(5), scale: 1, mode: 'continuous', containerW: 900, gap: 10, pad: 20 });
  ok(L.axis === 'y', '纵向连续模式的主轴是 y');
  ok(L.boxes.length === 5, `生成 5 个页面方框（实际 ${L.boxes.length}）`);
  ok(L.boxes[0].y === 20, '首页从 pad=20 开始');
  ok(near(L.boxes[1].y, 20 + A4.height + 10), '页间距 = gap（10）');
  ok(near(L.height, 20 + 5 * A4.height + 4 * 10 + 20), `世界高度 = pad + Σ页高 + 间隙 + pad = ${L.height.toFixed(1)}`);
  ok(near(L.boxes[0].x, (900 - A4.width) / 2), '页面在容器内水平居中');
  ok(L.width === 900, '世界宽度取容器宽（够居中）');

  const S = computeLayout({ sizes: mkSizes(3), scale: 2, mode: 'continuous', containerW: 900 });
  ok(near(S.boxes[0].w, A4.width * 2), 'scale=2 时页宽翻倍');

  const H = computeLayout({ sizes: mkSizes(4), scale: 1, mode: 'horizontal', containerH: 700 });
  ok(H.axis === 'x', '横向单页模式的主轴是 x');
  ok(near(H.boxes[1].x, H.boxes[0].x + A4.width + 14), '横向模式按 x 排布');
  ok(H.width > H.height, '横向模式世界宽 > 高');

  const SP = computeLayout({ sizes: mkSizes(5), scale: 1, mode: 'spread', containerW: 1700 });
  ok(SP.boxes.length === 5, '双页对开不丢页');
  ok(SP.boxes[0].row === 0 && SP.boxes[1].row === 1, '首页单独成行（封面惯例）');
  ok(SP.boxes[1].y === SP.boxes[2].y, '第 2、3 页同行并排');
  ok(SP.boxes[2].x > SP.boxes[1].x, '同行第二页在第一页右侧');
  ok(axisOf('spread') === 'y', '对开模式主轴是 y（按行纵向堆叠，滚动沿 Y）');

  const R = rotateSizes(mkSizes(2), 90);
  ok(near(R[0].width, A4.height) && near(R[0].height, A4.width), '旋转 90° 后宽高互换');
  ok(rotateSizes(mkSizes(2), 180)[0].width === A4.width, '旋转 180° 尺寸不变');
}

// ── 2. 适配缩放 ────────────────────────────────────────────
console.log('\n[2] 适配缩放（fitScale）');
{
  const byW = fitScale({ mode: 'width', size: A4, containerW: 900, containerH: 700, margin: 0 });
  ok(near(byW, 900 / A4.width, 1e-6), `适合宽度 = ${byW.toFixed(4)}`);
  const byP = fitScale({ mode: 'page', size: A4, containerW: 900, containerH: 700, margin: 0 });
  ok(near(byP, Math.min(900 / A4.width, 700 / A4.height), 1e-6), `适合页面取较小者 = ${byP.toFixed(4)}`);
  ok(byP < byW, '适合页面比适合宽度小（纵向受限）');
}

// ── 3. 锚点往返一致性 ──────────────────────────────────────
console.log('\n[3] 锚点 ↔ 坐标往返（读位置与恢复位置必须无损）');
{
  const L = computeLayout({ sizes: mkSizes(20), scale: 1, mode: 'continuous', containerW: 900, containerH: 800 });
  const cases = [0, 137, 1200, 5000, L.height - 800, L.height];
  let worst = 0;
  for (const pos of cases) {
    const a = anchorAt(L, pos, 800);
    const back = posOfAnchor(L, a);
    worst = Math.max(worst, Math.abs(back - pos));
  }
  ok(worst < 1.0, `6 个位置往返最大误差 ${worst.toFixed(4)} px（< 1px）`);

  const mid = anchorAt(L, L.height / 2, 800);
  ok(mid.page > 9 && mid.page < 11, `文档正中间落在第 ${(Math.floor(mid.page) + 1)} 页`);
  ok(near(mid.progress, 0.5, 0.02) || mid.progress > 0.4, `进度读数 = ${mid.progress.toFixed(3)}`);
  ok(near(anchorAt(L, 0, 800).page, 0), '顶部锚点是第 1 页（page=0）');
}

// ── 4. 跨文档页序映射（22 页 ↔ 10 页）──────────────────────
console.log('\n[4] 跨文档页序映射（实测素材：原文 22 页 / 译文 10 页）');
{
  const maps = [[0, 0], [21, 9], [10.5, 4.5], [5.25, 2.25]];
  for (const [from, want] of maps) {
    const got = mapPageIndex(from, 22, 10);
    ok(near(got, want, 1e-9), `第 ${from + 1} 页 → 第 ${(got + 1).toFixed(2)} 页（期望 ${(want + 1).toFixed(2)}）`);
  }
  ok(near(mapPageIndex(0, 22, 10), 0) && near(mapPageIndex(21, 22, 10), 9), '首尾锁死：首页→首页、末页→末页');
  let mono = true;
  let prev = -1;
  for (let i = 0; i <= 22; i += 0.25) {
    const v = mapPageIndex(i, 22, 10);
    if (v < prev - 1e-9) mono = false;
    prev = v;
  }
  ok(mono, '映射单调（滚动过程不会来回跳）');
  ok(near(mapPageIndex(5, 10, 10), 5), '两侧页数相同 → 原样映射（不引入误差）');
}

// ── 5. 锚点投影的三种策略 ──────────────────────────────────
console.log('\n[5] 锚点投影策略');
{
  const a = { page: 10.5, offset: 0.25, progress: 0.5 };
  const anchored = projectAnchor(a, 22, 10, SYNC_MODE.ANCHOR);
  ok(near(anchored.page, 4.5, 1e-9), `按相对位置：10.5/22 → ${(anchored.page + 1).toFixed(2)}/10`);
  ok(near(anchored.offset, 0.5, 1e-9),
    '页内比例由映射结果反推（4.5 → offset 0.5），而不是沿用源端的 0.25 —— 否则会偏 0.71 页');

  // 页数相同 → 映射恒等，页内比例必须原样保留（不引入任何损失）
  const sameCount = projectAnchor({ page: 3.42, offset: 0.42 }, 22, 22, SYNC_MODE.ANCHOR);
  ok(near(sameCount.page, 3.42, 1e-9) && near(sameCount.offset, 0.42, 1e-9),
    '两侧页数相同时位置与页内比例都精确保持');

  const byPage = projectAnchor(a, 22, 10, SYNC_MODE.PAGE);
  ok(near(Math.floor(byPage.page), 9), '按页号：22 页文档的第 11 页 → 被夹到译文末页（严格同页号在页数不等时必然夹边）');
  ok(byPage.offset === 0.25, '按页号模式保留页内比例（两侧版式一一对应时它才是有意义的）');

  const byProg = projectAnchor(a, 22, 10, SYNC_MODE.PROGRESS);
  ok(near(byProg.page, 4.5, 1e-9), `按进度：50% → 第 ${(byProg.page + 1).toFixed(2)} 页`);

  const sk = anchorSkew(a, { page: 4.6, offset: 0.5 }, 22, 10, SYNC_MODE.ANCHOR);
  ok(near(sk.dPage, 0.1, 1e-9) && near(sk.dOff, 0, 1e-9),
    '偏差度量可用于 UI 显示与断言（页序差 0.1 页）');
  const perfect = anchorSkew(a, projectAnchor(a, 22, 10, SYNC_MODE.ANCHOR), 22, 10, SYNC_MODE.ANCHOR);
  ok(perfect.dPage < 1e-9 && perfect.dOff < 1e-9, '投影结果自身的偏差为 0（没引入额外误差）');
}

// ── 6. 同步绑定器端到端（用替身视口跑真逻辑）──────────────
console.log('\n[6] SyncGroup 端到端（含防环与单帧合并）');
{
  /** 用纯数学模型做 View 替身：只实现 SyncGroup 依赖的那几个方法。 */
  function makeView(label, count, size, viewSize = 800) {
    const layout = computeLayout({
      sizes: mkSizes(count, size), scale: 1, mode: 'continuous',
      containerW: 960, containerH: viewSize, gap: 14, pad: 18,
    });
    const listeners = new Map();
    const v = {
      label,
      doc: { numPages: count },
      layout,
      pos: 0,
      programmaticFrames: 0,
      applyCalls: 0,
      on(type, fn) {
        if (!listeners.has(type)) listeners.set(type, new Set());
        listeners.get(type).add(fn);
        return () => listeners.get(type).delete(fn);
      },
      _emit(type, payload) {
        for (const fn of listeners.get(type) || []) fn(payload);
      },
      getAnchor() { return anchorAt(layout, v.pos, viewSize); },
      applyAnchor(anchor, { silent = false } = {}) {
        v.applyCalls++;
        const pos = posOfAnchor(layout, anchor);
        const max = Math.max(0, layout.height - viewSize);
        v.pos = Math.min(Math.max(0, pos), max);
        // 程序化定位 → 后续原生滚动事件会被标记为 programmatic（真实实现同此语义）
        v._emit('scroll', { anchor: v.getAnchor(), pos: v.pos, programmatic: true });
        return { actual: v.getAnchor() };
      },
      /** 用户滚动（非程序化）*/
      userScrollTo(pos) {
        v.pos = Math.min(Math.max(0, pos), Math.max(0, layout.height - viewSize));
        v._emit('scroll', { anchor: v.getAnchor(), pos: v.pos, programmatic: false });
      },
      baseScale() {
        return fitScale({ mode: 'width', size: size, containerW: 960, containerH: viewSize });
      },
      syncScale(peerScale, peerView, mode = 'fit') {
        if (mode === SCALE_MODE.EQUAL) return peerScale;
        const r = peerScale / peerView.baseScale();
        return r * v.baseScale();
      },
      applyScale(s) { v.scale = s; },
      scale: 1,
    };
    return v;
  }

  // 原文 22 页（窄页 A4）↔ 译文 10 页（同样式，页数不同）
  const src = makeView('原文', 22, A4);
  const peer = makeView('译文', 10, A4);
  const group = new SyncGroup({ mode: SYNC_MODE.ANCHOR, scaleMode: SCALE_MODE.FIT });
  group.add(src);
  group.add(peer);

  // ① 用户滚到原文 50% 处 → 译文应到自己的 50% 附近
  src.userScrollTo(src.layout.height * 0.5);
  await frame();
  const srcPct = src.getAnchor().progress;
  const peerPct = peer.getAnchor().progress;
  ok(peer.applyCalls === 1, `发起方滚动 1 次 → 对端定位 1 次（实际 ${peer.applyCalls}）`);
  ok(Math.abs(peerPct - srcPct) < 0.05, `对齐后进度差 ${(Math.abs(peerPct - srcPct) * 100).toFixed(2)}% （< 5%）`);
  ok(group.lastSkew && group.lastSkew.dPage < 0.01,
    `对齐偏差 ${group.lastSkew?.dPage.toFixed(5)} 页（< 0.01 页 —— 状态栏据此显示「已对齐」）`);
  ok(peer.getAnchor().page > 3.5 && peer.getAnchor().page < 5.5,
    `译文停在第 ${(peer.getAnchor().page + 1).toFixed(2)} 页（22 页的中间 → 10 页的中段，符合预期）`);

  // ② 防环：程序化定位不应回传给发起方
  const srcCallsAfter = src.applyCalls;
  ok(srcCallsAfter === 0, '发起方没有被反向定位（无回环）');
  await frame();
  ok(src.applyCalls === 0 && peer.applyCalls === 1, '再等一帧仍无回环（无抖动）');

  // ③ 单帧合并：同帧内连滚 30 次，只应产生 1 次对端定位
  const before = peer.applyCalls;
  for (let i = 0; i < 30; i++) src.userScrollTo(300 + i * 40);
  await frame();
  ok(peer.applyCalls - before === 1, `同帧 30 次滚动只同步 1 次（实际 ${peer.applyCalls - before}）—— 滚动不会被逐像素传声`);

  // ④ 反向：从译文滚 → 原文跟随
  const srcBefore = src.applyCalls;
  peer.userScrollTo(peer.layout.height * 0.25);
  await frame();
  if (src.applyCalls - srcBefore !== 1) {
    console.log('   调试 · 反向同步未生效',
      { srcCalls: src.applyCalls, srcBefore, peerCalls: peer.applyCalls },
      group.debugState(),
      'peerAnchor=', peer.getAnchor(), 'srcAnchor=', src.getAnchor());
  }
  ok(src.applyCalls - srcBefore === 1, '反向同步同样生效（译文滚 → 原文跟）');
  ok(Math.abs(src.getAnchor().progress - peer.getAnchor().progress) < 0.06,
    `反向对齐后进度差 ${(Math.abs(src.getAnchor().progress - peer.getAnchor().progress) * 100).toFixed(2)}%`);

  // ⑤ 按页号策略在页数不等时的表现（说明为何默认不用它）
  group.setMode(SYNC_MODE.PAGE);
  src.userScrollTo(src.layout.boxes[20].y);
  await frame();
  ok(peer.getAnchor().page > 8.5, '按页号策略：原文第 21 页 → 译文末页（页数不等的固有缺陷，故默认不启用）');

  // ⑥ 关掉同步后两边彻底独立
  group.setMode(SYNC_MODE.ANCHOR);
  group.enabled = false;
  const peerBefore = peer.applyCalls;
  src.userScrollTo(1000);
  await frame();
  ok(peer.applyCalls === peerBefore, '关闭同步后对端不再被驱动');
  group.enabled = true;

  // ⑦ 解除绑定后不再接收
  group.remove(peer);
  const peerAfterRemove = peer.applyCalls;
  src.userScrollTo(2000);
  await frame();
  ok(peer.applyCalls === peerAfterRemove, 'remove() 后彻底解绑（监听已摘掉）');
}

console.log(`\n结果：${pass} 通过 / ${fail} 失败\n`);
process.exit(fail ? 1 : 0);
