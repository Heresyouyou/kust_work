/* ═══════════════════════════════════════════════════════════════
 * kpdf/kernel-pdfjs.js —— 唯一一处依赖 pdf.js 的代码
 *
 * 为什么用 pdf.js 而不是从零解析 PDF：
 *   PDF 的字体子集、CID 编码、CMap、JPX/JBIG2 图像、加密、色彩空间……
 *   自己实现一遍是不可能在「高保真」上追平的。但 pdf.js 是**纯 JS 库**，
 *   它把页面光栅化到 <canvas>，不碰浏览器内置的 PDF 插件 ——
 *   所以「不依赖浏览器原生 PDF 阅读」这个目标是成立的，
 *   而阅读器的交互、布局、同步、UI 全部由本框架自己实现。
 *
 * ★ 内核包是**本地 vendor** 的（assets/vendor/pdfjs/），不外连任何 CDN：
 *   内网断网也必须能用，这是本项目的硬要求。
 *
 * ★ 兼容性策略（实测结论，别凭印象改）：
 *   main 包用了 Promise.try（4 处）与 Promise.withResolvers（27 处）。
 *   Promise.try 是相当新的 API —— 老浏览器（及 Node 22）都没有，
 *   缺了会在解析中途抛 "Promise.try is not a function"，表现为**白屏且无提示**。
 *   所以这里先做特性探测，再决定加载 main 还是 legacy；且 main 失败时
 *   兜底再试一次 legacy（双保险，因为探测不到的是「未来可能新增的依赖」）。
 *   验证脚本：modules/reader/tests/kernel_test.mjs
 * ═══════════════════════════════════════════════════════════════ */
import { defineKernel } from './kernel.js';

// ── 资源根：从本模块的 URL 推出来，跟挂载路径无关 ──
// 本文件位于 …/assets/kpdf/kernel-pdfjs.js → vendor 在 …/assets/vendor/pdfjs/
const VENDOR = new URL('../vendor/pdfjs/', import.meta.url);
const abs = (p) => new URL(p, VENDOR).href;

/** 1 PDF pt 对应多少 CSS px（96dpi / 72dpi）。pdf.js 官方也用它做 100% 基准。 */
export const CSS_UNITS = 96 / 72;

const BUILD_FILES = {
  main: { api: 'pdf.min.mjs', worker: 'pdf.worker.min.mjs' },
  legacy: { api: 'legacy/pdf.min.mjs', worker: 'legacy/pdf.worker.min.mjs' },
};

let _mod = null;
let _build = null;
let _loadError = null;
/** main 包需要的 Promise 静态方法是否齐备。 */
function supportsModernBuild() {
  return typeof Promise.try === 'function'
    && typeof Promise.withResolvers === 'function';
}

/**
 * 加载内核（带特性探测 + 失败回退），进程内只加载一次。
 * @param {string} [prefer] 'main' | 'legacy'，不传则自动判定
 */
export async function loadKernel(prefer) {
  if (_mod) return { mod: _mod, build: _build };

  const order = prefer ? [prefer] : (supportsModernBuild() ? ['main', 'legacy'] : ['legacy', 'main']);
  const tried = [];

  for (const name of order) {
    const f = BUILD_FILES[name];
    if (!f) continue;
    try {
      const mod = await import(/* @vite-ignore */ abs(f.api));
      mod.GlobalWorkerOptions.workerSrc = abs(f.worker);
      _mod = mod;
      _build = name;
      console.info(`[kpdf] 内核已加载：pdf.js ${mod.version}（${name} 包）`
        + (tried.length ? `；先试 ${tried.join('/')} 失败后回退` : ''));
      return { mod, build: name };
    } catch (err) {
      tried.push(name);
      _loadError = err;
      console.warn(`[kpdf] ${name} 包加载失败，尝试下一个构建：`, err);
    }
  }
  throw new Error(`kpdf: pdf.js 内核全部构建加载失败（${tried.join(', ')}）：${_loadError?.message}`);
}

/** 诊断信息（/api/reader/info 与页面自检面板用）。 */
export function kernelInfo() {
  const before = { promiseTry: typeof Promise.try, withResolvers: typeof Promise.withResolvers };
  return {
    vendor: VENDOR.href,
    loaded: Boolean(_mod),
    build: _build,
    version: _mod?.version || null,
    promiseSupport: before,
    expectedBuild: supportsModernBuild() ? 'main' : 'legacy',
  };
}

/** 公共打开参数：资源路径全部指向本地 vendor。 */
export function defaultOpenOptions() {
  return {
    cMapUrl: abs('cmaps/'),
    cMapPacked: true,
    standardFontDataUrl: abs('standard_fonts/'),
    wasmUrl: abs('wasm/'),
    iccUrl: abs('iccs/'),
    // 大文件按需分段取（Flask 的 send_file 支持 Range），首屏不必等整包下载
    rangeChunkSize: 262144,
    disableAutoFetch: false,
    disableStream: false,
    // 允许 FDF/XFA 表单页面走原生渲染，避免某些官方表单打不开
    enableXfa: true,
  };
}

// ═══════════════════════════════════════════════════════════════
//  KernelPage：页面级能力
// ═══════════════════════════════════════════════════════════════
class PdfjsKernelPage {
  /** @param {any} page pdf.js 的 PDFPageProxy */
  constructor(page, index) {
    this._page = page;
    this.index = index;
    const vp = page.getViewport({ scale: CSS_UNITS, rotation: page.rotate || 0 });
    this.width = vp.width;
    this.height = vp.height;
    this.rotate = page.rotate || 0;
    this.userUnit = page.userUnit || 1;
  }

  /**
   * 光栅化到 canvas。
   * @returns {{promise: Promise<void>, cancel: () => void}}
   */
  render(canvas, { scale = 1, dpr = 1, rotation = null, background = 'rgb(255,255,255)' } = {}) {
    const rot = rotation === null ? this.rotate : rotation;
    // ★ 用 CSS_UNITS 做基准：scale=1 即「100%」，与 pdf.js 官方查看器一致
    const viewport = this._page.getViewport({ scale: scale * CSS_UNITS, rotation: rot });
    const ctx = canvas.getContext('2d', { alpha: false });

    canvas.width = Math.max(1, Math.floor(viewport.width * dpr));
    canvas.height = Math.max(1, Math.floor(viewport.height * dpr));
    canvas.style.width = `${viewport.width}px`;
    canvas.style.height = `${viewport.height}px`;

    const task = this._page.render({
      canvasContext: ctx,
      viewport,
      background,
      transform: dpr !== 1 ? [dpr, 0, 0, dpr, 0, 0] : null,
    });
    // 取消后 pdf.js 会 reject 一个 RenderingCancelledException，调用方会忽略
    return { promise: task.promise, cancel: () => task.cancel() };
  }

  async textContent() {
    return this._page.getTextContent();
  }

  async annotations() {
    return this._page.getAnnotations({ intent: 'display' });
  }

  /**
   * 挂一个 pdf.js TextLayer 到容器上。返回的 `promise` 在文本层首次绘制完时 resolve。
   * pdf.js 6.x 的 TextLayer 是「把字符按 PDF 坐标铺到 DOM span 上」的内置组件，
   * 上层只管挂载与定位；这样我们不用自己解析变换矩阵。
   *
   * 文本层只在视觉上「像选择/搜索」时需要；如果不挂载，正文画布仍然完整，
   * 搜索与跳转可以走 getTextContent() 自己解析。
   */
  async createTextLayer(container, { scale = 1, rotation = null } = {}) {
    const rot = rotation === null ? this.rotate : rotation;
    const viewport = this._page.getViewport({ scale: scale * CSS_UNITS, rotation: rot });
    const tc = await this._page.getTextContent({ includeMarkedContent: false });
    const { mod } = await loadKernel();
    const { TextLayer } = mod;
    const layer = new TextLayer({ textContentSource: tc, container, viewport });
    // 6.x 的 render() 是 fire-and-forget：内部用 stream pump 把 span 铺到 DOM 上。
    // 我们暴露一个「render 调度完 + 一帧」就 resolve 的 promise，
    // 调用方 await 它之后就能拿到已经铺好的文本层（用来勾选 / 拷贝 / 搜索对齐）。
    const ready = (async () => {
      try {
        layer.render();
        await new Promise((r) => requestAnimationFrame(r));
      } catch { /* 单页文本失败不影响看图 */ }
    })();
    return { promise: ready, cancel: () => layer.cancel?.() };
  }

  /**
   * 把 PDF 坐标的矩形（pt，原点在左下）转成 CSS 像素（px，原点在左上）。
   * pdf.js 6.x 移除了 `viewport.convertToViewportRectangle`，改用对角两点
   * `convertToViewportPoint(x, y)` 自己算 —— 矩形可能因为旋转而翻转，
   * 取两点的最小/最大值即可。
   */
  rectToCss(rect, { scale = 1, rotation = null } = {}) {
    const rot = rotation === null ? this.rotate : rotation;
    const viewport = this._page.getViewport({ scale: scale * CSS_UNITS, rotation: rot });
    const [x1, y1, x2, y2] = rect;
    const p1 = viewport.convertToViewportPoint(x1, y1);
    const p2 = viewport.convertToViewportPoint(x2, y2);
    const left = Math.min(p1.x, p2.x);
    const top = Math.min(p1.y, p2.y);
    return { left, top, width: Math.abs(p2.x - p1.x), height: Math.abs(p2.y - p1.y) };
  }

  /** 页面应有的 CSS 尺寸（含 userUnit），布局引擎用它算占位高度。 */
  sizeAt(scale = 1, rotation = null) {
    const vp = this._page.getViewport({
      scale: scale * CSS_UNITS,
      rotation: rotation === null ? this.rotate : rotation,
    });
    return { width: vp.width, height: vp.height };
  }
}

// ═══════════════════════════════════════════════════════════════
//  KernelDoc：文档级能力
// ═══════════════════════════════════════════════════════════════
class PdfjsKernelDoc {
  constructor(task, doc) {
    this._task = task;   // ★ 6.x 的 destroy 在 loadingTask 上，doc 上没有
    this._doc = doc;
    this._pages = new Map();
  }

  get numPages() { return this._doc.numPages; }

  get fingerprints() { return this._doc.fingerprints || []; }

  async title() {
    const md = await this.metadata();
    const t = (md?.info?.Title || '').trim();
    return t || '';
  }

  async metadata() {
    try {
      return await this._doc.getMetadata();
    } catch {
      return { info: {} };
    }
  }

  async outline() {
    try {
      return (await this._doc.getOutline()) || null;
    } catch {
      return null;
    }
  }

  async getPage(index0) {
    const hit = this._pages.get(index0);
    if (hit) return hit;
    const page = await this._doc.getPage(index0 + 1);
    const wrap = new PdfjsKernelPage(page, index0);
    this._pages.set(index0, wrap);
    return wrap;
  }

  releasePage(index0) {
    this._pages.delete(index0);
  }

  /** 页数少时一次性把尺寸取齐（布局需要），页数多时由调用方限定范围。 */
  async warmPage(index0) {
    await this.getPage(index0);
  }

  async destroy() {
    this._pages.clear();
    try {
      await this._task.destroy();
    } catch {
      /* 已销毁 / 正在销毁，忽略 */
    }
  }
}

// ═══════════════════════════════════════════════════════════════
//  注册到契约层
// ═══════════════════════════════════════════════════════════════
defineKernel('pdfjs', async (source, opts = {}) => {
  const { onProgress, onPassword, ...rest } = opts;
  const { mod } = await loadKernel(opts.build);

  const params = { ...defaultOpenOptions(), ...rest };
  if (source.kind === 'url') params.url = source.url;
  else if (source.kind === 'data') params.data = source.data;
  else if (source.kind === 'file') {
    params.data = new Uint8Array(await source.file.arrayBuffer());
  } else {
    throw new Error(`kpdf: 不支持的内核来源 ${source.kind}`);
  }

  const task = mod.getDocument(params);
  if (typeof onProgress === 'function') task.onProgress = onProgress;
  if (typeof onPassword === 'function') {
    task.onPassword = (updatePassword, reason) => onPassword(updatePassword, reason);
  }

  let doc;
  try {
    doc = await task.promise;
  } catch (err) {
    // 统一错误语义，UI 才能给出人话提示
    const name = err?.name || '';
    if (name === 'PasswordException') {
      const e = new Error('该 PDF 有打开口令，需要密码');
      e.code = 'PASSWORD_REQUIRED';
      throw e;
    }
    if (name === 'InvalidPDFException') {
      const e = new Error('不是有效的 PDF，或文件已损坏');
      e.code = 'INVALID_PDF';
      throw e;
    }
    if (name === 'MissingPDFException') {
      const e = new Error('PDF 文件不存在或链接已失效');
      e.code = 'MISSING_PDF';
      throw e;
    }
    throw err;
  }

  return new PdfjsKernelDoc(task, doc);
});
