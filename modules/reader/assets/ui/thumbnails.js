/* ═══════════════════════════════════════════════════════════════
 * ui/thumbnails.js —— 缩略图面板
 *
 * 两个关键取舍：
 *  ① **只画看得见的**：用 IntersectionObserver 观察每个缩略图槽位，
 *     滚到哪画到哪。否则打开一本 300 页的 PDF 会瞬间排队 300 次渲染。
 *  ② **并发压到 2**：缩略图与正文抢同一个 pdf.js worker，抢太狠正文会卡。
 * 点击缩略图走 reader.gotoPage()，因此左右两栏会一起跳 —— 这正是双栏对照想要的。
 * ═══════════════════════════════════════════════════════════════ */
import { el, rafThrottle } from '../kpdf/util.js';

const THUMB_W = 108;      // 缩略图目标宽度（CSS px）
const CONCURRENCY = 2;

export const thumbnailPanel = {
  id: 'thumbnails',
  label: '缩略图',
  icon: '▤',

  create(container, reader) {
    const docList = reader.panes.filter((p) => p.doc);
    const scroll = el('div.kp-thumbScroll');
    container.append(scroll);

    /** @type {Array<{doc, index, box, thumb, label, canvas}>} */
    const cells = [];
    const queue = [];
    let running = 0;
    let destroyed = false;
    let currentPage = reader.store.get('page') || 1;

    const pump = () => {
      while (running < CONCURRENCY && queue.length && !destroyed) {
        const cell = queue.shift();
        if (cell.done || !cell.thumb.isConnected) continue;
        renderCell(cell);
      }
    };

    const renderCell = async (cell) => {
      running++;
      cell.done = true;
      try {
        const kp = await cell.doc.page(cell.index);
        if (destroyed) return;
        const canvas = el('canvas.kp-thumbCanvas');
        const scale = THUMB_W / kp.width;
        const task = kp.render(canvas, { scale, dpr: Math.min(window.devicePixelRatio || 1, 2) });
        cell.canvas = canvas;
        cell.thumb.replaceChildren(canvas);
        cell.cancel = task.cancel;
        await task.promise;
      } catch (err) {
        if (!/cancel/i.test(err?.name || '')) console.warn(`[kpdf] 缩略图 ${cell.index + 1} 渲染失败：`, err);
      } finally {
        running--;
        pump();
      }
    };

    const observer = new IntersectionObserver((entries) => {
      for (const e of entries) {
        if (!e.isIntersecting) continue;
        const cell = cells.find((c) => c.thumb === e.target);
        if (cell && !cell.done && !cell.queued) {
          cell.queued = true;
          queue.push(cell);
        }
      }
      pump();
    }, { root: scroll, rootMargin: '240px 0px' });

    // 每个文档一节
    for (const pane of docList) {
      const section = el('div.kp-thumbSection');
      section.append(el('div.kp-thumbHead', { text: `${pane.label} · ${pane.doc.numPages} 页` }));
      const grid = el('div.kp-thumbGrid');
      section.append(grid);
      for (let i = 0; i < pane.doc.numPages; i++) {
        const size = pane.doc.pages[i].sizeOr(pane.doc.commonSize);
        const thumb = el('div.kp-thumb', {
          title: `${pane.label} 第 ${i + 1} 页`,
          dataset: { page: String(i + 1) },
          style: { aspectRatio: `${size.width} / ${size.height}` },
        });
        thumb.append(el('span.kp-thumbNum', { text: String(i + 1) }));
        thumb.addEventListener('click', () => reader.gotoPage(i + 1));
        grid.append(thumb);
        const cell = { doc: pane.doc, index: i, thumb, label: pane.label, done: false, queued: false };
        cells.push(cell);
        observer.observe(thumb);
      }
      scroll.append(section);
    }

    // 跟随当前页：高亮 + 反向滚动（只在当前页变化时动，不跟每一帧滚动打架）
    const markCurrent = rafThrottle(() => {
      for (const c of cells) c.thumb.classList.toggle('on', c.index + 1 === currentPage);
      const hit = cells.find((c) => c.index + 1 === currentPage);
      if (hit && !hit.thumb.dataset.manual) hit.thumb.scrollIntoView({ block: 'nearest' });
    });

    const offStore = reader.store.subscribe((changed) => {
      if (!('page' in changed)) return;
      currentPage = changed.page;
      markCurrent();
    });
    markCurrent();

    return {
      el: scroll,
      destroy() {
        destroyed = true;
        queue.length = 0;
        observer.disconnect();
        offStore();
        cells.forEach((c) => {
          try { c.cancel?.(); } catch { /* 已结束 */ }
          c.canvas?.remove();
        });
        cells.length = 0;
      },
    };
  },
};
