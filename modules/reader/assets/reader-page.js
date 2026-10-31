/* ═══════════════════════════════════════════════════════════════
 * reader-page.js —— 独立阅读器页面的装配脚本（/reader）
 *
 * 支持两种进入方式：
 *  ① URL 参数：/reader?file=<原文URL>&peer=<译文URL>&title=…&layout=…&sync=…
 *  ② 拖入本地文件：拖 1 个 = 单栏阅读；拖 2 个 = 左右对照 + 自动同步
 *     （这条是为了「手上正好有两个版本的 PDF，想马上并排看」这个场景）
 * ═══════════════════════════════════════════════════════════════ */
import { createReader, MODES, SYNC_MODE } from './kpdf/index.js';
import { parseQuery, el, prettyName } from './kpdf/util.js';

const q = parseQuery();
const host = document.getElementById('reader');

// 嵌入模式（/PDFreader?...&embed=1）：给宿主页面做右侧分屏用。
// 藏掉「返回工作台」浮条，工具栏左内边距收回，其余交互不变。
const embed = q.embed === '1' || q.embed === 'true';
if (embed) document.documentElement.classList.add('kp-embed');

// 深链接：?page=7 直接翻到第 7 页（导读页的「P.7」胶囊用这条）
const initialPage = Math.max(1, parseInt(q.page || '0', 10) || 0);

/** 顶部提示条（拖拽 / 打开本地文件 / 最近文件） */
function mountLanding(onOpen) {
  const box = el('div.kp-landing', {}, [
    el('div.kp-landingIcon', { text: '📄' }),
    el('div.kp-landingTitle', { text: '把 PDF 拖到这里' }),
    el('div.kp-landingHint', {
      text: '拖 1 个文件 = 单栏阅读；拖 2 个文件 = 左右对照（自动同步滚动与缩放）',
    }),
    (() => {
      const label = el('label.kp-landingBtn', {}, ['选择文件', el('input', {
        type: 'file', accept: '.pdf,application/pdf', multiple: true, style: { display: 'none' },
      })]);
      label.querySelector('input').addEventListener('change', (e) => {
        const files = Array.from(e.target.files || []);
        if (files.length) onOpen(files.slice(0, 2));
      });
      return label;
    })(),
    el('div.kp-recent', { id: 'kpRecent' }),
  ]);
  host.append(box);

  // 拖拽
  const stop = (e) => { e.preventDefault(); e.stopPropagation(); };
  ['dragenter', 'dragover', 'dragleave', 'drop'].forEach((t) => document.addEventListener(t, stop));
  document.addEventListener('dragover', () => box.classList.add('over'));
  document.addEventListener('dragleave', () => box.classList.remove('over'));
  document.addEventListener('drop', (e) => {
    box.classList.remove('over');
    const files = Array.from(e.dataTransfer?.files || []).filter((f) => /\.pdf$/i.test(f.name));
    if (files.length) { box.remove(); onOpen(files.slice(0, 2)); }
  });

  // 最近文件（服务端提供，方便没有 URL 参数时直接点开）
  fetch('/api/reader/files', { cache: 'no-store' })
    .then((r) => r.json())
    .then((d) => {
      const wrap = box.querySelector('#kpRecent');
      const papers = d.papers || [];
      if (!papers.length) return;
      wrap.append(el('div.kp-recentHead', { text: `最近 ${d.window_days} 天的文件` }));
      for (const p of papers.slice(0, 12)) {
        const btns = p.files.filter((f) => f.available).map((f) => {
          const b = el('button.kp-recentBtn', { type: 'button', text: f.label });
          b.addEventListener('click', () => {
            host.replaceChildren();
            const pane = { label: f.label, src: f.url };
            const peer = p.files.find((x) => x.available && x.kind !== f.kind && (f.kind === 'original' ? x.kind === 'dual' : x.kind === 'original'));
            build([pane, ...(peer ? [{ label: peer.label, src: peer.url }] : [])]);
          });
          return b;
        });
        wrap.append(el('div.kp-recentRow', {}, [
          el('span.kp-recentTitle', { text: p.title, title: p.title }),
          el('span.kp-recentBtns', {}, btns),
        ]));
      }
    })
    .catch(() => { /* 列表拿不到不影响拖拽路径 */ });
}


/** 推断对齐表地址。三条路，从最精确到最通用：
 *  1) `/papers/pdf/<wid>`        → `/papers/align/<wid>`（论文模块）
 *  2) `/api/tr/file/<sha>/orig`  → `/api/tr/align/<sha>`（内容寻址仓库）
 *  3) 其它任意两个 PDF           → `/api/tr/align?orig=…&mono=…`
 *     后端按文件名认哪边是译文，所以 /PDFreader?left=/uploads/x&right=/translated/y
 *     这种「谁在左谁在右都可能」的情况也能用。
 *  有了它，/reader、/PDFreader、translate 页只要开两栏就有句对镜像，
 *  宿主不必传参 —— 和导读分屏是同一套效果。 */
function alignURLFor(src, peer) {
  const s = String(src || '');
  const m = s.match(/\/papers\/pdf\/([A-Za-z0-9_]+)/);
  if (m) return `/papers/align/${m[1]}`;
  const t = s.match(/\/api\/tr\/file\/([0-9a-fA-F]{16,64})\//);
  if (t) return `/api/tr/align/${t[1]}`;
  if (s && peer) {
    return `/api/tr/align?orig=${encodeURIComponent(s)}`
      + `&mono=${encodeURIComponent(String(peer))}`;
  }
  return '';
}

function paneLabel(url, fallback) {
  const n = prettyName(url).toLowerCase();
  if (n.includes('.dual.')) return '双语对照';
  if (n.includes('.mono.')) return '中文版';
  if (n.includes('.no_watermark')) return '中文版';
  return fallback;
}

async function build(panes, extra = {}) {
  document.title = `${extra.title || prettyName(panes[0]?.src) || 'PDF'} · 阅读器`;
  const reader = await createReader('#reader', {
    panes: panes.map((p, i) => ({ ...p, label: p.label || (i ? '文档二' : '原文') })),
    title: extra.title || '',
    mode: MODES.includes(extra.layout) ? extra.layout : 'continuous',
    fit: extra.fit || 'width',
    // 原文↔译文的对齐表（划词镜像是句级对齐，不是按版面猜）
    align: extra.align || alignURLFor(panes[0] && panes[0].src,
                                      panes[1] && panes[1].src),
    // 分屏嵌入时默认收起侧栏：iframe 本来就窄，两栏对照要的是横向空间
    sidebar: extra.sidebar === 'none' ? null
      : (extra.sidebar || (embed ? null : 'thumbnails')),
    sync: {
      enabled: panes.length > 1,
      mode: extra.sync || SYNC_MODE.ANCHOR,
      scaleMode: extra.scale || 'fit',
    },
  });
  window.kpdf = reader;   // 控制台调试 / 自动化自检入口
  window.__reader = reader;   // CDP 验收脚本（tests/browser_verify.mjs）读它
  if (initialPage > 1) {
    try { reader.gotoPage(initialPage, { primary: true }); } catch (e) { console.warn('[kpdf] 初始页跳转失败', e); }
  }
  restorePaneMode(reader, panes.length);
  bridgeToHost(reader);
  const s = reader.stats();
  console.log('[kpdf] 就绪', s);
  return reader;
}

/* ══ 「显示哪一版」跨会话记住 ══
 * 用户切到「只看中文版」后，关掉分屏再打开、或下次进另一篇对照文档，仍按上次的选择。
 * 键带版本号，将来行为变了可以换键名作废旧值。 */
const PANE_KEY = 'kpdf:paneMode';

function readPaneMode() {
  try {
    const v = localStorage.getItem(PANE_KEY);
    return ['both', 'a', 'b'].includes(v) ? v : 'both';
  } catch { return 'both'; }
}

function restorePaneMode(reader, paneCount) {
  if (paneCount < 2) return;
  const saved = readPaneMode();
  if (saved !== 'both') {
    try { reader.setPaneMode(saved); } catch (e) { console.warn('[kpdf] 恢复显示版本失败', e); }
  }
  reader.on('panemode', ({ mode }) => {
    try { localStorage.setItem(PANE_KEY, mode); } catch { /* 隐私模式忽略 */ }
  });
}

/**
 * 与宿主页面（论文导读）的双向通信：
 *   宿主 → 阅读器：{__papers:1, type:'goto', page:7}
 *   阅读器 → 宿主：{__kpdf:1, type:'ready'|'page', page, total}
 * 只在 iframe 内启用，独立打开 /PDFreader 时什么都不做。
 */
function bridgeToHost(reader) {
  if (window.parent === window) return;
  const view = reader.views && reader.views[0];
  const total = view?.doc?.numPages || 0;
  const post = (msg) => {
    try { window.parent.postMessage(msg, '*'); } catch { /* 跨域宿主忽略 */ }
  };

  post({ __kpdf: 1, type: 'ready', page: view?.page || 1, total });
  if (view?.on) {
    view.on('pagechange', ({ page }) => post({ __kpdf: 1, type: 'page', page, total }));
  }
  window.addEventListener('message', (e) => {
    const d = e.data;
    if (!d || d.__papers !== 1) return;
    if (d.type === 'goto' && Number(d.page) > 0) {
      const page = Number(d.page);
      if (d.quote) reader.focusQuote(page, d.quote);
      else reader.gotoPage(page, { primary: !!d.primary });
    }
    else if (d.type === 'fit' && d.mode) reader.fit(d.mode);
    else if (d.type === 'scale' && Number(d.scale) > 0) reader.setScale(Number(d.scale));
  });
}

// ── 入口分流 ──────────────────────────────────────────────────
if (q.file || q.src) {
  const primary = q.file || q.src;
  const panes = [{ label: q.label || paneLabel(primary, '原文'), src: primary }];
  if (q.peer) panes.push({ label: q.peerlabel || paneLabel(q.peer, '译文'), src: q.peer });
  build(panes, {
    title: q.title || '', layout: q.layout, fit: q.fit, sidebar: q.sidebar, sync: q.sync,
    scale: q.scale, align: q.align,
  }).catch(showFatal);
} else {
  mountLanding((files) => {
    host.replaceChildren();
    const panes = files.map((f, i) => ({ label: i ? '文档二' : '原文', src: f }));
    build(panes, { title: files.length > 1 ? '本地文件对照' : '' }).catch(showFatal);
  });
}

function showFatal(err) {
  console.error('[kpdf] 启动失败', err);
  host.replaceChildren(el('div.kp-fatal', {}, [
    el('div.kp-fatalIcon', { text: '⚠' }),
    el('div.kp-fatalTitle', { text: '阅读器启动失败' }),
    el('div.kp-fatalMsg', { text: err?.message || String(err) }),
    el('div.kp-fatalMsg', {
      text: '可访问 /api/reader/info 查看内核状态；或回退到「译文归档」用浏览器原生阅读器打开。',
    }),
  ]));
}
