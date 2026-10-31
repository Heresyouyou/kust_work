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

/** 悬停吸附半径（像素）：指针离句子矩形这么近就贴上去，做成轻微的鼠标吸附。 */
const HOVER_SNAP = 10;

export class Reader extends Emitter {
  constructor(target, options = {}) {
    super();
    const host = typeof target === 'string' ? document.querySelector(target) : target;
    if (!host) throw new Error('kpdf: 找不到挂载容器');

    this.options = { ...DEFAULTS, ...options };
    this.options.sync = { ...DEFAULTS.sync, ...(options.sync || {}) };
    this.host = host;

    // ★ 跨实例句对联动（2026-10-30）
    //   /translate 页左右是两个独立 Reader（各一栏，各有自己的滚动与缩放），
    //   没法用一个 Reader 的双栏逻辑联动。这里给每个实例一个 _side（0=原文 1=译文）
    //   和一条指向对侧的 _peer：悬停命中后由 peer 在它自己的对齐表里点同一句。
    this._side = (this.options.side | 0) || 0;
    this._peer = null;

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
      /** 两篇文档时显示哪一版：both | a | b（a=第一栏，b=第二栏） */
      paneMode: 'both',
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
    // 两栏还是单栏，交给 CSS 判断（data-panes=1 时可见栏占满宽度）
    this._viewsEl.setAttribute('data-panes', String(this.views.length));

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
    // 悬停整句高亮：单栏也要有（没有对齐表就高亮本句），
    // 所以不再用「两栏」当开关 —— 阅读器、在线预览、翻译页都走这条路。
    this._bindHover();
    // 有 align 就加载：两栏是常规分屏；单栏则是 /translate 页，靠 peer 跨实例联动
    if (this.options.align) {
      this._loadAlign();
    }
    return this;
  }

  /** 载入原文↔译文对齐表（没有译文时不会调用）。 */
  async _loadAlign() {
    const url = this.options.align;
    // 单 pane 也加载：/translate 页靠 peer 跨实例联动，不能要求本实例有两栏
    if (!url) return;
    try {
      const r = await fetch(url, { credentials: '<redacted-secret>' });
      if (!r.ok) throw new Error('HTTP ' + r.status);
      const d = await r.json();
      this._align = buildAlignIndex(d);
      this.emit('align', { pairs: this._align.length });
      console.log('[kpdf] 句级对齐表就绪：%d 段', this._align.length);
    } catch (e) {
      console.warn('[kpdf] 对齐表加载失败，悬停对齐不可用：', e?.message || e);
    }
  }

  /**
   * 悬停对齐：鼠标**停在**某个句子上（不用按），这一句整句标绿，另一侧对应句同时标绿。
   * 划词（拖选）不再参与镜像 —— 那是用户自己在选东西，不该被另一栏打断。
   *
   * 命中分两级：
   *   ① 精确 —— caretPositionFromPoint 拿到「光标下是哪个字符」，再查该页句子索引，
   *      所以同一行里有两句话也能分清；
   *   ② 吸附 —— 指针没压准字（行旁、行距、页面边缘）时，在 10px 圆形范围内
   *      找最近的句子矩形，轻推一下鼠标就贴上去，不必像划词那样精准落在字形上。
   */
  _bindHover() {
    if (this._hoverBound) return;
    this._hoverBound = true;
    this._hoverCache = Object.create(null);
    this._hoverKey = '';
    let raf = 0;
    const schedule = () => {
      if (raf) return;
      raf = requestAnimationFrame(() => {
        raf = 0;
        try { this._hoverAt(); } catch (e) { /* 悬停失败不影响阅读 */ }
      });
    };
    this._onMove = (e) => {
      // ⚠️ 每个 Reader 都监听 document：translate 页是两个实例，鼠标在右栏时
      //    左栏也会收到同一次 mousemove，它按同一坐标在自己的文档里「命中」并
      //    重画，把对侧刚点亮的句子又清掉 —— 表现就是「跨实例永远不亮」。
      //    先判断事件是否落在自己的宿主内，不在就完全忽略（由对侧点亮/熄灭）。
      const t = e.target;
      if (this.host && t && !this.host.contains(t)) return;
      this._mouse = { x: e.clientX, y: e.clientY, buttons: e.buttons | 0 };
      schedule();
    };
    this._onLeave = () => this._clearHover();
    document.addEventListener('mousemove', this._onMove, { passive: true });

    // 离开自己的宿主就熄灯（绑在 document 上会被对侧的事件误触发）
    if (this.host) {
      this.host.addEventListener('mouseleave', this._onLeave);
    } else {
      document.addEventListener('mouseleave', this._onLeave);
    }
  }

  /**
   * 某一页的句子索引：把文本层字符串起来，算出每句的字符区间。
   *
   * 两套来源，按有没有对齐表自动选：
   *   · 有对齐表（双语对照）→ `entries` 来自 align，命中后两侧一起标；
   *   · 没有对齐表（单栏阅读 / 在线预览 / 翻译页）→ 现场按「句末标点 + 版面块边界」
   *     断句（`_sentencesOf`），只标本栏这一句。以前没有对齐表就整个不响应，
   *     所以单栏阅读器、期刊在线预览这些入口都摸不到悬停高亮。
   *
   * 注意：文本层的 span 顺序按 PDF 内容流，不一定等于视觉阅读顺序（正文常常在题注之前），
   * 所以不能靠「单调游标」找句子；改为找**所有出现位置**，再用句子自己的版面框
   * （sb/db，0–1 归一化）挑出真正那一处 —— 同页重复文本也一并解决。
   */
  _pageIndex(viewIdx, page) {
    const view = this.views[viewIdx];
    const layer = view?.textLayer?.(page);
    if (!layer) return null;
    const key = viewIdx + ':' + page;
    const cached = this._hoverCache[key];
    // ★ 缓存失效有三条（2026-10-30 修「悬停乱飘」）：
    //   ① textLayer 元素换了；
    //   ② 元素还在但内容重绘过 —— 旧的 span 已脱离文档，caretPositionFromPoint
    //      拿到的是新节点，在旧 nodes 里找不到 → 字符位置算错 → 色带画到别的行；
    //   ③ 缩放变了 —— 矩形是按当时 scale 量的，复用就会偏。
    const sc = view.scale || 0;
    if (cached && cached.layer === layer && cached.layer.isConnected
        && cached.sc === sc
        && (!cached.nodes.length || cached.nodes[0].node.isConnected)) {
      return cached;
    }

    const spans = Array.from(layer.querySelectorAll('span'));
    let joined = '';
    let raw = '';
    const nodes = [];
    const map = [];
    const breaks = [];          // 版面块边界（换段/换栏）在拼接串里的位置
    let prev = null;
    for (const sp of spans) {
      const node = sp.firstChild;
      if (!node || node.nodeType !== 3) continue;
      const text = node.nodeValue || '';
      // 块边界：这一块离上一块「往下空出一截」或「跳回上一行以上」= 新段落/新栏
      let box = null;
      try { box = sp.getBoundingClientRect(); } catch (e) { box = null; }
      if (box && box.height > 0.5) {
        if (prev) {
          const gap = box.top - prev.bottom;
          const jump = box.top - prev.top;
          if (gap > Math.max(4, prev.h * 0.6) || jump < -prev.h * 0.6) {
            breaks.push(joined.length);
          }
        }
        prev = { top: box.top, bottom: box.bottom, h: box.height };
      }
      nodes.push({ node, start: joined.length, text });
      for (let i = 0; i < text.length; i++) {
        const ch = text[i];
        if (/[\s\u00a0\u00ad\u200b]/.test(ch)) continue;
        joined += ch.toLowerCase();
        raw += ch;
        map.push([node, i]);
      }
    }

    const pr = layer.getBoundingClientRect();
    const inBox = (at, box) => {
      if (!box || !pr.width || !pr.height) return false;
      const el = map[at] && map[at][0].parentElement;
      if (!el) return false;
      const r = el.getBoundingClientRect();
      const cx = (r.left + r.width / 2 - pr.left) / pr.width;
      const cy = (r.top + r.height / 2 - pr.top) / pr.height;
      return cx >= box[0] - 0.04 && cx <= box[2] + 0.04
        && cy >= box[1] - 0.04 && cy <= box[3] + 0.04;
    };

    const entries = [];
    for (const rec of (this._align || [])) {
      if (rec.p !== page) continue;
      for (const s of rec.sents) {
        // 双栏按栏位分；单栏按实例的 _side（translate 页左原文 / 右译文）
        const side = this.views.length > 1 ? viewIdx : this._side;
        const q = side === 0 ? s.ns : s.nd;
        const box = side === 0 ? s.sb : s.db;
        if (!q) continue;
        let at = joined.indexOf(q);
        if (at < 0) at = joined.indexOf(q.slice(0, Math.min(18, q.length)));
        if (at < 0) continue;
        if (box) {
          // 找落在句子框里的那一处；找不到才用第一处
          let pos = at;
          let n = 0;
          let best = -1;
          while (pos >= 0 && n < 40) {
            if (inBox(pos, box)) { best = pos; break; }
            pos = joined.indexOf(q, pos + 1);
            n++;
          }
          if (best >= 0) at = best;
        }
        entries.push({ start: at, end: at + q.length, rec, sent: s });
      }
    }
    entries.sort((a, b) => a.start - b.start);
    const idx = { layer, page, joined, raw, nodes, map, breaks, entries,
                  rects: null, local: null, sc: view.scale || 0 };
    this._hoverCache[key] = idx;
    return idx;
  }

  /**
   * 没有对齐表时的「就地断句」：句末标点 + 版面块边界。
   *
   * 只给悬停高亮用，不追求语言学正确：中文看 。！？；，英文看 .!?;，
   * 小数点、首字母缩写（e.g. / Fig. 5）不切断；句末的引号、右括号跟着上一句走。
   */
  _sentencesOf(idx) {
    if (idx.sents) return idx.sents;
    const n = idx.map.length;
    const marks = new Set([0, n]);
    for (const b of idx.breaks || []) {
      if (b > 0 && b < n) marks.add(b);
    }
    const END = '。！？；!?;';
    const CLOSE = '”’"\')）]】」』…';
    const NEXT_OK = /[A-Z\u4e00-\u9fff"“「『]/;   // 句首才切：小写/数字/左括号都算句内
    for (let i = 0; i < n; i++) {
      const ch = idx.joined[i];
      let cut = -1;
      if (END.indexOf(ch) >= 0) {
        cut = i + 1;
      } else if (ch === '.') {
        const prev = idx.raw[i - 1] || '';
        const next = idx.raw[i + 1] || '';
        const num = /[0-9]/.test(prev) && /[0-9]/.test(next);   // 0.52
        if (!num && next && NEXT_OK.test(next)) cut = i + 1;    // 只有句首才切
      }
      if (cut < 0) continue;
      while (cut < n && CLOSE.indexOf(idx.joined[cut]) >= 0) cut++;
      marks.add(cut);
    }
    const pts = Array.from(marks).sort((a, b) => a - b);
    const out = [];
    for (let i = 0; i < pts.length - 1; i++) {
      if (pts[i + 1] > pts[i]) out.push({ start: pts[i], end: pts[i + 1] });
    }
    idx.sents = out;
    return out;
  }

  /** 命中表：有对齐表用对齐句，没有就用现场断的句子。 */
  _localOf(idx) {
    if (!idx.local) {
      idx.local = this._sentencesOf(idx).map((s, i) => ({
        start: s.start, end: s.end, sent: null,
        id: 'p' + idx.page + ':' + i,
      }));
    }
    return idx.local;
  }

  _entriesOf(idx) {
    if (idx.entries && idx.entries.length) return idx.entries;
    return this._localOf(idx);
  }

  /** caret 的 (node, offset) → 该页字符索引（按非空白字符计）。 */
  _charAt(idx, node, offset) {
    const rec = idx.nodes.find((n) => n.node === node);
    if (!rec) return -1;
    let c = 0;
    const end = Math.min(offset, rec.text.length);
    for (let i = 0; i < end; i++) {
      if (!/[\s\u00a0\u00ad\u200b]/.test(rec.text[i])) c++;
    }
    return rec.start + c;
  }

  /** 字符区间 → DOM Range。 */
  _rangeOf(idx, start, end) {
    const a = idx.map[start];
    const b = idx.map[Math.min(end - 1, idx.map.length - 1)];
    if (!a || !b) return null;
    try {
      const r = document.createRange();
      r.setStart(a[0], a[1]);
      r.setEnd(b[0], b[1] + 1);
      return r;
    } catch (e) {
      return null;
    }
  }

  _hoverAt() {
    if (!this.views.length) return;
    const pos = this._mouse;
    if (!pos) return;
    if (pos.buttons) { this._clearHover(); return; }   // 按住鼠标 = 在划词，不画悬停

    const hit = this._hitTest(pos.x, pos.y);
    this._lastHit = hit ? { vi: hit.vi, page: hit.page, sent: !!hit.sent,
                            rects: (hit.rects || []).length, text: (hit.text || '').slice(0, 24),
                            // 调试：带上句对两侧原文，便于核对「亮的是不是同一句」
                            id: hit.sent ? hit.sent._id : null,
                            s: hit.sent ? String(hit.sent.s || '').slice(0, 80) : null,
                            d: hit.sent ? String(hit.sent.d || '').slice(0, 80) : null } : null;
    if (!hit) { this._clearHover(); return; }
    if (this._hoverKey === hit.key && this._hoverAlive()) return;  // 同一句不重复绘制

    // ★ 必须先清上一次（含对侧），再决定画哪些。
    //   顺序反了就是「刚调 peer 点亮、紧接着这行又把它清空」——
    //   跨实例联动的 bug 就出在这里，双栏同实例不受影响（它不走 peer）。
    this._clearHover();

    const paints = [{ vi: hit.vi, page: hit.page, rects: hit.rects, text: hit.text }];
    // 只有「两侧 + 对齐句」才联动另一栏；单栏阅读器就标本句
    if (hit.sent && this.views.length === 1 && this._peer) {
      // 跨实例（/translate 页）：让对侧 Reader 点同一句
      try { this._peer.highlightSent(hit.sent, hit.page); } catch (e) { /* 忽略 */ }
    }
    if (hit.sent && this.views.length > 1) {
      const pvi = 1 - hit.vi;
      const pidx = this._pageIndex(pvi, hit.page);
      const peer = pidx ? this._entriesOf(pidx).find((x) => x.sent === hit.sent) : null;
      if (pidx && peer) {
        const range = this._rangeOf(pidx, peer.start, peer.end);
        const rects = range ? this.views[pvi].lineRectsOf(hit.page, range) : [];
        if (rects.length) {
          paints.push({
            vi: pvi, page: hit.page, rects,
            text: pvi === 0 ? hit.sent.s : hit.sent.d,
          });
        }
      }
    }

    this._hoverKey = hit.key;
    this._hoverPaints = [];
    for (const pt of paints) {
      const layerEl = this.views[pt.vi].paintHover(pt.page, pt.rects, { sent: this._hoverKey });
      if (!layerEl) continue;
      this._hoverPaints.push({
        vi: pt.vi,
        page: pt.page,
        id: this._hoverKey,
        text: pt.text || '',
        rects: pt.rects,
        el: layerEl,
      });
    }
    if (!this._hoverPaints.length) this._clearHover();
  }

  /** 画出来的色带还挂在页上吗（缩放/换页会把槽位重建，旧的会掉下来）。 */
  _hoverAlive() {
    const ps = this._hoverPaints;
    if (!ps || !ps.length) return false;
    return ps.every((p) => p.el && p.el.isConnected);
  }

  /**
   * 命中两级：先精确（caret 落在哪个字符），再 10px 吸附（最近的句子矩形）。
   * 返回 {vi, page, key, sent, text, rects}，rects 是页面本地坐标，可直接拿去画。
   */
  _hitTest(cx, cy) {
    const exact = this._caretHit(cx, cy);
    if (exact) return exact;

    let best = null;
    for (let vi = 0; vi < this.views.length; vi++) {
      const at = this.views[vi].pagePointAt(cx, cy, HOVER_SNAP);
      if (!at) continue;
      const idx = this._pageIndex(vi, at.page);
      if (!idx) continue;
      for (const e of this._entriesOf(idx)) {
        const rects = this._rectsOf(idx, vi, at.page, e);
        let d = Infinity;
        for (const r of rects) {
          const dx = Math.max(r.x - at.x, 0, at.x - (r.x + r.w));
          const dy = Math.max(r.y - at.y, 0, at.y - (r.y + r.h));
          const dd = Math.hypot(dx, dy);
          if (dd < d) d = dd;
        }
        if (d > HOVER_SNAP) continue;
        if (!best || d < best.d) best = { vi, page: at.page, entry: e, rects, d };
      }
    }
    if (!best) return null;
    return this._hitOf(best.vi, best.page, best.entry, best.rects);
  }

  /** 统一成前台要的形状：key 用于去重（对齐句用 id，本地句用页码+序号）。 */
  _hitOf(vi, page, e, rects) {
    const idx = this._pageIndex(vi, page);
    const key = e.sent ? e.sent._id : e.id;
    const text = e.sent
      ? (vi === 0 ? e.sent.s : e.sent.d)
      : (idx ? idx.raw.slice(e.start, e.end) : '');
    return { vi, page, key, sent: e.sent || null, text, entry: e, rects };
  }

  /** 指针压在文字上：caret → 字符序号 → 该页句子表。 */
  _caretHit(cx, cy) {
    const caret = this._caretAt(cx, cy);
    if (!caret || !caret.offsetNode) return null;
    const layerEl = caret.offsetNode.parentElement
      ? caret.offsetNode.parentElement.closest('.kp-textLayer') : null;
    if (!layerEl) return null;
    const host = layerEl.closest('.kp-view');
    const vi = host ? this.views.findIndex((v) => v._el === host) : -1;
    if (vi < 0) return null;
    const page = parseInt(layerEl.dataset.page, 10) || 0;
    const idx = this._pageIndex(vi, page);
    if (!idx) return null;

    const p = this._charAt(idx, caret.offsetNode, caret.offset);
    if (p < 0) return null;
    let e = this._entriesOf(idx).find((x) => p >= x.start && p < x.end);
    if (!e) {
      // 对齐表只覆盖正文段落：页眉、标题、图注、参考文献常没有记录。
      // 这里回退「本侧就地断句」，至少当前栏亮；sent=null 所以对侧不亮。
      // 不这么写的话，这些区域会整块没反应 —— 探针就卡在标题上。
      e = this._localOf(idx).find((x) => p >= x.start && p < x.end) || null;
    }
    if (!e) return null;
    const rects = this._rectsOf(idx, vi, page, e);
    return rects.length ? this._hitOf(vi, page, e, rects) : null;
  }

  /** caretPositionFromPoint（Chrome 128+/Edge/Firefox）与老 caretRangeFromPoint 的统一入口。 */
  _caretAt(x, y) {
    if (document.caretPositionFromPoint) {
      const c = document.caretPositionFromPoint(x, y);
      return c && c.offsetNode ? c : null;
    }
    if (document.caretRangeFromPoint) {
      const r = document.caretRangeFromPoint(x, y);
      if (r && r.startContainer) return { offsetNode: r.startContainer, offset: r.startOffset };
    }
    return null;
  }

  /** 某句在本页的行矩形（页面本地坐标）。句子的字不动，量一次存着。 */
  _rectsOf(idx, vi, page, e) {
    if (!idx.rects) idx.rects = Object.create(null);
    const key = e.sent ? e.sent._id : e.id;
    const cached = idx.rects[key];
    if (cached) return cached;
    const range = this._rangeOf(idx, e.start, e.end);
    const rects = range ? this.views[vi].lineRectsOf(page, range) : [];
    idx.rects[key] = rects;
    return rects;
  }

  _clearHover(silent) {
    this._hoverKey = '';
    this._hoverPaints = null;
    for (const v of this.views) {
      try { v.clearHoverPaint(); } catch (e) { /* 忽略 */ }
    }
    // 跨实例：本侧移开时对侧也要灭。silent 防止两边来回递归
    if (!silent && this._peer) {
      try { this._peer._clearHover(true); } catch (e) { /* 忽略 */ }
    }
  }

  /** 与另一个 Reader 建立句对联动（/translate 页左右两栏）。 */
  setPeer(other) {
    this._peer = other || null;
    if (other) other._peer = this;
    return this;
  }

  /** 换对齐表地址并重新加载（translate 页两侧都就绪后才算得出地址）。 */
  async setAlign(url) {
    if (!url || url === this.options.align) return this._align || null;
    this.options.align = url;
    this._align = null;
    this._hoverCache = Object.create(null);
    this._clearHover();
    await this._loadAlign();
    return this._align || null;
  }

  /**
   * 被对侧点亮：在自己的对齐表里找同一句（按稳定的 _id），画色带。
   * 返回是否画成。对侧没有这句（例如跨页）就什么都不做，不报错。
   */
  highlightSent(sent, page) {
    if (!sent || !this._align || !this.views.length) return false;
    const vi = 0;
    const idx = this._pageIndex(vi, page);
    if (!idx) return false;
    const e = (idx.entries || []).find((x) => x.sent && x.sent._id === sent._id);
    if (!e) return false;
    const rects = this._rectsOf(idx, vi, page, e);
    if (!rects.length) return false;
    this._hoverKey = sent._id;
    this._hoverPaints = [];
    const layerEl = this.views[vi].paintHover(page, rects, { sent: sent._id });
    if (!layerEl) { this._hoverPaints = null; return false; }
    this._hoverPaints.push({ vi, page, id: sent._id, text: '', rects, el: layerEl });
    return true;
  }

  _clearMirror() {
    for (const v of this.views) v.clearMirror();
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

  /** 当前可见的视图（单栏时只有一个）。 */
  get visibleViews() {
    const m = this._paneMode || 'both';
    const panes = this.panes.filter((p) => p.view && p.el);
    if (panes.length < 2) return this.views;
    if (m === 'a') return [panes[0].view];
    if (m === 'b') return [panes[1].view];
    return panes.map((p) => p.view);
  }

  /**
   * 显示哪一版：'both' 对照 | 'a' 只看第一栏 | 'b' 只看第二栏。
   *
   * 只有两篇文档时才有意义（单文档直接返回 'both'）。切到单栏后：
   *   · 隐藏的栏从同步组里摘掉，避免它继续被程序化定位、白跑渲染；
   *   · 可见栏的宽度从「固定 --kp-split」改回 100%（见 reader.css）；
   *   · 容器尺寸变了要重排，否则滚动条长度与页位置都会漂。
   */
  setPaneMode(mode) {
    const panes = this.panes.filter((p) => p.view && p.el);
    if (panes.length < 2) return 'both';
    const m = ['both', 'a', 'b'].includes(mode) ? mode : 'both';
    this._paneMode = m;
    const show = m === 'both' ? panes : [m === 'a' ? panes[0] : panes[1]];

    for (const p of panes) {
      const on = show.includes(p);
      p.el.hidden = !on;
      p.el.classList.toggle('kp-paneActive', on);
    }
    this._viewsEl.setAttribute('data-panes', String(show.length));

    if (this.sync) {
      for (const p of panes) {
        try { this.sync.remove(p.view); } catch { /* 未注册 */ }
      }
      if (show.length > 1 && this.options.sync.enabled && this.store.get('syncOn')) {
        for (const p of show) this.sync.add(p.view);
      }
    }

    // 容器宽度变了：先重排，再按原来的适配模式重算比例。
    // 只 relayout 不重算 fit，会出现「只看原文」时页面仍按半屏宽度渲染、右侧留白。
    const fix = () => show.forEach((p) => {
      const v = p.view;
      if (!v) return;
      v.relayout({ keepAnchor: true });
      if (v.fitMode && v.fitMode !== 'none') v.fit(v.fitMode);
    });
    if (typeof requestAnimationFrame === 'function') requestAnimationFrame(fix);
    else fix();

    this.store.set({ paneMode: m });
    this.emit('panemode', { mode: m });
    return m;
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

  /**
   * 跳到第 n 页。
   * opts.primary=true 时只翻主文档，另一栏交给同步组按锚点跟 —— 对照的中文版
   * 往往比原文页数少（15 页 vs 4 页），两栏同时 goto 会被夹到末页，错位。
   */
  gotoPage(n, opts = {}) {
    if (opts.primary && this.views.length > 1) {
      this.views[0].gotoPage(n);
      // 立刻把主文档的位置投影给对照栏：否则另一栏还停在原页，
      // 它接下来的滚动事件会把主文档拽回去（实测 peer 文档会把 11 页拉回 1 页）
      this.sync?.resync();
      return;
    }
    this.views.forEach((v) => v.gotoPage(n));
    this._syncSnapshot();
  }

  /**
   * 跳到第 n 页并高亮 quote 所在的段落（导读 P.x 用）。
   * 主文档跳页后由同步组带另一栏；高亮只标主文档，避免双语版重复标注。
   */
  focusQuote(n, quote) {
    const v = this.views[0];
    if (!v) return Promise.resolve(0);
    this.gotoPage(Number(n) || 1, { primary: this.views.length > 1 });
    return v.focusQuote(Number(n) || 1, quote);
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

  /** 开/关缩放联动（两栏对照时用）。没有同步组就什么都不做。 */
  setScaleSync(on) {
    const v = this.sync ? this.sync.setScaleSync(on) : false;
    this.store.set({ scaleSync: v });
    return v;
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
    if (this._hoverBound) {
      document.removeEventListener('mousemove', this._onMove);
      document.removeEventListener('mouseleave', this._onLeave);
      this._hoverBound = false;
      this._clearHover();
    }
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


/* ══ 原文↔译文 对齐表（划词镜像用）══════════════════════════════
 * 数据来自 /papers/align/<work_id>：BabelDOC 原位替换，两版段落框基本重合，
 * align.py 按版面重叠配好「英文段落 ↔ 中文段落」，段内做了句子对齐，
 * **每句话还带自己的版面框**（相对页宽高 0–1）。
 * 所以这里的高亮按矩形走，不靠整页文字搜索 —— 同一句话出现两次也不会标错。 */

/** 归一化：去空白（PDF 换行、两端对齐空格）、软连字符、零宽字符，转小写。 */
function normText(s) {
  return String(s || '').replace(/[\s\u00a0\u00ad\u200b]+/g, '').toLowerCase();
}

/** 逐句在段落文本里定位，得到 [start,end) 偏移；找不到的句子记 null。 */
function sentenceOffsets(text, sents, key) {
  const out = [];
  let cur = 0;
  for (const x of sents) {
    const q = x[key] || '';
    if (!q) { out.push(null); continue; }
    let at = text.indexOf(q, cur);
    if (at < 0) at = text.indexOf(q.slice(0, Math.min(20, q.length)), cur);
    if (at < 0) { out.push(null); continue; }
    out.push([at, at + Math.max(1, q.length)]);
    cur = at + 1;
  }
  return out;
}

/** 预处理：把每段每句的字符偏移算好，选词时只做一次 indexOf。 */
let sentSeq = 0;

function buildAlignIndex(d) {
  const out = [];
  for (const p of (d && d.pairs) || []) {
    if (!p.st || !p.dt) continue;
    // ⚠️ _id 必须**可复现**：/translate 页是两个独立 Reader，各自 fetch 同一份
    //    对齐表；用自增序号会让两个实例的同一句拿到不同 id，跨实例联动必然失败。
    //    改成「页号:句序」，与实例无关。
    const sents = (p.sent || []).filter((x) => x && x.s && x.d).map((x, si) => ({
      s: x.s, d: x.d,
      ns: normText(x.s), nd: normText(x.d),
      sb: Array.isArray(x.sb) ? x.sb : null,
      db: Array.isArray(x.db) ? x.db : null,
      _id: (p.p | 0) + ':' + si,
    }));
    const rec = {
      p: p.p | 0,
      st: p.st, dt: p.dt,
      nst: normText(p.st), ndt: normText(p.dt),
      s: Array.isArray(p.s) ? p.s : null,
      d: Array.isArray(p.d) ? p.d : null,
      sents,
    };
    rec.offF = sentenceOffsets(rec.nst, sents, 'ns');
    rec.offB = sentenceOffsets(rec.ndt, sents, 'nd');
    out.push(rec);
  }
  return out;
}

/**
 * 在一条记录里定位选区，返回另一侧的对应文字与版面框。
 * @param dir 'f' 原文→译文；'b' 译文→原文
 * @returns {text:string, boxes:Array|null} 或 null
 */
function pickAligned(rec, dir, selNorm) {
  const text = dir === 'f' ? rec.nst : rec.ndt;
  let at = text.indexOf(selNorm);
  let len = selNorm.length;
  if (at < 0) {
    // 选区跨了段落，或首尾带了别的段落：退一步用开头一小段去找
    const probe = selNorm.slice(0, Math.min(24, selNorm.length));
    if (probe.length < 6) return null;
    at = text.indexOf(probe);
    len = probe.length;
  }
  if (at < 0) return null;
  if (!rec.sents.length) {
    return { text: dir === 'f' ? rec.dt : rec.st, boxes: dir === 'f' ? rec.d : rec.s };
  }
  const offs = dir === 'f' ? rec.offF : rec.offB;
  const endPos = at + Math.max(1, len) - 1;
  let si = -1;
  let ei = -1;
  for (let i = 0; i < offs.length; i++) {
    const r = offs[i];
    if (!r) continue;
    if (si < 0 && at < r[1]) si = i;
    if (r[0] <= endPos) ei = i;
  }
  if (si < 0) si = ei >= 0 ? ei : 0;
  if (ei < si) ei = si;
  const parts = [];
  const boxes = [];
  for (let i = si; i <= ei && i < rec.sents.length; i++) {
    const x = rec.sents[i];
    parts.push(dir === 'f' ? x.d : x.s);
    const b = dir === 'f' ? x.db : x.sb;
    if (b) boxes.push(b);
  }
  return { text: parts.join(''), boxes: boxes.length ? boxes : null };
}

/** 公共入口。 */
export async function createReader(target, options = {}) {
  return Reader.create(target, options);
}

export default createReader;
