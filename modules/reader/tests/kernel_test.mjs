// ═══════════════════════════════════════════════════════════════
//  reader 内核回归测试（Node 环境，无需浏览器）
//
//  跑： node modules/reader/tests/kernel_test.mjs
//
//  验证：
//    1. 两套 vendor 内核（main / legacy）都能 import，导出我们依赖的 API
//    2. ★ 兼容性判定：main 包用了 Promise.try / Promise.withResolvers，
//       缺任一就得回退 legacy —— 这是 kernel-pdfjs.js 探测规则的依据
//    3. 真实论文 PDF（原文 / 中文 / 双语）能解析出页数与页面尺寸
//    4. 中文文本能正确抽取 —— 反向证明 cmaps 路径配对
//       （中文字体多为 CID 编码，cMapUrl 配错时文本会乱码或为空）
//
//  注意：Node 下**不能验证渲染**（缺 @napi-rs/canvas / DOMMatrix），
//        渲染正确性由浏览器冒烟测试覆盖（tests/browser_smoke.md）。
// ═══════════════════════════════════════════════════════════════
import { pathToFileURL } from 'node:url';
import { readFileSync, readdirSync, existsSync } from 'node:fs';
import { join } from 'node:path';

// 必须在 import 内核之前采样：legacy 包会往全局补 Promise.try
const MODERN_BEFORE = typeof Promise.try === 'function' && typeof Promise.withResolvers === 'function';

const ROOT = new URL('../../../', import.meta.url).pathname.replace(/^\/([A-Za-z]:)/, '$1');
const VENDOR = pathToFileURL(join(ROOT, 'modules/reader/assets/vendor/pdfjs') + '/').href;

let pass = 0, fail = 0;
const ok = (cond, msg) => {
  if (cond) { pass++; console.log('  \u2713 ' + msg); }
  else { fail++; console.log('  \u2717 ' + msg); }
};

// ── 1. 内核可加载性与 API 面 ──────────────────────────────
console.log('\n[1] 内核可加载性与 API 面');
const NEEDED = ['getDocument', 'GlobalWorkerOptions', 'TextLayer', 'Util'];
const builds = {};
for (const [name, file] of [['main', 'pdf.min.mjs'], ['legacy', 'legacy/pdf.min.mjs']]) {
  const mod = await import(VENDOR + file);
  builds[name] = mod;
  ok(typeof mod.version === 'string', `[${name}] 版本 = ${mod.version}`);
  const missing = NEEDED.filter((k) => !mod[k]);
  ok(missing.length === 0, `[${name}] 关键 API 齐备（缺失: ${missing.join(',') || '无'}）`);
  ok(typeof mod.TextLayer === 'function', `[${name}] TextLayer 是类（文本层用）`);
  ok(typeof mod.PixelsPerInch?.PDF_TO_CSS_UNITS === 'number',
    `[${name}] pt→CSS px 换算 = ${mod.PixelsPerInch.PDF_TO_CSS_UNITS.toFixed(4)}（96/72）`);
  mod.GlobalWorkerOptions.workerSrc = VENDOR + (name === 'legacy' ? 'legacy/pdf.worker.min.mjs' : 'pdf.worker.min.mjs');
  ok(mod.GlobalWorkerOptions.workerSrc.endsWith('.min.mjs'), `[${name}] workerSrc 指向本地 vendor`);
}

// ── 2. 兼容性判定（不回退就会白屏）────────────────────────
console.log('\n[2] 兼容性判定：main 包对 Promise 新 API 的依赖');
console.log(`  · 内核加载前本环境：Promise.try = ${typeof Promise.try}，`
  + `Promise.withResolvers = ${typeof Promise.withResolvers}`);
console.log(`  · 探测规则：二者都是 function → main 包；否则 → legacy 包`);
console.log(`  · 本环境判定结果 → ${MODERN_BEFORE ? 'main' : 'legacy'}`);
// 断言的是一条**设计不变式**：无论环境新老，回退目标 legacy 必须真的可用
ok(builds.legacy && typeof builds.legacy.getDocument === 'function',
  'legacy 包始终可用 —— 回退路径不会失效');

// ── 3/4. 真实 PDF 解析（Node 下用 legacy，pdf.js 官方建议）──
console.log('\n[3] 真实 PDF 解析');
const cands = [];
for (const [dir, suffix] of [['uploads', null], ['translated', '.zh.dual.pdf'], ['translated', '.zh.mono.pdf']]) {
  const d = join(ROOT, dir);
  if (!existsSync(d)) continue;
  const f = readdirSync(d).filter((n) => n.toLowerCase().endsWith('.pdf'))
    .find((n) => (suffix ? n.endsWith(suffix) : true));
  if (f) cands.push([dir === 'uploads' ? '原文' : (suffix.includes('dual') ? '双语' : '中文'), join(d, f)]);
}
ok(cands.length >= 2, `找到测试素材 ${cands.length} 份：${cands.map((c) => c[0]).join(' / ')}`);

const OPEN = {
  cMapUrl: VENDOR + 'cmaps/',
  cMapPacked: true,
  standardFontDataUrl: VENDOR + 'standard_fonts/',
  wasmUrl: VENDOR + 'wasm/',
  iccUrl: VENDOR + 'iccs/',
};

const pdfjs = builds.legacy;
const results = [];
for (const [label, p] of cands) {
  const data = new Uint8Array(readFileSync(p));
  // ★ 6.x 的 destroy 在 loadingTask 上，PDFDocumentProxy 没有 destroy()
  const task = pdfjs.getDocument({ data, ...OPEN });
  const doc = await task.promise;
  const page = await doc.getPage(1);
  const vp = page.getViewport({ scale: 1 });
  const tc = await page.getTextContent();
  const text = tc.items.map((i) => i.str).join('');
  const cjk = (text.match(/[\u4e00-\u9fff]/g) || []).length;
  results.push({ label, pages: doc.numPages, w: vp.width, h: vp.height, items: tc.items.length, cjk });

  ok(doc.numPages > 0, `[${label}] 页数 = ${doc.numPages}`);
  ok(vp.width > 200 && vp.height > 200, `[${label}] 首页尺寸 = ${vp.width.toFixed(1)} × ${vp.height.toFixed(1)} pt`);
  ok(tc.items.length > 20, `[${label}] 第 1 页文本块 = ${tc.items.length}`);
  if (label !== '原文') ok(cjk > 50, `[${label}] 抽出汉字 ${cjk} 个 → cmaps 路径正确`);

  const outline = await doc.getOutline().catch(() => null);
  const annots = await page.getAnnotations().catch(() => []);
  console.log(`       · 大纲 ${outline ? outline.length + ' 条' : '无'} · 第 1 页标注 ${annots.length} 个`
    + ` · 元数据 ${Object.keys(await doc.getMetadata().catch(() => ({})).info || {}).length} 项`);
  await task.destroy();
}

// ── 5. 三版本页数关系（决定同步映射策略）──────────────────
console.log('\n[4] 三版本页数关系（决定左右同步的映射策略）');
if (results.length >= 2) {
  console.log('  · ' + results.map((r) => `${r.label}=${r.pages}`).join('  '));
  const same = results.every((r) => r.pages === results[0].pages);
  console.log('  · ' + (same
    ? '页数一致 → 可用「严格同页号」策略（最精确）'
    : '页数不一致 → 必须用「归一化锚点」策略（sync.js 默认路径）'));
}

console.log(`\n结果：${pass} 通过 / ${fail} 失败\n`);
process.exit(fail ? 1 : 0);
