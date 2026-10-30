# GeoKB「Agent 亲自入库」规范（离线模式 · 零 DeepSeek）

> 文档类型：**入库要求固化**。目的：让任意一次新会话（上下文已丢失）读完本文件即可
> 接手积压文献的入库工作，无需回溯对话历史。
>
> 首次编写：2026-10-22　｜　适用仓库：`D:/ZSK/geokb`
> 相关脚本：`scripts/agent_ingest.py`、`scripts/ingest_digest.py`
> 相关 Skill：`~/.workbuddy/skills/geokb-agent-ingest/`、`~/.workbuddy/skills/geokb-night-ingest/`

> ## ⚠ v3 覆盖条款（2026-10-24，优先级高于本文件其余章节与 §0.4）
>
> 1. **时间窗口与 07:05 硬停全部取消**：任何时段均可入库；`write`/`commit` 一律加 `--force`
>    （脚本仍会打印时间窗 WARN，那是提醒而非阻断）。停止条件只有三种：**P0 耗尽 / 系统级错误 / 用户明令停止**。
> 2. **单篇读入不设上限**：质量优先。先 `python scripts/ingest_digest.py next` 取导航（锚点/摘要/结论/数值候选），
>    再用 `--full` 通读整篇、`--slices 起-止` 回看（**次数不限**）。**禁止只读摘要就写。**
>    （旧版「≤3000 字符 digest / ≤2 次补读 / ≤600 行」预算已作废。）
> 3. **腾空间靠出栈，不靠限读**：每批 `commit` 后立即
>    `python scripts/ctx_evict.py evict <sha...>`，把已入库篇的 `wip/<sha>.md`、`payload-<sha>.json`、
>    `state/text/<sha>*.txt` 移入 `state/_cold/`（只移动不删除，`restore` 可回滚）。
>    合规判据：`ctx_evict.py status` 的「已入库却仍在热区」= 0。账本 `state/ctx_evicted.jsonl`。
> 4. **父上下文零正文**：整篇处理放进 `Agent(general-purpose)` 子代理，父代理只收一行回执
>    （sha | 条目+kp 数 | WARN | 成功与否），再攒批 `commit` + 出栈。
> 5. 上下文内只保留：①本规范/skill ②完成清单 `state/night_ingest_progress.md`
>    ③当前篇笔记 ④当前篇正文。断点恢复读 `state/ingest_handoff.md`。
> 6. 本条对**所有采用本规范的会话、子代理、定时任务**生效，不得以“旧流程”为由绕过。

---

## 0. 定位、红线与边界

### 0.1 什么是「Agent 亲自入库」模式

既有的自动入库管线（`pdf_corpus_ingest.py` → `kp_extract.py`）依赖 **DeepSeek API**
做摘要提取与知识点拆分。本模式提供一条**完全不调用任何 LLM API** 的替代路径：

| 环节 | 自动管线 | **Agent 入库模式** |
|------|----------|-------------------|
| 摘要 / 元数据提取 | DeepSeek `ds_extract` | **Agent 阅读 PDF 全文后撰写** |
| 关键词 / 标签词条 | DeepSeek | **Agent 按受控词表归类** |
| 分面 facets | `build_facet_index.py` 自动归一 | 同左（**自动**，Agent 不手写） |
| 知识点拆分 | `kp_extract.py`（DeepSeek） | **Agent 撰写 `kp-<sha12>-<n>.md`** |
| 索引 | `index_one_entry.py` | 同左（已有脚本） |
| 台账登记 | `daily_archive_papers.py` / 各类入库脚本 | 由 `agent_ingest.py` 统一登记 |

**关键洞察**：`build_facet_index.py` 会按 `config/taxonomy.yaml` 自动把
`tags + title + key_content` 归一到五分面并回写 `facets` 字段，还会规范化 `tags`
（规范词置顶、保留具体词、≤12 条）。**因此 Agent 不需要手写 facets**，只需产出高质量的
`tags` 与正文，跑一次 `build_facet_index.py` 即可完成分面归属。

### 0.2 绝对红线 ⛔

1. **严禁调用 DeepSeek API**（`deepseek_client.py`、`ds_extract`、`kp_extract.py`、`llm_extract.py`）。
   本模式下这些路径**一律不得触发**，也不得通过子进程间接触发。
2. **严禁编造**。所有摘要、知识点、数值、结论必须**来自 PDF 原文**；
   原文没有的信息不写、不推测、不用领域常识"补全"。
3. **数值必须原样抄录**（含单位、误差、范围写法），不得换算、不得四舍五入。
   例如原文 `320 ± 5 °C` 就写 `320 ± 5 °C`，不写 `约320度`。
4. **不得因入库而修改 PDF 原文文件**；只写 `content/` 与台账。
5. **不得整篇覆盖**已有条目：若条目已存在且质量合格，只**补充**缺失部分。
6. 索引与台账**必须走既有脚本**（`index_one_entry.py` / 台账追加函数），
   不得手写 SQLite 插入或裸写 jsonl。
7. **只在 23:00–次日 08:00 执行写操作**（见 §0.4）。

### 0.4 时间窗（用户口径 2026-10-22）🕚

> **当入库属于 Agent 亲自入库时，允许入库时段为 23:00 – 次日 08:00；
> 该窗口内不调用 DeepSeek API，全程由 Agent 依据本规范完成入库工作。**

**边界（精确）**

| 时刻 | 是否允许写 |
|---|---|
| `22:59` | ❌（等 1 分钟进今晚窗口） |
| `23:00` | ✅ |
| `23:59` | ✅ |
| `00:00` – `07:59` | ✅（归属昨晚那班） |
| `08:00` 起 | ❌（下一次开窗 = 今晚 23:00） |

**分班规则**：`23:00–23:59` 归属「今晚这班」；`00:00–07:59` 归属「昨晚那班」。

**实施方式**（`scripts/agent_ingest.py`）

- 常量 `WINDOW_START_H=23` / `WINDOW_END_H=8` / `READONLY_CMDS`。
- `window_state()` 返回 `{ok, now, start, end, left_min, eta_open_min, reason}`。
- `gate(cmd, force)` 在 `main()` 里对所有子命令生效：
  **写操作**（`write` / `commit`）窗口外 → 打印提示并 `sys.exit(2)`；
  **只读**（`scan` / `prep` / `verify` / `status` / `window`）全天候放行。
- 窗口外紧急修正：顶层或子命令加 `--force`（会打印 WARN，提醒操作者自查未调 API）。

```bash
$PY scripts/agent_ingest.py window     # 查看当前窗口状态
```

> ⚠️ 与用户级 DeepSeek 时段铁律是**叠加**关系，不是替代：
> 23:00–08:00 走 agent 入库模式时一律离线；其它时段的常规 API 规则不变。
> 也**不要**把这道闸门写进任何业务脚本（如每日管线），业务必须 7×24 可用。

### 0.5 边界

- **不在本模式范围**：`source: metadata_only` 的纯题录（无 PDF，拿不到原文，
  无法由 Agent 亲自解析）——只能等自动链路补 PDF。
- `.zh.mono.pdf` / `.zh.dual.pdf` 是**翻译产物**，不是独立文献，不单独入库。
- 扫描版（无文本层）PDF 无法由 Agent 解析，归为 `needs_ocr`，不硬写条目。

---

## 1. 「积压」的定义与判据

**积压 = 已下载 PDF，但入库流程未跑全的历史文献。**

入库流程包含五个环节，缺任一即为积压：

| 环节 | 判据 |
|------|------|
| ① PDF 已下载 | 台账 `repo_path` 含 `papers_repo/` 且 `size > 0` |
| ② 条目已生成 | `content/papers/paper-<sha12>.md` 存在 |
| ③ 知识点已拆分 | `content/kpoints/kp-<sha12>-*.md` 数量 > 0 |
| ④ 分面已归属 | 条目 frontmatter `facets` 至少 1 个分面非空 |
| ⑤ 索引已建立 | `index/geokb.db` 的 `entries` 表有该 `eid` |

分桶：

| 桶 | 含义 | 优先级 |
|----|------|--------|
| **P0** | 有 PDF，无条目（②③④⑤ 全缺） | 最高——需完整入库 |
| **P1** | 有条目，无知识点（③ 缺） | 高——补知识点即可 |
| **P2** | 有知识点，缺分面（④ 缺） | 中——跑分面索引 |
| **P3** | 有分面，缺标签（tags 空） | 中 |
| **P4** | 五项齐全 | 无需处理 |
| **NP** | 无 PDF（纯题录） | 不在本模式 |

> **⚠️ 判据陷阱（实测）**：关联条目与台账**必须用 `sha1[:12]`，不能用 `eid` 字段**。
> 历史台账行存在 `eid` 为空但文件已生成的情况（文件按 sha1 命名），
> 用 eid 关联会把已入库的条目误判为「无条目」，导致积压数量严重失真
> （实测误判 1425 篇）。正确做法：`eid = "paper-" + sha1[:12]`，
> sha1 缺失时现算文件 sha1。

---

## 2. 每篇的交付物清单

处理一篇积压文献，必须产出/确认以下全部：

1. **条目 md** `content/papers/paper-<sha12>.md`（字段齐全，见 §3）
2. **知识点文件** `content/kpoints/kp-<sha12>-<1..N>.md`，N ∈ [0, 8]（**0 条合法**，见 §4）
3. **分面归属** 由 `build_facet_index.py` 自动写入 `facets`（见 §5）
4. **索引行** `index/geokb.db`：paper 条目 + 全部 kp，共 N+1 行
5. **台账行** `state/papers_index.jsonl`
6. **状态行** `state/agent_ingest_state.jsonl`（stage=committed）

---

## 3. 条目格式契约

```yaml
---
id: paper-<sha1前12位>
title: 英文论文写英文标题；中文论文写中文标题
module: papers
lang: en                      # en / zh，可选但建议写
tags:                         # 受控规范词 + 英文具体词，3–12 条
- 矽卡岩
- skarn
- 白钨矿
- scheelite
- 流体包裹体
summary: 一段 200–400 字的中文摘要，必须包含该文的关键数值与结论
confidence: B                 # Agent 亲自审阅定稿 = B；仅题录/信息不足 = C
status: active
updated: '2026-10-22'
sources:
- name: <期刊名，与 journal 一致>
  path: papers_repo/by_journal/<期刊>/<年>/<文件名>.pdf
journal: <期刊名>
year: 'YYYY'
extraction: agent             # ★ Agent 亲自入库标记（区别于 llm/fallback/manual）
facets:                       # 由 build_facet_index.py 自动回写，Agent 不手写
  deposit_type: [...]
  process: [...]
  method: [...]
  commodity: [...]
  region: [...]
origin: user_upload           # 仅用户上传文献写
remarks: ...                  # 仅用户备注非空时写
---

## Abstract        ← 英文论文

<英文摘要原文或基于原文的凝练>

## Key Content

<关键内容>

## Core Methods

- 方法 1
- 方法 2

## Study Area

<研究区>

## Application Scenarios

- 场景 1

## 中文翻译

<small>**标题**：中文译名</small>

<small>**摘要**：中文摘要</small>

<small>**方法**：方法列表</small>
```

**中文论文**结构对应为：`## 摘要` / `## 关键内容` / `## 核心方法` / `## 研究区` / `## 应用场景`。

> 字段契约源自 `server/ingest.py::parse_entry()`，缺失 `id` 会整条解析失败。

---

## 4. 知识点格式契约

文件：`content/kpoints/kp-<sha12>-<n>.md`，`n` 从 1 递增，**每篇 3–6 条**。

```yaml
---
id: kp-<sha12>-<n>
title: <知识点标题，≤30 字，具体不空泛>
module: kpoints
tags: [受控词 + 具体词]
summary: <知识点的完整表述，含原文数值与限定条件>
confidence: B
status: active
updated: 'YYYY-MM-DD'
sources:
- name: <期刊名>
  path: papers_repo/by_journal/<期刊>/<年>/<文件名>.pdf
  url: https://sjck.kust.work/api/entry/kp-<sha12>-<n>/pdf
  accessed: 'YYYY-MM-DD'
source_tier: T3              # 知识点固定 T3（来自论文，降权）
parent: paper-<sha12>        # ★ 必须填，否则无法回溯原文
evidence: L100-L112          # ★ 必须填，原文行号区间
extraction: agent-kp         # ★ Agent 入库标记
journal: <期刊名>
year: 'YYYY'
---

## 知识点

<正文段落>

**证据行号**：L100-L112

## 来源论文

- <论文标题>
```

**证据行号规则**（沿用 `kp_extract.py` 约定）：
- 行号基于 `pdf_corpus_ingest.extract_text()` 产出的纯文本行序列（1 基）；
- 单条知识点证据跨度建议 ≤ 20 行，避免"贴了但等于没贴"；
- 数值型知识点（年龄、温度、同位素比值）**必须**给出可核对的证据行号；
- 无法定位到具体行号时，写 `evidence: ""` 并在 notes 说明，不编造行号。

### 4.1 知识点收录判据（2026-10-23 重写）

**只问一件事：这条能不能用于团队今后的研究？换一个研究区、换一个矿床，还用得上吗？**

按「复用方式」归类，写进 frontmatter 的 `kp_reuse`：

| 复用方式 | 含义 | 典型 |
|---|---|---|
| 解释借鉴 | 成因机制、判别判据、解释框架 | 「高 Sr/Y + 负 εHf 指示加厚下地壳」 |
| 方法迁移 | 新方法/新技术/新流程/关键参数 | 「BrF5-MAT253 测白钨矿氧同位素，Mo 校正公式…」 |
| 类比参照 | 构造背景、地球动力环境、成矿格架 | 「志留纪碰撞后伸展阶段的 A 型花岗岩」 |
| 标定参照 | 年龄/品位/温压/同位素参照值 | 「锆石 U-Pb 95.4±1.2 Ma」 |
| 本区事实 | 只对本区有意义（样品描述、局部数据）| 「斜长石 45–50%、角闪石 30–35%」 |

- `本区事实` **每篇最多 1 条**，且必须是罕见实例（如全球仅有的科马提岩露头），否则不收。
- 条数 **0–8，没有下限**：信息密度低的论文写 0 条是正常结论。
- 知识点必须能被程序复核，硬错会被 `write` 直接拒绝：

| 硬错（拦） | 软警（不拦） |
|---|---|
| 正文里的带单位数值在原文找不到 | 信息量分偏低（<0.32） |
| 与同篇另一条重复（相似度 ≥0.62 / 同源数据） | 疑似套话表述 |
| 正文 <40 字或 >800 字 | 标题 >30 字 |
| 证据行号缺 `L` 前缀（写成 `120-135`）| 证据跨度 >20 行；非行号定位（`内容提要（7.2…）`） |

### 4.2 知识点写法

- 标题 ≤26 字，含「对象 + 属性」，能被单独检索（不要「摘要」「结论」这类章节名）；
- 正文 100–260 字：**结论 + 关键数值 + 限定条件**，脱离原文可读；
- 数值照抄原文（含 ±、范围、单位、有效位），不换算、不四舍五入；
- 不从中文版回译英文字段；英文论文的知识点用英文写；
- 同一批测试数据只写一条，不要按「年龄」「同位素」「模式年龄」拆成三四条。

---

## 5. 分面与标签（受控词表）

受控词表唯一事实源：`config/taxonomy.yaml`，**五个分面**：

| 分面 key | 含义 | 例 |
|---|---|---|
| `deposit_type` | 矿床类型 | 矽卡岩、斑岩铜矿、浅成低温热液、VMS |
| `process` | 成矿/地质过程 | 岩浆-热液演化、流体包裹体、蚀变分带 |
| `method` | 方法技术 | 锆石U-Pb、LA-ICP-MS、数值模拟、机器学习 |
| `commodity` | 矿种 | Cu、Au、W、Sn、REE、Li |
| `region` | 研究区 | 冈底斯、秦岭、长江中下游、安第斯 |

**Agent 的操作**：给条目 `tags` 里同时放入**规范词**与**英文具体词**；
`build_facet_index.py` 会自动完成映射归一（含同义词归并）并回写 `facets`。

> ⚠️ 该脚本会**重写**条目 frontmatter 的 `facets` 与 `tags`，因此**必须先写条目、
> 后跑分面索引**，顺序颠倒会丢失 Agent 撰写的内容。

---

## 6. 台账登记规范

文件：`state/papers_index.jsonl`（追加，主副本；NAS `index.jsonl` 被 QTS 独占，只读）。

必备字段：

| 字段 | 说明 |
|---|---|
| `title` | 条目标题 |
| `journal` | 期刊名（**用户上传文献恒为 `Addition`**） |
| `date` | `YYYY-MM-DD` |
| `repo_path` | NAS 绝对路径 |
| `size` | 字节数 |
| `source` | 来源标记（见下） |
| `sha1` | 40 位 hex |
| `eid` | `paper-<sha12>` |
| `indexed_at` | ISO 时间戳 |

**`source` 取值**：原有来源标记（`gsw`/`oup`/`springer`/`kcdz`/…）保持不变；
Agent 入库不改写 `source`，入库方式由条目的 `extraction: agent` 标记体现。

**写后校验**：追加后必须回读文件尾部做语义校验（确认 sha1 + eid 行确实落盘），
防外部进程重写竞态。失败标记 `write_verify_failed`，可重试。

---

## 7. 索引

统一走 `index_one_entry.py`（进程内 import，`main()` 返回 0 为成功，~0.6s/条）。

- 必须先 `enable_load_extension(True)` → `sqlite_vec.load(conn)` → `enable_load_extension(False)`；
- 三张表同步：`entries`、`fts_entries`（列名 `id`）、`vec_entries`（列名 **`entry_id`**，不是 `id`）；
- 每次入库索引 `paper-<sha12>` + 全部 `kp-<sha12>-<n>`，共 N+1 个 id。

---

## 7.5 积压实测底数（2026-10-22 00:2x）

`python scripts/agent_ingest.py scan` 实测：

| 项 | 值 |
|---|---|
| 台账总行数 | 1485 |
| PDF 可达 | 1457 |
| **P0** 有 PDF 无条目 | **24** ← 需 Agent 完整撰写条目 |
| **P1** 有条目无知识点 | **3** ← 需 Agent 补知识点 |
| **P2** 有知识点缺分面 | **169** ← 跑分面索引自动回写 |
| P3 缺标签 | 0 |
| P4 完整 | 1261 |
| ORPHAN / PX | 0 / 0 |

> 表中为 **2026-10-22 01:3x 第二次 scan** 的结果（首批 6 篇已提交）。
> 首次 scan 时为 P0=30 / P1=3 / P2=773 / P4=651；期间每日定时管线已自动
> 回写一批分面，P2 降至 169，P4 升至 1261。
>
> ⚠️ **P2 的 169 篇是脚本自动归一也救不回来的**：跑 `build_facet_index.py`
> 后 frontmatter 更新数为 **0**，说明其 tags 多为英文自由词（如
> `sulfur speciation`、`X-ray absorption spectroscopy`），
> 与 `config/taxonomy.yaml` 的中文受控词表无法命中 → **需要 Agent 亲自补受控词**。

**结论**：需要 Agent 亲自阅读撰写的为 **P0 + P1 = 27 篇**，
另加 P2 中英文自由词条目 169 篇（补受控词，不需重读全文）。

### ⚠️ 排查积压时的四个实测陷阱

1. **不能用 `eid` 字段关联条目**：历史台账行存在 `eid` 为空但条目已生成的情况
   （条目按 `paper-<sha12>` 命名）。用 eid 关联会把已入库条目误判为「无条目」，
   实测**误判 1425 篇**，积压底数完全失真。必须用 `sha1[:12]` 反推。
2. **不能在 scan 里逐个读 PDF 算 sha1**：台账 1485 行中仅 2 行有 `sha1` 字段，
   若缺失就去读 NAS 上的大文件，实测约 **0.5s/篇 × 1485 ≈ 12 分钟**，
   命令会被超时 SIGTERM。缺失时从 `eid` 前缀反推即可（零成本）。
3. **D4 维度与本模式冲突**：`find_duplicate` 的 D4（内容条目存在）在
   「Agent 先写条目、后登记台账」路径下必然命中 → 正常登记被判重复（报 `content_entry`）。
   修复：`append_ledger(rec, allow_entry=True)` → `find_duplicate(skip_content_entry=True)`。
4. **台账判重不能用 sha1**（同上 sha1 缺失）→ 改用 **repo_path 归一化**比对。
   ⚠️ 该陷阱同样存在于 `cmd_verify()`：早期版本 verify 直接取 `sha1`，
   导致 entry/kp/facets 只统计到 2 条（实际 1433 条），已在同一次修复中对齐 scan 逻辑。

---

## 8. 工作流（5 个阶段）

```
scan → prep → write → commit → verify
 (脚本)  (脚本)  (Agent)  (脚本)   (脚本)
```

### 8.1 scan — 生成积压清单

```bash
python scripts/agent_ingest.py scan
```

输出 `state/agent_ingest_queue.jsonl` 并打印分桶统计（P0/P1/P2/P3/NP）。

### 8.2 prep — 抽出待读文本（供 Agent 阅读）

```bash
python scripts/agent_ingest.py prep --limit 5      # 取前 5 篇
python scripts/agent_ingest.py prep --sha <sha12>  # 指定一篇
```

生成 `state/agent_ingest_wip/<sha12>.md`：含文件元信息、PDF 元数据、
正文前 N 字、章节标题、数值候选行（带行号）、以及待填模板。
**Agent 读这个文件即可撰写摘要与知识点，无需再打开 PDF。**

### 8.3 write — Agent 撰写产物落盘

Agent 产出 JSON payload（摘要 / tags / 知识点列表），执行：

```bash
python scripts/agent_ingest.py write --sha <sha12> --payload payload.json
```

写入 `content/papers/paper-<sha12>.md` 与 `content/kpoints/kp-<sha12>-*.md`，
状态置 `written`。**此步不碰索引与台账。**

### 8.4 commit — 索引 + 台账 + 校验

```bash
python scripts/agent_ingest.py commit --sha <sha12>
python scripts/agent_ingest.py commit --all-written   # 批量
```

依次：跑 `build_facet_index.py`（分面回写）→ `index_one_entry.py`（paper+kp）→
台账登记（写后校验）→ 状态置 `committed`。任一环节失败 → `failed`，保留产物可重试。

### 8.5 verify — 全库一致性校验

```bash
python scripts/agent_ingest.py verify
```

逐篇核对 §1 五个环节，输出全绿矩阵或差异清单。

---

## 8.6 端到端验证记录（第 1 篇 · 2026-10-22 00:2x）

对象：`1220b5290d7c` — *Tourmaline Breccias from the Río Blanco-Los Bronces
Porphyry Copper District, Chile*（Economic Geology, 2023，文本 123299 字符）

| 阶段 | 命令 | 结果 |
|------|------|------|
| scan | `agent_ingest.py scan` | ✅ 3.8s，P0=30 / P1=3 / P2=773 / P4=651 |
| prep | `prep --limit 1 --bucket P0` | ✅ 生成工作笔记 11564 字节（摘要 + 各章节开头 + 60 条数值候选行带行号） |
| write | `write --sha 1220b5290d7c --payload ...` | ✅ 条目 + 6 知识点，`parse_entry` 校验通过 |
| commit | `commit --sha 1220b5290d7c` | ✅ 分面回写 + 索引 7 行（1 paper + 6 kp）+ 台账 1 行 |
| 检索 | `5556 /api/search` + `/api/entry/<eid>` | ✅ 条目 API 200，知识点 API 200 |

**产物核对**：条目 `extraction: agent` / `lang: en` / `facets` 已自动回写；
知识点 `parent=paper-1220b5290d7c`、`evidence=L18-L20 … L38-L43` 均可回溯原文；
台账保留真实期刊 `journal: Economic Geology`（**不是** Addition——Addition 只用于用户上传入口）。

### 踩坑：D4 维度与 Agent 入库路径的冲突

首次 commit 台账登记失败，报 `content_entry`。原因：`find_duplicate` 的
**D4 维度**以「内容条目 `paper-<sha12>.md` 存在」为判重依据，而本模式下条目
**恰恰就是 Agent 刚才写好的** → D4 必然命中 → 正常登记被误判为重复。

**修复**：`append_ledger(rec, allow_entry=True)` → 内部传
`find_duplicate(..., skip_content_entry=True)` 跳过 D4。默认 `False`，
原有上传链路行为不变。

### 踩坑：台账判重不能用 sha1

`_ledger_has(sha1)` 原按 sha1 比对台账，但历史台账行几乎无 sha1 字段
→ 误判「未登记」→ 重复追加。改为按 **repo_path 归一化**比对（该字段覆盖完整）。

### 踩坑：`verify` 与 `scan` 的 sha1 逻辑必须同改

`cmd_verify()` 早期版本直接 `sha = r.get("sha1")`，未做 eid 反推，
导致 entry/kp/facets/index 只统计到 **2 条**（真实为 1433 条），
`全绿: False` 且缺口虚高到 1455，完全不可信。

**修复**：在 `cmd_verify()` 中加入与 scan 一致的 eid 反推分支。
**教训**：凡是「台账行 → eid」的关联，必须走同一段逻辑，不能各写各的
（对应 §7.5 陷阱 1）。

---

## 8.7 第二批验证记录（5 篇 P0 · 2026-10-22 01:2x–01:3x）

窗口内执行（`window` 显示 `2026-10-21 23:00 → 2026-10-22 08:00`），全程零 DeepSeek 调用。

| sha12 | 题名（节选） | 刊 / 年 | 字符 | 知识点 |
|---|---|---|---|---|
| `460b7d404ccf` | High-Grade Copper and Gold … Postpotassic Chlorite-White Mica-Albite Stage, Far Southeast | Economic Geology / 2022 | 100184 | 6 |
| `26fc54a5c9f1` | Fluid Flow, Alteration and Timing of Cu-Ag Mineralization, White Pine | Economic Geology / 2023 | ~90k | 6 |
| `ff70bdf64e60` | Pluton Exhumation in the Precordillera of Northern Chile (17.8°–24.2°S) | Economic Geology / 2022 | 159937 | 6 |
| `1fd3aa4494be` | Fluid evolution and Cu-Co ore-forming processes, Katanga Copperbelt | Economic Geology / 2022 | ~50k | 6 |
| `9a8114798d28` | Mineralization of the Bayan Obo REE deposit by recrystallization and decarbonation | Economic Geology / 2022 | ~40k | 6 |

全部 `write` 校验通过 → `commit` 成功（分面回写 + 索引 7 行/篇 + 台账登记）；
`verify` 修正后统计：pdf 1457 / entry 1433 / kp 1430 / facets 1264 / index 1433。

**产物核对（抽样）**：`paper-460b7d404ccf` facets =
`deposit_type: [斑岩铜矿, 浅成低温热液]`、`process: [岩浆-热液演化, 蚀变分带, 成矿时代]`、
`method: [LA-ICP-MS]`、`commodity: [Cu, Au, Ag]`、`region: [环太平洋]`；
tags 12 条（受控规范词置顶 + 英文具体词保留）。

**注意**：`/api/entry/<eid>` 的响应体**不含** `facets`/`extraction` 字段，
校验分面与来源请以 `content/papers/<eid>.md` 的 frontmatter 为准。

---

## 9. 自检清单（每篇提交前逐条过）

- [ ] 标题取自 PDF 首页/元数据，未凭空生成
- [ ] `summary` 200–400 字，含关键数值与结论，全部可在原文找到
- [ ] `tags` 含 ≥1 个受控规范词 + 英文具体词，总量 ≤12
- [ ] 知识点 0–8 条（**没有下限**），标题具体、互不重复；0 条须能说清「为何不可复用」
- [ ] 每条知识点有 `evidence` 行号（或明确留空并说明）
- [ ] `extraction: agent` 已写
- [ ] `parent` 指向正确父论文 id
- [ ] commit 后索引行数 = N+1，台账有且仅有一行

---

## 10. 状态文件与断点续跑

- 队列：`state/agent_ingest_queue.jsonl`（scan 生成，含 sha12/journal/桶/路径）
- 状态：`state/agent_ingest_state.jsonl`（逐篇 stage 变更，追加式，最新一条为准）
- stage 流转：`pending → prepped → written → committed`（失败：`failed`）
- 断点续跑：任何时刻重跑 `scan` 会重建队列；`commit --all-written` 只处理
  stage=written 的条目，已 committed 的自动跳过（幂等）。

---

## 11. 相关文件

| 文件 | 作用 |
|------|------|
| `D:/ZSK/geokb/scripts/agent_ingest.py` | 本模式工具（scan/prep/write/commit/verify/status/window） |
| `D:/ZSK/geokb/scripts/meta_guard.py` | **元数据红线唯一判据**（标题/出版日期/分面），两套入库共用 |
| `D:/ZSK/geokb/state/agent_ingest_queue.jsonl` | 积压队列（scan 生成） |
| `D:/ZSK/geokb/state/agent_ingest_state.jsonl` | 逐篇 stage 状态（追加式，幂等） |
| `D:/ZSK/geokb/state/agent_ingest_wip/<sha12>.md` | 待读工作笔记（含原文片段与行号） + `payload-<sha12>.json` |
| `~/.workbuddy/skills/geokb-agent-ingest/SKILL.md` | 工作流 skill 入口 |
| `~/.workbuddy/skills/geokb-agent-ingest/references/entry_schema.md` | 条目字段契约 |
| `~/.workbuddy/skills/geokb-agent-ingest/references/taxonomy_facets.md` | 五分面受控词表用法 |
| `D:/ZSK/geokb/docs/用户上传文献功能_20261021.md` | 用户上传入口（Addition 归属）需求与验证 |

---

## 12. 元数据红线（2026-10-26 · **两套入库共用**）

> 2026-10-26 做了两次全库审计（出版日期 1940 篇、标题 1940 篇）。查出来的问题**全部出在入库环节**，
> 不是检索/展示的问题。DeepSeek 管线（`pdf_corpus_ingest` / `inbox_ingest`）与 Agent 亲自入库
> （`agent_ingest`）代码不同，但**元数据判据只有一份**：`scripts/meta_guard.py`。
> 改规则只改它，两边都自动生效 —— 严禁在任一侧另写一套。

### 12.1 标题（以原文为准，**不参考翻译版**）

| 编号 | 红线 | 例 |
|---|---|---|
| E1 | 空 / 短于 8 字 | — |
| E2 | 以引号开头 | `title: '"Tourmaline Breccias…'` |
| E3 | 哈希占位 | `331170fb1909` |
| E4 | slug 占位（无空格纯标识符） | `Huang_Weijia_W_Skarn` |
| E5 | 文件名当标题 | `2024-01-01_降珂楠等：…_hist` |
| E6 | 乱码（`�` / `Ã©` / 私有区 `\ue000-\uf8ff`） | — |
| W1 | 标题不在 PDF 前两页正文里（归一化、去行号后比对） | 多为译名或严重排版差异 |
| W2 | 标题文字种类与原文标题块相反 | 英文刊写中文译名 / 中文刊写英文 |
| W3 | 含换行/制表符 | 多行 YAML 没合并 |

**取标题的顺序**：原文首页最大字号块 > 期刊站点题录（台账）> 最后才用 LLM 抽取。
**英文刊必须是英文标题，中文刊必须是中文标题**；括号里的「（OGR 2022）」这类书目标注不许进标题。

### 12.2 出版日期（= 在线首发日）

| 编号 | 红线 |
|---|---|
| E7 | 缺失，或格式不是 `YYYY` / `YYYY-MM` / `YYYY-MM-DD` |
| W4 | PDF 里的 `Available online` / `PUBLISHED` 与库内年份不符 |
| W5 | 库内比原文更粗（能补到日就补到日，至少补到月） |

判定链（`guess_date` / `best_date` 已实现）：`Available online` → `PUBLISHED`/`Published online`/`First published`
→ 中文刊刊头「YYYY年M月」→ Elsevier `Accepted`（只到月）→ 刊头 `Month YYYY` → DOI 年份 → 首页首个年份。

**三个已知坑**（都已在代码里修好，改代码时别回退）：
1. Elsevier 首页的 `Available online` 可能在 **4000+ 字符**处 → 搜索窗口 9000，别缩回 3000；
2. 中文刊数字常被字距拆开（`2 0 2 6 年 9 月`）→ 先 `squeeze_digits()` 再匹配；
3. 台账旧行只有年份精度（`YYYY-01-01`）时，**以 PDF 实际日期为准**，别沿用。

### 12.3 分面

W6：条目没有 `facets` → 站点筛选里不会出现。业务管线自己不建分面，必须由收尾的
`build_facet_index.py` 补（已挂在 `inbox_ingest --run` 末尾）。

### 12.4 落地位置

| 环节 | 行为 |
|---|---|
| `pdf_corpus_ingest.write_entry()`（写盘后） | 报警，不阻断批次 |
| `agent_ingest.commit_shas()`（commit 前） | **硬红线拒绝提交**，写回 missing 列表 |
| `inbox_ingest --run`（收尾） | 批量检查并写日志「元数据红线：检查 N 条｜红线 M」 |
| 手动/定时巡检 | `python scripts/meta_guard.py --recent-hours 24` |

### 12.5 写 frontmatter 的两个硬约束

1. **替换 `title:` 必须整段替换**：原文件里 title 常被折成 2–3 行（YAML 多行标量），
   只改首行会留下孤立续行 → 整段 frontmatter 解析失败（2026-10-26 踩过，19 个文件当场崩）。
2. **PDF 标题块按行拼接要分语言**：英文断行处补空格（否则 `ofScheelite`、`mineralizingevents`），
   中文不补；并且要先剔除行号与 `ACCEPTED MANUSCRIPT` / `REVISION N` / `RETRACTED` 这类审稿戳。
