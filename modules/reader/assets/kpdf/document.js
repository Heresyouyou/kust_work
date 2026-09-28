/* ═══════════════════════════════════════════════════════════════
 * kpdf/document.js —— 文档模型
 *
 * 职责边界：
 *   内核（kernel.js）负责「解码」，本层负责「把解码结果组织成阅读器要的形状」：
 *   · 页面尺寸的**惰性获取 + 预热**（布局需要全部尺寸，但不必等全部就绪）
 *   · 页面对象的按需加载与释放
 *   · 大纲扁平化（把嵌套 dest 解析成可跳转的 page 序号）
 *   · 全文检索（跨页扫描 + 命中定位）
 *
 * ★ 尺寸单位约定：本层对外一律给 **CSS px（scale=1 时的 100% 尺寸）**。
 *   内核给的 pt 已经在 kernel-pdfjs.js 里乘过 96/72。上层只跟 CSS px 打交道。
 * ═══════════════════════════════════════════════════════════════ */
import { openDocument } from './kernel.js';
import './kernel-pdfjs.js'; // 副作用：把 pdfjs 内核注册进契约层

/** 单个页面的句柄：尺寸可先缺省，用 ensure() 补齐。 */
export class KPage {
  constructor(document_, index) {
    this._doc = document_;
    this.index = index;
    this._size = null;      // {width, height} CSS px @ scale 1
    this._rotate = 0;
    this._kernelPage = null;
    this._loading = null;
  }

  get loaded() { return Boolean(this._kernelPage); }

  /** @returns {{width:number,height:number}|null} */
  get size() { return this._size; }

  get rotate() { return this._rotate; }

  /** 已知尺寸（含旋转后），拿不到时回退到已测到的公共尺寸。 */
  sizeOr(fallback) {
    return this._size || fallback || { width: 595.28, height: 841.89 }; // A4 兜底
  }

  /** 拉取内核页面对象（含尺寸）。并发调用会复用同一个 Promise。 */
  async ensure() {
    if (this._kernelPage) return this._kernelPage;
    if (this._loading) return this._loading;
    this._loading = this._doc._kernel.getPage(this.index).then((kp) => {
      this._kernelPage = kp;
      this._size = { width: kp.width, height: kp.height };
      this._rotate = kp.rotate || 0;
      this._loading = null;
      this._doc._onPageReady(this.index, this._size);
      return kp;
    }).catch((err) => {
      this._loading = null;
      throw err;
    });
    return this._loading;
  }

  /** 释放内核页面对象（大文档翻久了要还内存）。 */
  release() {
    this._kernelPage = null;
    this._doc._kernel.releasePage?.(this.index);
  }
}

export class KDocument {
  constructor(kernelDoc, { onProgress, onPageReady } = {}) {
    this._kernel = kernelDoc;
    this._onProgress = onProgress || null;
    this._onPageReadyHook = onPageReady || null;
    this._pages = Array.from({ length: kernelDoc.numPages }, (_, i) => new KPage(this, i));
    this._outline = undefined;
    this._title = undefined;
    this._textIndex = null;
    /** 已测到的最大尺寸，作为未知页的兜底，避免布局一会儿变一次 */
    this._commonSize = null;
  }

  /**
   * 打开文档。
   * @param {string|File|ArrayBuffer} source
   * @param {object} opts {onProgress, onPassword, kernel}
   */
  static async open(source, opts = {}) {
    const kernelDoc = await openDocument(source, {
      kernel: opts.kernel || 'pdfjs',
      onProgress: opts.onProgress,
      onPassword: opts.onPassword,
    });
    return new KDocument(kernelDoc, opts);
  }

  get numPages() { return this._kernel.numPages; }
  get pages() { return this._pages; }
  get fingerprints() { return this._kernel.fingerprints; }

  /** 已测到的公共页面尺寸（未探测前为 null）。 */
  get commonSize() { return this._commonSize; }

  /** 给布局引擎用的尺寸数组（未知页用 fallback 填充）。 */
  sizeList() {
    const fb = this._commonSize || { width: 595.28, height: 841.89 };
    return this._pages.map((p) => p.sizeOr(fb));
  }

  async title() {
    if (this._title === undefined) {
      this._title = (await this._kernel.title()) || '';
      if (!this._title) this._title = `${this.numPages} 页文档`;
    }
    return this._title;
  }

  async metadata() {
    return this._kernel.metadata();
  }

  _onPageReady(index, size) {
    if (!this._commonSize) this._commonSize = size;
    this._onPageReadyHook?.(index, size);
  }

  /** 单页（Promise<KernelPage>）。 */
  async page(index) {
    return this._pages[index].ensure();
  }

  /**
   * 预热尺寸：布局要知道所有页多高才能算准滚动条。
   * 少页文档一次性取齐；多页文档按可视范围滚动着取（由 View 驱动）。
   *
   * @param {object} o {from, to, concurrency, signal, onProgress}
   */
  async warmSizes({ from = 0, to = this.numPages - 1, concurrency = 6, signal, onProgress } = {}) {
    const list = [];
    for (let i = from; i <= to; i++) if (!this._pages[i]?.size) list.push(i);

    let done = 0;
    const total = list.length;
    let cursor = 0;

    const worker = async () => {
      while (cursor < list.length) {
        if (signal?.aborted) return;
        const i = list[cursor++];
        try {
          await this._pages[i].ensure();
        } catch (err) {
          console.warn(`[kpdf] 第 ${i + 1} 页尺寸获取失败：`, err);
        }
        done++;
        onProgress?.(done, total);
      }
    };

    await Promise.all(Array.from({ length: Math.min(concurrency, Math.max(1, total)) }, worker));
    return { done, total };
  }

  /**
   * 文档大纲（扁平化）。
   *
   * 原始 dest 可能是字符串（命名目标）或数组，且可能指向外部链接。
   * 这里统一解成 {title, page, depth, url}，拿不到页码的条目保留但标记 page=null
   * （UI 上仍显示层级，只是点了不跳 —— 比整棵大纲消失好）。
   */
  async outline() {
    if (this._outline !== undefined) return this._outline;
    const raw = await this._kernel.outline();
    if (!raw || !raw.length) {
      this._outline = null;
      return null;
    }
    const flat = [];
    const walk = async (items, depth) => {
      for (const it of items) {
        let page = null;
        let url = null;
        if (typeof it.dest === 'string') {
          try {
            const dest = await this._kernel._doc.getDestination(it.dest);
            page = await this._kernel._doc.getPageIndex(dest[0]) + 1;
          } catch { page = null; }
        } else if (Array.isArray(it.dest)) {
          try {
            const dest = await this._kernel._doc.getDestination(it.dest[0]) ?? it.dest;
            page = await this._kernel._doc.getPageIndex(dest[0]) + 1;
          } catch { page = null; }
        } else if (it.url) {
          url = it.url;
        }
        flat.push({ title: (it.title || '').trim(), page, depth, url });
        if (it.items?.length) await walk(it.items, depth + 1);
      }
    };
    try {
      await walk(raw, 0);
    } catch (err) {
      console.warn('[kpdf] 大纲解析中断：', err);
    }
    this._outline = flat.length ? flat : null;
    return this._outline;
  }

  /**
   * 全文检索。
   *
   * 实现取向：**逐页扫描内核文本层**，命中记在页粒度上，高亮由文本层负责
   * （在已渲染的 span 上做大小写无关替换）。跨 span 的长句会漏 —— 这是刻意的
   * 取舍：精确的跨项匹配需要自己实现一套字符级索引，收益远小于成本。
   *
   * @param {string} query
   * @param {object} o {onHit, onProgress, signal, limit}
   * @returns {Promise<Array<{page:number, snippet:string}>>}
   */
  async search(query, { onHit, onProgress, signal, limit = 500 } = {}) {
    const needle = String(query || '').trim();
    if (!needle) return [];
    const lower = needle.toLowerCase();
    const hits = [];

    for (let i = 0; i < this.numPages; i++) {
      if (signal?.aborted) break;
      try {
        const kp = await this._pages[i].ensure();
        const tc = await kp.textContent();
        const text = tc.items.map((it) => it.str).join('');
        const at = text.toLowerCase().indexOf(lower);
        if (at >= 0) {
          const hit = {
            page: i + 1,
            snippet: text.slice(Math.max(0, at - 24), at + needle.length + 24).replace(/\s+/g, ' '),
          };
          hits.push(hit);
          onHit?.(hit);
        }
      } catch (err) {
        console.warn(`[kpdf] 第 ${i + 1} 页检索失败：`, err);
      }
      onProgress?.(i + 1, this.numPages);
      if (hits.length >= limit) break;
    }
    return hits;
  }

  async destroy() {
    this._pages.forEach((p) => p.release());
    await this._kernel.destroy();
  }
}
