"use strict";

const $ = id => document.getElementById(id);
const state = { files: [], taskId: null, timer: null, logLines: [],
                frameLong: null, frameShort: null, dimsKey: '', scanSeq: 0,
                budgetPix: null, budgetAt: 0 };
const API = location.origin;

// ── Drop zone ──
const dz = $('dropZone'), fi = $('fileInput');
dz.addEventListener('click', () => fi.click());
['dragover','dragenter'].forEach(ev => dz.addEventListener(ev, e => { e.preventDefault(); dz.classList.add('drag'); }));
['dragleave','drop'].forEach(ev => dz.addEventListener(ev, () => dz.classList.remove('drag')));
dz.addEventListener('drop', e => {
  e.preventDefault();
  addFiles(Array.from(e.dataTransfer.files).filter(f => f.type.startsWith('image/')));
});
fi.addEventListener('change', e => addFiles(Array.from(e.target.files)));

$('clearBtn').addEventListener('click', () => {
  state.files = []; render(); hide(['listSection','setSection','resSection','errSection','progSection']);
});

// >>> REC_BEGIN
// ══ 输出图幅「推荐」——纯函数区（不碰 DOM，可单独跑）═══════════════════
// 思路：显微拍照是"载物台平移"取图，全景 ≈ 把 n 张帧按栅格拼起来。
//   ① 栅格边长 k = ceil(sqrt(n))      —— 用平方数定档
//   ② 最长边 ≈ 并排的帧数 × 帧最长边
//   ③ 档位 = 不小于该估算值的最小档位（档位是"上限"，估大不损失质量）
//   ④ 再用内存预算收口 —— 否则会选出一个注定被内存兜底的档位
//
// 为什么不是简单 k×0.8：那个式子对 n=2 会**低估**（2 张重叠帧的真实并排长度
// 约 1.8 帧，而 2×0.8=1.6 帧）。改成结构式 `(k-1)·增益 + 1`，它就自动退化成
// "首帧 + 后面每帧推进一点"，n=2 也对得上。增益取 0.8（=只假设 20% 重叠，
// 偏保守）。三个实测点（帧 2752×2208）：
//   n=2  → 真值 4914 (1.79 帧) | 估算 (2-1)·0.8+1 = 1.8 帧 ✓
//   n=4  → 真值 4075 (1.85 帧) | 估算 1.8 帧                   ✓
//   n=16 → 真值 8265 (3.00 帧) | 估算 (4-1)·0.8+1 = 3.4 帧 ✓（偏大，安全方向）
// 全都 ≥ 真值 → 推荐档位不会小于真实画布 → 不会主动降采样。
const GRID_STEP_GAIN = 0.8;
const REC_EST = 'estimate';   // 估算已超最大档位
const REC_MEM = 'memory';     // 被内存预算收口

/** 只读文件头拿尺寸，不解码像素（JPEG SOF / PNG IHDR）。失败返回 null。 */
function probeImageSize(b) {
  if (!b || b.length < 26) return null;
  if (b[0] === 0x89 && b[1] === 0x50 && b[2] === 0x4E && b[3] === 0x47) {
    const v = new DataView(b.buffer, b.byteOffset, b.byteLength);
    return { w: v.getUint32(16), h: v.getUint32(20) };
  }
  if (b[0] !== 0xFF || b[1] !== 0xD8) return null;       // 非 JPEG
  let i = 2;
  while (i + 4 <= b.length) {
    if (b[i] !== 0xFF) { i++; continue; }                // 跳过填充字节
    const m = b[i + 1];
    if (m === 0xFF || m === 0x01 || (m >= 0xD0 && m <= 0xD8)) { i += 2; continue; }
    if (m === 0xDA || m === 0xD9) break;                 // 进入扫描数据，后面没有 SOF
    const len = (b[i + 2] << 8) | b[i + 3];
    if (len < 2) break;
    // SOF0~SOF15，但 C4=DHT、C8=JPG、CC=DAC 不是
    if (m >= 0xC0 && m <= 0xCF && m !== 0xC4 && m !== 0xC8 && m !== 0xCC) {
      if (i + 9 > b.length) break;
      return { h: (b[i + 5] << 8) | b[i + 6], w: (b[i + 7] << 8) | b[i + 8] };
    }
    i += 2 + len;
  }
  return null;
}

/**
 * @param n         图片张数
 * @param frameLong 单帧最长边（px，**原图**尺寸：档位就是按原图名义像素定义的）
 * @param tiers     可选档位（任意顺序）
 * @param budgetPix 内存可承受的画布像素数（null/0 = 未知，不做内存收口）
 * @param frameShort 单帧最短边（用于估算实际像素数；缺省按正方形，偏保守）
 */
function recommendOutSide(n, frameLong, tiers, budgetPix, frameShort) {
  if (!(n >= 2) || !(frameLong > 0) || !tiers || !tiers.length) return null;
  const sorted = tiers.filter(t => t > 0).sort((a, b) => a - b);
  if (!sorted.length) return null;

  const grid = Math.ceil(Math.sqrt(n));                  // ② 平方数定栅格
  const spanFrames = (grid - 1) * GRID_STEP_GAIN + 1;     // ③ 结构式并排帧数
  const est = spanFrames * frameLong;

  let need = sorted.find(t => t >= est);                 // ④ 向上取最小档位
  const reasons = [];
  if (need === undefined) { need = sorted[sorted.length - 1]; reasons.push(REC_EST); }

  let tier = need;
  const aspect = frameShort > 0 ? Math.min(1, frameShort / frameLong) : 1;
  if (budgetPix > 0) {
    const fits = sorted.filter(t => t * t * aspect <= budgetPix);
    const best = fits.length ? fits[fits.length - 1] : null;
    if (best === null || best < tier) {
      tier = best === null ? sorted[0] : best;
      reasons.push(REC_MEM);
    }
  }
  return { tier, need, est: Math.round(est), grid, spanFrames, aspect, reasons,
           budgetPix: budgetPix > 0 ? budgetPix : null };
}

/** 推荐依据的一句话说明（纯字符串，便于单测）。返回不含按钮的文本。 */
function recExplain(rec, n, frameLong, frameShort) {
  if (!(n >= 2)) return '选够 2 张图后给出推荐尺寸。';
  if (!rec) return '读不到图片尺寸，本机也无法解码，请手动选档位（或选「自动」）。';
  const dims = frameShort > 0 ? `${frameLong}×${frameShort}` : `${frameLong}`;
  const bits = [`${n} 张 → ${rec.grid}×${rec.grid} 栅格`,
                `帧 ${dims}`,
                `估算 ${rec.est} px（≈${rec.spanFrames.toFixed(2)} 帧并排）`];
  if (rec.reasons.indexOf(REC_EST) >= 0) {
    bits.push(`估算超出最大档位，建议直接用「自动」`);
  } else if (rec.reasons.indexOf(REC_MEM) >= 0) {
    const mp = (rec.budgetPix / 1e6).toFixed(0);
    bits.push(`按内存收口（估算需 ${rec.need}，当前预算约 ${mp} MP）`);
  } else if (rec.budgetPix) {
    bits.push('在内存预算内');
  }
  return bits.join('；');
}
// <<< REC_END

// ── Params ──
// 羽化默认 0（不羽化，保留真实纹理）；0 时明确显示"不羽化"，避免误以为已启用。
const featherLabel = v => (String(v) === '0' ? '0（不羽化）' : String(v));
$('feather').addEventListener('input', e => $('featherV').textContent = featherLabel(e.target.value));
$('maxSide').addEventListener('change', e => {
  const v = e.target.value;
  $('maxV').textContent = v == '0' ? '原尺寸' : v + 'px';
});
// 输出图幅：档位是"上限"。三档特殊值：
//   -1 自定义…（露出数字框，后端夹紧到 1024~65536）
//   -2 推荐（按张数与帧宽自动；提交时由 outSideValue() 解析成真实档位）
// 后端 API 只认整数 px，所以"推荐"在前端就解析掉，服务端不用改。
const OUT_AUTO = 0, OUT_CUSTOM = -1, OUT_RECO = -2;
function domTiers() {
  // Array.from 而不是展开运算符：HTMLOptionsCollection 在不同浏览器上
  // 的可迭代性不一致，Array.from 对"类数组"同样有效。
  return Array.from($('outSide').options)
             .map(o => parseInt(o.value, 10)).filter(v => v > 0);
}
function outSideValue() {
  const raw = $('outSide').value;
  if (raw === String(OUT_RECO)) {
    const rec = recommendOutSide(state.files.length, state.frameLong,
                                 domTiers(), state.budgetPix, state.frameShort);
    return rec ? rec.tier : OUT_AUTO;   // 张数不足/读不到尺寸 → 退回自动
  }
  if (raw === String(OUT_CUSTOM)) {
    const n = parseInt($('outSideCustom').value, 10);
    if (!n || n <= 0) return OUT_AUTO;            // 空值 → 退回自动，不报错
    return Math.max(1024, Math.min(65536, n));
  }
  return parseInt(raw, 10) || OUT_AUTO;
}
function syncOutSide() {
  const raw = $('outSide').value;
  const custom = raw === String(OUT_CUSTOM);
  const reco = raw === String(OUT_RECO);
  $('outSideCustom').style.display = custom ? 'block' : 'none';
  const v = outSideValue();
  if (v === OUT_AUTO) {
    $('outV').textContent = custom ? '自定义（待填）' : (reco ? '推荐（待定）' : '自动');
  } else {
    $('outV').textContent = (reco ? '推荐 ' : custom ? '自定义 ' : '') + v + 'px';
  }
}

// 推荐那一行的渲染（含"用推荐值"按钮）
function refreshRec() {
  const el = $('outRec'); if (!el) return;
  const rec = recommendOutSide(state.files.length, state.frameLong,
                               domTiers(), state.budgetPix, state.frameShort);
  const txt = recExplain(rec, state.files.length, state.frameLong, state.frameShort);
  // 若另开了「匹配缩放」，特征是在缩小图上算的 —— 输出再大也只是插值，
  // 得把"真实信息上限"写在明面上（实测在 µm/px 标定上最容易踩）。
  let extra = '';
  const ms = parseInt($('maxSide').value, 10) || 0;
  if (rec && ms > 0 && state.frameLong > 0) {
    const s0 = Math.min(1, ms / state.frameLong);
    if (s0 < 0.999) {
      extra = `；匹配缩放 ×${s0.toFixed(3)}，真实信息上限约 ${Math.round(rec.est * s0)} px，`
            + `再大只是插值`;
    }
  }
  el.innerHTML = rec
    ? `<b>推荐 ${rec.tier} px</b> —— ${txt}${extra}`
      + `<button type="button" class="mini rec-apply">用推荐值</button>`
    : txt;
  const btn = el.querySelector('button.rec-apply');
  if (btn) btn.onclick = () => {
    $('outSide').value = String(rec.tier);   // 填成明确档位（想让它跟着张数自动走就选「推荐」）
    syncOutSide();
  };
  syncOutSide();     // 「推荐」模式下 outV 的档位也要跟着刷新
}

// 帧尺寸：只读文件头（每张只取前 64KB），不解码 2752×2208 的像素
async function probeFile(file) {
  try {
    const head = new Uint8Array(await file.slice(0, 65536).arrayBuffer());
    const d = probeImageSize(head);
    if (d && d.w > 0 && d.h > 0) return d;
  } catch (e) { /* 落到下面的解码兜底 */ }
  try {
    const bm = await createImageBitmap(file);
    const d = { w: bm.width, h: bm.height };
    if (bm.close) bm.close();
    return d;
  } catch (e) { return null; }
}
async function scanFrameDims() {
  const key = state.files.map(f => f.name + ':' + f.size).join('|');
  if (key && key === state.dimsKey) return;
  state.dimsKey = key;
  const seq = ++state.scanSeq;
  let dims = null;
  for (const f of state.files) {
    const d = await probeFile(f);
    if (!d) continue;
    if (!dims || Math.max(d.w, d.h) > Math.max(dims.w, dims.h)) dims = d;
  }
  if (seq !== state.scanSeq) return;      // 期间文件又变过，这次结果作废
  state.frameLong = dims ? Math.max(dims.w, dims.h) : null;
  state.frameShort = dims ? Math.min(dims.w, dims.h) : null;
  refreshRec();
}

// 内存预算：从 health 拿后端算出来的画布像素上限（"推荐"要用它收口）
async function refreshBudget() {
  try {
    const r = await fetch(API + '/api/stitch/health');
    const d = await r.json();
    const mp = Number(d.canvas_budget_mp || 0);
    state.budgetPix = mp > 0 ? mp * 1e6 : null;
  } catch (e) { state.budgetPix = null; }
  state.budgetAt = Date.now();
  refreshRec();
}

$('outSide').addEventListener('change', syncOutSide);
$('outSideCustom').addEventListener('input', syncOutSide);
syncOutSide();
refreshBudget();

// ── Run ──
$('runBtn').addEventListener('click', run);

function addFiles(newFiles) {
  state.files.push(...newFiles);
  render();
  scanFrameDims();                       // 读帧尺寸 → 更新推荐
  if (Date.now() - state.budgetAt > 20000) refreshBudget();   // 内存预算会变，隔一会儿再问
  if (state.files.length >= 2) show(['listSection','setSection']);
}

function render() {
  $('countBadge').textContent = state.files.length;
  const grid = $('thumbs'); grid.innerHTML = '';
  state.files.forEach((f, i) => {
    const url = URL.createObjectURL(f);
    const d = document.createElement('div');
    d.className = 'thumb';
    d.innerHTML = `<img src="${url}"><span class="idx">${i+1}</span>
      <button class="rm" title="移除">✕</button>`;
    d.querySelector('.rm').onclick = e => {
      e.stopPropagation(); state.files.splice(i, 1); render();
      if (state.files.length < 2) hide(['setSection']);
    };
    grid.appendChild(d);
  });
  $('runHint').textContent = state.files.length < 2
    ? '需至少 2 张'
    : `预计耗时 ~${Math.max(3, state.files.length*2)}s`;
  refreshRec();
}

function show(ids) { ids.forEach(id => $(id).classList.remove('hidden')); }
function hide(ids) { ids.forEach(id => $(id).classList.add('hidden')); }

async function run() {
  show(['progSection']); hide(['resSection','errSection']);
  setStatus('running'); $('spinner').style.display = '';
  $('progTitle').textContent = '上传中…'; $('progPct').textContent = '';
  $('barFill').style.width = '5%'; $('logBox').textContent = '';
  state.logLines = [];

  // 选了「推荐」就必须先有帧尺寸/内存预算，否则会静默退回自动 ——
  // 用户刚拖完图就点开始，扫描可能还没跑完，这里补一次（都是毫秒级 + 一次本地请求）。
  if ($('outSide').value === String(OUT_RECO)) {
    if (state.frameLong == null) await scanFrameDims();
    if (state.budgetPix == null) await refreshBudget();
  }

  const fd = new FormData();
  state.files.forEach(f => fd.append('files', f));
  fd.append('feather', $('feather').value);
  fd.append('max_side', $('maxSide').value);
  fd.append('out_side', String(outSideValue()));
  fd.append('engine', $('engine').value);
  fd.append('detect_scale_bar', $('tScale').checked ? '1' : '0');
  fd.append('inpaint', $('tInpaint').checked ? '1' : '0');

  const async = $('tAsync').checked;
  const endpoint = async ? '/api/stitch/submit' : '/api/stitch/run';

  try {
    const resp = await fetch(API + endpoint, { method: 'POST', body: fd });
    const data = await resp.json();
    if (!resp.ok || !data.ok) throw new Error(data.error || '失败');

    if (async) {
      state.taskId = data.task_id;
      $('progTitle').textContent = '后台处理中…';
      state.timer = setInterval(poll, 1500);
    } else {
      showResult(data.result, data.meta);
    }
  } catch (err) {
    showError(err.message);
  }
}

async function poll() {
  try {
    const resp = await fetch(API + '/api/stitch/task/' + state.taskId);
    const data = await resp.json();
    if (data.progress) {
      state.logLines = data.progress.slice();
      $('logBox').textContent = data.progress.join('\n');
    }
    $('logBox').scrollTop = $('logBox').scrollHeight;

    // 实时百分比
    const pct = data.percent || 0;
    $('barFill').style.width = pct + '%';
    $('progPct').textContent = pct + '%';
    // 与文献翻译共用一条串行队列：还没轮到自己时明确说清「在排队」，
    // 否则用户会以为卡住了（进度条不动、日志只有一行）。
    if (data.queued) {
      const pos = data.queue_position;
      $('progTitle').textContent = pos ? ('排队中（第 ' + pos + ' 位）…') : '排队中…';
    } else if (pct >= 85) $('progTitle').textContent = '即将完成…';
    else if (pct >= 60) $('progTitle').textContent = '羽化融合中…';
    else if (pct >= 30) $('progTitle').textContent = '匹配优化中…';
    else if (pct >= 10) $('progTitle').textContent = '处理图像中…';
    else $('progTitle').textContent = '上传完成，准备中…';

    if (data.status === 'done') {
      clearInterval(state.timer);
      state.timer = null;
      $('barFill').style.width = '100%'; $('progPct').textContent = '100%';
      showResult(data.result, data.result.meta);
    } else if (data.status === 'error') {
      clearInterval(state.timer);
      state.timer = null;
      showError(data.error);
    }
  } catch {}
}

const ENGINE_LABEL = {
  fallback_opencv: '内置引擎',
  openstitching: 'OpenStitching',
};

// 剔除原因的中文短名（后端 reason 形如 "位移过小 (412px < 520px)，疑似误匹配"）
// 键必须与 geometry.py 中 reject() 的实参前缀完全一致
const REASON_LABEL = {
  '特征点不足': '特征点太少',
  '匹配失败': '特征匹配失败',
  '粗匹配不足': '粗匹配太少',
  '相似变换拟合失败': '几何拟合失败',
  '内点过少': '内点太少',
  '尺度异常': '尺度不一致',
  '位移过小': '重叠区太小',
  '内点聚集': '重叠区过窄',
  '内点率低': '匹配不可靠',
};

function reasonLabel(raw) {
  const key = String(raw || '未知').split('(')[0].trim();
  return REASON_LABEL[key] || key || '未知';
}

function showResult(r, meta) {
  hide(['progSection']); show(['resSection']);
  setStatus('ok'); $('spinner').style.display = 'none';
  $('resImg').src = API + r.url;
  $('resInfo').textContent = `${r.width}×${r.height} · ${r.size_kb} KB`;
  $('dlLink').href = API + r.url.replace('/result/', '/result/').replace(/$/, '/dl');
  $('dlLink').setAttribute('download', r.filename);
  $('barFill').style.width = '100%';

  // 后端把 meta 放在 result 与响应顶层两处，取先有的那个
  const m = meta || r.meta || null;
  renderDiag(m);

  // 进度条阶段的日志在此时不再刷新，把完整日志留一份在详情里
  $('logBox').textContent = (state.logLines || []).join('\n') || $('logBox').textContent;
  renderDetail(m);
}

function renderDiag(m) {
  const box = $('resDiag');
  if (!m) { box.classList.add('hidden'); return; }
  const parts = [];

  const eng = ENGINE_LABEL[m.method] || m.method || '—';
  parts.push(`<span class="chip">引擎 ${esc(eng)}</span>`);

  // 图幅档位与整体缩放（指定档位时才有意义；自动模式通常为 1.000）
  const cap = Number(m.output_side_cap || 0);
  const osc = m.output_scale != null ? Number(m.output_scale) : 1;
  if (cap > 0) parts.push(`<span class="chip">图幅 ≤ ${cap}px</span>`);
  if (osc < 0.999) parts.push(`<span class="chip">缩放 ×${osc.toFixed(3)}</span>`);

  // 采用张数在 coverage_stats.used_images 下
  const used = (m.coverage_stats && m.coverage_stats.used_images) ?? m.used_images;
  const total = m.n_images;
  if (used != null && total != null) {
    const dropped = total - used;
    parts.push(`<span class="chip${dropped > 0 ? ' warn' : ''}">`
      + `采用 ${used}/${total} 张${dropped > 0 ? ` · 排除 ${dropped}` : ''}</span>`);
  }

  const mm = summarizeMatches(m.matches);
  if (mm.total > 0) {
    parts.push(`<span class="chip${mm.accepted === 0 ? ' warn' : ''}">`
      + `匹配 ${mm.accepted} 接受 / ${mm.rejected} 剔除</span>`);
  }

  const g = m.groups;
  if (Array.isArray(g) && g.length > 1) {
    parts.push(`<span class="chip warn">检出不连通 ${g.length} 组</span>`);
  }
  if (m.engine_meta && m.engine_meta.focal) {
    parts.push(`<span class="chip">${esc(String(m.engine_meta.focal))}</span>`);
  }

  let html = `<div class="chips">${parts.join('')}</div>`;
  const warns = Array.isArray(m.warnings) ? m.warnings.filter(Boolean) : [];
  if (warns.length) {
    html += '<ul class="warn-list">'
      + warns.map(w => `<li>${esc(w)}</li>`).join('') + '</ul>';
  }
  box.innerHTML = html;
  box.classList.remove('hidden');
}

// meta.matches 是逐图对的数组，这里归并成计数与原因分布
function summarizeMatches(matches) {
  const out = { total: 0, accepted: 0, rejected: 0, reasons: {} };
  if (!Array.isArray(matches)) return out;
  out.total = matches.length;
  matches.forEach(m => {
    if (m && m.accepted) { out.accepted++; return; }
    out.rejected++;
    const k = reasonLabel(m && m.reason);
    out.reasons[k] = (out.reasons[k] || 0) + 1;
  });
  return out;
}

function renderDetail(m) {
  const wrap = $('resDetail'), body = $('resDetailBody');
  if (!m) { wrap.classList.add('hidden'); return; }
  const rows = [];

  if (m.engine_meta && m.engine_meta.config) {
    const cfg = m.engine_meta.config;
    const keys = ['detector', 'nfeatures', 'matcher_type', 'estimator',
                  'warper_type', 'compensator', 'blender_type', 'blend_strength'];
    const shown = keys.filter(k => cfg[k] != null)
      .map(k => `${k}=${cfg[k]}`).join('  ·  ');
    if (shown) rows.push(`<div class="drow"><b>引擎配置</b><span>${esc(shown)}</span></div>`);
  }

  const mm = summarizeMatches(m.matches);
  if (Object.keys(mm.reasons).length) {
    const items = Object.entries(mm.reasons)
      .sort((a, b) => b[1] - a[1])
      .map(([k, v]) => `${esc(k)}: ${v}`);
    rows.push(`<div class="drow"><b>剔除原因</b><span>${items.join(' · ')}</span></div>`);
  }

  // 已接受的图对：列出真实的重叠位移与旋转，便于核对拼接依据
  if (Array.isArray(m.matches)) {
    const ok = m.matches.filter(e => e && e.accepted);
    if (ok.length) {
      const lines = ok.slice(0, 40).map(e =>
        `#${Number(e.i) + 1}↔#${Number(e.j) + 1}  位移 ${e.shift}px  `
        + `旋转 ${e.angle_deg}°  内点 ${e.inliers}  `
        + `重叠区 ${(Number(e.hull_ratio) * 100).toFixed(1)}%`);
      rows.push(`<div class="drow"><b>可靠匹配 (${ok.length})</b>`
        + `<span class="mono">${lines.join('<br>')}</span></div>`);
    }
  }

  if (Array.isArray(m.groups) && m.groups.length > 1) {
    const gs = m.groups.map((grp, i) =>
      `组${i + 1}(${grp.length}张): ${grp.join(',')}`).join('<br>');
    rows.push(`<div class="drow"><b>图像分组</b><span>${gs}</span></div>`);
  }

  if (m.coverage_stats && typeof m.coverage_stats === 'object') {
    const cs = Object.entries(m.coverage_stats)
      .map(([k, v]) => `${esc(k)}=${esc(String(v))}`).join('  ·  ');
    rows.push(`<div class="drow"><b>覆盖率</b><span>${cs}</span></div>`);
  }

  // 图幅：把「选了多大 / 实际出多大 / 缩了几倍」摊开，方便反算等效像素尺度
  if (m.output_size || m.output_side_cap || m.output_scale != null) {
    const osp = m.output_size ? `${m.output_size[0]}×${m.output_size[1]}` : '—';
    const cap = Number(m.output_side_cap || 0);
    const osc = m.output_scale != null ? Number(m.output_scale) : 1;
    const bits = [`实际 ${osp}`,
                  `档位 ${cap > 0 ? '≤' + cap + 'px' : '自动'}`,
                  `缩放 ×${osc.toFixed(4)}`];
    const nom = m.engine_meta && m.engine_meta.nominal_canvas;
    if (nom) bits.push(`名义画布 ${nom[0]}×${nom[1]}`);
    rows.push(`<div class="drow"><b>输出图幅</b><span>${esc(bits.join('  ·  '))}</span></div>`);
  }

  if (!rows.length) { wrap.classList.add('hidden'); return; }
  body.innerHTML = rows.join('');
  wrap.classList.remove('hidden');
  wrap.open = false;
}

function esc(s) {
  return String(s).replace(/[&<>"']/g,
    c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}

function showError(msg) {
  hide(['progSection']); show(['errSection']);
  setStatus('err'); $('spinner').style.display = 'none';
  $('errMsg').textContent = msg;
}

function setStatus(kind) {
  const dot = $('stDot'), txt = $('stText');
  dot.className = 'dot' + (kind === 'ok' ? ' ok' : kind === 'run' ? ' run' : kind === 'err' ? ' run' : '');
  txt.textContent = kind === 'ok' ? '拼合完成' : kind === 'run' ? '处理中…' : kind === 'err' ? '出错' : '准备就绪';
}
