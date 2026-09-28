/* ═══════════════════════════════════════════════════════════════
 * ui/toolbar.js —— 工具栏
 *
 * 只做「把 reader 的接口接到按钮上」+「把 store 的变化画到界面上」两件事。
 * 不自己保存任何状态 —— 单一数据源是 reader.store，避免出现
 * 「按钮显示 100% 而实际是 85%」这类双份状态不一致。
 * ═══════════════════════════════════════════════════════════════ */
import { el, fmtPct, prettyName } from '../kpdf/util.js';
import { MODES, MODE_LABEL } from '../kpdf/layout.js';
import { SYNC_MODE } from '../kpdf/sync.js';

const ZOOM_STEPS = [0.5, 0.75, 1, 1.25, 1.5, 2, 3, 4];

/** 同步下拉的四档：三档同步策略 + 关闭。 */
const SYNC_CHOICES = [
  { value: SYNC_MODE.ANCHOR, label: '同步 · 按相对位置' },
  { value: SYNC_MODE.PAGE, label: '同步 · 按页号' },
  { value: SYNC_MODE.PROGRESS, label: '同步 · 按整篇进度' },
  { value: 'off', label: '不同步（独立滚动）' },
];

export function mountToolbar(reader, host) {
  const root = el('div.kp-toolbar');

  const mkBtn = (label, title, onClick, { cls = '' } = {}) => {
    const b = el('button.kp-btn' + (cls ? `.${cls}` : ''), {
      type: 'button', title, 'aria-label': title, text: label,
    });
    b.addEventListener('click', (e) => { e.preventDefault(); onClick(b); });
    return b;
  };

  // ── 左：侧栏 + 标题 ──
  const btnThumb = mkBtn('▤', '缩略图侧栏 (T)', () => reader.openSidebar('thumbnails'));
  const btnOutline = mkBtn('☰', '目录大纲 (O)', () => reader.openSidebar('outline'));
  const title = el('span.kp-title', {
    text: reader.options.title || prettyName(reader.panes[0]?.src) || 'PDF',
    title: reader.panes.map((p) => `${p.label}: ${p.src}`).join('\n'),
  });

  // ── 中：翻页 ──
  const btnPrev = mkBtn('‹', '上一页 (PageUp)', () => reader.gotoPage(reader.store.get('page') - 1));
  const btnNext = mkBtn('›', '下一页 (PageDown)', () => reader.gotoPage(reader.store.get('page') + 1));
  const pageInput = el('input.kp-pageInput', {
    type: 'text', inputmode: 'numeric', 'aria-label': '页码',
  });
  pageInput.addEventListener('change', () => {
    const n = parseInt(pageInput.value, 10);
    if (Number.isFinite(n)) reader.gotoPage(n);
    else syncFromStore();
  });
  pageInput.addEventListener('keydown', (e) => { if (e.key === 'Enter') pageInput.blur(); });
  const pageTotal = el('span.kp-pageTotal');

  // ── 右：缩放 ──
  const btnZoomOut = mkBtn('−', '缩小 (Ctrl+-)', () => reader.setScale(reader.store.get('scale') / 1.2, { by: 'button' }));
  const btnZoomIn = mkBtn('+', '放大 (Ctrl++)', () => reader.setScale(reader.store.get('scale') * 1.2, { by: 'button' }));
  const zoomLabel = el('span.kp-zoomLabel');
  const zoomSelect = el('select.kp-select', {
    title: '缩放预设',
    'aria-label': '缩放预设',
  }, [el('option', { value: 'fit', text: '适应' }),
      el('option', { value: '100', text: '100%' }),
      ...ZOOM_STEPS.map((z) => el('option', { value: String(z * 100), text: `${Math.round(z * 100)}%` }))]);
  zoomSelect.addEventListener('change', () => {
    const v = zoomSelect.value;
    if (v === 'fit') reader.fit(reader.store.get('mode') === 'spread' ? 'page' : 'width');
    else reader.setScale(Number(v) / 100, { by: 'preset' });
    zoomSelect.selectedIndex = 0;
    zoomSelect.blur();
  });

  // ── 右：布局 ──
  const modeSelect = el('select.kp-select', { title: '布局模式', 'aria-label': '布局模式' },
    MODES.map((m) => el('option', { value: m, text: MODE_LABEL[m] })));
  modeSelect.addEventListener('change', () => reader.setLayout(modeSelect.value));

  // ── 右：左右同步 ──
  const syncSelect = el('select.kp-select', {
    title: '左右对照同步策略', 'aria-label': '同步策略',
  }, SYNC_CHOICES.map((c) => el('option', { value: c.value, text: c.label })));
  syncSelect.addEventListener('change', () => {
    const v = syncSelect.value;
    if (v === 'off') reader.toggleSync(false);
    else {
      reader.toggleSync(true);
      reader.setSyncMode(v);
    }
    syncSelect.blur();
  });

  // ── 右：其他 ──
  const btnRotate = mkBtn('⟳', '旋转 90°', () => reader.setRotation((reader.store.get('rotation') || 0) + 90));
  const btnText = mkBtn('A', '切换可选中文字层', (b) => {
    const on = !reader.store.get('textLayer');
    reader.setTextLayer(on);
    b.classList.toggle('on', on);
  }, { cls: 'on' });
  const btnFind = mkBtn('⌕', '全文检索 (Ctrl+F)', () => reader._ui?.findbar?.toggle?.());
  const btnDownload = mkBtn('⤓', '下载当前文档', () => reader.download(0));
  const btnSelfTest = mkBtn('?', '自检：打印渲染/同步统计到控制台', () => {
    const s = reader.stats();
    console.log('[kpdf] 自检快照', s);
    window.__kpdfLastStats = s;
    return s;
  });

  // 进度条（加载文档 / 预热尺寸时可见）
  const progress = el('div.kp-progress');
  const progressFill = el('div.kp-progressFill');
  progress.append(progressFill);

  const sep = () => el('span.kp-sep');

  root.append(
    progress,
    btnThumb, btnOutline, title,
    el('span.kp-spacer'),
    btnPrev, pageInput, pageTotal, btnNext,
    el('span.kp-spacer'),
    btnZoomOut, zoomLabel, btnZoomIn, zoomSelect, sep(),
    modeSelect, sep(),
    syncSelect, sep(),
    btnRotate, btnText, btnFind, btnDownload, btnSelfTest,
  );
  host.append(root);

  // ── store → DOM ──
  const syncFromStore = () => {
    const s = reader.store.get();
    if (document.activeElement !== pageInput) pageInput.value = String(s.page || 1);
    pageTotal.textContent = `/ ${s.total || '—'}`;
    pageInput.style.width = `${Math.max(2, String(s.total || 99).length) + 1}ch`;
    btnPrev.disabled = (s.page || 1) <= 1;
    btnNext.disabled = (s.page || 1) >= (s.total || 1);

    // 适配模式下标「适配比例」，手动缩放时才标真实缩放值 —— 与 Acrobat 一致
    const v = reader.views[0];
    const inFit = s.fit && s.fit !== 'none';
    zoomLabel.textContent = inFit ? `适应 ${fmtPct(v?.fitRatio ?? 1)}` : fmtPct(s.scale || 1);

    modeSelect.value = s.mode;
    syncSelect.value = s.syncOn ? s.syncMode : 'off';
    syncSelect.disabled = reader.views.length < 2;
    modeSelect.value = s.mode;
    btnText.classList.toggle('on', Boolean(s.textLayer));
    btnThumb.classList.toggle('on', reader._sidebarOpen === 'thumbnails');
    btnOutline.classList.toggle('on', reader._sidebarOpen === 'outline');
  };

  const offStore = reader.store.subscribe(syncFromStore);
  const offProgress = reader.on('progress', ({ done, total }) => {
    if (!total) return;
    progressFill.style.width = `${Math.round((done / total) * 100)}%`;
    progress.style.opacity = done >= total ? '0' : '1';
  });
  const offReady = reader.on('ready', () => { progress.style.opacity = '0'; });

  syncFromStore();

  return {
    el: root,
    update: syncFromStore,
    destroy() {
      offStore();
      offProgress();
      offReady();
      root.remove();
    },
  };
}
