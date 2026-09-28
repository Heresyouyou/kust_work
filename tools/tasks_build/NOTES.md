# tasks_build · 任务打卡看板复现管线

把上游「大学任务轴」看板（WorkBuddy playbook 案例 / 本地资料库发布态页面的原件，
标题里的字样是 NPC）改造成 `kust.work` 门户模块 `modules/tasks/` 的可重复执行的离线管线。

> **改名约定（2026-09-21）**：这个模块对外一律叫 **RPG 时间轴**，不再用 NPC。
> 重命名**做在 `build_module.py` 里**（不是直接改 `extract/`）—— 上游原件保持原样，
> 所以哪天重新下载 `extract/` 也不会退回 NPC。详见下面「NPC → RPG 改名」一节。

## 什么时候用它

- 上游看板改版了，要重新拉一份并同步到 kust.work
- 要核对 `modules/tasks/assets/*` 是不是从 `extract/` 忠实生成的（而非手改漂移）
- 要换个配色再出一版（调色板集中在 `build_css.py` 的 `DARK` / `LIGHT` 两个字典里）

> **`modules/tasks/assets/` 下的一切都是生成物，不要手改。**
> 想改行为 → 改本目录的脚本 → 重新生成。

## 两步

```bash
cd D:\PostNews\Translate\tools\tasks_build
python3 build_css.py       # extract/page.css → out_css.css（色值全部变量化为 --np-*）
python3 build_module.py    # 生成 modules/tasks/{assets/tasks.css,assets/app.js,assets/roles.js,templates/tasks.html}
```

## 文件分工

| 文件 | 角色 | 上游改版后 |
|---|---|---|
| `extract/` | 上游产物（css / js / body 三份） | 需重新下载 |
| `build_css.py` | 色值 → `--np-*` 变量 + 两套主题变量块 | 重跑即可 |
| `build_module.py` | CSS/JS/HTML 三件产出 | 重跑即可 |
| `roles.js` | **手写**：角色解析 + 联网同步运行时 | 不受上游影响，但改它要小心（见下） |
| `out_css.css` | `build_css.py` 的中间产物 | — |

## extract/ 从哪来

上游发布态页面（`/p/<nodeId>`）下载后的产物，按 `<style>` / `<body>` / 最后一个 `<script>`
切成三份：

| 文件 | 内容 |
|---|---|
| `page.css` | 页面内联样式（浅色设计原件） |
| `page.js` | 看板主脚本（原版用 `npc_task_axis_*` 单身份存储 + `__SMART_PAGE__.database` 云端） |
| `page_body.html` | 看板 DOM（含资料库页面编辑器留下的 `data-page-node-id` / `<!--pnid:-->` 元数据） |

重新取产物：

```bash
printf '%s' "<token>" | python3 "<library skill>/page/download_page_artifacts.py" \
  --token-stdin --node-id "https://workbuddy.link/p/<nodeId>" --source publish --out-dir <dir>
```

## 脚本做了什么（要点，改之前先读）

`build_css.py`
- 9 条渐变整体替换 → `var(--np-grad-*)`（必须先做，否则渐变里的 hex 被逐色拆散）
- ~25 条 `rgba()` → `var(--np-sh-*)` / `var(--np-br-*)` / `var(--np-go-*)`；`rgba(255,255,255,…)`
  是渐变页头上的半透明白，两套主题都成立，**保持字面量不替换**
- ~50 个 hex → `var(--np-*)`；其中 `#fff` **按属性区分**：`color` → `--np-on-brand`（品牌底白字，
  两套主题都不变），其余 → `--np-card`
- 输出末尾自检「剩余未映射色值」，必须为空数组
- **调色板 = 站点色**：品牌绿 `#2e7d4f`(浅) / `#4a9e6a`(深) 取自 `portal/assets/theme.js` 与
  `modules/triangle/assets/styles.css` 的 `--accent` 族；中性面走站点深灰阶梯
  （`#1e1e1e / #262626 / #2a2a2a / #333`）与浅色暖白（`#f2f1ec` 系）；
  金色 `#f6a201` 只做奖励点缀，不动
- `--np-teal-bg` 特意留成蓝调（`#e4eef7` / `#1c2a33`）：品牌改绿之后，
  统计卡四个图标（绿/蓝/金/玫红）才拉得开层次

`build_module.py`
- `LS_*` 常量块 → `ROLES.key('…')`，把存储按角色隔离；顺手把裸字符串
  `'npc_task_axis_form_draft'` 也换成 `LS_DRAFT`
- 末尾的 `if (db) { getSchema… } else { loadLocal(); onReady(); }` → 直接 `loadLocal(); onReady();`，
  并删掉已无用的 `loadCloud()`（kust.work 里没有 `__SMART_PAGE__`）
- **删掉 `sampleNodes()` / `seedSample()` 整段**：新角色一律空工作台（上游自带示例数据，
  这里刻意不要 —— 空态提示已经写得很清楚）
- 补 `onReady` 里 `window.__TASKS_BOARD__ = { reload }`：给 roles.js 一个「远端变了，重绘一下」
  的唯一入口，同步层因此不需要碰看板内部状态（调用关系保持单向：roles.js → 看板）
- 补齐侧栏 `<nav>` 的 4 个 `.nav-item`（上游这个 `<nav>` 是空的，四个视图根本没法互相跳）
- **NPC → RPG 改名**（见下节）
- DOM 清掉 `data-page-node-id` / `data-pnid-children` / `<!--pnid:-->` / `data-sp-bindable`
  / `data-sp-database-id`
- 模板里的内联 `#a09db5` / `#6a6780` → `var(--np-ink-5)` / `var(--np-ink-3)`
- 套门户外壳（`<header class="np-top">` + 角色条 + 角色管理弹窗 + 创建角色引导弹窗）
- **HTML 里不再静态引 `app.js`** —— 由 `roles.js` 在「角色解析完 + 数据到手」之后再注入

**顶栏必须用 `<header>` 标签**：`portal/assets/theme.js` 靠 `document.querySelector('header')`
挂主题切换按钮，用 `div` 会把按钮塞进绿色渐变页头里。

## 数据与同步架构（`roles.js` + `modules/tasks/store.py`）

```
浏览器                     服务端
  cookie: kust_tasks_role  →  modules/tasks/data/store.json
  localStorage 缓存        ←  { roles: [{ id, name, avatar, rev,
  kust.tasks.v2.<roleId>.*        data{nodes,checkins,badges,focus,pet}, tomb }] }
```

- **角色清单是全局的**（内网免登录）：谁都能看到全部角色并切过去看同一份数据
- **cookie 决定「这台浏览器在哪个角色」**；cookie 空 / 指向已删除的角色 → 弹创建角色命名弹窗，
  **看板脚本不注入**（避免先渲染一版错的再跳）
- **同步键只有 5 个**：`nodes / checkins / badges / focus / pet`。
  `tab / view / form_draft` 是「本机视图状态」，**故意不同步** —— 否则两台机器互相打架
- **推送靠拦截 `localStorage.setItem`**：看板脚本照旧写本地存储，roles.js 顺手防抖 700ms 推上去。
  这样上游改版时同步层不用重做（值没变不回推，拦掉「渲染即回写」的抖动）
- **拉取靠轮询**：15s 一次 `GET /state?role=&rev=`，rev 没变只回 `changed:false`；
  切回前台时补一次；`beforeunload` 用 `keepalive` fetch 尽力把未推的改动送出去
- **冲突合并（服务端）**：`rev` 一致直接采纳；`rev` 落后 → 按 `_id` 求并集（同 id 客户端优先）；
  客户端把「本地已删 id」带上来落进**墓碑**，防止另一端手里的旧快照把删掉的条目复活；
  墓碑 30 天后回收
- **离线降级**：服务端连不上就用本地缓存照常读写，`dirty` 标记留着，恢复后自动补传

## NPC → RPG 改名

改动只落在两个地方，其余全部由构建产出：

| 位置 | 内容 |
|---|---|
| `build_module.py` §2e | `'npc'` → `'rpg'`（视图名，全脚本仅 1 处）、`renderNPCView` → `renderRPGView`（定义+调用）、`渲染：NPC 时间轴` 注释、徽章文案「NPC 故事由你书写」 |
| `build_module.py` §4b / §4e | 侧栏按钮 `data-view="rpg"` + 文案「🧭 RPG 时间轴」、`<div id="view-rpg">`、视图标题 |
| `build_css.py` | 产出样式的头注释改成「由『大学 RPG 任务轴』复刻」 |

三段都带 `assert`：改名没命中会直接**构建失败**，而不是悄悄出一版半吊子产物。

- **视图名换了要防旧值**：`LS_VIEW` 存的是视图名（per-role，本机视图态、不同步）。
  旧浏览器里存着 `npc` 的话，`switchView('npc')` 会什么都不匹配 → **整块内容区空掉**。
  对策：新增 `VIEWS = ['rpg','stage','focus','pet']` 常量，`onReady` 里
  `VIEWS.indexOf(savedView) < 0 → savedView = ''` 回落默认视图（比写死一个旧名映射更耐脏）。
- **`extract/` 保持原样**：那是上游下载来的原件（含 `npc_task_axis_*` 存储键、
  `renderNPCView`），是管线的输入而不是产物；改名做在管线里才不会被下一次下载冲掉。
- **两条「不可避免」的 NPC 字样**（都不对用户可见，别误删）：
  1. `build_module.py` 的 `old_block` —— 必须逐字匹配上游原文才能替换；
  2. `verify_tasks_module.py` 的 `check('无旧存储键 npc_task_axis', ...)` —— 它断言的是
     **旧键已消失**，删了反而失去守卫。

## 示例角色（`demo`，参考样例）

库里常驻一个 `id = 'demo'` 的角色给新同学对照「填满的工作台长什么样」。

- **不写 cookie、不参与任何自动跳转**。需求原话是「保留一个示例角色用于参考，但不是默认进入这个角色」——
  所以它只是一条普通记录：列在「创建角色」弹窗的「或进入已有角色」里（带「示例」小标 + 「看样例 →」），
  以及角色条最右边（虚线边框弱化）。cookie 为空时照旧弹创建引导，**没有 cookie 就没有 active**。
- **常驻语义**：`store.ensure_demo()` 每次启动都调，角色缺失就补回（不是只播种一次）。
  删掉了想找回来 → 管理弹窗里的「恢复示例角色」；被改乱了 → 同一个按钮会重置（`force=true`）。
- **排序**：`list_roles()` 把 `demo` 固定压到最后（自己的角色在前，参考物不抢位置但要看得见）。
- **示例数据**（`store.py` 的 `DEMO_NODES / DEMO_CHECKINS / DEMO_FOCUS`）：
  5 个任务节点取自各考试官方真实日程（教资 / 计算机二级 / 四级 / 普通话 / 大广赛），
  覆盖「已报名×3 + 未报名×2」「通用证书×4 + 专业赛事×1」「报名中 / 准考证中 / 临近开考 / 已结束」各态；
  10 条打卡 + 5 条番茄 + 桌宠互动，经验正好推到 **Lv.3**，「三日之约」「十次修行」两枚徽章点亮。
- **两种时间策略**（为了让样例永远「是活的」）：
  - 任务日程写死在 `DEMO_BASELINE_YEAR = 2026`，播种时按 `max(0, 当年 - 2026)` **整体平移年份**，
    月份日号不变 → 「教资 10 月底笔试」这类规律跨年仍成立
  - 打卡 / 专注 / 桌宠用「距今天 N 天」生成 → 任何时间打开都像最近刚练过
- **重置必须清墓碑**：示例条目的 `_id` 是固定的（`demo-n1`…），
  重置时若留着旧墓碑（里面有这些 id），刚写进去的条目会被自己的墓碑过滤掉 → 重建出空壳。

### 踩过的坑

- **单引号 vs 双引号**：官方复刻文档给的 `databaseId` 探测命令只匹配双引号，
  而这个页面用单引号 —— 差点被误判成「纯 HTML 页面」而丢掉全部数据表关联。
- **`app.js` 不能静态引入**：它一加载就 `loadLocal(); onReady();`，
  角色没定下来就是白渲染一版错数据。必须先解析角色再注入。
- **深色主题验收别直接写 localStorage**：Playwright 的 `add_init_script` 每次加载都会跑，
  会把模式重置回去。走真实的 `#kustThemeBtn` 点击才靠得住。
- **`setCookie('')` 曾经是个空操作**：函数开头 `if (!id) { return; }`，
  于是「退出本机」和「当前角色被队友删了」这两条清 cookie 的路径**从来没生效过**
  （cookie 还在 → 刷新又自动进回去）。清 cookie 必须真的写一次
  `COOKIE + '=;path=/;max-age=0;SameSite=Lax'`，且 path/SameSite 要和写入时一致。
- **服务端 `rev=0` + 客户端无缓存 = 空板**：客户端首拉带 `rev=0`，
  而服务端恰好也是 `rev=0`（有内容但版本号为 0）→ 命中 `GET /state` 的 `changed:false` 快路径，
  客户端以为「没变化」就不写本地缓存 → 看板全空。
  两头都修了：示例角色 `rev` 从 1 起；客户端**没有 syncrev 时用 `-1` 强制全量拉**。
- **元组会在 `_clean()` 里静默消失**：`_clean` 只认 list/dict/str/num，
  `'plan': (...)` 这种元组会被丢成 `None` → 阶段计划整段没了。示例数据一律写 list，
  并对模块级常量做 `copy.deepcopy` 再改。
- **离线兜底别顺手拿 `roles[0]`**：原来 `if (!found && roles.length) { found = roles[0]; }`
  在「离线 + 库里只剩示例角色」时会把用户自动丢进样例里。兜底只在**非 demo** 的角色里挑。
- **在线时不要叠本地 `active` 缓存**：`res.active` 已经是服务端按 cookie 解析的结果，
  再 `|| lsGet('…active')` 会把「退出本机」抵消掉（cookie 清了、缓存还在 → 又进回去）。
  cookie 是唯一权威；本地那份只在**离线**时当线索用。
- **时间轴按 tab 过滤**：`renderTimeline()` 只渲染 `type === state.tab` 的节点，
  默认 tab 是「通用证书」。验收断言别按「节点总数」写（示例 5 个节点只会渲染出 4 个）。

## 验收

```bash
# 1) 离线断言（147 条：路由 / 静态资源 / 穿越防护 / 脚本关键点 / CSS 主题 / RPG 改名 / 示例角色 / 同步接口全流程）
"D:\Python 3.12.4\python.exe" D:\PostNews\Translate\tools\verify_tasks_module.py

# 2) 浏览器验收（100 条：独立实例 @5601 + 临时 store，浅/深/窄屏/无 cookie 引导/双向同步/示例角色闭环/RPG 文案）
"D:\Python 3.12.4\python.exe" D:\PostNews\Translate\tools\verify_tasks_visual.py

# 3) 线上端到端（31 条：跑完删掉所有非示例角色，只留示例角色）
"D:\Python 3.12.4\python.exe" D:\PostNews\Translate\tools\check_tasks_live.py
```

1) 只建独立 Flask 实例挂 tasks 蓝图，**绝不 `import web_app`**（那会 taskkill 掉线上 5555）；
接口部分把 `store.DATA_DIR/STORE` 重定向到临时目录，跑完对比确认真实库没被动过。
3) 会真写线上库，但开头和结尾都会清掉所有 `demo: false` 的角色 —— 中途崩了也不留垃圾，可以反复跑。

重启线上才生效（改了 `.py`）；
记得先看 `curl -s http://127.0.0.1:5555/api/portal/health` 的 `active` 是不是 0。
