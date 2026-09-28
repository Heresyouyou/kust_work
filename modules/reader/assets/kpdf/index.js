/* ═══════════════════════════════════════════════════════════════
 * kpdf/index.js —— 框架装配层与公共入口
 *
 * 用法（两行）：
 *   import { createReader } from '/api/reader/assets/kpdf/index.js';
 *   const reader = await createReader('#host', {
 *     panes: [{ label: '原文', src: '/uploads/a.pdf' },
 *             { label: '译文', src: '/translated/a.zh.dual.pdf' }],
 *   });
 *
 * 装配顺序是有讲究的：先开文档 → 再预热尺寸 → 才有布局 → 才能建视口 →
 * 才能绑同步。反过来会在拿不到页面尺寸时算出错误的滚动高度，
 * 表现为「滚动条长度不对、跳页后位置漂移」。
 * ═══════════════════════════════════════════════════════════════ */
import { Emitter, Store } from './events.js';
import { KDocument } from './document.js';
import { View } from './viewport.js';
import { SyncGroup, SYNC_MODE, SCALE_MODE } from './sync.js';
import { MODES, MODE_LABEL } from './layout.js';
import { PluginHost } from './plugin.js';
import { el, clamp, prettyName } from './util.js';

import { mountToolbar } from '../ui/toolbar.js';
import { mountStatusbar } from '../ui/statusbar.js';
import { mountSidebar } from '../ui/sidebar.js';
import { mountFindbar } from '../ui/findbar.js';
import { thumbnailPanel } from '../ui/thumbnails.js';
import { outlinePanel } from '../ui/outline.js';

export const VERSION = '1.0.0';

export { SYNC_MODE, SCALE_MODE, MODES, MODE_LABEL };

const DEFAULTS = {
  /** 面板：一个 = 单栏阅读，两个 = 左右对照（内置同步） */
  panes: null,
  /** 单面板简写 */
  src: null,
  label: '文档',
  /** 布局：continuous（纵向连续）/ horizontal（横向单页）/ spread（双页对开） */
  mode: 'continuous',
  /** 适配：page / width / none */
  fit: 'width',
  scale: null,
  /** 同步绑定 */
  sync: { enabled: true, mode: SYNC_MODE.ANCHOR, scaleMode: SCALE_MODE.FIT },
  /** UI 开关 */
  toolbar: true,
  statusbar: true,
  findbar: true,
  sidebar: 'thumbnails',       // 'thumbnails' | 'outline' | null
  textLayer: true,
  links: true,
  /** 版面 */
  gap: 14,
  pad: 18,
  background: 'rgb(255,255,255)',
  /** 性能 */
  renderConcurrency: 2,
  maxLiveCanvas: 22,
  eagerSizes: 80,             // 前 N 页尺寸先取齐，其余后台补
  /** 扩展 */
  plugins: [],
  title: '',
  onProgress: null,
  onError: null,
};

export class Reader extends Emitter {
  constructor(target, options = {}) {
    super();
    const host = typeof target === 'string' ? document.querySelector(target) : target;
    if (!host) throw new Error('kpdf: 找不到挂载容器');

    this.options = { ...DEFAULTS, ...options };
    this.options.sync = { ...DEFAULTS.sync, ...(options.sync || {}) };
    this.host = host;

    this.store = new Store({
      mode: this.options.mode,
      fit: this.options.fit,
      scale: this.options.scale ?? 1,
      page: 1,
      total: 0,
      syncOn: Boolean(this.options.sync.enabled),
      syncMode: this.options.sync.mode,
      scaleMode: this.options.sync.scaleMode,
      textLayer: Boolean(this.options.textLayer),
      rotation: 0,
      skew: null,
      loading: true,
    });

    this.panes = [];        // [{label, src, doc, view, error}]
    this.docs = [];
    this.plugins = new PluginHost(this);
    this._destroyed = false;
    this._sidebarOpen = null;
  }

  static async create(target, options = {}) {
    const reader = new Reader(target, options);
    await reader._boot();
    return reader;
  }

  // ── 启动 ────────────────────────────────────────────────────
  async _boot() {
    this._buildDom();
    this._bindDivider();

    const specs = this._paneSpecs();
    // 文档并行打开：两栏对照时，等待时间等于较慢的那个，而不是两者之和
    const settled = await Promise.allSettled(specs.map((sp, i) => this._openPane(sp, i)));

    const ok = this.panes.filter((p) => p.doc);
    if (!ok.length) {
      const firstErr = settled.find((r) => r.status === 'rejected');
      this._showFatal(firstErr?.reason || new Error('没有可打开的文档'));
      throw firstErr?.reason || new Error('kpdf: 没有可打开的文档');
    }

    this.store.set({
      total: ok[0].doc.numPages,
      loading: false,
    });

    // 尺寸预热：布局需要全部页高才准；先取前 N 页让首屏立即可用
    await Promise.all(ok.map((p) => p.doc.warmSizes({
      to: Math.min(p.doc.numPages - 1, this.options.eagerSizes - 1),
      onProgress: (done, total) => this.emit('progress', { label: p.label, done, total }),
    })));

    // 建视口
    for (const pane of this.panes) {
      if (!pane.doc) continue;
      this._mountView(pane);
    }

    // 同步绑定
    this.sync = new SyncGroup(this.options.sync);
    this.sync.onSkew = (skew) => this.store.set({ skew });
    if (this.views.length > 1 && this.options.sync.enabled) {
      this.views.forEach((v) => this.sync.add(v));
    }

    // UI
    this._ui = {};
    if (this.options.toolbar) this._ui.toolbar = mountToolbar(this, this._toolbarHost);
    if (this.options.statusbar) this._ui.statusbar = mountStatusbar(this, this._statusHost);
    if (this.options.sidebar) {
      this._ui.sidebar = mountSidebar(this, this._bodyEl, {
        initial: this.options.sidebar,
        panels: [thumbnailPanel, outlinePanel],
      });
    }
    if (this.options.findbar) this._ui.findbar = mountFindbar(this, this._bodyEl);

    for (const p of this.options.plugins) {
      try { this.plugins.use(p); } catch (err) { console.warn('[kpdf] 插件装载失败：', err); }
    }

    // 首屏：按适配模式定初始缩放并定位到第 1 页
    for (const v of this.views) {
      if (this.options.scale) v.setScale(this.options.scale, { by: 'init' });
      else v.fit(this.options.fit === 'none' ? 'width' : this.options.fit);
      v.gotoPage(1);
    }
    this._syncSnapshot();
    this.views[0]?.on('pagechange', () => this._syncSnapshot());
    this.views[0]?.on('scaled', () => this._syncSnapshot());

    // 剩余页尺寸后台补齐（不再触发首屏等待）
    for (const pane of ok) {
      if (pane.doc.numPages > this.options.eagerSizes) {
        pane.doc.warmSizes({
          from: this.options.eagerSizes,
          concurrency: 3,
        }).then(() => {
          for (const v of this.views) if (v.doc === pane.doc) v.relayout({ keepAnchor: true });
        });
      }
    }

    this.emit('ready', { reader: this });
    return this;
  }

  _paneSpecs() {
    const o = this.options;
    if (Array.isArray(o.panes) && o.panes.length) return o.panes.slice(0, 2);
    if (o.src) return [{ label: o.label, src: o.src }];
    throw new Error('kpdf: 需要通过 panes 或 src 指定 PDF 地址');
  }

  async _openPane(spec, index) {
    const pane = {
      label: spec.label || (index === 0 ? '原文' : '文档二'),
      src: spec.src,
      error: null,
      doc: null,
      view: null,
      el: null,
    };
    this.panes.push(pane);
    this.emit('panestart', { index, label: pane.label, src: pane.src });
    try {
      const doc = await KDocument.open(spec.src, {
        kernel: 'pdfjs',
        onProgress: (p) => this.emit('paneProgress', {
          index, loaded: p.loaded, total: p.total,
        }),
      });
      pane.doc = doc;
      this.docs.push(doc);
      this.emit('paneready', { index, label: pane.label, pages: doc.numPages });
      return pane;
    } catch (err) {
      pane.error = err;
      console.error(`[kpdf] 「${pane.label}」打开失败：`, err);
      this.emit('paneerror', { index, label: pane.label, error: err });
      this.options.onError?.(err);
      return pane;
    }
  }

  // ── DOM 骨架 ────────────────────────────────────────────────
  _buildDom() {
    this.root = el('div.kp-root', { 'data-kpdf': VERSION });

    this._toolbarHost = el('div.kp-toolbarHost');
    this._bodyEl = el('div.kp-body');
    this._viewsEl = el('div.kp-views');
    this._statusHost = el('div.kp-statusbarHost');

    this._bodyEl.append(this._viewsEl);
    this.root.append(this._toolbarHost, this._bodyEl, this._statusHost);
    this.host.append(this.root);
  }

  _mountView(pane) {
    const o = this.options;
    const paneEl = el('div.kp-pane', { 'data-label': pane.label });
    const head = el('div.kp-paneHead', {}, [
      el('span.kp-dot'),
      el('span.kp-paneName', { text: pane.label }),
      el('span.kp-paneMeta', { text: `共 ${pane.doc.numPages} 页` }),
    ]);
    const holder = el('div.kp-paneBody');
    paneEl.append(head, holder);

    if (this.panes.filter((p) => p.doc).length > 1 && this._viewsEl.children.length === 1) {
      this._viewsEl.append(el('div.kp-divider', { title: '拖动调整左右宽度' }));
    }
    this._viewsEl.append(paneEl);

    const view = new View({
      doc: pane.doc,
      container: holder,
      label: pane.label,
      mode: o.mode,
      fit: o.fit,
      scale: o.scale ?? 1,
      textLayer: o.textLayer,
      links: o.links,
      gap: o.gap,
      pad: o.pad,
      background: o.background,
      renderConcurrency: o.renderConcurrency,
      maxLiveCanvas: o.maxLiveCanvas,
    });
    view.relayout({ keepAnchor: false });

    pane.view = view;
    pane.el = paneEl;
    this.emit('viewmount', { pane, view });
    return view;
  }

  _bindDivider() {
    // 用事件委托：分割条是随第二个面板动态插入的
    let dragging = null;
    this._viewsEl.addEventListener('pointerdown', (e) => {
      const d = e.target.closest?.('.kp-divider');
      if (!d) return;
      dragging = d;
      d.classList.add('active');
      d.setPointerCapture(e.pointerId);
      e.preventDefault();
    });
    this._viewsEl.addEventListener('pointermove', (e) => {
      if (!dragging) return;
      const rect = this._viewsEl.getBoundingClientRect();
      const ratio = clamp((e.clientX - rect.left) / Math.max(1, rect.width), 0.15, 0.85);
      this._viewsEl.style.setProperty('--kp-split', `${(ratio * 100).toFixed(2)}%`);
      for (const p of this.panes) p.view?.relayout({ keepAnchor: true });
    });
    const stop = (e) => {
      if (!dragging) return;
      dragging.classList.remove('active');
      try { dragging.releasePointerCapture(e.pointerId); } catch { /* 已释放 */ }
      dragging = null;
      for (const p of this.panes) p.view?.relayout({ keepAnchor: true });
    };
    this._viewsEl.addEventListener('pointerup', stop);
    this._viewsEl.addEventListener('pointercancel', stop);
  }

  _showFatal(err) {
    const box = el('div.kp-fatal', {}, [
      el('div.kp-fatalIcon', { text: '⚠' }),
      el('div.kp-fatalTitle', { text: '文档打不开' }),
      el('div.kp-fatalMsg', { text: err?.message || String(err) }),
    ]);
    this._bodyEl.append(box);
  }

  // ── 公共 API ────────────────────────────────────────────────
  get views() {
    return this.panes.filter((p) => p.view).map((p) => p.view);
  }

  /** 切布局模式（所有面板一起切）。 */
  setLayout(mode) {
    if (!MODES.includes(mode)) throw new Error(`kpdf: 未知布局模式 ${mode}`);
    this.sync?.lock(() => {
      for (const v of this.views) v.setMode(mode);
    });
    this.store.set({ mode });
    return mode;
  }

  /** 设置缩放（所有面板，按同步比例规则联动）。 */
  setScale(scale, { by = 'api' } = {}) {
    const [src, ...rest] = this.views;
    if (!src) return;
    this.sync?.lock(() => {
      src.setScale(scale, { by });
      for (const v of rest) {
        v.applyScale(v.syncScale(scale, src, this.store.get('scaleMode')));
      }
      src.fitMode = 'none';
    });
    this._syncSnapshot();
  }

  /** 适配（page / width / none）。 */
  fit(mode = 'page') {
    this.sync?.lock(() => {
      for (const v of this.views) v.fit(mode);
    });
    this.store.set({ fit: mode });
    this._syncSnapshot();
  }

  gotoPage(n) {
    this.views.forEach((v) => v.gotoPage(n));
    this._syncSnapshot();
  }

  setRotation(deg) {
    this.sync?.lock(() => {
      for (const v of this.views) v.setRotation(deg);
    });
    this.store.set({ rotation: this.views[0]?.rotation || 0 });
  }

  setTextLayer(on) {
    for (const v of this.views) v.setTextLayer(on);
    this.store.set({ textLayer: Boolean(on) });
  }

  /** 开/关左右同步。 */
  toggleSync(on = !this.store.get('syncOn')) {
    const enabled = Boolean(on);
    this.store.set({ syncOn: enabled });
    if (!this.sync) return enabled;
    this.sync.enabled = enabled;
    if (enabled) this.sync.resync();
    for (const v of this.views) v.suppressEcho(120);
    return enabled;
  }

  setSyncMode(mode) {
    this.sync?.setMode(mode);
    this.store.set({ syncMode: mode });
  }

  setScaleMode(mode) {
    this.sync?.setScaleMode(mode);
    this.store.set({ scaleMode: mode });
  }

  /** 全文检索（两个文档都搜）。 */
  async find(query) {
    const out = [];
    for (const pane of this.panes) {
      if (!pane.doc) continue;
      const hits = await pane.doc.search(query, {
        onHit: (h) => this.emit('hit', { label: pane.label, ...h }),
      });
      for (const h of hits) h.label = pane.label;
      out.push(...hits);
      if (pane.view) pane.view.highlight(query);
    }
    this.emit('searchdone', { query, hits: out });
    return out;
  }

  /** 打开/切换侧栏面板。 */
  openSidebar(panel) {
    const api = this._ui?.sidebar;
    if (!api) return null;
    if (this._sidebarOpen === panel) return api.close();
    this._sidebarOpen = panel;
    return api.open(panel);
  }

  /** 下载当前面板文档。 */
  download(paneIndex = 0) {
    const pane = this.panes[paneIndex];
    if (!pane?.src) return;
    const url = typeof pane.src === 'string' ? pane.src : null;
    if (!url) return;
    const a = el('a', { href: url, download: prettyName(url) });
    a.style.display = 'none';
    document.body.append(a);
    a.click();
    setTimeout(() => a.remove(), 0);
  }

  /** 自检快照：状态栏与 /api 排查都用它，避免各处各拼一份。 */
  stats() {
    return {
      version: VERSION,
      mode: this.store.get('mode'),
      scale: this.store.get('scale'),
      fitRatio: this.views[0]?.fitRatio ?? 1,
      page: this.store.get('page'),
      total: this.store.get('total'),
      syncOn: this.store.get('syncOn'),
      skew: this.store.get('skew'),
      panes: this.panes.map((p) => ({
        label: p.label,
        pages: p.doc?.numPages ?? 0,
        scale: p.view ? Number(p.view.scale.toFixed(4)) : null,
        error: p.error?.message || null,
        rendered: p.view?.renderer.stats || null,
        live: p.view?.renderer.liveCount ?? 0,
      })),
      plugins: this.plugins.list(),
    };
  }

  _syncSnapshot() {
    const v = this.views[0];
    if (!v) return;
    this.store.set({
      page: v.page,
      total: v.doc.numPages,
      scale: v.scale,
      fit: v.fitMode,
    });
  }

  destroy() {
    if (this._destroyed) return;
    this._destroyed = true;
    this.plugins.destroy();
    for (const k of ['findbar', 'sidebar', 'statusbar', 'toolbar']) {
      try { this._ui?.[k]?.destroy?.(); } catch { /* 忽略 */ }
    }
    this.sync?.destroy();
    for (const v of this.views) v.destroy();
    for (const d of this.docs) d.destroy();
    this.root.remove();
    this.clear();
  }
}

/** 公共入口。 */
export async function createReader(target, options = {}) {
  return Reader.create(target, options);
}

export default createReader;
