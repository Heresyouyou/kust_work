/* ═══════════════════════════════════════════════════════════════
 * kpdf/util.js —— 无依赖工具层
 *
 * 只放「跟业务无关、但每个模块都要用」的东西。刻意不引入任何第三方库：
 * 整个框架除 pdf.js 内核外零依赖，这是能塞进内网 Flask 静态白名单的前提。
 * ═══════════════════════════════════════════════════════════════ */

export const clamp = (v, lo, hi) => (v < lo ? lo : v > hi ? hi : v);

export const round2 = (v) => Math.round(v * 100) / 100;

/** 取小数位固定字符串（缩放百分比显示用）。 */
export const fmtPct = (v) => `${Math.round(v * 100)}%`;

export function fmtSize(bytes) {
  if (!bytes && bytes !== 0) return '—';
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1048576) return `${(bytes / 1024).toFixed(0)} KB`;
  return `${(bytes / 1048576).toFixed(1)} MB`;
}

export const nextFrame = () => new Promise((r) => requestAnimationFrame(() => r()));

/** 一帧内只执行最后一次 —— 滚动同步的必备节流。 */
export function rafThrottle(fn) {
  let ticking = false;
  let lastArgs = null;
  return function throttled(...args) {
    lastArgs = args;
    if (ticking) return;
    ticking = true;
    requestAnimationFrame(() => {
      ticking = false;
      fn.apply(this, lastArgs);
    });
  };
}

export function debounce(fn, ms) {
  let timer = 0;
  return function debounced(...args) {
    clearTimeout(timer);
    timer = setTimeout(() => fn.apply(this, args), ms);
  };
}

/**
 * 创建元素。
 * @param {string} tag 支持 'div.kp-x.kp-y#id' 这种带类名/ID 的简写
 * @param {object} props 普通属性直挂；style 传对象；text/html 走文本或 HTML
 * @param {Array} children
 */
export function el(tag, props = {}, children = []) {
  const m = /^([a-zA-Z0-9-]+)((?:[.#][^.#]+)*)$/.exec(tag);
  const node = document.createElement(m ? m[1] : tag);
  if (m && m[2]) {
    for (const part of m[2].split(/(?=[.#])/)) {
      if (!part) continue;
      if (part[0] === '.') node.classList.add(part.slice(1));
      else if (part[0] === '#') node.id = part.slice(1);
    }
  }
  for (const [k, v] of Object.entries(props)) {
    if (v === null || v === undefined || v === false) continue;
    if (k === 'style' && typeof v === 'object') Object.assign(node.style, v);
    else if (k === 'text') node.textContent = v;
    else if (k === 'html') node.innerHTML = v;
    else if (k === 'dataset' && typeof v === 'object') Object.assign(node.dataset, v);
    else if (k.startsWith('on') && typeof v === 'function') node.addEventListener(k.slice(2).toLowerCase(), v);
    else if (k === 'class') node.className = v;
    else node.setAttribute(k, v === true ? '' : v);
  }
  for (const c of [].concat(children)) {
    if (c === null || c === undefined || c === false) continue;
    node.append(c instanceof Node ? c : document.createTextNode(String(c)));
  }
  return node;
}

/**
 * 反推可用的设备像素比。
 *
 * Canvas 面积有硬上限（Safari/iOS 约 16.7M 像素，超了整块画布静默变空白）。
 * 所以不能让 dpr 与 scale 相乘后无限放大 —— 这里按「面积预算」反推，
 * 宁可少一点清晰度，也不能白屏。
 *
 * @param {number} cssW 逻辑宽
 * @param {number} cssH 逻辑高
 * @param {number} dprCap 期望上限（一般取 min(devicePixelRatio, 2)）
 * @param {number} maxPixels 单画布像素预算
 */
export function safeDpr(cssW, cssH, dprCap = 2, maxPixels = 4096 * 4096) {
  const want = Math.min(window.devicePixelRatio || 1, dprCap);
  const area = Math.max(1, cssW * cssH);
  const allow = Math.sqrt(maxPixels / area);
  return clamp(Math.min(want, allow), 0.5, dprCap);
}

/** 把 URL 的查询参数解析成对象（阅读器用 URL 传参，如 ?file=…&peer=…）。 */
export function parseQuery(search = window.location.search) {
  const out = {};
  const sp = new URLSearchParams(search);
  for (const [k, v] of sp.entries()) out[k] = v;
  return out;
}

/** 供下载用的文件名（挂 content-disposition 时用不到，但 UI 提示用得上）。 */
export function prettyName(url) {
  if (!url) return 'document.pdf';
  try {
    const u = new URL(url, window.location.href);
    const base = decodeURIComponent(u.pathname.split('/').pop() || '');
    return base || 'document.pdf';
  } catch {
    return String(url).split('/').pop() || 'document.pdf';
  }
}

/** 触发一次浏览器下载（不新开标签页，避免满天飞的原生 PDF 标签）。 */
export function downloadUrl(url, filename) {
  const a = el('a', { href: url, download: filename || prettyName(url) });
  a.style.display = 'none';
  document.body.append(a);
  a.click();
  setTimeout(() => a.remove(), 0);
}

/** 空操作，占位用（便于统一风格）。 */
export const noop = () => {};
