/* ═══════════════════════════════════════════════════════════════
 * kpdf/events.js —— 极简事件总线 + 反应式仓库
 *
 * 自研框架的内部通信基石。刻意不做复杂实现：
 *   · 同步派发（阅读器的状态变更需要「当帧可见」，异步会让滚动同步抖动）
 *   · 派发过程中的 on/off 用快照遍历，避免回调里增删导致漏派/重入
 *   · Store 只做浅比较，值没变就不通知 —— 这是抑制无意义重渲染的第一道闸
 * ═══════════════════════════════════════════════════════════════ */

/** 事件总线。 */
export class Emitter {
  constructor() {
    /** @type {Map<string, Set<Function>>} */
    this._handlers = new Map();
  }

  /**
   * 订阅。
   * @returns {Function} 取消订阅函数（比记 type+fn 更省心，闭包里带引用）
   */
  on(type, fn, { once = false } = {}) {
    if (typeof fn !== 'function') throw new TypeError('kpdf: on() 需要函数');
    let set = this._handlers.get(type);
    if (!set) this._handlers.set(type, (set = new Set()));

    const wrapped = once
      ? (payload) => { this.off(type, wrapped); fn(payload); }
      : fn;
    set.add(wrapped);
    return () => this.off(type, wrapped);
  }

  /** 只触发一次。 */
  once(type, fn) {
    return this.on(type, fn, { once: true });
  }

  off(type, fn) {
    const set = this._handlers.get(type);
    if (set) {
      set.delete(fn);
      if (!set.size) this._handlers.delete(type);
    }
  }

  /** 派发。回调异常逐个兜住 —— 一个 UI 插件崩掉不该让滚动停摆。 */
  emit(type, payload) {
    const set = this._handlers.get(type);
    if (!set || !set.size) return;
    for (const fn of Array.from(set)) {
      try {
        fn(payload);
      } catch (err) {
        console.error(`[kpdf] 事件 ${type} 的监听器抛错：`, err);
      }
    }
  }

  /** 某类事件的监听数（自检用）。 */
  count(type) {
    return this._handlers.get(type)?.size || 0;
  }

  clear() {
    this._handlers.clear();
  }
}

/**
 * 反应式仓库：`store.set({scale: 2})` → 订阅者拿到变更集。
 * 只做一层浅比较，够用且可预测。
 */
export class Store {
  constructor(initial = {}) {
    this._state = { ...initial };
    this._emitter = new Emitter();
  }

  get(key) {
    return key === undefined ? { ...this._state } : this._state[key];
  }

  /** @returns {boolean} 是否真的发生了变更 */
  set(patch, { silent = false } = {}) {
    const changed = {};
    let dirty = false;
    for (const [k, v] of Object.entries(patch)) {
      if (this._state[k] !== v) {
        this._state[k] = v;
        changed[k] = v;
        dirty = true;
      }
    }
    if (dirty && !silent) this._emitter.emit('change', changed);
    return dirty;
  }

  subscribe(fn) {
    return this._emitter.on('change', fn);
  }
}
