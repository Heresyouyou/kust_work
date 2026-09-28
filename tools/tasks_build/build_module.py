# -*- coding: utf-8 -*-
"""由复刻产物生成 kust.work「任务打卡」模块的静态资源。

产出：
  modules/tasks/assets/tasks.css      （绿色主题变量化样式 + 角色条 / 角色引导弹窗）
  modules/tasks/assets/app.js         （看板脚本：按角色隔离存储、新角色为空、暴露重载入口）
  modules/tasks/assets/roles.js       （角色运行时：角色解析 + 联网同步，源文件在 tools/tasks_build/）
  modules/tasks/templates/tasks.html  （门户外壳 + 角色条 + 看板主体 + 角色弹窗）

约定：本文件 / build_css.py / roles.js 是「上游看板改版后重新生成」的三件套；
      modules/tasks/assets/ 下的一切都是生成物，不要手改。
"""
import re
from pathlib import Path

BASE = Path(r'D:\PostNews\Translate')
HERE = Path(__file__).parent
MOD = BASE / 'modules' / 'tasks'
EX = HERE / 'extract'

# ─────────────────────────── 1. CSS ───────────────────────────
css = (HERE / 'out_css.css').read_text(encoding='utf-8')

ROLEB_CSS = '''

/* ===== 角色条（多角色工作台切换 · 联网共享） ===== */
.rolebar {
  position: sticky; top: 0; z-index: 60;
  background: var(--np-card);
  border-bottom: 1px solid var(--np-brand-line1);
  box-shadow: 0 2px 10px var(--np-sh-2);
}
.rolebar .rb-in {
  max-width: 1400px; margin: 0 auto; padding: 8px 16px;
  display: flex; align-items: center; gap: 10px;
}
.rolebar .rb-label {
  font-size: 12px; color: var(--np-ink-4); flex-shrink: 0; font-weight: 600; letter-spacing: .5px;
}
.rolebar .rb-list {
  display: flex; align-items: center; gap: 8px; flex: 1; min-width: 0;
  overflow-x: auto; scrollbar-width: thin; padding: 2px 0;
}
.rolebar .rb-list::-webkit-scrollbar { height: 4px; }
.rolebar .rb-list::-webkit-scrollbar-thumb { background: var(--np-brand-line1); border-radius: 2px; }
.rolechip {
  display: inline-flex; align-items: center; gap: 6px; flex-shrink: 0;
  min-height: 36px; padding: 0 14px; border-radius: 18px; cursor: pointer;
  border: 1px solid var(--np-brand-line1); background: var(--np-card-alt3);
  color: var(--np-ink-2); font: inherit; font-size: 13px; font-weight: 600;
  transition: background .16s, color .16s, border-color .16s, transform .16s;
}
.rolechip:hover { border-color: var(--np-brand-chip); transform: translateY(-1px); }
.rolechip .rc-av { font-size: 15px; line-height: 1; }
/* 示例角色：虚线边框弱化处理 —— 它是个参考物，不是「我的角色」 */
.rolechip.demo { border-style: dashed; color: var(--np-ink-3); background: transparent; }
.rolechip.on {
  background: var(--np-grad-brand); color: var(--np-on-brand);
  border-color: transparent; box-shadow: 0 4px 12px var(--np-br-35);
}
.rolechip.add { border-style: dashed; color: var(--np-brand); background: transparent; }
.rolebar .rb-mng {
  flex-shrink: 0; min-height: 36px; padding: 0 12px; border-radius: 18px; cursor: pointer;
  border: 1px solid var(--np-brand-line1); background: var(--np-card-alt3);
  color: var(--np-ink-3); font: inherit; font-size: 12.5px; font-weight: 600;
  transition: color .16s, border-color .16s;
}
.rolebar .rb-mng:hover { color: var(--np-brand); border-color: var(--np-brand-chip); }

/* 同步状态指示器（已同步 / 待同步 / 同步中 / 离线模式） */
.sync-pill {
  flex-shrink: 0; font-size: 11.5px; padding: 3px 10px; border-radius: 999px;
  border: 1px solid var(--np-brand-line1); background: var(--np-card-alt3);
  color: var(--np-ink-4); white-space: nowrap; font-weight: 600;
}
.sync-pill.ok { color: var(--np-brand); border-color: var(--np-brand-chip); background: var(--np-brand-soft0); }
.sync-pill.syncing { color: var(--np-gold-3); border-color: var(--np-gold-line); background: var(--np-gold-bg); }
.sync-pill.pending { color: var(--np-gold-4); border-color: var(--np-gold-line); background: var(--np-gold-bg2); }
.sync-pill.offline { color: var(--np-rose-2); border-color: var(--np-rose-line); background: var(--np-rose-bg2); }

/* 角色管理 / 角色引导弹窗 */
.rmask {
  position: fixed; inset: 0; background: var(--np-scrim); backdrop-filter: blur(3px);
  z-index: 120; display: none; align-items: flex-start; justify-content: center;
  overflow-y: auto; padding: 40px 14px;
}
.rmask.show { display: flex; }
.rmask .rbox {
  background: var(--np-card); border-radius: 18px; width: 100%; max-width: 560px;
  padding: 22px; box-shadow: 0 20px 60px var(--np-sh-6); animation: rbPop .22s ease;
}
@keyframes rbPop { from { opacity: 0; transform: translateY(-10px) scale(.98); } to { opacity: 1; transform: none; } }
.rmask h3 { font-size: 17px; color: var(--np-ink); margin-bottom: 4px; }
.rmask .rsub { font-size: 12px; color: var(--np-ink-4); margin-bottom: 16px; line-height: 1.7; }
.rrow {
  display: flex; align-items: center; gap: 10px; padding: 10px; border-radius: 12px;
  border: 1px solid var(--np-brand-line1); background: var(--np-card-alt3); margin-bottom: 8px;
}
.rrow.on { border-color: var(--np-brand); background: var(--np-brand-soft1); }
.rrow .rav {
  width: 38px; height: 38px; flex-shrink: 0; border-radius: 10px; border: 0; cursor: pointer;
  background: var(--np-grad-brand); color: var(--np-on-brand); font-size: 19px; line-height: 1;
  display: flex; align-items: center; justify-content: center;
}
.rrow .rnm {
  flex: 1; min-width: 0; height: 38px; padding: 0 10px; border-radius: 9px;
  border: 1px solid var(--np-brand-line1); background: var(--np-card);
  color: var(--np-ink); font: inherit; font-size: 16px;
}
.rrow .rnm:focus { outline: none; border-color: var(--np-brand); }
.rrow .rflag {
  font-size: 11px; color: var(--np-brand); background: var(--np-brand-soft0);
  border: 1px solid var(--np-brand-line1); border-radius: 999px; padding: 2px 8px; flex-shrink: 0;
}
.rrow .rbtn {
  flex-shrink: 0; min-height: 34px; padding: 0 12px; border-radius: 9px; cursor: pointer;
  border: 1px solid var(--np-brand-line1); background: var(--np-card); color: var(--np-ink-3);
  font: inherit; font-size: 12.5px; font-weight: 600;
}
.rrow .rbtn:hover { border-color: var(--np-brand-chip); color: var(--np-brand); }
.rrow .rbtn.del:hover { border-color: var(--np-rose); color: var(--np-rose-2); }
.rmask .rfoot { display: flex; gap: 10px; justify-content: flex-end; align-items: center; margin-top: 16px; flex-wrap: wrap; }
.rmask .rfoot .btn { min-height: 40px; padding: 0 18px; }
.rmask .rfoot-extra { display: flex; gap: 10px; margin-right: auto; flex-wrap: wrap; }
.rwarn {
  font-size: 12px; color: var(--np-gold-4); background: var(--np-gold-bg);
  border: 1px solid var(--np-gold-line); border-radius: 10px; padding: 9px 12px;
  line-height: 1.7; margin-bottom: 14px;
}

/* 角色引导（cookie 为空 / 新建角色 共用）：命名 + 选头像 + 进入已有角色 */
.gate label {
  display: block; font-size: 12px; color: var(--np-ink-4); margin-bottom: 6px;
  font-weight: 600; letter-spacing: .5px;
}
.gate input#gateName {
  width: 100%; height: 46px; padding: 0 14px; border-radius: 12px;
  border: 1px solid var(--np-brand-line1); background: var(--np-card-alt3);
  color: var(--np-ink); font: inherit; font-size: 16px; box-sizing: border-box;
}
.gate input#gateName::placeholder { color: var(--np-ink-5); }
.gate input#gateName:focus { outline: none; border-color: var(--np-brand); box-shadow: 0 0 0 3px var(--np-br-12); }
.gate-avs { display: flex; flex-wrap: wrap; gap: 6px; margin-top: 12px; }
.gate-av {
  width: 42px; height: 42px; border-radius: 11px; cursor: pointer; font-size: 19px; line-height: 1;
  border: 1px solid var(--np-brand-line1); background: var(--np-card-alt3); color: var(--np-ink-2);
  display: flex; align-items: center; justify-content: center; padding: 0;
}
.gate-av.on { border-color: var(--np-brand); background: var(--np-brand-soft0); box-shadow: 0 0 0 2px var(--np-br-12); }
.gate-err { min-height: 18px; font-size: 12px; color: var(--np-rose-2); margin: 10px 0 4px; }
#gateCreate { min-height: 46px; padding: 0 22px; }
.gate-sep { font-size: 12px; color: var(--np-ink-4); margin: 20px 0 12px; text-align: center; position: relative; }
.gate-sep::before, .gate-sep::after {
  content: ''; position: absolute; top: 50%; width: calc(50% - 52px);
  height: 1px; background: var(--np-brand-line1);
}
.gate-sep::before { left: 0; }
.gate-sep::after { right: 0; }
.gate-row {
  width: 100%; display: flex; align-items: center; gap: 10px; padding: 11px 12px; margin-bottom: 8px;
  border-radius: 12px; border: 1px solid var(--np-brand-line1); background: var(--np-card-alt3);
  color: var(--np-ink); font: inherit; font-size: 13.5px; cursor: pointer; text-align: left;
}
.gate-row:hover { border-color: var(--np-brand); background: var(--np-brand-soft1); }
.gate-row .gr-av { font-size: 18px; line-height: 1; }
.gate-row .gr-nm { font-weight: 600; }
.gate-row .gr-meta { margin-left: auto; font-size: 12px; color: var(--np-ink-4); }
.gate-row .gr-go { font-size: 12px; color: var(--np-brand); font-weight: 600; flex-shrink: 0; }
/* 「示例」小标：告诉用户这条不是队友的角色，而是填好内容的参考样例 */
.gate-row .gr-tag {
  flex-shrink: 0; padding: 2px 7px; border-radius: 9px; font-size: 10.5px; font-weight: 700;
  letter-spacing: .5px; color: var(--np-brand); background: var(--np-brand-soft1);
  border: 1px solid var(--np-brand-line1);
}

@media (max-width: 768px) {
  .rolebar .rb-in { padding: 7px 12px; gap: 8px; }
  .rolebar .rb-label { display: none; }
  .rolechip { min-height: 44px; padding: 0 14px; font-size: 12.5px; border-radius: 22px; }
  .rolebar .rb-mng { min-height: 44px; border-radius: 22px; }
  .sync-pill { padding: 4px 8px; font-size: 11px; }
  .rmask { padding: 20px 10px; }
  .rrow { flex-wrap: wrap; }
  .rrow .rnm { flex-basis: 100%; order: 3; height: 44px; }
  .rrow .rbtn { min-height: 44px; }
  .rrow .rav { width: 44px; height: 44px; }
  .gate-av { width: 46px; height: 46px; }
}
'''

# 收尾样式：分类页签扩到 4 个之后的换行，以及窄屏下 main 被时间轴顶宽的修复
TABS_CSS = '''
/* ===== 任务轴分类页签（通用证书 / 专业赛事 / 课程学习 / 科研进度）===== */
.tabs { flex-wrap: wrap; }

/* ── 窄屏：别让时间轴把整页顶出横向滚动 ★ ─────────────────────────────────
   .app 在 ≤900px 变成 column 方向，而它同时写着 align-items:flex-start
   —— 交叉轴不再拉伸，<main> 就按「内容宽度」撑开；时间轴内层固定
   min-width: 900px，于是整页横向溢出（实测 375px 下溢出 581px）。
   给 main 一个显式宽度即可：超宽的时间轴交给 .tl-wrap{overflow-x:auto} 自己滚。 */
@media (max-width: 900px) {
  .main { width: 100%; max-width: 100%; min-width: 0; }
}

@media (max-width: 767px) {
  .tabs { display: flex; width: 100%; }
  .tabs button { flex: 1 1 auto; min-height: 44px; padding: 10px 12px; font-size: 13px; }
}
'''

MOD.joinpath('assets').mkdir(parents=True, exist_ok=True)
(MOD / 'assets' / 'tasks.css').write_text(css + ROLEB_CSS + TABS_CSS, encoding='utf-8')

# ─────────────────────────── 2. JS（看板） ───────────────────────────
js = (EX / 'page.js').read_text(encoding='utf-8')

# 2a. LS_* 常量 → 按角色隔离（去掉 seeded：新角色一律空，不再播种示例数据）
old_block = """  var APP_NAME = '大学 NPC 任务轴';
  var LS_NODES = 'npc_task_axis_nodes';
  var LS_CHECKINS = 'npc_task_axis_checkins';
  var LS_TAB = 'npc_task_axis_tab';
  var LS_SEEDED = 'npc_task_axis_seeded';
  var LS_BADGES = 'npc_task_axis_badges';
  var LS_FOCUS = 'npc_task_axis_focus';
  var LS_PET = 'npc_task_axis_pet';
  var LS_VIEW = 'npc_task_axis_view';

  var db = null;
  try { db = window.__SMART_PAGE__ && window.__SMART_PAGE__.database; } catch (e) { db = null; }
"""
new_block = """  var APP_NAME = '任务打卡';
  /* 存储按「角色」隔离：每个角色一套完整数据（任务轴 / 打卡 / 徽章 / 桌宠 / 番茄钟）。
     真源在服务端，本地 kust.tasks.v2.<角色>.* 只是缓存 —— 由 roles.js 负责同步。 */
  var ROLES = window.__TASKS_ROLES__;
  var LS_NODES = ROLES.key('nodes');
  var LS_CHECKINS = ROLES.key('checkins');
  var LS_TAB = ROLES.key('tab');
  var LS_BADGES = ROLES.key('badges');
  var LS_FOCUS = ROLES.key('focus');
  var LS_PET = ROLES.key('pet');
  var LS_VIEW = ROLES.key('view');
  var LS_DRAFT = ROLES.key('form_draft');

  /* 四个视图的内部名，与侧栏 <button data-view> 及 #view-<name> 一一对应。
     用于校验本地存下来的视图名（旧版本存的值会被忽略，回落到默认视图）。 */
  var VIEWS = ['rpg', 'stage', 'focus', 'pet'];

  /* 任务轴分类（即节点的 type 字段，直接存这个中文标签）。
     ★ 顺序即页签顺序；【必须同时改 4 处】，否则会出现「存得进去、刷新就变回通用证书」：
       ① 这里；② normalizeNode() 的类型归一化；③ 新建/编辑弹窗的类型下拉 fallback；
       ④ onReady() 里恢复上次页签的白名单。 */
  var TASK_TYPES = ['通用证书', '专业赛事', '课程学习', '科研进度'];
  function isTaskType(t) { return TASK_TYPES.indexOf(t) >= 0; }

  var db = null;
"""
assert old_block in js, 'LS 常量块未命中'
js = js.replace(old_block, new_block)
# 2b. 表单草稿键也按角色隔离
n_draft = js.count("'npc_task_axis_form_draft'")
assert n_draft == 3, '草稿键数量异常: %d' % n_draft
js = js.replace("'npc_task_axis_form_draft'", 'LS_DRAFT')

# 2c. 去掉资料库云端分支，固定走本地缓存（kust.work 无 __SMART_PAGE__）
i = js.index('  if (db) {')
j = js.index('  loadLocal();\n    onReady();\n  }\n})();')
tail = '  loadLocal();\n  onReady();\n})();\n'
js = js[:i] + tail
assert 'getSchema' not in js and 'loadCloud' not in js.split('function loadCloud')[0][i:], '云端分支残留'
m = re.search(r'\n  function loadCloud\(\) \{.*?\n  \}\n', js, re.S)
if m:
    js = js[:m.start()] + '\n' + js[m.end():]
assert 'window.__SMART_PAGE__' not in js, '仍有 __SMART_PAGE__ 引用'

# 2d. 新角色一律空：删掉示例数据播种（上游自带的 sampleNodes / seedSample 全部移除）
seed_start = js.index('  /* ================= 示例数据 ================= */')
seed_end = js.index('  /* ================= 视图切换 ================= */')
assert 'function seedSample()' in js[seed_start:seed_end], '示例数据块定位失败'
js = js[:seed_start] + js[seed_end:]

old_ready = """  function onReady() {
    var seeded = false;
    try { seeded = !!localStorage.getItem(LS_SEEDED); } catch (e) { seeded = false; }
    try {
      var savedTab = localStorage.getItem(LS_TAB);
      if (savedTab === '专业赛事' || savedTab === '通用证书') { state.tab = savedTab; }
    } catch (e) { }
    try {
      var savedView = localStorage.getItem(LS_VIEW);
      if (savedView) { switchView(savedView); }
    } catch (e) { }
    if (!state.nodes.length && !seeded) {
      seedSample().then(refreshAll);
    } else {
      refreshAll();
    }
    if (state.tab !== '通用证书') { switchTab(state.tab); }
  }


  loadLocal();
  onReady();
})();"""
new_ready = """  function onReady() {
    try {
      var savedTab = localStorage.getItem(LS_TAB);
      if (savedTab === '专业赛事' || savedTab === '通用证书') { state.tab = savedTab; }
    } catch (e) { }
    try {
      var savedView = localStorage.getItem(LS_VIEW);
      if (VIEWS.indexOf(savedView) < 0) { savedView = ''; }   /* 旧版/未知视图名 → 回落默认视图 */
      if (savedView) { switchView(savedView); }
    } catch (e) { }
    refreshAll();
    if (state.tab !== '通用证书') { switchTab(state.tab); }
  }

  /* 暴露给角色运行时（roles.js）的最小入口：远端数据变了由它调用 reload()，
     同步层因此不需要知道看板内部状态 —— 调用关系保持单向（roles.js → 看板）。 */
  window.__TASKS_BOARD__ = {
    reload: function () { loadLocal(); refreshAll(); }
  };

  loadLocal();
  onReady();
})();"""
assert old_ready in js, 'onReady 块未命中'
js = js.replace(old_ready, new_ready)
assert 'seedSample' not in js and 'LS_SEEDED' not in js, '播种逻辑残留'

# 2e. 视图名与文案：NPC → RPG（上游原件叫「NPC 时间轴」，本模块统一改称「RPG 时间轴」）
n = js.count("'npc'")
assert n == 1, '视图名 npc 出现 %d 次（期望 1，即 switchView 的分支）' % n
js = js.replace("'npc'", "'rpg'")
n = js.count('renderNPCView')
assert n == 2, 'renderNPCView 出现 %d 次（期望 2：定义 + 调用）' % n
js = js.replace('renderNPCView', 'renderRPGView')
js = js.replace('/* ================= 渲染：NPC 时间轴 ================= */',
                '/* ================= 渲染：RPG 时间轴 ================= */')
js = js.replace('NPC 故事由你书写', 'RPG 故事由你书写')
assert 'NPC' not in js, 'js 中仍有 NPC 残留'
assert "if (name === 'rpg') { renderRPGView(); }" in js, '视图分支未改名'

# 2f. 任务轴分类从 2 个扩到 4 个（通用证书 / 专业赛事 / 课程学习 / 科研进度）
#     上游把「分类」直接当节点的 type 存中文标签，所以下面每一处都拿着字面量做二选一归一化，
#     漏改任何一处都会出现「存得进去、刷新就变回通用证书」。4 处必须一起改。
_OLD_TYPE_EXPR = "(t === '专业赛事' || t === 'p8n3l5yt6dcxq2b7') ? '专业赛事' : '通用证书'"
_NEW_TYPE_EXPR = "isTaskType(t) ? t : (t === 'p8n3l5yt6dcxq2b7' ? '专业赛事' : '通用证书')"
n = js.count(_OLD_TYPE_EXPR)
assert n == 1, 'normalizeNode 的类型归一化出现 %d 次（期望 1）' % n
js = js.replace(_OLD_TYPE_EXPR, _NEW_TYPE_EXPR)

_OLD_SAVE_TYPE = "(typeText === '专业赛事' || typeId === 'p8n3l5yt6dcxq2b7') ? '专业赛事' : '通用证书'"
_NEW_SAVE_TYPE = "isTaskType(typeText) ? typeText : (isTaskType(typeId) ? typeId : (typeId === 'p8n3l5yt6dcxq2b7' ? '专业赛事' : '通用证书'))"
n = js.count(_OLD_SAVE_TYPE)
assert n == 1, '保存时的类型归一化出现 %d 次（期望 1）' % n
js = js.replace(_OLD_SAVE_TYPE, _NEW_SAVE_TYPE)

_OLD_OPTS = "var opts = state.typeOptions.length ? state.typeOptions : [{ text: '通用证书', id: '通用证书' }, { text: '专业赛事', id: '专业赛事' }];"
_NEW_OPTS = ("var opts = state.typeOptions.length ? state.typeOptions : TASK_TYPES.map(function (t) { return { text: t, id: t }; });")
assert _OLD_OPTS in js, '弹窗类型下拉 fallback 未命中'
js = js.replace(_OLD_OPTS, _NEW_OPTS)

_OLD_TAB_BIND = """  document.getElementById('tabCert').onclick = function () { switchTab('通用证书'); };
  document.getElementById('tabMatch').onclick = function () { switchTab('专业赛事'); };
  function switchTab(tab) {
    state.tab = tab;
    document.getElementById('tabCert').className = tab === '通用证书' ? 'active' : '';
    document.getElementById('tabMatch').className = tab === '专业赛事' ? 'active' : '';
    try { localStorage.setItem(LS_TAB, tab); } catch (e) { }
    renderTimeline();
  }"""
_NEW_TAB_BIND = """  /* 分类页签：按钮由构建脚本按 TASK_TYPES 生成（data-type 即分类名），
     这里统一绑定 —— 以后再加分类只改 TASK_TYPES，不用回来逐个写 getElementById。 */
  (function () {
    var tb = document.querySelectorAll('.tabs button[data-type]');
    for (var i = 0; i < tb.length; i++) {
      (function (b) { b.onclick = function () { switchTab(b.getAttribute('data-type')); }; })(tb[i]);
    }
  })();
  function switchTab(tab) {
    if (!isTaskType(tab)) { tab = '通用证书'; }
    state.tab = tab;
    var tb = document.querySelectorAll('.tabs button[data-type]');
    for (var i = 0; i < tb.length; i++) {
      tb[i].className = tb[i].getAttribute('data-type') === tab ? 'active' : '';
    }
    try { localStorage.setItem(LS_TAB, tab); } catch (e) { }
    renderTimeline();
  }"""
assert _OLD_TAB_BIND in js, 'switchTab / 页签绑定块未命中'
js = js.replace(_OLD_TAB_BIND, _NEW_TAB_BIND)

_OLD_SAVED_TAB = "if (savedTab === '专业赛事' || savedTab === '通用证书') { state.tab = savedTab; }"
assert _OLD_SAVED_TAB in js, 'onReady 的 savedTab 白名单未命中'
js = js.replace(_OLD_SAVED_TAB, 'if (isTaskType(savedTab)) { state.tab = savedTab; }')
assert '专业赛事' in js and js.count('通用证书') >= 1, '分类字面量被误删'
# 兜底：页签块必须先于 onReady() 执行，否则 querySelectorAll 拿不到按钮
assert js.index('.tabs button[data-type]') < js.index('  loadLocal();\n  onReady();\n})();'), \
    '页签绑定块落在 onReady 之后'

(MOD / 'assets' / 'app.js').write_text(js, encoding='utf-8')

# ─────────────────────────── 3. JS（角色运行时） ───────────────────────────
# 角色运行时是手写件（不是复刻产物），源文件放在 tools/tasks_build/roles.js 随管线一起走
(MOD / 'assets' / 'roles.js').write_text((HERE / 'roles.js').read_text(encoding='utf-8'),
                                         encoding='utf-8')
# 「学习目标」视图同样是手写件：源文件在 tools/tasks_build/goals.{js,css}
for _hand in ('goals.js', 'goals.css'):
    (MOD / 'assets' / _hand).write_text((HERE / _hand).read_text(encoding='utf-8'), encoding='utf-8')

# ─────────────────────────── 4. HTML ───────────────────────────
body = (EX / 'page_body.html').read_text(encoding='utf-8')

# 4a. 清掉资料库页面编辑器留下的元数据（kust.work 不需要）
body = re.sub(r'\s*data-page-node-id="[^"]*"', '', body)
body = re.sub(r'\s*data-pnid-children="[^"]*"', '', body)
body = re.sub(r'<!--pnid:[^>]*?-->', '', body)
body = re.sub(r'\s*data-sp-bindable="[^"]*"', '', body)
body = re.sub(r'\s*data-sp-database-id="[^"]*"', '', body)
assert 'data-page-node-id' not in body and 'pnid' not in body and 'data-sp-' not in body

# 4b. 补齐侧栏导航（上游页面 <nav> 是空的，四个视图因此没法互相跳转）
body = body.replace("""    <nav>
    </nav>""", """    <nav>
      <button class="nav-item active" type="button" data-view="rpg">🧭 RPG 时间轴</button>
      <button class="nav-item" type="button" data-view="stage">📚 阶段任务打卡</button>
      <button class="nav-item" type="button" data-view="focus">🍅 沉浸式学习</button>
      <button class="nav-item" type="button" data-view="pet">🎓 桌宠陪伴</button>
    </nav>""")
assert 'data-view="rpg"' in body, '侧栏导航未注入'

# 4b2. 任务轴分类页签：2 个 → 4 个（顺序即 TASK_TYPES；data-type 直接是分类名）
_OLD_TABS = """          <button class="active" id="tabCert">📜 通用证书</button>
          <button id="tabMatch">🏆 专业赛事</button>"""
_NEW_TABS = """          <button class="active" type="button" data-type="通用证书">📜 通用证书</button>
          <button type="button" data-type="专业赛事">🏆 专业赛事</button>
          <button type="button" data-type="课程学习">📚 课程学习</button>
          <button type="button" data-type="科研进度">🔬 科研进度</button>"""
assert _OLD_TABS in body, '分类页签块未命中（上游结构变了？）'
body = body.replace(_OLD_TABS, _NEW_TABS)
assert body.count('data-type="') == 4, '分类页签数不为 4'

# 4c. 内联硬编码色值 → 主题变量（规则区不写死色值）
inline_colors = {
    'style="font-size:12px; color:#a09db5"': 'style="font-size:12px; color:var(--np-ink-5)"',
    'style="font-size:13px; color:#6a6780"': 'style="font-size:13px; color:var(--np-ink-3)"',
}
for a, b in inline_colors.items():
    body = body.replace(a, b)
assert '#a09db5' not in body and '#6a6780' not in body, '内联色值未清干净'

# 4d. 页头文案本地化
body = body.replace('大学 NPC 任务轴', '任务打卡')
body = body.replace('报名・准考证・考试・查分，接任务打怪升级不漏节点',
                    '证书考试 × 专业赛事：报名・准考证・考试・查分，一个节点都不漏')
body = body.replace('证书考试 × 专业赛事：报名・准考证・考试・查分，一个节点都不漏',
                    '证书 · 赛事 · 课程 · 科研：报名・准考证・考试・查分，一个节点都不漏')
body = body.replace('大学 NPC 任务轴 · 报名・准考证・考试・查分，一个不漏',
                    'kust.work · 任务打卡：多角色游戏化备考工作台（数据同步在 kust.work 上）')

# 4e. 视图名与文案统一：NPC → RPG（含 <div id="view-npc"> 与 <span>🧭 NPC 时间轴</span>）
body = body.replace('data-view="npc"', 'data-view="rpg"')
body = body.replace('id="view-npc"', 'id="view-rpg"')
body = body.replace('NPC', 'RPG')          # 其余都是文案：视图标题 / 注释
assert 'NPC' not in body, 'body 中仍有 NPC 残留'
assert 'npc' not in body, 'body 中仍有小写 npc 残留'
assert 'id="view-rpg"' in body and '🧭 RPG 时间轴' in body, '视图未改名'

HTML = '''<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>任务打卡 · kust.work</title>
<link rel="stylesheet" href="/api/tasks/assets/tasks.css">
<!-- 主题引擎：必须同步加载（勿加 defer/async），否则会先闪深色再变浅色。
     由它在右上角挂出「自动 / 浅色 / 深色」三态切换按钮。 -->
<script src="/theme.js"></script>
<!-- 角色运行时：解析角色与数据 → 没有角色就弹「创建角色」→ 再注入看板脚本。
     看板脚本由它动态注入，所以这里不写 <script src="app.js">。 -->
<script src="/api/tasks/assets/roles.js"></script>
</head>
<body>

<!-- 门户顶栏（与站内其它页面一致；用 header 标签，主题引擎会在这里挂切换按钮） -->
<header class="np-top">
  <a class="back" href="/">&larr; 工作台</a>
  <span class="logo">ku</span>
  <span class="brand">任务打卡</span>
  <span class="spacer"></span>
</header>

<!-- 角色条：多角色切换入口（当前角色记在 cookie，数据在服务端，团队共享） -->
<div class="rolebar" id="roleBar"></div>

__BODY__

<div class="rmask" id="roleMask">
  <div class="rbox">
    <h3>角色管理</h3>
    <p class="rsub">每个角色拥有一套完整的独立工作台（任务时间轴・阶段打卡・番茄钟・徽章・桌宠）。切换角色即切换整套数据。</p>
    <div class="rwarn" id="roleWarn">角色数据存在 kust.work 上，团队内其它人也能切到你的角色查看。</div>
    <div id="roleRows"></div>
    <div class="rfoot" id="roleFoot">
      <button class="btn btn-ghost" id="roleAddBtn" type="button">＋ 新建角色</button>
      <button class="btn" id="roleCloseBtn" type="button">完成</button>
    </div>
  </div>
</div>

<div class="rmask" id="gateMask">
  <div class="rbox gate">
    <h3 id="gateTitle">先创建一个角色</h3>
    <p class="rsub" id="gateSub">每个角色是一整套独立的工作台，数据存在 kust.work 上。</p>
    <div class="gate-form">
      <label for="gateName">角色名称</label>
      <input id="gateName" type="text" maxlength="16" autocomplete="off"
             placeholder="如：阿呆 / 2027 考研 / 我的备考台">
      <div class="gate-avs" id="gateAvatars"></div>
    </div>
    <div class="gate-err" id="gateErr"></div>
    <div class="rfoot">
      <button class="btn btn-ghost" id="gateClose" type="button" hidden>取消</button>
      <button class="btn btn-primary" id="gateCreate" type="button">创建并进入</button>
    </div>
    <div id="gateExistingWrap" hidden>
      <div class="gate-sep">或进入已有角色</div>
      <div id="gateExisting"></div>
    </div>
  </div>
</div>
</body>
</html>
'''

TOP_CSS = '''
/* ===== 门户顶栏（与站内其它页面保持一致；变量在 :root 两套主题里给值） ===== */
.np-top {
  height: 48px; display: flex; align-items: center; gap: 10px; padding: 0 16px;
  background: var(--surface); border-bottom: 1px solid var(--border);
}
.np-top .back { font-size: 12.5px; color: var(--muted); text-decoration: none; }
.np-top .back:hover { color: var(--text); }
.np-top .logo {
  width: 24px; height: 24px; border-radius: 6px; display: flex; align-items: center;
  justify-content: center; font-size: 11px; font-weight: 700;
  background: var(--logo-bg); color: var(--logo-fg);
}
.np-top .brand { font-size: 13px; font-weight: 600; color: var(--text); }
.np-top .spacer { flex: 1; }
@media (max-width: 768px) { .np-top { padding: 0 12px; } }
'''

html = HTML.replace('__BODY__', body.strip())
(MOD / 'templates').mkdir(parents=True, exist_ok=True)
(MOD / 'templates' / 'tasks.html').write_text(html, encoding='utf-8')

p = MOD / 'assets' / 'tasks.css'
p.write_text(p.read_text(encoding='utf-8') + TOP_CSS, encoding='utf-8')

print('OK  css=%d  app.js=%d  roles.js=%d  html=%d' % (
    len(p.read_text(encoding='utf-8')),
    len((MOD / 'assets' / 'app.js').read_text(encoding='utf-8')),
    len((MOD / 'assets' / 'roles.js').read_text(encoding='utf-8')),
    len((MOD / 'templates' / 'tasks.html').read_text(encoding='utf-8'))))
