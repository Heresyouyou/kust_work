/* ═══════════════════════════════════════════════════════════════
 * ui/findbar.js —— 全文检索条
 *
 * 说明一个实现边界：检索是**逐页扫描内核文本层**（document.search），
 * 高亮则落在已挂载的文本层 span 上。因此：
 *   · 命中列表精确到页，够用且便宜
 *   · 若目标页尚未渲染出文本层，高亮要等那一页滚进视野后才可见
 *     （这是虚拟化的必然结果，不是 bug）
 * ═══════════════════════════════════════════════════════════════ */
import { el, debounce } from '../kpdf/util.js';

export function mountFindbar(reader, host) {
  const input = el('input.kp-findInput', {
    type: 'search', placeholder: '全文检索…（Enter 下一个）', 'aria-label': '全文检索',
  });
  const count = el('span.kp-findCount', { text: '' });
  const btnPrev = el('button.kp-btn', { type: 'button', title: '上一个', text: '‹' });
  const btnNext = el('button.kp-btn', { type: 'button', title: '下一个', text: '›' });
  const btnClose = el('button.kp-btn', { type: 'button', title: '关闭 (Esc)', text: '✕' });

  const root = el('div.kp-findbar', { hidden: true }, [input, count, btnPrev, btnNext, btnClose]);
  host.append(root);

  let hits = [];
  let cursor = -1;
  let searching = false;
  let token = 0;

  const show = () => { root.hidden = false; input.focus(); input.select(); };
  const hide = () => {
    root.hidden = true;
    for (const v of reader.views) v.highlight('');
  };

  const jump = (delta) => {
    if (!hits.length) return;
    cursor = (cursor + delta + hits.length) % hits.length;
    const h = hits[cursor];
    const paneIdx = reader.panes.findIndex((p) => p.label === h.label);
    reader.gotoPage(h.page);
    count.textContent = `${cursor + 1}/${hits.length} · ${h.label} p${h.page}`;
    reader.views[paneIdx]?.highlight(input.value);
  };

  const run = debounce(async () => {
    const q = input.value.trim();
    if (!q) {
      hits = []; cursor = -1; count.textContent = '';
      for (const v of reader.views) v.highlight('');
      return;
    }
    if (searching) return;
    searching = true;
    const my = ++token;
    count.textContent = '检索中…';
    try {
      const found = await reader.find(q);
      if (my !== token) return;   // 已被更新的检索取代
      hits = found;
      cursor = hits.length ? 0 : -1;
      count.textContent = hits.length ? `1/${hits.length}` : '无匹配';
      if (hits.length) jump(0);
    } catch (err) {
      console.warn('[kpdf] 检索失败：', err);
      count.textContent = '检索失败';
    } finally {
      searching = false;
    }
  }, 320);

  input.addEventListener('input', run);
  input.addEventListener('keydown', (e) => {
    if (e.key === 'Enter') { e.preventDefault(); if (hits.length) jump(e.shiftKey ? -1 : 1); else run(); }
    if (e.key === 'Escape') hide();
  });
  btnNext.addEventListener('click', () => jump(1));
  btnPrev.addEventListener('click', () => jump(-1));
  btnClose.addEventListener('click', hide);

  // Ctrl/⌘+F 打开
  const onKey = (e) => {
    if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'f') {
      e.preventDefault();
      show();
    }
    if (e.key === 'Escape' && !root.hidden) hide();
  };
  window.addEventListener('keydown', onKey);

  return {
    el: root,
    open: show,
    close: hide,
    toggle: () => (root.hidden ? show() : hide()),
    destroy() {
      window.removeEventListener('keydown', onKey);
      root.remove();
    },
  };
}
