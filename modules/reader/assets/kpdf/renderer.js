/* ═══════════════════════════════════════════════════════════════
 * kpdf/renderer.js —— 渲染调度器
 *
 * 阅读器好不好用，八成看这层的调度是否克制。四个必须做对的地方：
 *
 *  ① **并发闸门**：同时只允许 N 页在渲染（默认 2）。pdf.js 的渲染在主线程
 *     合成位图，并发过高会把滚动帧率打穿，反而更卡。
 *  ② **可取消**：翻页/缩放瞬间会产生大量「已经不需要的渲染」，
 *     必须能 cancel 掉在途任务，否则卡顿会叠加。
 *  ③ **画布上限 + LRU 回收**：每页一张 canvas，32 页全留着就是几百 MB 显存。
 *     超出预算的页回收成占位符，滚回去再重画。
 *  ④ **dpr 预算**：canvas 面积有硬上限，超了会静默白屏（见 util.safeDpr）。
 * ═══════════════════════════════════════════════════════════════ */
import { safeDpr } from './util.js';

const STATE = { QUEUED: 'queued', RENDERING: 'rendering', READY: 'ready', STALE: 'stale', ERROR: 'error' };

export class PageRenderer {
  /**
   * @param {object} o
   *   doc             KDocument
   *   concurrency     并行渲染页数
   *   maxLive         canvas 同时存活上限（LRU）
   *   maxPixels       单画布像素预算
   *   onReady         (index, canvas) => void
   *   onRelease       (index) => void
   *   onError         (index, err) => void
   */
  constructor({
    doc, concurrency = 2, maxLive = 24, maxPixels = 4096 * 4096,
    onReady, onRelease, onError,
  } = {}) {
    this.doc = doc;
    this.concurrency = concurrency;
    this.maxLive = maxLive;
    this.maxPixels = maxPixels;
    this.onReady = onReady;
    this.onRelease = onRelease;
    this.onError = onError;

    /** index → {canvas, scale, dpr, rotation, state, task, promise, usedAt} */
    this._entries = new Map();
    this._queue = [];
    this._running = 0;
    this._clock = 0;
    this.stats = { rendered: 0, cancelled: 0, evicted: 0, reused: 0 };
  }

  /** 取某页当前已画好的画布（没有则 null）。 */
  canvasOf(index) {
    const e = this._entries.get(index);
    return e && e.state === STATE.READY ? e.canvas : null;
  }

  /**
   * 该页是否已经以目标规格画好。
   * ★ 只比 scale / rotation，**不比 dpr** —— dpr 是本层的内部决策
   *   （safeDpr 会按画布面积预算反推），外部传进来的 dpr 与之不可能一致，
   *   拿它做判据会导致每次滚动都重画一遍。
   */
  isReady(index, want) {
    const e = this._entries.get(index);
    if (!e || e.state !== STATE.READY) return false;
    if (!want) return true;
    return Math.abs(e.spec.scale - want.scale) < 1e-3
      && (e.spec.rotation || 0) === (want.rotation || 0);
  }

  /**
   * 请求渲染某页。返回的 Promise 在画布就绪时 resolve（可能被取消 → reject(AbortError)）。
   * @param {number} index
   * @param {object} want {scale, rotation, background}
   */
  render(index, want) {
    const dpr = safeDpr(
      // 先按 scale=1 的尺寸估个大致面积，真正精确的 dpr 在拿到页面尺寸后再算
      (this.doc.pages[index].size?.width || 600) * want.scale,
      (this.doc.pages[index].size?.height || 800) * want.scale,
      want.dprCap ?? 2,
      this.maxPixels,
    );
    const spec = { scale: want.scale, dpr, rotation: want.rotation || 0, background: want.background || 'rgb(255,255,255)' };

    if (this.isReady(index, spec)) {
      const e = this._entries.get(index);
      e.usedAt = ++this._clock;
      this.stats.reused++;
      this.onReady?.(index, e.canvas, { reused: true });
      return Promise.resolve(e.canvas);
    }

    const prev = this._entries.get(index);
    // 已在渲染同一规格 → 复用同一 Promise（避免同一页被排两次队）
    if (prev && prev.state === STATE.RENDERING && sameSpec(prev, spec)) return prev.promise;

    // 规格变了 → 取消在途任务，重排
    if (prev) this._cancelEntry(index, prev);

    const entry = {
      index, spec, canvas: null, state: STATE.QUEUED, task: null,
      usedAt: ++this._clock, promise: null, resolve: null, reject: null,
    };
    entry.promise = new Promise((res, rej) => { entry.resolve = res; entry.reject = rej; });
    entry.promise.catch(() => {}); // 取消时避免 unhandled rejection 噪声
    this._entries.set(index, entry);
    this._queue.push(entry);
    this._sortQueue();
    this._pump();
    return entry.promise;
  }

  _sortQueue() {
    // 越靠近当前视野中心的越先画（View 会按距离写入 priority）
    this._queue.sort((a, b) => (a.priority ?? 1e9) - (b.priority ?? 1e9));
  }

  setPriority(index, priority) {
    const e = this._entries.get(index);
    if (e && e.state === STATE.QUEUED) {
      e.priority = priority;
      this._sortQueue();
    }
  }

  _pump() {
    while (this._running < this.concurrency && this._queue.length) {
      const entry = this._queue.shift();
      if (!entry || entry.state !== STATE.QUEUED) continue;
      this._run(entry);
    }
  }

  async _run(entry) {
    const { index, spec } = entry;
    this._running++;
    entry.state = STATE.RENDERING;

    try {
      const kp = await this.doc.page(index);
      if (entry.state !== STATE.RENDERING) { this._finish(entry); return; } // 期间被取消

      const canvas = entry.canvas || document.createElement('canvas');
      canvas.className = 'kp-canvas';
      const task = kp.render(canvas, {
        scale: spec.scale, dpr: spec.dpr, rotation: spec.rotation, background: spec.background,
      });
      entry.task = task;
      await task.promise;

      if (entry.state !== STATE.RENDERING) { this._finish(entry); return; }

      entry.canvas = canvas;
      entry.state = STATE.READY;
      this.stats.rendered++;
      this._evictIfNeeded(index);
      this.onReady?.(index, canvas, { reused: false });
      entry.resolve(canvas);
    } catch (err) {
      const cancelled = /cancel/i.test(err?.name || '') || entry.state === STATE.CANCELLED;
      if (cancelled) {
        this.stats.cancelled++;
        entry.reject(Object.assign(new Error('rendering cancelled'), { name: 'AbortError' }));
      } else {
        console.error(`[kpdf] 第 ${index + 1} 页渲染失败：`, err);
        entry.state = STATE.ERROR;
        this.onError?.(index, err);
        entry.reject(err);
      }
    } finally {
      this._finish(entry);
    }
  }

  _finish(entry) {
    this._running = Math.max(0, this._running - 1);
    entry.task = null;
    this._pump();
  }

  /** 取消某页在途渲染。 */
  _cancelEntry(index, entry) {
    try {
      entry.task?.cancel?.();
    } catch { /* 已结束 */ }
    entry.state = STATE.CANCELLED;
    entry.reject?.(Object.assign(new Error('rendering cancelled'), { name: 'AbortError' }));
    this._entries.delete(index);
  }

  cancel(index) {
    const e = this._entries.get(index);
    if (e) this._cancelEntry(index, e);
  }

  /** 释放某页画布（回到「未渲染」状态，DOM 里的占位由 View 负责）。 */
  release(index, { keepSpec = false } = {}) {
    const e = this._entries.get(index);
    if (!e) return;
    if (e.state === STATE.QUEUED) {
      e.state = STATE.CANCELLED;
      this._queue = this._queue.filter((x) => x !== e);
    }
    this._cancelEntry(index, e);
    if (!keepSpec) this._entries.delete(index);
    this.onRelease?.(index);
  }

  /** 超出存活上限时回收最久未用的（且不在视野里的由 View 标记 protect）。 */
  _evictIfNeeded(protectIndex) {
    const ready = Array.from(this._entries.values())
      .filter((e) => e.state === STATE.READY && e.index !== protectIndex && !e.protect);
    if (ready.length + 1 <= this.maxLive) return;
    ready.sort((a, b) => a.usedAt - b.usedAt);
    const n = ready.length + 1 - this.maxLive;
    for (let i = 0; i < n; i++) {
      const victim = ready[i];
      victim.canvas.width = victim.canvas.height = 0; // 主动释放位图内存
      this._entries.delete(victim.index);
      this.stats.evicted++;
      this.onRelease?.(victim.index);
    }
  }

  /** View 用：告诉调度器哪些页现在在视野里，别回收。 */
  setProtected(indexes) {
    const set = indexes instanceof Set ? indexes : new Set(indexes);
    for (const e of this._entries.values()) e.protect = set.has(e.index);
  }

  /** 清空全部（切换文档 / 关闭阅读器）。 */
  clear() {
    for (const index of Array.from(this._entries.keys())) this.release(index);
    this._queue = [];
    this.stats = { rendered: 0, cancelled: 0, evicted: 0, reused: 0 };
  }

  get liveCount() { return this._entries.size; }
}

function sameSpec(a, b) {
  const x = a.spec || a;
  return Math.abs((x.scale || 0) - b.scale) < 1e-3
    && Math.abs((x.dpr || 0) - b.dpr) < 1e-3
    && (x.rotation || 0) === (b.rotation || 0);
}
