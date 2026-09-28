/* ═══════════════════════════════════════════════════════════════
 * kpdf/plugin.js —— 插件宿主
 *
 * 阅读器的能力（缩略图、大纲、检索、文本层…）都做成插件，
 * 让「核心」保持很小：核心只负责文档、布局、视口、同步、渲染。
 *
 * 插件约定（一个对象或一个工厂函数）：
 *   {
 *     name: 'thumbnails',
 *     install(reader, opts) → { destroy?(), 以及任意对外方法 }
 *   }
 * 插件通过 reader 暴露的公开接口工作（reader.views / reader.store /
 * reader.on(...)），不去摸内部字段 —— 这样核心重构不会无声炸掉插件。
 * ═══════════════════════════════════════════════════════════════ */
export class PluginHost {
  constructor(reader) {
    this.reader = reader;
    this._plugins = new Map();
  }

  /**
   * 装载插件。
   * @param {object|Function} plugin 插件对象，或返回插件对象的工厂
   * @param {object} opts
   * @returns {any} 插件的返回值（一般是该插件的公开接口对象）
   */
  use(plugin, opts = {}) {
    const p = typeof plugin === 'function' ? plugin() : plugin;
    if (!p || !p.name || typeof p.install !== 'function') {
      throw new TypeError('kpdf: 插件必须形如 { name, install(reader, opts) }');
    }
    if (this._plugins.has(p.name)) {
      console.warn(`[kpdf] 插件「${p.name}」已装载，跳过重复装载`);
      return this._plugins.get(p.name).api;
    }
    const api = p.install(this.reader, opts) || {};
    this._plugins.set(p.name, { plugin: p, api });
    return api;
  }

  get(name) {
    return this._plugins.get(name)?.api ?? null;
  }

  has(name) {
    return this._plugins.has(name);
  }

  list() {
    return Array.from(this._plugins.keys());
  }

  destroy() {
    for (const [, rec] of this._plugins) {
      try {
        rec.api?.destroy?.();
      } catch (err) {
        console.warn(`[kpdf] 插件「${rec.plugin.name}」卸载出错：`, err);
      }
    }
    this._plugins.clear();
  }
}
