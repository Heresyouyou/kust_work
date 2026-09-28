/* ═══════════════════════════════════════════════════════════════
 * ui/statusbar.js —— 状态栏
 *
 * 除了常规的页码/缩放，这里刻意暴露两个**可验证的量**：
 *   · 对齐偏差：两侧锚点的页序差（同步是否真的在生效，一眼能看出来）
 *   · 已渲染页数 / 画布存活数：证明虚拟化在起作用（不是把整本都画了）
 * 把内部指标放到界面上，是排查「看起来卡/看起来没同步」最省事的办法。
 * ═══════════════════════════════════════════════════════════════ */
import { el, fmtPct } from '../kpdf/util.js';
import { SYNC_MODE } from '../kpdf/sync.js';

export function mountStatusbar(reader, host) {
  const root = el('div.kp-status');
  const left = el('span.kp-statusSeg');
  const mid = el('span.kp-statusSeg');
  const right = el('span.kp-statusSeg.kp-statusRight');
  root.append(left, mid, right);
  host.append(root);

  const draw = () => {
    const s = reader.store.get();
    const v0 = reader.views[0];
    const inFit = s.fit && s.fit !== 'none';

    const parts = [];
    parts.push(`第 ${s.page || 1} / ${s.total || '—'} 页`);
    parts.push(inFit ? `${v0?.fitMode === 'width' ? '适合宽度' : '适合页面'} ${fmtPct(v0?.fitRatio ?? 1)}`
      : `缩放 ${fmtPct(s.scale || 1)}`);
    left.textContent = parts.join('  ·  ');

    const st = s.skew;
    const syncName = { [SYNC_MODE.ANCHOR]: '相对位置', [SYNC_MODE.PAGE]: '页号', [SYNC_MODE.PROGRESS]: '进度' }[s.syncMode] || s.syncMode;
    if (reader.views.length < 2) {
      mid.textContent = s.loading ? '正在打开文档…' : '单栏阅读';
    } else if (!s.syncOn) {
      mid.textContent = '左右独立滚动';
    } else if (st) {
      // 偏差 < 0.02 页可视为完全对齐（0.02 页 ≈ 半行字）
      const ok = st.dPage < 0.02 && st.dOff < 0.02;
      mid.textContent = `${ok ? '✓ 已对齐' : '对齐中'}（${syncName} 偏差 ${st.dPage.toFixed(3)} 页）`;
      mid.classList.toggle('kp-warn', !ok);
      mid.title = `页序偏差 ${st.dPage.toFixed(4)}，页内偏移偏差 ${st.dOff.toFixed(4)}`;
    } else {
      mid.textContent = `同步已开（${syncName}）`;
    }

    const stats = reader.panes.map((p) => {
      if (!p.view) return p.error ? `${p.label}: 打开失败` : `${p.label}: —`;
      const r = p.view.renderer.stats;
      return `${p.label} 已渲染 ${r.rendered} 页 / 在存 ${p.view.renderer.liveCount}`;
    }).join('　');
    right.textContent = stats;
  };

  const offStore = reader.store.subscribe(draw);
  const offPage = reader.views[0]?.on('pagerendered', draw);
  draw();

  // 渲染是异步的，指标要跟着刷新（低频即可，不必每帧）
  const timer = setInterval(draw, 1200);

  return {
    el: root,
    update: draw,
    destroy() {
      clearInterval(timer);
      offStore();
      offPage?.();
      root.remove();
    },
  };
}
