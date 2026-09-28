/* ═══════════════════════════════════════════════════════════════
 * kpdf/sync.js —— 跨视口同步绑定器
 *
 * 这是「左右同时滑动 / 同步缩放」的实现核心。
 *
 * ── 为什么不能按页号同步 ──────────────────────────────────────
 *  实测本项目同一篇论文：原文 22 页、中文版 10 页、双语版 10 页
 *  （且双语版页面宽 1190pt，是双栏拼接版式）。
 *  按页号同步时，原文第 15 页在对面根本没有对应页 —— 两岸页码越走越偏。
 *  老方案（iframe 内嵌浏览器原生阅读器 + Chromium postMessage 协议）
 *  正是按页号同步的，所以在这个场景下结构上就不可能对齐。
 *
 * ── 本实现的做法 ─────────────────────────────────────────────
 *  两侧各自算出**归一化锚点** {page, offset, progress}：
 *    page     0 起的浮点页序（3.4 = 第 4 页的 40% 处）
 *    offset   页内偏移比例 0~1
 *    progress 整篇进度 0~1
 *  再把源锚点**投影**到目标文档的页序空间（按页数比例映射，首尾锁死、单调）。
 *  这样页数 22 ↔ 10 也能保持「读到同一段内容」的相对位置。
 *
 * ── 防环与防抖（不写这三条，双栏就会互相抢滚动条抽风）──────────
 *  ① 程序化定位期间，目标视口的滚动事件带 programmatic 标记，直接忽略
 *  ② 目标位置与预期位置差值 < slop 时视为已对齐，不再回写
 *  ③ 同一帧内多次请求用 rAF 合并，只同步最后一次（避免滚动逐像素传声）
 * ═══════════════════════════════════════════════════════════════ */
import { clamp } from './util.js';
import { mapPageIndex } from './layout.js';

export const SYNC_MODE = { ANCHOR: 'anchor', PAGE: 'page', PROGRESS: 'progress' };
export const SCALE_MODE = { FIT: 'fit', EQUAL: 'equal' };

export const SYNC_MODE_LABEL = {
  anchor: '按相对位置（推荐，页数不同也能对齐）',
  page: '按页号（两侧页数一致时用）',
  progress: '按整篇进度',
};

/**
 * 锚点投影：把一个文档的锚点换算到另一个文档的页序空间。纯函数，可单测。
 *
 * ★ 关于 offset：跨页数不同的文档时，**不能**沿用源端的页内比例。
 *   两边页码不能一一对应时，页内比例的参照系已经变了 —— 例如源在 22 页的第 11 页
 *   底部（offset=1.0），对端映射到 10 页的第 5.29 页，若仍按 offset=1.0 定位，
 *   就会落到第 5 页的底部（实测偏差 0.71 页）。
 *   正确做法是：把「归一化位置」映射过去后，再从映射结果里**反推**页内比例。
 *   两侧页数相同时映射是恒等的，页内比例自然原样保留，不引入任何损失。
 */
export function projectAnchor(anchor, fromCount, toCount, mode = SYNC_MODE.ANCHOR) {
  const a = anchor || { page: 0, offset: 0, progress: 0 };
  if (toCount <= 0) return { page: 0, offset: 0, progress: 0 };

  if (mode === SYNC_MODE.PAGE) {
    // 严格同页号：两侧版式一一对应时最精确（例如双语版与原文同源同版式）。
    // 此模式下页内比例是有意义的，原样保留。
    const p = clamp(Math.floor(a.page), 0, toCount - 1);
    return { page: p + (a.offset || 0), offset: a.offset || 0, progress: a.progress || 0 };
  }

  const page = mode === SYNC_MODE.PROGRESS
    ? (a.progress || 0) * (toCount - 1)
    : mapPageIndex(a.page, fromCount, toCount);

  const clamped = clamp(page, 0, Math.max(0, toCount - 1));
  return {
    page: clamped,
    offset: clamped - Math.floor(clamped),   // ★ 由映射结果反推，而非沿用源端
    progress: toCount > 1 ? clamped / (toCount - 1) : 0,
  };
}

/** 同步偏差（用于状态栏显示「对齐偏差」与测试断言）。 */
export function anchorSkew(a, b, fromCount, toCount, mode = SYNC_MODE.ANCHOR) {
  const expect = projectAnchor(a, fromCount, toCount, mode);
  const dPage = Math.abs((b?.page || 0) - expect.page);
  const dOff = Math.abs((b?.offset || 0) - expect.offset);
  return { dPage, dOff };
}

export class SyncGroup {
  /**
   * @param {object} o
   *   mode       'anchor' | 'page' | 'progress'
   *   scaleMode  'fit'（保持同等「适配比例」，宽窄页面视觉一致）| 'equal'（缩放值相同）
   *   slop       位置差小于该值(px)视为已对齐，跳过回写
   *   enabled    是否启用（关掉即两边独立滚动，便于人工比对）
   */
  constructor({ mode = SYNC_MODE.ANCHOR, scaleMode = SCALE_MODE.FIT, slop = 1.5, enabled = true } = {}) {
    this.mode = mode;
    this.scaleMode = scaleMode;
    this.slop = slop;
    this.enabled = enabled;

    /** @type {Map<any, {view:any, off:Function, offScale:Function}>} */
    this._views = new Map();
    this._applying = new Set();   // 正在被程序化定位的视口
    this._locked = 0;             // lock() 计数
    this._raf = 0;
    this._pending = null;         // {source, kind, anchor, scale, by}
    this.lastSkew = null;         // 最近一次对齐偏差（状态栏/自检用）
    this.onSkew = null;           // (skew) => void
  }

  get size() { return this._views.size; }
  get views() { return Array.from(this._views.values(), (v) => v.view); }

  /** 绑定一个视口（View 实例）。 */
  add(view, { master = false } = {}) {
    if (this._views.has(view)) return () => {};

    const off = view.on('scroll', (e) => {
      if (e?.programmatic) return;
      this._schedule(view, 'scroll', { anchor: e.anchor });
    });

    const offScale = view.on('scale', (e) => {
      if (e?.programmatic) return;
      this._schedule(view, 'scale', { scale: e.scale, anchor: e.anchor, by: e.by });
    });

    this._views.set(view, { view, off, offScale, master });
    return () => this.remove(view);
  }

  remove(view) {
    const rec = this._views.get(view);
    if (!rec) return;
    rec.off?.();
    rec.offScale?.();
    this._views.delete(view);
  }

  /**
   * 批量操作期间暂停同步（例如程序化设置布局、初始化）。
   * 用计数而不是布尔，避免嵌套调用时提前解锁。
   */
  lock(fn) {
    this._locked++;
    try {
      return fn();
    } finally {
      this._locked--;
    }
  }

  setMode(mode) {
    if (mode && mode !== this.mode) {
      this.mode = mode;
      this.resync();
    }
  }

  setScaleMode(mode) {
    if (mode && mode !== this.scaleMode) {
      this.scaleMode = mode;
      this.resync();
    }
  }

  /** 以第一个视口为基准，重新对齐其余视口。 */
  resync() {
    const list = this.views;
    if (list.length < 2) return;
    const src = list[0];
    this._apply(src, { anchor: src.getAnchor() }, { includeSource: false });
  }

  /**
   * 同一帧内合并多次同步请求。
   *
   * ★ 闸门只看「发起方自己是否正被程序化定位」，不能写成 `_applying.size`：
   *   后者会因为**别的**视口正在被定位而把一次真实的用户滚动丢掉。
   *   而 _applying 是在下一帧才清理的，所以这个窗口是真实存在的（实测复现）。
   */
  _schedule(source, kind, payload) {
    if (!this.enabled || this._locked || this._applying.has(source)) return;
    this._pending = { source, kind, ...payload };
    if (this._raf) return;
    this._raf = requestAnimationFrame(() => {
      this._raf = 0;
      const p = this._pending;
      this._pending = null;
      if (!p) return;
      this._apply(p.source, p, { includeSource: false });
    });
  }

  /**
   * 把源视口的状态投影到其余视口。
   * @param {any} source 源视口
   * @param {object} payload {anchor?, scale?, by?}
   * @param {object} o {includeSource}
   */
  _apply(source, payload, { includeSource = false } = {}) {
    if (!this.enabled) return;
    const srcRec = this._views.get(source);
    if (!srcRec) return;

    const fromCount = source.doc.numPages;
    const srcAnchor = payload.anchor || source.getAnchor();
    let worst = { dPage: 0, dOff: 0 };

    for (const rec of this._views.values()) {
      const target = rec.view;
      if (target === source && !includeSource) continue;

      const toCount = target.doc.numPages;
      this._applying.add(target);
      try {
        if (payload.kind === 'scale' && payload.scale) {
          // 缩放同步：'fit' 保持同等适配比例（宽页与窄页视觉一致），'equal' 用同一缩放值
          const ep = target.syncScale(payload.scale, source, this.scaleMode);
          target.applyScale(ep, { silent: true });
        }
        const anchor = projectAnchor(srcAnchor, fromCount, toCount, this.mode);
        const res = target.applyAnchor(anchor, { silent: true, returnSkew: true });
        if (res) {
          const skew = anchorSkew(srcAnchor, res.actual, fromCount, toCount, this.mode);
          if (skew.dPage > worst.dPage) worst = skew;
        }
      } catch (err) {
        console.error('[kpdf] 同步定位失败：', err);
      } finally {
        // 让目标视口的 programmatic 标记多存活一帧：滚动事件是异步派发的
        requestAnimationFrame(() => this._applying.delete(target));
      }
    }

    this.lastSkew = worst;
    this.onSkew?.(worst);
  }

  /**
   * 调试快照。
   * 「两边没同步上」时先看这里：是 enabled 关了、还是被闸门拦了、
   * 还是有待处理的帧没跑。浏览器里可直接 window.kpdf.sync.debugState()。
   */
  debugState() {
    return {
      enabled: this.enabled,
      mode: this.mode,
      scaleMode: this.scaleMode,
      views: this.views.length,
      applying: this._applying.size,
      locked: this._locked,
      hasPending: Boolean(this._pending),
      hasRaf: Boolean(this._raf),
      lastSkew: this.lastSkew,
    };
  }

  destroy() {
    for (const rec of this._views.values()) {
      rec.off?.();
      rec.offScale?.();
    }
    this._views.clear();
    if (this._raf) cancelAnimationFrame(this._raf);
    this._raf = 0;
  }
}
