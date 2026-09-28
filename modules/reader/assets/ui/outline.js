/* ═══════════════════════════════════════════════════════════════
 * ui/outline.js —— 目录大纲面板
 *
 * 大纲条目可能指向外部 URL 或文档内页码（document.js 已把 dest 解析成页码）。
 * 拿不到页码的条目仍然显示（保留层级），只是点击不跳 —— 比整棵大纲消失友好。
 * 双栏时两栏大纲分节展示：译文 PDF 常常没有大纲，这时只显示原文那一节。
 * ═══════════════════════════════════════════════════════════════ */
import { el } from '../kpdf/util.js';

export const outlinePanel = {
  id: 'outline',
  label: '目录',
  icon: '☰',

  create(container, reader) {
    const wrap = el('div.kp-outlineScroll');
    container.append(wrap);

    const sections = [];
    let destroyed = false;

    for (const pane of reader.panes.filter((p) => p.doc)) {
      const section = el('div.kp-outlineSection');
      section.append(el('div.kp-thumbHead', { text: `${pane.label} · 目录` }));
      const list = el('div.kp-outlineList');
      section.append(list);
      wrap.append(section);

      pane.doc.outline().then((items) => {
        if (destroyed) return;
        if (!items || !items.length) {
          list.append(el('div.kp-outlineEmpty', { text: '该文档没有书签目录' }));
          return;
        }
        for (const it of items) {
          const row = el('a.kp-outlineRow', {
            href: it.url || '#',
            target: it.url ? '_blank' : null,
            rel: it.url ? 'noopener' : null,
            title: it.url || (it.page ? `跳转到第 ${it.page} 页` : '无跳转目标'),
            style: { paddingLeft: `${8 + it.depth * 12}px` },
          }, [
            el('span.kp-outlineText', { text: it.title || '(无标题)' }),
            it.page ? el('span.kp-outlinePage', { text: String(it.page) }) : null,
          ]);
          if (!it.url && it.page) {
            row.addEventListener('click', (e) => {
              e.preventDefault();
              reader.gotoPage(it.page);
            });
          }
          list.append(row);
        }
      }).catch((err) => {
        console.warn('[kpdf] 大纲读取失败：', err);
        list.append(el('div.kp-outlineEmpty', { text: '大纲读取失败' }));
      });

      sections.push(section);
    }

    if (!sections.length) wrap.append(el('div.kp-outlineEmpty', { text: '没有可用的目录' }));

    return {
      el: wrap,
      destroy() {
        destroyed = true;
        wrap.remove();
      },
    };
  },
};
