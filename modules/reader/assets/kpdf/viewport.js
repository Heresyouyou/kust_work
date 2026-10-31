/* ═══════════════════════════════════════════════════════════════
 * kpdf/viewport.js —— 视口引擎
 *
 * 一个 View = 一个独立的滚动阅读区。它把「文档模型」和「布局」变成屏幕上
 * 真正能滚、能缩、能选中的东西。要点：
 *
 *  · **虚拟化**：只为可视范围（含预取窗）创建页面槽位与画布，
 *    滚动条长度由内容层尺寸保证 —— 所以 500 页文档和 20 页文档一样顺。
 *  · **锚点读写**：对外只暴露 {page, offset, progress} 这种与缩放无关的锚点，
 *    同步绑定器因此完全不需要知道 DOM 的存在。
 *  · **缩放保持阅读位置**：缩放时以「当前视口顶部锚点」为不动点重排，
 *    否则每放大一次就会跳到别处（体验上最容易被察觉的退化）。
 *  · **文本层 / 链接层**：按需挂载，可通过 options 关掉以省内存。
 * ═══════════════════════════════════════════════════════════════ */
import { Emitter } from './events.js';
import { PageRenderer } from './renderer.js';
import {
  computeLayout, fitScale, anchorAt, posOfAnchor, axisOf, sizeAt, rotateSizes,
} from './layout.js';
import { clamp, rafThrottle, el } from './util.js';

/** 预取窗口系数：视野外再预画这么多，滚动时不至于看到空白闪烁。 */
const PREFETCH_RATIO = 0.9;

/**
 * 归一化一段引文，便于在文本层里做包含匹配：
 * 去空白（PDF 换行、两端对齐的额外空格）、去软连字符、转小写。
 */
function normQuote(s) {
  return String(s || '')
    .replace(/[\s\u00a0\u00ad\u200b]+/g, '')
    .toLowerCase();
}

export class View extends Emitter {
  /**
   * @param {object} o
   *   doc          KDocument
   *   container    挂载容器（元素）
   *   label        标题（原文 / 译文）
   *   mode         布局模式
   *   fit          'page' | 'width' | 'none'
   *   scale        初始缩放（fit !== 'none' 时会被覆盖）
   *   textLayer    是否挂文本层（默认 true）
   *   links        是否渲染链接（默认 true）
   *   gap / pad    间距
   *   background   页面底色（默认白——刻意不跟主题走，见项目主题铁律）
   *   renderConcurrency / maxLiveCanvas
   */
  constructor({
    doc, container, label = '', mode = 'continuous', fit = 'width', scale = 1,
    textLayer = true, links = true, gap = 14, pad = 18,
    background = 'rgb(255,255,255)', renderConcurrency = 2, maxLiveCanvas = 22,
    interactive = true,
  } = {}) {
    super();
    this.doc = doc;
    this.label = label;
    this.mode = mode;
    this.fitMode = fit;
    this.scale = scale;
    this.rotation = 0;
    this.useTextLayer = textLayer;
    this.useLinks = links;
    this.gap = gap;
    this.pad = pad;
    this.background = background;
    this.interactive = interactive;

    this._container = container || document.body;
    this._layout = null;
    this._slots = new Map();       // index → {box, root, canvas, textLayer, linksBuilt, renderToken}
    this._current = 1;
    this._expect = null;           // 程序化滚动的预期位置（用于抑制回环）
    this._suppressUntil = 0;
    this._destroyed = false;

    this._buildDom();

    this.renderer = new PageRenderer({
      doc,
      concurrency: renderConcurrency,
      maxLive: maxLiveCanvas,
      onReady: (i, canvas) => this._paint(i, canvas),
      onRelease: (i) => this._unpaint(i),
      onError: (i, err) => this.emit('pageerror', { page: i + 1, error: err }),
    });

    this._onScrollThrottled = rafThrottle(() => this._handleScroll());
    this._onResizeThrottled = rafThrottle(() => this.handleResize());

    this._el.addEventListener('scroll', this._onScrollThrottled, { passive: true });
    this._resizeObs = new ResizeObserver(this._onResizeThrottled);
    this._resizeObs.observe(this._el);

    if (interactive) this._bindInteractions();
  }

  // ── DOM ─────────────────────────────────────────────────────
  _buildDom() {
    this._el = el('div.kp-view', {
      tabindex: '0',
      role: 'region',
      'aria-label': this.label ? `${this.label} 阅读区` : 'PDF 阅读区',
    });
    this._content = el('div.kp-content');
    this._el.append(this._content);
    this._container.append(this._el);
  }

  _bindInteractions() {
    // 键盘：翻页 / 缩放 / 首末页
    this._el.addEventListener('keydown', (e) => {
      const axis = this._layout?.axis || 'y';
      const step = this._el.clientHeight * 0.9;
      const map = {
        PageDown: () => this.scrollBy(step),
        PageUp: () => this.scrollBy(-step),
        ArrowDown: () => this.scrollBy(axis === 'y' ? 60 : 0, axis === 'x' ? 60 : 0),
        ArrowRight: () => this.scrollBy(axis === 'x' ? 60 : 0, axis === 'y' ? 0 : 0),
        Home: () => this.applyAnchor({ page: 0, offset: 0 }),
        End: () => this.applyAnchor({ page: this.doc.numPages - 1, offset: 1 }),
        '+': () => this.setScale(this.scale * 1.15, { by: 'keyboard' }),
        '=': () => this.setScale(this.scale * 1.15, { by: 'keyboard' }),
        '-': () => this.setScale(this.scale / 1.15, { by: 'keyboard' }),
        '0': () => this.fit(this.fitMode === 'none' ? 'page' : this.fitMode),
      };
      const fn = map[e.key];
      if (fn) { e.preventDefault(); fn(); }
    });

    // Ctrl/⌘ + 滚轮缩放（触控板双指捏合也会走这条）
    this._el.addEventListener('wheel', (e) => {
      if (!e.ctrlKey && !e.metaKey) return;
      e.preventDefault();
      const factor = e.deltaY < 0 ? 1.1 : 1 / 1.1;
      this.setScale(this.scale * factor, { by: 'wheel' });
    }, { passive: false });
  }

  // ── 尺寸 ────────────────────────────────────────────────────
  get viewSize() {
    return {
      w: this._el.clientWidth || 1,
      h: this._el.clientHeight || 1,
    };
  }

  get axis() { return axisOf(this.mode); }

  _viewSizeOnAxis() {
    if (this._suppressUntil > performance.now()) { /* 抑制窗内不重排 */ }
    return this.axis === 'y' ? this.viewSize.h : this.viewSize.w;
  }

  get scrollPos() {
    return this.axis === 'y' ? this._el.scrollTop : this._el.scrollLeft;
  }

  // ── 布局 ────────────────────────────────────────────────────
  /**
   * 重排。keepAnchor 为真时保持当前阅读位置（缩放、切模式、容器变化都靠它）。
   */
  relayout({ keepAnchor = true, anchor = null } = {}) {
    const sizes = rotateSizes(this.doc.sizeList(), this.rotation);
    const { w, h } = this.viewSize;
    const keep = anchor || (keepAnchor ? this.getAnchor() : null);

    this._layout = computeLayout({
      sizes,
      scale: this.scale,
      mode: this.mode,
      gap: this.gap,
      pad: this.pad,
      containerW: w,
      containerH: h,
    });

    this._content.style.width = `${this._layout.width}px`;
    this._content.style.height = `${this._layout.height}px`;

    // 槽位跟随新布局重排
    for (const [i, slot] of this._slots) {
      const box = this._layout.boxes[i];
      if (!box) { this._removeSlot(i); continue; }
      slot.box = box;
      Object.assign(slot.root.style, {
        left: `${box.x}px`, top: `${box.y}px`, width: `${box.w}px`, height: `${box.h}px`,
      });
      // 缩放变了 → 已挂的文本层/画布作废
      // 旋转变了也要作废：renderer.isReady 含 rotation，画布会按新旋转重画，
      // 但 _mountTextLayer 见 slot.textLayer 已存在会提前返回，文本层会残留
      // 旧旋转几何（span 位置按旧视口算）→ 划词与笔画错位。
      if (slot.renderedScale !== undefined && (Math.abs(slot.renderedScale - this.scale) > 1e-3 || slot.textRotation !== this.rotation)) {
        this._teardownSlotLayers(i);
      }
      this.renderer?.setProtected(this._slots.keys());
    }

    if (keep) this.applyAnchor(keep, { silent: true, programmatic: false });
    this.emit('layout', { layout: this._layout, scale: this.scale, mode: this.mode });
    this._updateVisible();
    return this._layout;
  }

  handleResize() {
    if (this._destroyed) return;
    this.relayout({ keepAnchor: true, anchor: this.getAnchor() });
  }

  // ── 锚点 ────────────────────────────────────────────────────
  /** 当前阅读位置（与缩放无关）。 */
  getAnchor() {
    if (!this._layout) return { page: 0, offset: 0, progress: 0 };
    return anchorAt(this._layout, this.scrollPos, this._viewSizeOnAxis());
  }

  /**
   * 定位到锚点。
   * @returns {{actual: object}} 实际落点（供同步绑定器算对齐偏差）
   */
  applyAnchor(anchor, { silent = false, programmatic = false } = {}) {
    if (!this._layout) this.relayout({ keepAnchor: false });
    const pos = posOfAnchor(this._layout, anchor);
    const max = Math.max(0, (this.axis === 'y' ? this._layout.height : this._layout.width) - this._viewSizeOnAxis());
    const target = clamp(pos, 0, max);

    if (programmatic || silent) this._expect = { pos: target, t: performance.now() };
    if (this.axis === 'y') this._el.scrollTop = target;
    else this._el.scrollLeft = target;

    const actual = this.getAnchor();
    if (!silent) this.emit('anchor', { anchor: actual });
    return { actual };
  }

  scrollBy(dy, dx) {
    if (dy) this._el.scrollTop += dy;
    if (dx) this._el.scrollLeft += dx;
  }

  gotoPage(n, { offset = 0 } = {}) {
    const i = clamp((n | 0) - 1, 0, this.doc.numPages - 1);
    return this.applyAnchor({ page: i, offset });
  }

  get page() { return this._current; }

  // ── 缩放 ────────────────────────────────────────────────────
  /** 「适配比例为 1」时的缩放值：fit 模式下的 100% 基准。 */
  baseScale(fitMode = this.fitMode) {
    const size = this.doc.commonSize || this.doc.pages[0]?.size;
    if (!size) return this.scale;
    const { w, h } = this.viewSize;
    return fitScale({
      mode: fitMode === 'none' ? 'width' : fitMode,
      size,
      containerW: w,
      containerH: h,
    });
  }

  /** 适配比例（1 = 恰好适配）。UI 显示「适合宽度 100%」用这个。 */
  get fitRatio() {
    // 处于适配模式时恒为 1：此时 scale 就是按当前容器算出来的适配值。
    // 不能拿 scale/commonSize 基准去比 —— 页尺寸不一致的文档会算出 1200% 这种怪数。
    if (this.fitMode && this.fitMode !== 'none') return 1;
    const base = this.baseScale();
    return base ? this.scale / base : 1;
  }

  /**
   * 设置缩放。以当前锚点为不动点。
   * @returns {boolean} 是否真的变了
   */
  setScale(scale, { anchor = null, by = 'api', silent = false } = {}) {
    const next = clamp(scale, 0.1, 8);
    if (Math.abs(next - this.scale) < 1e-3) return false;
    const keep = anchor || this.getAnchor();
    this.scale = next;
    this.fitMode = 'none';   // 手动缩放即脱离适配模式

    this.emit('scale', { scale: next, anchor: keep, by });
    this.relayout({ keepAnchor: true, anchor: keep });
    if (!silent) this.emit('scaled', { scale: next, fitRatio: this.fitRatio });
    return true;
  }

  /** 同步绑定器专用：静默套用缩放（不回发 scale 事件，避免环）。 */
  applyScale(scale, { silent = true } = {}) {
    const next = clamp(scale, 0.1, 8);
    if (Math.abs(next - this.scale) < 1e-3) return false;
    const keep = this.getAnchor();
    this.scale = next;
    this.fitMode = 'none';
    this._suppressUntil = performance.now() + 80;
    this.relayout({ keepAnchor: true, anchor: keep });
    if (!silent) this.emit('scaled', { scale: next, fitRatio: this.fitRatio });
    return true;
  }

  /**
   * 由对端缩放值换算本视口的缩放值。
   *  'fit'   → 保持同等**适配比例**：原文页窄、双语页宽（实测 1190pt vs 595pt），
   *            同比例才是「看到一样多的内容」，直接套用同一数值反而会一边过小一边过大
   *  'equal' → 直接用同一个缩放值
   */
  syncScale(peerScale, peerView, scaleMode = 'fit') {
    if (scaleMode === 'equal') return peerScale;
    const peerRatio = peerView.baseScale() ? peerScale / peerView.baseScale() : 1;
    return clamp(peerRatio * this.baseScale(), 0.1, 8);
  }

  /** 适配。 */
  fit(mode = 'page') {
    this.fitMode = mode;
    if (mode === 'none') return this.scale;
    const { w, h } = this.viewSize;
    const list = this.doc.sizeList();
    // 以当前页尺寸为基准（各页尺寸差异大的文档，比首屏基准更合理）
    const size = list[this._current - 1] || this.doc.commonSize || list[0];
    const next = fitScale({ mode, size, containerW: w, containerH: h });
    this.scale = next;
    this.relayout({ keepAnchor: true, anchor: this.getAnchor() });
    this.emit('scaled', { scale: next, fitRatio: 1, fit: mode });
    return next;
  }

  setMode(mode) {
    if (!mode || mode === this.mode) return;
    const keep = this.getAnchor();
    this.mode = mode;
    this.relayout({ keepAnchor: true, anchor: keep });
    this.emit('modechange', { mode });
  }

  setRotation(deg) {
    const next = ((deg % 360) + 360) % 360;
    if (next === this.rotation) return;
    this.rotation = next;
    for (const slot of this._slots.values()) slot.rotation = next;
    this.relayout({ keepAnchor: true, anchor: this.getAnchor() });
    this.emit('rotation', { rotation: next });
  }

  setTextLayer(on) {
    this.useTextLayer = Boolean(on);
    if (!this.useTextLayer) {
      for (const [i] of this._slots) this._teardownTextLayer(i);
    } else {
      this._updateVisible();
    }
  }

  /**
   * 在一段时间内把本视口的滚动视为「程序化」的，不回发给同步绑定器。
   * 用于同步开关切换、批量布局变更后的一次性防抖（避免两边互相抢滚动）。
   */
  suppressEcho(ms = 120) {
    this._suppressUntil = performance.now() + ms;
  }

  // ── 滚动事件 ────────────────────────────────────────────────
  _handleScroll() {
    if (this._destroyed) return;
    const pos = this.scrollPos;
    const exp = this._expect;
    let programmatic = performance.now() < this._suppressUntil;
    if (exp && performance.now() - exp.t < 500 && Math.abs(pos - exp.pos) <= 3) {
      programmatic = true;
      this._expect = null;
    }
    this.emit('scroll', { anchor: this.getAnchor(), pos, programmatic });
    this._updateVisible();
  }

  // ── 虚拟化与渲染 ────────────────────────────────────────────
  _visibleRange() {
    const L = this._layout;
    if (!L) return { from: 0, to: -1, vis: new Set() };
    const axis = L.axis;
    const pos = this.scrollPos;
    const size = this._viewSizeOnAxis();
    const pre = Math.max(280, size * PREFETCH_RATIO);
    const lo = pos - pre;
    const hi = pos + size + pre;
    const vis = new Set();
    for (const b of L.boxes) {
      const start = b[axis];
      const len = axis === 'y' ? b.h : b.w;
      if (start < hi && start + len > lo) vis.add(b.i);
    }
    const arr = Array.from(vis);
    return { from: arr.length ? Math.min(...arr) : 0, to: arr.length ? Math.max(...arr) : -1, vis, lo, hi };
  }

  _updateVisible() {
    if (this._destroyed || !this._layout) return;
    const axis = this._layout.axis;
    const pos = this.scrollPos;
    const size = this._viewSizeOnAxis();
    const center = pos + size / 2;
    const { vis } = this._visibleRange();

    // 视野中心所在的页（状态栏与同步用）
    let best = this._current;
    let bestD = Infinity;
    for (const i of vis) {
      const b = this._layout.boxes[i];
      const c = b[axis] + (axis === 'y' ? b.h : b.w) / 2;
      const d = Math.abs(c - center);
      if (d < bestD) { bestD = d; best = i + 1; }
    }
    if (best !== this._current) {
      this._current = best;
      this.emit('pagechange', { page: best, total: this.doc.numPages });
    }

    // 建槽 + 排队渲染（按到视野中心的距离定优先级）
    for (const i of vis) {
      const slot = this._ensureSlot(i);
      if (!slot) continue;
      const b = this._layout.boxes[i];
      const c = b[axis] + (axis === 'y' ? b.h : b.w) / 2;
      const priority = Math.abs(c - center);
      const ready = this.renderer.isReady(i, { scale: this.scale, rotation: this.rotation });
      if (!ready) {
        this.renderer.render(i, {
          scale: this.scale, rotation: this.rotation,
          background: this.background, dprCap: 2,
        }).catch(() => { /* 取消/失败已在 renderer 内处理 */ });
      }
      slot.priority = priority;
    }
    this.renderer.setProtected(vis);

    // 回收：离视野太远的槽位连 DOM 一起拆掉（画布由 renderer 的 LRU 另行回收）
    for (const [i, slot] of Array.from(this._slots)) {
      if (vis.has(i)) continue;
      const b = this._layout.boxes[i];
      if (!b) { this._removeSlot(i); continue; }
      const start = b[axis];
      const len = axis === 'y' ? b.h : b.w;
      const far = start + len < pos - size * 1.5 || start > pos + size * 2.5;
      if (far) {
        this.renderer.release(i);
        this._removeSlot(i);
      }
    }
  }

  _ensureSlot(index) {
    const hit = this._slots.get(index);
    if (hit) return hit;
    const box = this._layout?.boxes[index];
    if (!box) return null;

    const root = el('div.kp-slot', { 'data-page': index + 1 });
    const ph = el('div.kp-placeholder', {}, [`第 ${index + 1} 页`]);
    root.append(ph);
    Object.assign(root.style, {
      left: `${box.x}px`, top: `${box.y}px`, width: `${box.w}px`, height: `${box.h}px`,
    });
    this._content.append(root);
    const slot = { index, box, root, placeholder: ph, canvas: null, textLayer: null, linksBuilt: false, rotation: this.rotation };
    this._slots.set(index, slot);
    return slot;
  }

  _removeSlot(index) {
    const slot = this._slots.get(index);
    if (!slot) return;
    this._teardownSlotLayers(index);
    slot.root.remove();
    this._slots.delete(index);
  }

  /** 画布就绪 → 贴进槽位（并挂文本层 / 链接层）。 */
  _paint(index, canvas) {
    const slot = this._slots.get(index);
    if (!slot || this._destroyed) return;
    canvas.style.width = '100%';
    canvas.style.height = '100%';
    canvas.style.display = 'block';
    if (slot.canvas !== canvas) {
      slot.canvas?.remove();
      slot.canvas = canvas;
      slot.root.prepend(canvas);
    }
    slot.placeholder?.remove();
    slot.renderedScale = this.scale;
    this.emit('pagerendered', { page: index + 1 });

    if (this.useTextLayer) this._mountTextLayer(index);
    if (this.useLinks) this._mountLinks(index);
  }

  _unpaint(index) {
    const slot = this._slots.get(index);
    if (!slot) return;
    this._teardownSlotLayers(index);
    if (!slot.placeholder) {
      slot.placeholder = el('div.kp-placeholder', {}, [`第 ${index + 1} 页`]);
      slot.root.append(slot.placeholder);
    }
  }

  _teardownTextLayer(index) {
    const slot = this._slots.get(index);
    if (!slot?.textLayer) return;
    try { slot.textLayer.cancel?.(); } catch { /* 忽略 */ }
    slot.textLayerEl?.remove();
    slot.textLayer = null;
    slot.textLayerEl = null;
  }

  _teardownSlotLayers(index) {
    this._teardownTextLayer(index);
    const slot = this._slots.get(index);
    if (!slot) return;
    slot.linksEl?.remove();
    slot.linksEl = null;
    slot.linksBuilt = false;
    slot.canvas?.remove();
    slot.canvas = null;
    slot.hoverLayerEl?.remove();   // 缩放/旋转后字框作废，色带必须跟着清
    slot.hoverLayerEl = null;
    slot.renderedScale = undefined;
    slot.textRotation = undefined;
  }

  async _mountTextLayer(index) {
    const slot = this._slots.get(index);
    if (!slot || slot.textLayer || this._destroyed) return;
    try {
      const kp = await this.doc.page(index);
      if (!this._slots.has(index) || slot.textLayer) return;
      const layerEl = el('div.kp-textLayer', { 'data-page': index + 1 });
      slot.root.append(layerEl);
      const handle = kp.createTextLayer(layerEl, { scale: this.scale, rotation: this.rotation });
      slot.textLayerEl = layerEl;
      slot.textLayer = handle;
      slot.textRotation = this.rotation;   // 记录挂载时的旋转：relayout 据此判断文本层是否作废
      await handle.promise;
      this.emit('textlayer', { page: index + 1 });
    } catch (err) {
      // 文本层失败不影响看图，只记录
      console.warn(`[kpdf] 第 ${index + 1} 页文本层失败：`, err);
    }
  }

  /** 只渲染 Link 类标注：自己实现，比引入 pdf.js 的 AnnotationLayer + 整套 CSS 轻得多。 */
  async _mountLinks(index) {
    const slot = this._slots.get(index);
    if (!slot || slot.linksBuilt || this._destroyed) return;
    slot.linksBuilt = true;
    try {
      const kp = await this.doc.page(index);
      const annots = await kp.annotations();
      const layer = el('div.kp-linkLayer');
      let n = 0;
      for (const a of annots) {
        if (a.subtype !== 'Link' || !a.rect) continue;
        const r = kp.rectToCss(a.rect, { scale: this.scale, rotation: this.rotation });
        if (r.width < 2 || r.height < 2) continue;
        const href = a.url || (a.dest ? `#page=${index + 1}` : null);
        if (!href) continue;
        const link = el('a.kp-link', {
          href,
          target: a.url ? '_blank' : null,
          rel: a.url ? 'noopener' : null,
          title: a.url || '文档内跳转',
          style: {
            left: `${r.left}px`, top: `${r.top}px`,
            width: `${r.width}px`, height: `${r.height}px`,
          },
        });
        layer.append(link);
        n++;
      }
      if (n) {
        slot.root.append(layer);
        slot.linksEl = layer;
      }
    } catch (err) {
      console.warn(`[kpdf] 第 ${index + 1} 页链接层失败：`, err);
    }
  }

  /** 检索高亮：在已挂载的文本层 span 上做大小写无关标记。 */
  highlight(query) {
    const q = String(query || '').trim().toLowerCase();
    let count = 0;
    for (const [, slot] of this._slots) {
      const layerEl = slot.textLayerEl;
      if (!layerEl) continue;
      layerEl.querySelectorAll('span').forEach((sp) => {
        sp.classList.remove('kp-hit');
        if (!q) return;
        if (sp.textContent.toLowerCase().includes(q)) { sp.classList.add('kp-hit'); count++; }
      });
    }
    return count;
  }

  /**
   * 跳到第 page 页，并高亮 quote 所在的段落（导读的「P.x」用它）。
   *
   * 文本层是虚拟化的：目标页可能还没挂载，所以这里轮询等待（最多 tries×100ms）。
   * 匹配失败不抛错——只保留页码跳转，导读那边的点击体验不受影响。
   * 匹配用「去空白 + 转小写」后的整页拼接串，因此跨行的句子也能对上；
   * 原文有软连字符、断词时逐级缩短前缀重试。
   */
  focusQuote(page, quote, { tries = 30 } = {}) {
    const q = normQuote(quote);
    if (!q) return Promise.resolve(0);
    const index = Math.max(0, Math.min(this.doc.numPages - 1, (page | 0) - 1));
    return new Promise((resolve) => {
      let n = 0;
      const tick = () => {
        if (this._destroyed) { resolve(0); return; }
        const hit = this._markQuote(index, q);
        if (hit) { resolve(hit); return; }
        if (++n >= tries) { resolve(0); return; }
        setTimeout(tick, 100);
      };
      tick();
    });
  }

  /** 在某一页的文本层里标记与 q（已归一化）匹配的 span，返回标记数。 */
  _markQuote(index, q) {
    const slot = this._slots.get(index);
    const layer = slot?.textLayerEl;
    if (!layer) return 0;
    const spans = Array.from(layer.querySelectorAll('span'));
    if (!spans.length) return 0;
    const norm = spans.map((sp) => normQuote(sp.textContent));
    const joined = norm.join('');
    if (!joined) return 0;
    const starts = [];
    let acc = 0;
    for (const t of norm) { starts.push(acc); acc += t.length; }

    // 先试完整片段，逐级缩短（PDF 断词、连字符、错字都会让长片段对不上）
    const probes = [];
    for (const L of [q.length, 90, 70, 50, 35, 24, 16, 12]) {
      if (L <= q.length && L >= 12) probes.push(q.slice(0, L));
    }
    let at = -1;
    let len = 0;
    for (const p of probes) {
      at = joined.indexOf(p);
      if (at >= 0) { len = p.length; break; }
    }
    if (at < 0) return 0;

    const end = at + len;
    let first = -1;
    let count = 0;
    for (let i = 0; i < spans.length; i++) {
      const s = starts[i];
      const e = s + norm[i].length;
      if (!norm[i] || e <= at || s >= end) continue;
      spans[i].classList.add('kp-hit', 'kp-hit-current');
      if (first < 0) first = i;
      count++;
    }
    if (first >= 0) {
      try {
        spans[first].scrollIntoView({ block: 'center', behavior: 'smooth' });
      } catch { /* 老浏览器不支持 options，忽略 */ }
    }
    return count;
  }


  /** 清掉划词镜像的高亮。 */
  clearMirror() {
    try {
      if (window.CSS && CSS.highlights) CSS.highlights.delete('kpdf-mirror');
    } catch (e) { /* 忽略 */ }
    for (const [, slot] of this._slots) {
      const layer = slot.textLayerEl;
      if (!layer) continue;
      layer.querySelectorAll('span.kp-mirror').forEach((sp) => sp.classList.remove('kp-mirror'));
    }
  }

  // ── 悬停整句高亮：一行一个矩形 ───────────────────────────────
  /**
   * 客户端坐标 → 某页的页面本地坐标（可越界），slack 是外扩容差（像素）。
   *
   * 悬停吸附靠它：指针不必压在字上，落在行旁、行距里 10px 以内，
   * 也能算出「离哪一页多远、页面内哪个点最近」。
   */
  pagePointAt(cx, cy, slack = 0) {
    let best = null;
    for (const [i, slot] of this._slots) {
      const r = slot.root.getBoundingClientRect();
      if (!r.width || !r.height) continue;
      const dx = Math.max(r.left - cx, 0, cx - r.right);
      const dy = Math.max(r.top - cy, 0, cy - r.bottom);
      const d = Math.hypot(dx, dy);
      if (d > slack) continue;
      if (!best || d < best.d) {
        best = { page: i + 1, d, x: cx - r.left, y: cy - r.top, w: r.width, h: r.height };
      }
    }
    return best;
  }

  /**
   * 一段 Range → 「一行一个矩形」，坐标是本页本地 CSS 像素。
   *
   * 高亮和选区不是一回事：选区跟着字形走，逐字/逐 span 拼出来，边缘坑坑洼洼；
   * 高亮要的是一条条齐整的色带，所以这里按行聚类，每行只出一个矩形：
   *   · 左、右 = 该行最左/最右的字框边（句子跨几行就出几个矩形）；
   *   · 上、下 = 该行**最高的那个字框**（行高），不是每个字自己的框。
   * 这样一行看过去就是一个完整矩形，不会出现高低不齐的碎块。
   */
  lineRectsOf(page, range) {
    const index = Math.max(0, Math.min(this.doc.numPages - 1, (page | 0) - 1));
    const slot = this._slots.get(index);
    if (!slot || !range) return [];
    const pr = slot.root.getBoundingClientRect();
    if (!pr.width || !pr.height) return [];

    const raw = [];
    for (const r of range.getClientRects()) {
      if (r.width < 0.5 || r.height < 0.5) continue;
      raw.push({ x: r.left - pr.left, y: r.top - pr.top, w: r.width, h: r.height });
    }
    if (!raw.length) return [];
    raw.sort((a, b) => (a.y + a.h / 2) - (b.y + b.h / 2));

    // 同行的判据：垂直中心足够近（阈值跟行内较高的那个框走，容许上下标）
    const rows = [];
    for (const r of raw) {
      const cy = r.y + r.h / 2;
      let row = null;
      for (const q of rows) {
        if (Math.abs(q.cy - cy) <= Math.max(2, 0.5 * Math.max(q.h, r.h))) { row = q; break; }
      }
      if (!row) {
        rows.push({ cy, top: r.y, h: r.h, x0: r.x, x1: r.x + r.w });
        continue;
      }
      if (r.h > row.h + 0.5) { row.h = r.h; row.top = r.y; row.cy = cy; }
      row.x0 = Math.min(row.x0, r.x);
      row.x1 = Math.max(row.x1, r.x + r.w);
    }
    rows.sort((a, b) => a.top - b.top);
    return rows.map((q) => ({ x: q.x0, y: q.top, w: q.x1 - q.x0, h: q.h }));
  }

  /** 某一页的悬停色带层（按需创建，永远不吃鼠标事件）。 */
  _hoverLayer(slot) {
    if (slot.hoverLayerEl && slot.hoverLayerEl.isConnected) return slot.hoverLayerEl;
    const layer = el('div.kp-hoverLayer', { 'data-page': String(slot.index + 1) });
    slot.root.append(layer);
    slot.hoverLayerEl = layer;
    return layer;
  }

  /**
   * 画悬停色带。rects 是 lineRectsOf 的结果（本页本地坐标）。
   * 返回承载色带的图层元素，调用方用它判断「还挂在页上吗」。
   */
  paintHover(page, rects, { sent = '' } = {}) {
    const index = Math.max(0, Math.min(this.doc.numPages - 1, (page | 0) - 1));
    const slot = this._slots.get(index);
    if (!slot || !rects || !rects.length) return null;
    const layer = this._hoverLayer(slot);
    layer.textContent = '';
    if (sent === '' || sent === null) layer.removeAttribute('data-sent');
    else layer.dataset.sent = String(sent);
    const frag = document.createDocumentFragment();
    for (const r of rects) {
      const d = el('div.kp-hoverRect');
      d.style.left = `${r.x.toFixed(2)}px`;
      d.style.top = `${r.y.toFixed(2)}px`;
      d.style.width = `${r.w.toFixed(2)}px`;
      d.style.height = `${r.h.toFixed(2)}px`;
      frag.append(d);
    }
    layer.append(frag);
    return layer;
  }

  /** 清掉所有悬停色带（两栏都清）。 */
  clearHoverPaint() {
    for (const [, slot] of this._slots) {
      slot.hoverLayerEl?.remove();
      slot.hoverLayerEl = null;
    }
  }

  /** 取某一页已挂载的文本层元素（虚拟化，没挂载就返回 null）。 */
  textLayer(page) {
    const idx = Math.max(0, Math.min(this.doc.numPages - 1, (page | 0) - 1));
    return this._slots.get(idx)?.textLayerEl || null;
  }

  /**
   * 字符级高亮（划词镜像首选）：在指定页里把 text 这段文字精确标出来。
   *
   * 之前按 span 标会导致「一句话把整行都染上」（PDF 文本层一行常是一个 span）。
   * 这里改用 CSS Custom Highlight API：把 span 拼成一条带索引的字符串，
   * 定位到起止字符后再还原成 DOM Range，只有这句话的字符会变色。
   * boxes（句子版面框，0–1）用来在同页出现多次时挑对那一处。
   */
  highlightTextRange(page, text, boxes) {
    const index = Math.max(0, Math.min(this.doc.numPages - 1, (page | 0) - 1));
    const slot = this._slots.get(index);
    const layer = slot?.textLayerEl;
    if (!layer) return 0;
    this.clearMirror();

    const spans = Array.from(layer.querySelectorAll('span'));
    let joined = '';
    const map = [];
    for (const sp of spans) {
      const node = sp.firstChild;
      if (!node || node.nodeType !== 3) continue;
      const t = node.nodeValue || '';
      for (let i = 0; i < t.length; i++) {
        const ch = t[i];
        if (/[\s\u00a0\u00ad\u200b]/.test(ch)) continue;
        joined += ch.toLowerCase();
        map.push([node, i]);
      }
    }
    const q0 = normQuote(text);
    if (!joined || q0.length < 4) return 0;

    // 逐级缩短：译文与原文偶尔差一个标点，长片段对不上时退一步
    let at = -1;
    let len = 0;
    for (const L of [q0.length, 120, 90, 60, 40, 24, 16]) {
      if (L > q0.length || L < 8) continue;
      const q = q0.slice(0, L);
      let pos = joined.indexOf(q);
      if (pos >= 0) {
        // 有句子框时，挑起点落在框内的那一处（同页重复文本的最优解）
        if (boxes && boxes.length) {
          const pr = slot.root.getBoundingClientRect();
          const hit = [];
          while (pos >= 0) {
            hit.push(pos);
            pos = joined.indexOf(q, pos + 1);
          }
          const inside = hit.find((p) => {
            const r = map[p][0].parentElement.getBoundingClientRect();
            const cx = (r.left + r.width / 2 - pr.left) / (pr.width || 1);
            const cy = (r.top + r.height / 2 - pr.top) / (pr.height || 1);
            return boxes.some((b) => cx >= b[0] - 0.02 && cx <= b[2] + 0.02
              && cy >= b[1] - 0.02 && cy <= b[3] + 0.02);
          });
          pos = inside === undefined ? hit[0] : inside;
        }
        at = pos;
        len = q.length;
        break;
      }
    }
    if (at < 0) return 0;

    const start = map[at];
    const end = map[Math.min(at + len - 1, map.length - 1)];
    try {
      const range = document.createRange();
      range.setStart(start[0], start[1]);
      range.setEnd(end[0], end[1] + 1);
      if (window.CSS && CSS.highlights && window.Highlight) {
        CSS.highlights.set('kpdf-mirror', new Highlight(range));
        return len;
      }
      return this._markSpans(range) ? len : 0;
    } catch (e) {
      return 0;
    }
  }

  /** 不支持 Custom Highlight 时退化成 span 标记（会把整行染上，但不会漏）。 */
  _markSpans(range) {
    const layer = range.startContainer.parentElement?.closest('.kp-textLayer');
    if (!layer) return false;
    for (const sp of layer.querySelectorAll('span')) {
      if (range.intersectsNode(sp)) sp.classList.add('kp-mirror');
    }
    return true;
  }

  /**
   * 按**版面框**高亮（划词镜像首选路径）。
   *
   * boxes 是 `align.py` 给的归一化矩形 [fx0,fy0,fx1,fy1]（相对页宽高）。
   * 相比「整页文字搜索」，它不会因为同一句话在页面上出现两次而标错位置，
   * 也不会把相邻段落的字带进来 —— 句子的框是算出来的，不是猜的。
   */
  highlightBoxes(page, boxes) {
    const index = Math.max(0, Math.min(this.doc.numPages - 1, (page | 0) - 1));
    const slot = this._slots.get(index);
    const layer = slot?.textLayerEl;
    if (!layer || !boxes || !boxes.length) return 0;
    const pr = slot.root.getBoundingClientRect();
    if (!pr.width || !pr.height) return 0;
    this.clearMirror();
    const rects = [];
    for (const b of boxes) {
      if (!b || b.length < 4) continue;
      rects.push({
        left: pr.left + b[0] * pr.width,
        top: pr.top + b[1] * pr.height,
        right: pr.left + b[2] * pr.width,
        bottom: pr.top + b[3] * pr.height,
      });
    }
    if (!rects.length) return 0;
    let n = 0;
    for (const sp of layer.querySelectorAll('span')) {
      const r = sp.getBoundingClientRect();
      if (r.width < 1 || r.height < 1) continue;
      const cx = r.left + r.width / 2;
      const cy = r.top + r.height / 2;
      const inside = rects.some((b) => cx >= b.left - 2 && cx <= b.right + 2
        && cy >= b.top - 2 && cy <= b.bottom + 2);
      if (inside) { sp.classList.add('kp-mirror'); n++; }
    }
    return n;
  }

  /**
   * 高亮某一页里的一段文字（划词镜像用）。
   *
   * 和 focusQuote 一样用「整页归一化拼接串 + 跨 span 覆盖」定位，区别是：
   *   · 只标这一页，不滚动（滚动会跟同步组打架）；
   *   · 找不到就返回 0，由调用方决定是否降级。
   */
  highlightText(page, text, { cls = 'kp-mirror' } = {}) {
    const index = Math.max(0, Math.min(this.doc.numPages - 1, (page | 0) - 1));
    const slot = this._slots.get(index);
    const layer = slot?.textLayerEl;
    if (!layer) return 0;
    layer.querySelectorAll('span.' + cls).forEach((sp) => sp.classList.remove(cls));
    const spans = Array.from(layer.querySelectorAll('span'));
    if (!spans.length) return 0;
    const norm = spans.map((sp) => normQuote(sp.textContent));
    const joined = norm.join('');
    const q = normQuote(text);
    if (!joined || q.length < 4) return 0;
    let at = -1;
    let len = 0;
    for (const L of [q.length, 140, 100, 70, 50, 32, 20, 14]) {
      if (L > q.length || L < 10) continue;
      at = joined.indexOf(q.slice(0, L));
      if (at >= 0) { len = L; break; }
    }
    if (at < 0) return 0;
    const end = at + len;
    const starts = [];
    let acc = 0;
    for (const t of norm) { starts.push(acc); acc += t.length; }
    let n = 0;
    for (let i = 0; i < spans.length; i++) {
      const a = starts[i];
      const b = a + norm[i].length;
      if (!norm[i] || b <= at || a >= end) continue;
      spans[i].classList.add(cls);
      n++;
    }
    return n;
  }

  destroy() {
    if (this._destroyed) return;
    this._destroyed = true;
    this._resizeObs?.disconnect();
    for (const i of Array.from(this._slots.keys())) this._removeSlot(i);
    this.renderer?.clear();
    this._el.remove();
    this.clear();
  }
}
