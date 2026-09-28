/* ═══════════════════════════════════════════════════════════════
 * kpdf/layout.js —— 布局引擎（纯函数，无 DOM）
 *
 * 输入：页尺寸数组 + 缩放 + 模式 + 容器尺寸
 * 输出：每页在「世界坐标」里的方框 {i,x,y,w,h} + 世界总尺寸
 *
 * 为什么把布局做成纯函数：
 *   ① 可在 Node 里单测（tests/core_test.mjs 直接 import 本文件跑数学断言）
 *   ② 三种模式（纵向连续 / 横向连续 / 双页对开）共用同一套坐标系统，
 *      视口层只需按 axis 取坐标，不必为每种模式写一套滚动逻辑
 *   ③ 同步绑定器需要「按坐标反推页码」的逆函数，纯函数才敢到处复用
 *
 * 坐标约定：世界坐标原点在内容左上角，单位 CSS px（已含 scale）。
 * ═══════════════════════════════════════════════════════════════ */
import { clamp } from './util.js';

export const MODES = ['continuous', 'horizontal', 'spread'];

export const MODE_LABEL = {
  continuous: '纵向连续',
  horizontal: '横向单页',
  spread: '双页对开',
};

/** 该模式的主滚动轴。 */
export function axisOf(mode) {
  // 只有 horizontal 是真横向滚动；spread 虽然名字像横排，
  // 但其布局是「双页并排 → 一行 → 折行向下」，实际滚动依然沿 Y，
  // 把 axis 标成 'y' 才能让 anchorAt / posOfAnchor 用对的坐标轴。
  // （之前返回 'x' 时，spread 模式下 anchorAt 用 b.x 比较 pos，永远命中最后一页。）
  return mode === 'horizontal' ? 'x' : 'y';
}

/** 单页在给定缩放下的 CSS 尺寸。 */
export function sizeAt(size, scale) {
  return { width: size.width * scale, height: size.height * scale };
}

/**
 * 按旋转角换算页面尺寸。
 * 页面尺寸来自内核（未旋转），旋转 90/270 时宽高互换即可 ——
 * 不必为此重新加载页面对象，也就不会因为旋转触发一轮尺寸预热。
 */
export function rotateSizes(sizes, rotation = 0) {
  const r = ((rotation % 360) + 360) % 360;
  if (r !== 90 && r !== 270) return sizes;
  return sizes.map((s) => ({ width: s.height, height: s.width }));
}

/**
 * 计算全部页面的方框。
 *
 * @param {object} o
 *   sizes       Array<{width,height}>  每页在 scale=1 的 CSS 尺寸
 *   scale       number
 *   mode        'continuous'|'horizontal'|'spread'
 *   gap         页间距
 *   pad         内容四周留白
 *   containerW  容器宽（纵向模式用于水平居中）
 *   containerH  容器高（横向模式用于垂直居中）
 *   spreadGap   对开模式下两页的间隙
 *   firstAlone  对开模式首页是否单独占一屏（封面惯例）
 * @returns {{boxes: Array, width:number, height:number, axis:'x'|'y', cols:number, pad:number, gap:number}}
 */
export function computeLayout({
  sizes,
  scale = 1,
  mode = 'continuous',
  gap = 14,
  pad = 18,
  containerW = 0,
  containerH = 0,
  spreadGap = 10,
  firstAlone = true,
} = {}) {
  const boxes = [];
  const axis = axisOf(mode);

  if (mode === 'spread') {
    // 双页对开：每行 1~2 页，横向并排，纵向堆叠
    let cursor = 0;
    let row = 0;
    while (cursor < sizes.length) {
      const take = (row === 0 && firstAlone) ? 1 : 2;
      const group = [];
      for (let k = 0; k < take && cursor + k < sizes.length; k++) {
        group.push({ i: cursor + k, s: sizeAt(sizes[cursor + k], scale) });
      }
      const rowW = group.reduce((a, g) => a + g.s.width, 0) + spreadGap * (group.length - 1);
      const rowH = Math.max(...group.map((g) => g.s.height));
      const worldW = Math.max(rowW, containerW);
      let x = (worldW - rowW) / 2;
      const y = pad + row * (rowH + gap);
      for (const g of group) {
        boxes.push({ i: g.i, x, y, w: g.s.width, h: g.s.height, row });
        x += g.s.width + spreadGap;
      }
      row++;
      cursor += take;
    }
    const height = pad * 2 + (boxes.length ? (boxes.at(-1).y + boxes.at(-1).h) : 0);
    return {
      boxes,
      width: Math.max(containerW, ...boxes.map((b) => b.x + b.w + pad)),
      height,
      axis,
      cols: 2,
      pad,
      gap,
    };
  }

  // 连续模式：纵向堆叠 / 横向排列
  let offset = pad;
  let maxCross = 0;
  for (let i = 0; i < sizes.length; i++) {
    const s = sizeAt(sizes[i], scale);
    maxCross = Math.max(maxCross, axis === 'y' ? s.width : s.height);
    const box = axis === 'y'
      ? { i, x: 0, y: offset, w: s.width, h: s.height }
      : { i, x: offset, y: 0, w: s.width, h: s.height };
    boxes.push(box);
    offset += (axis === 'y' ? s.height : s.width) + gap;
  }

  const cross = Math.max(
    (axis === 'y' ? containerW : containerH) || 0,
    maxCross + pad * 2,
  );
  for (const b of boxes) {
    // 交叉轴居中：纵向模式在容器宽内居中，横向模式在容器高内居中。
    // 留白优先于居中：容器比页面还窄时也不能把页面推到左边界外面。
    if (axis === 'y') b.x = Math.max(pad, (cross - b.w) / 2);
    else b.y = Math.max(pad, (cross - b.h) / 2);
  }

  return {
    boxes,
    width: axis === 'y' ? cross : offset - gap + pad,
    height: axis === 'y' ? offset - gap + pad : cross,
    axis,
    cols: 1,
    pad,
    gap,
  };
}

/**
 * 计算适配缩放。
 * @param {object} o {mode, size:{width,height}, containerW, containerH, margin}
 *   mode: 'width' 适配宽度 | 'page' 整页可见 | 'none' 保持当前
 */
export function fitScale({ mode = 'page', size, containerW, containerH, margin = 8 } = {}) {
  if (!size || !containerW) return 1;
  const avaW = Math.max(60, containerW - margin * 2);
  const avaH = Math.max(60, (containerH || 900) - margin * 2);
  const byW = avaW / size.width;
  const byH = avaH / size.height;
  const s = mode === 'width' ? byW : Math.min(byW, byH);
  return clamp(s, 0.1, 8);
}

// ═══════════════════════════════════════════════════════════════
//  坐标 ↔ 锚点（同步绑定的基础）
// ═══════════════════════════════════════════════════════════════

/**
 * 锚点结构（跨文档通用、与缩放无关）：
 *   page      0 起的**浮点**页序 —— 例如 3.4 表示「第 4 页的 40% 处」
 *   offset    页内偏移比例 0~1（沿主滚动轴）
 *   progress  整篇进度 0~1（滚动位置 / 可滚动区间）
 *
 * ★ 为什么必须有 page 浮点数而不是整数页号：
 *   实测同一篇论文的原文 22 页、中文版 10 页、双语版 10 页。
 *   整数页号在两侧根本对不上（第 11 页对面没有对应页），
 *   只有「相对位置」才有意义 —— 这正是老「iframe + postMessage 按页号同步」
 *   方案在本项目里注定对不齐的根本原因。
 */
export function anchorAt(layout, pos, viewSize = 0) {
  const { boxes, axis, pad = 0 } = layout;
  if (!boxes.length) return { page: 0, offset: 0, progress: 0 };

  const key = axis;
  const lenOf = (b) => b[key === 'y' ? 'h' : 'w'];

  // 找到包含 pos 的页；pos 落在页间空隙时取前一页末端
  let box = boxes[0];
  for (const b of boxes) {
    if (b[key] <= pos + 0.5) box = b;
    else break;
  }
  const len = Math.max(1, lenOf(box));

  // ★ 端点宽容：滚动位置允许落在「首页上方的留白 / 末页下方的留白」里。
  //   若把 offset 夹死在 [0,1]，pos→anchor→pos 往返会在首尾丢掉一个 pad
  //   （实测 18px），表现为同步在文档开头/结尾有恒定偏差。
  //   故首末页各放宽 pad/len，其余页仍严格夹在 [0,1]。
  const slack = pad / len;
  const lo = box.i === 0 ? -slack : 0;
  const hi = box.i === boxes.length - 1 ? 1 + slack : 1;
  const local = clamp((pos - box[key]) / len, lo, hi);

  // 可滚动总长（世界尺寸 - 视口尺寸），用于 progress
  const total = axis === 'y' ? layout.height : layout.width;
  const scrollable = Math.max(1, total - viewSize);
  const progress = clamp(pos / scrollable, 0, 1);

  // page 恒在 [0, 页数-1]：端点那点超出量由 offset 承载（offset 才是权威的定位量）。
  // 这样 page 既能安全地当页索引用（state / 状态栏 / 页序映射），
  // 又不会破坏首尾的往返精度。
  return {
    page: clamp(box.i + local, 0, boxes.length - 1),
    offset: local,
    progress,
  };
}

/** 锚点 → 主滚动轴坐标（逆函数）。 */
export function posOfAnchor(layout, anchor) {
  const { boxes, axis, pad = 0 } = layout;
  if (!boxes.length) return 0;
  const key = axis;
  const lenOf = (b) => b[key === 'y' ? 'h' : 'w'];

  const float_ = clamp(anchor.page ?? 0, 0, boxes.length - 1);
  const i = Math.min(boxes.length - 1, Math.floor(float_));
  // page 的整数部分已是页序，小数为页内比例；offset 优先级更高（更精确）
  const raw = anchor.offset !== undefined && anchor.offset !== null
    ? anchor.offset
    : (float_ - i);
  const box = boxes[i];
  // 与 anchorAt 的端点宽容保持一致（见那里的说明），否则首尾一往返就丢 pad
  const len = Math.max(1, lenOf(box));
  const slack = pad / len;
  const lo = i === 0 ? -slack : 0;
  const hi = i === boxes.length - 1 ? 1 + slack : 1;
  const local = clamp(raw, lo, hi);
  return box[key] + local * len;
}

/**
 * ★ 跨文档页序映射：把 A 文档的浮点页序换算到 B 文档。
 *
 * 两侧页数不同（22 vs 10）时按比例映射，并保证：
 *   · 首页→首页、末页→末页（不会因为取整把末尾对齐跑偏）
 *   · 单调（进度只增不减，不会来回跳）
 *
 * @param {number} pageFloat A 的浮点页序
 * @param {number} fromCount A 页数
 * @param {number} toCount   B 页数
 */
export function mapPageIndex(pageFloat, fromCount, toCount) {
  if (fromCount <= 1 || toCount <= 1 || fromCount === toCount) {
    return clamp(pageFloat, 0, toCount - 1);
  }
  const ratio = clamp(pageFloat / (fromCount - 1), 0, 1);
  return clamp(ratio * (toCount - 1), 0, toCount - 1);
}
