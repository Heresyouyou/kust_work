/* ═══════════════════════════════════════════════════════════════
 * kpdf/kernel.js —— 内核契约层
 *
 * 这一层存在的唯一理由：把「PDF 怎么解码/光栅化」这件事，从框架里彻底隔离出去。
 * 上层（document / layout / viewport / sync / ui）只认下面这份契约，
 * 不认 pdf.js。要换解码器（比如换成服务端渲染、换成 WebAssembly 自研解析），
 * 只需再写一个适配器注册进来到这个注册表，框架其余代码一行不用改。
 *
 * ── 契约（适配器必须实现）──────────────────────────────────────
 *   defineKernel('名字', openFn)
 *   openFn(source, opts) -> Promise<KernelDoc>
 *     source: {kind:'url'|'file'|'data', url?, data?, file?}
 *     opts:   {onProgress, onPassword, cMapUrl, standardFontDataUrl, wasmUrl, iccUrl, ...}
 *
 *   KernelDoc
 *     numPages: number
 *     fingerprints: string[]
 *     title(): Promise<string>
 *     metadata(): Promise<object>
 *     outline(): Promise<Array|null>
 *     getPage(index0): Promise<KernelPage>       // index 从 0 起
 *     destroy(): Promise<void>
 *
 *   KernelPage
 *     index: number                               // 0 起
 *     width / height: number                      // ★ 已换算成 CSS px（scale=1 时）
 *     rotate: number                              // 0/90/180/270
 *     render(canvas, {scale, dpr, rotation, background})
 *           -> {promise, cancel()}                // 必须可取消
 *     textContent(): Promise<{items:Array}>
 *     annotations(): Promise<Array>                // 供链接层用
 * ═══════════════════════════════════════════════════════════════ */

/** 已注册的内核：name → openFn */
const _kernels = new Map();

/**
 * 注册一个内核适配器。同名的后注册者覆盖前者（便于热替换与测试替身）。
 * @param {string} name
 * @param {(source:object, opts:object)=>Promise<any>} openFn
 */
export function defineKernel(name, openFn) {
  if (typeof openFn !== 'function') throw new TypeError('kpdf: defineKernel 需要函数');
  _kernels.set(name, openFn);
}

export function hasKernel(name) {
  return _kernels.has(name);
}

export function listKernels() {
  return Array.from(_kernels.keys());
}

/**
 * 统一入口：按名字取内核并打开文档。
 * 上层代码永远调这个，不直接 import 任何解码器。
 */
export async function openDocument(source, { kernel = 'pdfjs', ...opts } = {}) {
  const openFn = _kernels.get(kernel);
  if (!openFn) {
    throw new Error(`kpdf: 内核「${kernel}」未注册（已注册：${listKernels().join(', ') || '无'}）`);
  }
  return openFn(_normalizeSource(source), opts);
}

/** 把 'http://…' / File / ArrayBuffer 统一成 source 描述对象。 */
export function _normalizeSource(source) {
  if (!source) throw new Error('kpdf: 缺少文档来源');
  if (typeof source === 'string') return { kind: 'url', url: source };
  if (source instanceof URL) return { kind: 'url', url: source.href };
  if (typeof File !== 'undefined' && source instanceof File) return { kind: 'file', file: source };
  if (source instanceof ArrayBuffer || ArrayBuffer.isView(source)) {
    return { kind: 'data', data: new Uint8Array(source.buffer || source) };
  }
  if (typeof source === 'object' && source.url) return { kind: 'url', url: source.url };
  throw new Error('kpdf: 无法识别的文档来源');
}

/** 契约文本（/api/reader/info 回显给排查用，避免文档只活在注释里）。 */
export const CONTRACT_DOC = [
  'defineKernel(name, openFn)',
  'openFn(source, opts) -> KernelDoc',
  'KernelDoc: numPages, fingerprints, title(), metadata(), outline(), getPage(i), destroy()',
  'KernelPage: index, width, height, rotate, render(canvas, opts), textContent(), '
  + 'annotations(), rectToCss(rect, opts), createTextLayer(container, opts)',
].join('\n');
