/* ═══════════════════════════════════════════════════════════════
 * ui/sidebar.js —— 侧栏壳（面板注册与切换）
 *
 * 侧栏本身不放任何业务逻辑，只负责：
 *   · 标签页的渲染与互斥切换
 *   · 面板的装载/卸载（切走就把上一个 destroy 掉，避免隐藏面板继续渲染缩略图）
 * 具体面板（缩略图 / 目录）在 ui/thumbnails.js、ui/outline.js 里，
 * 各自形如 { id, label, icon, create(container, reader) → {destroy?} }。
 * ═══════════════════════════════════════════════════════════════ */
import { el } from '../kpdf/util.js';

export function mountSidebar(reader, host, { initial = null, panels = [] } = {}) {
  const root = el('aside.kp-sidebar', { hidden: true, 'aria-label': '阅读器侧栏' });
  const tabs = el('div.kp-sideTabs');
  const body = el('div.kp-sideBody');
  root.append(tabs, body);
  host.insertBefore(root, host.firstChild);

  const registry = new Map();
  let activeId = null;
  let activeApi = null;

  const unmountActive = () => {
    try { activeApi?.destroy?.(); } catch (err) { console.warn('[kpdf] 侧栏面板卸载出错：', err); }
    activeApi = null;
    body.replaceChildren();
  };

  const setActiveTab = () => {
    tabs.querySelectorAll('.kp-sideTab').forEach((t) => {
      t.classList.toggle('on', t.dataset.id === activeId);
    });
  };

  function open(id) {
    const panel = registry.get(id);
    if (!panel) { console.warn(`[kpdf] 侧栏没有面板「${id}」`); return null; }
    if (activeId === id && !root.hidden) { close(); setActiveTab(); return null; }
    unmountActive();
    activeId = id;
    root.hidden = false;
    activeApi = panel.create(body, reader) || null;
    setActiveTab();
    // 侧栏占位变化会改变阅读区宽度 → 让视口按锚点重排
    requestAnimationFrame(() => {
      for (const v of reader.views) v.relayout({ keepAnchor: true });
    });
    return activeApi;
  }

  function close() {
    unmountActive();
    activeId = null;
    root.hidden = true;
    setActiveTab();
    requestAnimationFrame(() => {
      for (const v of reader.views) v.relayout({ keepAnchor: true });
    });
  }

  for (const p of panels) {
    registry.set(p.id, p);
    const tab = el('button.kp-sideTab', { type: 'button', title: p.label, dataset: { id: p.id } }, [
      el('span.kp-sideIcon', { text: p.icon || '•' }),
      el('span.kp-sideLabel', { text: p.label }),
    ]);
    tab.addEventListener('click', () => (activeId === p.id ? close() : open(p.id)));
    tabs.append(tab);
  }

  reader.on('destroy', () => close());
  if (initial) open(initial);

  return {
    el: root,
    open,
    close,
    get active() { return activeId; },
    destroy() { close(); root.remove(); },
  };
}
