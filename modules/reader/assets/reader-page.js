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
    sidebar: extra.sidebar === 'none' ? null : (extra.sidebar || 'thumbnails'),
    sync: {
      enabled: panes.length > 1,
      mode: extra.sync || SYNC_MODE.ANCHOR,
      scaleMode: extra.scale || 'fit',
    },
  });
  window.kpdf = reader;   // 控制台调试 / 自动化自检入口
  window.__reader = reader;   // CDP 验收脚本（tests/browser_verify.mjs）读它
  const s = reader.stats();
  console.log('[kpdf] 就绪', s);
  return reader;
}

// ── 入口分流 ──────────────────────────────────────────────────
if (q.file || q.src) {
  const primary = q.file || q.src;
  const panes = [{ label: q.label || paneLabel(primary, '原文'), src: primary }];
  if (q.peer) panes.push({ label: q.peerlabel || paneLabel(q.peer, '译文'), src: q.peer });
  build(panes, {
    title: q.title || '', layout: q.layout, fit: q.fit, sidebar: q.sidebar, sync: q.sync, scale: q.scale,
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
