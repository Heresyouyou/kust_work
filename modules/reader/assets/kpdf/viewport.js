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
      // 尺寸变了 → 已挂的文本层/画布作废
      if (slot.renderedScale !== undefined && Math.abs(slot.renderedScale - this.scale) > 1e-3) {
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
    slot.renderedScale = undefined;
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
