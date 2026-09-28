"use strict";

/* ============ 基础配置（内部角度统一用「度」） ============ */
const D2R = Math.PI / 180;
const R2D = 180 / Math.PI;
const VBW = 640, VBH = 520;     // SVG viewBox
const LAB_OFF_ANG = 46;          // 角标签距顶点的偏移（vb 单位）
const LAB_OFF_SIDE = 32;         // 边标签距中点的偏移（vb 单位）
const PAD = 22;                  // clamp 到画布内边的余量
const FIELDS = ["A", "B", "C", "a", "b", "c"];
const SCHEMATIC = { a: 5, b: 6, c: 7 };

const sinD  = d => Math.sin(d * D2R);
const cosD  = d => Math.cos(d * D2R);
const tanD  = d => { const c = cosD(d); return Math.abs(c) < 1e-12 ? NaN : sinD(d) / c; };
const acosD = x => Math.acos(Math.max(-1, Math.min(1, x))) * R2D;
const asinD = x => Math.asin(Math.max(-1, Math.min(1, x))) * R2D;

/* ============ 全局状态 ============ */
let unit = "deg";                // "deg" | "rad"
let knownVals = {};              // 用户填写（角度为度）
let solved = null;               // 完整三角形 {A,B,C,a,b,c}（度）

const canvasEl = document.getElementById("canvas");
const svg      = document.getElementById("tri");
const panel    = document.getElementById("panel");
const statusEl = document.getElementById("status");

const labelEls = {};             // f -> { div, letter }
const fieldInputs = {};          // f -> [图上 input, 下方 input]
const trigCells = {};            // A/B/C -> { sin, cos, tan }
FIELDS.forEach(f => fieldInputs[f] = []);

/* ============ 数值格式化 / 解析 ============ */
const fmt = v => (v === undefined || v === null || isNaN(v)) ? ""
                : String(Math.round(v * 1e4) / 1e4);

function parseVal(str, isAngle) {
  if (str.trim() === "") return undefined;
  const n = parseFloat(str);
  if (isNaN(n)) return undefined;
  return isAngle && unit === "rad" ? n * R2D : n;
}
function displayVal(v, isAngle) {
  if (v === undefined || v === null || isNaN(v)) return "";
  const x = isAngle && unit === "rad" ? v * D2R : v;
  return fmt(x);
}

/* ============ 三角形求解（已知 3 项 → 求其余 3 项） ============ */
function solve(known) {
  const filled = FIELDS.filter(k => known[k] !== undefined && !isNaN(known[k]));
  const angles = filled.filter(k => "ABC".includes(k));
  const sides  = filled.filter(k => "abc".includes(k));
  if (filled.length !== 3)
    return { error: "请填写恰好 3 个已知量（角 A/B/C 或边 a/b/c）来求解唯一三角形。" };
  if (sides.length === 0)
    return { error: "只知道三个角（AAA）无法唯一确定三角形，只能确定形状。请至少输入一条边长。" };

  // SSS
  if (sides.length === 3) {
    const { a, b, c } = known;
    if (a <= 0 || b <= 0 || c <= 0) return { error: "边长必须为正数。" };
    if (!(a + b > c && a + c > b && b + c > a))
      return { error: "三边不满足三角形不等式，无法构成三角形。" };
    return {
      A: acosD((b*b + c*c - a*a) / (2*b*c)),
      B: acosD((a*a + c*c - b*b) / (2*a*c)),
      C: acosD((a*a + b*b - c*c) / (2*a*b)),
      a, b, c
    };
  }
  // 两边 + 一角
  if (sides.length === 2) {
    const ang = angles[0];
    const [s1, s2] = sides;
    const opp = ang.toLowerCase();
    const included = s1 !== opp && s2 !== opp;
    if (included) {  // SAS
      const inc = known[ang];
      if (inc <= 0 || inc >= 180) return { error: "夹角必须在 0° 与 180° 之间。" };
      const x = known[s1], y = known[s2];
      if (x <= 0 || y <= 0) return { error: "边长必须为正数。" };
      const third = opp;
      const thirdSide = Math.sqrt(x*x + y*y - 2*x*y*cosD(inc));
      const res = { A: known.A, B: known.B, C: known.C, a: known.a, b: known.b, c: known.c };
      res[third] = thirdSide;
      const others = ["a","b","c"].filter(s => s !== third);
      others.forEach(s => {
        const oa = others.find(t => t !== s);
        const cv = (thirdSide*thirdSide + known[oa]*known[oa] - known[s]*known[s])
                   / (2*thirdSide*known[oa]);
        res[s.toUpperCase()] = acosD(cv);
      });
      res[ang] = inc;
      return res;
    }
    // SSA（含歧义）
    const oppSide   = s1 === opp ? s1 : s2;
    const otherSide = s1 === opp ? s2 : s1;
    const A0 = known[ang];
    if (A0 <= 0 || A0 >= 180) return { error: "角度必须在 0° 与 180° 之间。" };
    if (known[oppSide] <= 0 || known[otherSide] <= 0) return { error: "边长必须为正数。" };
    const p = known[oppSide];
    const ratio = known[otherSide] * sinD(A0) / p;
    if (ratio > 1) return { error: "无解（SSA）：已知边过短，无法构成三角形。" };
    const beta1 = asinD(ratio), beta2 = 180 - beta1;
    const build = (beta) => {
      const g = 180 - A0 - beta;
      if (g <= 0) return null;
      const r = p * sinD(g) / sinD(A0);
      const res = { A: known.A, B: known.B, C: known.C, a: known.a, b: known.b, c: known.c };
      res[ang] = A0;
      res[otherSide.toUpperCase()] = beta;
      const ra = FIELDS.filter(k => "ABC".includes(k)).find(an => an !== ang && an !== otherSide.toUpperCase());
      const rs = ["a","b","c"].find(s => s !== oppSide && s !== otherSide);
      res[ra] = g;
      res[rs] = r;
      return res;
    };
    const valid = [build(beta1), build(beta2)].filter(Boolean);
    if (valid.length === 0) return { error: "无解：三组数无法构成三角形（SSA）。" };
    if (valid.length === 2)
      return { error: "歧义（SSA）：可画出两个不同三角形，无法唯一确定。请改用两边夹角 SAS 或补充条件。" };
    return valid[0];
  }
  // 一边 + 两角（ASA / AAS）
  const a3 = FIELDS.filter(k => "ABC".includes(k)).find(an => !angles.includes(an));
  const third = 180 - known[angles[0]] - known[angles[1]];
  if (third <= 0) return { error: "三角之和不超过 180° 才能构成三角形。" };
  const res = { A: known.A, B: known.B, C: known.C, a: known.a, b: known.b, c: known.c };
  res[a3] = third;
  const side = sides[0];
  const oppAng = side.toUpperCase();
  const R = res[side] / sinD(res[oppAng]);
  ["a","b","c"].forEach(s => { if (s !== side) res[s] = R * sinD(res[s.toUpperCase()]); });
  return res;
}

/* ============ 屏幕坐标（基于当前可绘制的边长） ============ */
function shapeSides() {
  if (solved && solved.a > 0 && solved.b > 0 && solved.c > 0) return solved;
  return SCHEMATIC;
}
function computeScreen(a, b, c) {
  // B=(0,0), C=(a,0); 由边长 a=BC, b=AC, c=AB 求 A
  const Bp = { x: 0, y: 0 };
  const Cp = { x: a, y: 0 };
  const xA = (a*a + c*c - b*b) / (2*a);
  const yA = Math.sqrt(Math.max(0, c*c - xA*xA));
  const pts = { A: { x: xA, y: yA }, B: Bp, C: Cp };
  const xs = Object.values(pts).map(p => p.x);
  const ys = Object.values(pts).map(p => p.y);
  const minx = Math.min(...xs), maxx = Math.max(...xs);
  const miny = Math.min(...ys), maxy = Math.max(...ys);
  const w = maxx - minx || 1, h = maxy - miny || 1;
  const margin = 110;
  const scale = Math.min((VBW - 2*margin) / w, (VBH - 2*margin) / h);
  const screen = {};
  for (const k in pts) {
    const p = pts[k];
    screen[k] = {
      x: (VBW - w*scale)/2 + (p.x - minx)*scale,
      y: (VBH - h*scale)/2 + (maxy - p.y)*scale
    };
  }
  return screen;
}

/* ============ 标签几何定位（vb 坐标系） ============ */
// 角标签：沿「外角平分线」方向（远离三角内部）放置
function angleLabelPos(A, B, C, off) {
  const v = (x, y) => { const L = Math.hypot(x, y) || 1; return { x: x/L, y: y/L }; };
  const u = v(B.x - A.x, B.y - A.y);
  const w = v(C.x - A.x, C.y - A.y);
  // 内角平分线 = u + w（指向三角形内部）；取反即外部
  let dx = -(u.x + w.x), dy = -(u.y + w.y);
  const L = Math.hypot(dx, dy) || 1;
  dx /= L; dy /= L;
  return { x: A.x + dx*off, y: A.y + dy*off, dx, dy };
}
// 边标签：沿边的法线方向，取远离三角形质心那一侧
function sideLabelPos(P, Q, ctr, off) {
  const tx = Q.x - P.x, ty = Q.y - P.y;
  let nx = -ty, ny = tx;
  const L = Math.hypot(nx, ny) || 1;
  nx /= L; ny /= L;
  const mx = (P.x + Q.x)/2, my = (P.y + Q.y)/2;
  // 远离质心：dot(n, mid - ctr) > 0
  if ((mx - ctr.x)*nx + (my - ctr.y)*ny < 0) { nx = -nx; ny = -ny; }
  return { x: mx + nx*off, y: my + ny*off, dx: nx, dy: ny };
}
function clamp(p, dx, dy) {
  // dx/dy 用于让标签沿其方向远离边/顶点，这里只做画布内边 clamp
  let x = p.x, y = p.y;
  if (x < PAD) x = PAD;
  if (x > VBW - PAD) x = VBW - PAD;
  if (y < PAD) y = PAD;
  if (y > VBH - PAD) y = VBH - PAD;
  return { x, y };
}

/* ============ 当前值与「计算求得」判定 ============ */
function currentValue(f) {
  if (f in knownVals) return knownVals[f];
  if (solved && solved[f] !== undefined && solved[f] !== null) return solved[f];
  return undefined;
}
function isComputed(f) {
  return !!solved && !(f in knownVals) && solved[f] !== undefined && solved[f] !== null;
}

/* ============ 绘图 ============ */
let lastScreen = null;

function draw() {
  const s = shapeSides();
  const S = computeScreen(s.a, s.b, s.c);
  lastScreen = S;
  const A = S.A, B = S.B, C = S.C;

  // SVG 内容
  let html = "";
  html += `<polygon class="tri-shape" points="${A.x},${A.y} ${B.x},${B.y} ${C.x},${C.y}"/>`;
  html += `<line class="tri-side" x1="${B.x}" y1="${B.y}" x2="${C.x}" y2="${C.y}"/>`;
  html += `<line class="tri-side" x1="${A.x}" y1="${A.y}" x2="${C.x}" y2="${C.y}"/>`;
  html += `<line class="tri-side" x1="${A.x}" y1="${A.y}" x2="${B.x}" y2="${B.y}"/>`;
  for (const k in S) {
    const p = S[k];
    html += `<circle class="tri-vertex" cx="${p.x}" cy="${p.y}" r="5"/>`;
  }
  // 角 A 的小弧线（示意，标识顶点位置）
  const arcR = 16;
  function arc(ang) {
    const u = { x: (ang.B.x - ang.A.x), y: (ang.B.y - ang.A.y) };
    const v = { x: (ang.C.x - ang.A.x), y: (ang.C.y - ang.A.y) };
    const ul = Math.hypot(u.x, u.y) || 1, vl = Math.hypot(v.x, v.y) || 1;
    u.x /= ul; u.y /= ul; v.x /= vl; v.y /= vl;
    return `M ${ang.A.x + u.x*arcR} ${ang.A.y + u.y*arcR} A ${arcR} ${arcR} 0 0 1 ${ang.A.x + v.x*arcR} ${ang.A.y + v.y*arcR}`;
  }
  html += `<path class="tri-angle-arc" d="${arc({A,B,C})}"/>`;
  svg.innerHTML = html;

  // 标签位置（vb 坐标）
  const ctr = { x: (A.x + B.x + C.x)/3, y: (A.y + B.y + C.y)/3 };
  const posA = clamp(angleLabelPos(A, B, C, LAB_OFF_ANG));
  const posB = clamp(angleLabelPos(B, A, C, LAB_OFF_ANG));
  const posC = clamp(angleLabelPos(C, A, B, LAB_OFF_ANG));
  const posa = clamp(sideLabelPos(B, C, ctr, LAB_OFF_SIDE));
  const posb = clamp(sideLabelPos(A, C, ctr, LAB_OFF_SIDE));
  const posc = clamp(sideLabelPos(A, B, ctr, LAB_OFF_SIDE));

  placeLabel("A", posA);
  placeLabel("B", posB);
  placeLabel("C", posC);
  placeLabel("a", posa);
  placeLabel("b", posb);
  placeLabel("c", posc);
}

// 把 div 放到计算点（vb 坐标）上，并按当前 canvas 实际像素做缩放
function placeLabel(f, p) {
  const div = labelEls[f].div;
  // vb -> canvas px
  const rect = canvasEl.getBoundingClientRect();
  const scaleX = rect.width  / VBW;
  const scaleY = rect.height / VBH;
  div.style.left = (p.x * scaleX) + "px";
  div.style.top  = (p.y * scaleY) + "px";
}

/* ============ 输入刷新（图上 + 下方 + 三角函数） ============ */
function updateInputs() {
  FIELDS.forEach(f => {
    const isAngle = "ABC".includes(f);
    const v = displayVal(currentValue(f), isAngle);
    const comp = isComputed(f);
    fieldInputs[f].forEach(inp => {
      if (inp !== document.activeElement) inp.value = v;
      inp.classList.toggle("computed", comp);
    });
  });
  // 三角函数（仅在有解时显示）
  ["A","B","C"].forEach(f => {
    const ang = solved ? solved[f] : undefined;
    const cells = trigCells[f];
    if (!cells) return;
    if (ang === undefined || ang === null || isNaN(ang)) {
      cells.sin.textContent = "—";
      cells.cos.textContent = "—";
      cells.tan.textContent = "—";
      cells.sin.classList.remove("computed");
      cells.cos.classList.remove("computed");
      cells.tan.classList.remove("computed");
      return;
    }
    const s = sinD(ang), c = cosD(ang), t = tanD(ang);
    const comp = isComputed(f);
    cells.sin.innerHTML = `sin=${fmt(s)}`;
    cells.cos.innerHTML = `cos=${fmt(c)}`;
    cells.tan.innerHTML = isNaN(t) ? "tan=—" : `tan=${fmt(t)}`;
    [cells.sin, cells.cos, cells.tan].forEach(el => el.classList.toggle("computed", comp));
  });
}

function setStatus(msg, ok) {
  statusEl.textContent = msg || "";
  statusEl.className = "status" + (ok ? " ok" : "");
}

/* ============ 求解流程 ============ */
function attemptSolve() {
  const keys = Object.keys(knownVals);
  if (keys.length !== 3) { solved = null; return; }
  const res = solve(knownVals);
  if (res.error) { solved = null; setStatus(res.error, false); return; }
  solved = res;
  setStatus("已根据 3 个已知量求解出其余三角形参数。", true);
}
function recompute() {
  attemptSolve();
  updateInputs();
  draw();
}
function onInput(f, val) {
  const isAngle = "ABC".includes(f);
  if (val.trim() === "") {
    delete knownVals[f];
  } else {
    const parsed = parseVal(val, isAngle);
    if (parsed === undefined) return;
    knownVals[f] = parsed;
  }
  recompute();
}

/* ============ 构建图上标签 + 下方面板 ============ */
function buildLabels() {
  FIELDS.forEach(f => {
    const div = document.createElement("div");
    div.className = "lab " + ("ABC".includes(f) ? "angle" : "side");
    const letter = document.createElement("span");
    letter.className = "lab-letter";
    letter.textContent = f;
    const inp = document.createElement("input");
    inp.type = "text";
    inp.inputMode = "decimal";
    inp.setAttribute("aria-label", f);
    inp.id = "in_" + f;
    inp.addEventListener("input", () => onInput(f, inp.value));
    div.appendChild(letter);
    div.appendChild(inp);
    canvasEl.appendChild(div);
    labelEls[f] = { div, letter, input: inp };
    fieldInputs[f].push(inp);
  });

  // 下方两列面板
  const cols = [
    { t: "角度（度 / 弧度）", list: ["A","B","C"] },
    { t: "边长（单位 1）",    list: ["a","b","c"] }
  ];
  cols.forEach(c => {
    const col = document.createElement("div");
    col.className = "panel-col";
    const h = document.createElement("h3");
    h.textContent = c.t;
    col.appendChild(h);
    c.list.forEach(f => {
      const isAng = "ABC".includes(f);
      const row = document.createElement("div");
      row.className = "row " + (isAng ? "angle" : "side");
      const lab = document.createElement("label");
      lab.textContent = f;
      const inp = document.createElement("input");
      inp.type = "text";
      inp.inputMode = "decimal";
      inp.id = "low_" + f;
      inp.addEventListener("input", () => onInput(f, inp.value));
      row.appendChild(lab);
      row.appendChild(inp);
      if (isAng) {
        const trig = document.createElement("div");
        trig.className = "trig";
        const sinS = document.createElement("span"); sinS.textContent = "sin=—";
        const cosS = document.createElement("span"); cosS.textContent = "cos=—";
        const tanS = document.createElement("span"); tanS.textContent = "tan=—";
        trig.appendChild(sinS); trig.appendChild(cosS); trig.appendChild(tanS);
        row.appendChild(trig);
        trigCells[f] = { sin: sinS, cos: cosS, tan: tanS };
      }
      col.appendChild(row);
      fieldInputs[f].push(inp);
    });
    panel.appendChild(col);
  });
}

/* ============ 单位切换 / 按钮 ============ */
document.getElementById("unitDeg").addEventListener("click", () => {
  unit = "deg";
  document.getElementById("unitDeg").classList.add("active");
  document.getElementById("unitRad").classList.remove("active");
  updateInputs();
});
document.getElementById("unitRad").addEventListener("click", () => {
  unit = "rad";
  document.getElementById("unitRad").classList.add("active");
  document.getElementById("unitDeg").classList.remove("active");
  updateInputs();
});
document.getElementById("btnCalc").addEventListener("click", () => {
  const keys = Object.keys(knownVals);
  if (keys.length !== 3) {
    setStatus(keys.length < 3
      ? "请先输入 3 个已知量再计算。"
      : "已知量超过 3 个，请只保留 3 个（其余清空）以唯一确定三角形。", false);
    return;
  }
  attemptSolve();
  updateInputs();
  draw();
});
document.getElementById("btnClear").addEventListener("click", () => {
  knownVals = {};
  solved = null;
  setStatus("", false);
  updateInputs();
  draw();
});

/* ============ 响应式：窗口尺寸变化时重排标签 ============ */
let resizeRaf = 0;
function onResize() {
  if (resizeRaf) return;
  resizeRaf = requestAnimationFrame(() => {
    resizeRaf = 0;
    draw();   // draw 会读取 canvas 实际像素并缩放 left/top
  });
}
window.addEventListener("resize", onResize);

/* ============ 启动 ============ */
buildLabels();
setStatus("默认三角形为示意（无数值）。在图上或下方输入 3 个已知量即可自动求解。", true);
updateInputs();
draw();
// 移动端首次进入时再排一次（iOS 视口变化后）
setTimeout(draw, 60);