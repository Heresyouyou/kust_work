# Agent 夜间入库执行卡（长期提示词）

> 用途：每次进入「入库模式」先读本文件，然后按 FIFO 逐篇执行，**直到 P0 队列清空或用户叫停**。
> 上次更新：2026-10-24（v4：断点续跑统一入口 `scripts/ingest_resume.py`；prep 防重 + 批次清单 + 原子写）。
> 修改此文件即修改执行策略；**所有采用本执行卡的任务一律适用 v4 规则**。

## 0. 硬约束（不可协商）

1. **全程零 API**：不得调用 DeepSeek（不得跑 `kp_extract.py` / `pdf_corpus_ingest.py` / 任何 embedding 之外的远端 LLM）。
   本地 `index_bulk.py` / `index_one_entry.py` 用的是**本地 bge-small-zh 模型**，不算 API，允许。
2. **必须真正读懂全文，读多深不设上限**：不得只读摘要就写。先 `python scripts/ingest_digest.py next`
   拿导航（标题/锚点/摘要/结论/数值候选），再 `--full` 通读整篇，需要回看某段用 `--slices 起-止`（**次数不限**）。
   **禁止**为了省上下文而降级读法；质量优先。
3. **英文条目英文抽出**：`*_en` 字段从原文抽取，**严禁**由中文回译。
4. **无时间窗口**：任何时段都可入库（原 23:00–08:00 窗口、07:05 硬停均已取消）；`--force` 只打印 WARN，不阻断。
5. 拿不准就 `skip --sha <x> --reason "…"`，**不向用户提问、不停工**。
6. FIFO：**不跳序、不因刊别改顺序**，新下载排队尾。不必清空，能写多少写多少。
7. **绝不中断**：禁止向用户提问（「要不要继续」「这样写对不对」），禁止跑几篇就自动退出，禁止汇报后停下等指令。
   只要 P0 未空就**持续** scan → digest → 逐篇写；阶段进度写 `state/night_ingest_progress.md`，不打断会话。
   只有三种情况可停：① **P0 耗尽**；② **系统级错误无法自愈**；③ **用户明令停止**。
8. **产量目标（引导性）**：质量优先，达不到不追责、不补凑；**严禁**为凑数跳读、只读摘要或降低 G1–G7。
9. **角色边界（2026-10-24 强制）**：父代理只做内容分发（读状态 → `prep` → 派子代理 → 收一行回执），
   **不执行任何入库动作**（不读正文、不 write、不 commit、不 evict）；子代理独立完成 读→写→commit→evict，
   完成后立刻回一行回执。细则见下方「角色硬边界」。

## 0.1 上下文管理：不限制读入，靠出栈腾空间（v3 强制）

> **事实前提**：上下文是追加式的，**已进入的 token 无法被任何工具删除**。
> 因此「清掉已入库文献」只能靠 ①事前不让它进来（子代理隔离）②事后把磁盘诱因移走（出栈工具）。

### 三条铁律

| 编号 | 铁律 | 落地 |
|---|---|---|
| R1 | **单篇不设读入上限** | `ingest_digest.py <sha> --full`；`--slices` 次数不限 |
| R2 | **父上下文零正文** | 每篇一个 `Agent(general-purpose)`，父代理只收一行回执 |
| R3 | **commit 即出栈** | `ctx_evict.py evict <sha...>`，把笔记/payload/全文缓存沉到 `state/_cold/` |

```bash
python scripts/ctx_evict.py status                  # 「已入库却仍在热区」必须为 0
python scripts/ctx_evict.py evict <sha1> ... <sha6> # 每批 commit 后立即执行
python scripts/ctx_evict.py evict --committed       # 全库清理（首次接入/交接时一次）
python scripts/ctx_evict.py restore <sha>           # 返工复核时拉回
```

- 安全闸门：只对已生成 `content/papers/paper-<sha>.md` 的篇出栈；待办篇拒绝。
- 账本 `state/ctx_evicted.jsonl`；只移动不删除，`restore` 可回滚。
- 出栈后 `verify`/`digest` 自动走冷区兜底，`write` 走 PDF 解析缓存，均不受影响。

### 上下文里只留这些

| 序号 | 保留项 | 位置 |
|---|---|---|
| 1 | 原始规则 md（本执行卡 / `geokb-night-ingest` skill） | 全文保留 |
| 2 | 完成清单 | `state/night_ingest_progress.md`：一行一批 |
| 3 | 当前篇笔记 | `state/agent_ingest_wip/<sha12>.md`（只留当前这一篇） |
| 4 | 当前篇正文 | 读多深都行；commit 后立即出栈 |

**禁止**：已入库篇正文复读（副本已移走）、批量预读多篇正文、payload 内容复述、把 JSON 整段贴回对话。

### 断点恢复（唯一入口，任何中断点都适用）

```bash
cd /d/ZSK/geokb
venv/Scripts/python.exe scripts/ingest_resume.py     # 诊断 + NEXT ACTION
```

- **只信磁盘事实**：`content/papers/paper-<sha>.md`（条目）、`index/geokb.db`（索引）、
  `state/papers_index.jsonl`（台账）。三者齐备才算 committed。
- **不要**用 `agent_ingest.py status` 判进度（stage 来自事件日志，会严重低估：
  实测报 355，实际 1697）。**不要**用进度 md 判进度。
- 动作优先级（resume 已按此排序，照抄即可）：
  ① 有条目没索引 → `commit --sha ...`；② 热区残留 → `evict`；
  ③ 有 payload/wip 无条目且未 skip → 派子代理重做；④ P0 未空 → `prep` 再派。
- `prep` 已硬跳过「条目已存在」的篇，并落 `open_batch.json`；`commit` 会按磁盘事实收口批次
  （`closed` / `partial`）。看到 `partial` 就别开新批。
- 队列 bucket 滞后用 `ingest_resume.py --fix` 原地修，**不要用 scan 修**
  （NAS 掉线时 scan 会把队列清空）。
- 中断最多损失当前这一篇（在飞 payload 会被识别为孤儿并重派）。

### 角色硬边界（2026-10-24 用户要求，强制）

| 角色 | 只做什么 | 绝对不做 |
|---|---|---|
| **父代理** | `ingest_resume.py` 读状态 → `prep` 取批 → 派子代理 → 收一行回执 → 记进度 | 读正文、写 payload、`write`、`commit`、`evict`、`build_facet` |
| **子代理·单篇** | 读全文 → 写 payload → `write` → `commit(本篇)` → `evict(本篇)` → **立刻回一行回执** | `prep`、碰别的篇、删文件 |
| **子代理·单批次** | 同上，一次吃完整批 N 篇 | `prep`（批次由父代理取） |

父代理唯一的写操作是 `prep`：只生成工作笔记与批次清单，不产生条目、不建索引、不登台账。

### 子代理隔离（长跑默认）

每篇一个子代理（general-purpose），父代理只接收一行回执：

> 提示词模板：读 `~/.workbuddy/skills/geokb-night-ingest/SKILL.md` + 本执行卡；目标 sha=`<sha12>`；
> 只做这一篇；正文可通读全文（`--full`/`--slices`，深度不限）；产出 payload → `write --force`；
> **然后自己收口**：`commit --sha <sha12> --force`（不加 `--no-facet`）→ `ctx_evict.py evict <sha12>`；
> 回执 `sha | 条目+kp 数 | commit ok | evict ok | WARN | 成功与否`；不得 prep、不得碰其他篇。

子代理漏收口时，父代理**派一个收口子代理**去补 `commit` + `evict`，自己仍然不动手。



## 1. 每篇标准流程

```bash
# 1) 取队首
python - <<'EOF'
import json
rows=[json.loads(l) for l in open('state/agent_ingest_queue.jsonl',encoding='utf-8') if l.strip()]
st={}
for l in open('state/agent_ingest_state.jsonl',encoding='utf-8'):
    if l.strip(): r=json.loads(l); st[r['sha12']]=r['stage']
pend=[r for r in rows if st.get(r['sha12'],'pending')=='pending']
print(pend[0]['sha12'], pend[0]['title'][:40], pend[0]['path'])
EOF

# 2) 抽全文并合并（中文 PDF 逐字断行，必须 join）
python - <<'EOF'
import glob
t=open(glob.glob('state/text/<sha12>*.txt')[0],encoding='utf-8',errors='ignore').read()
m=''.join(l.strip() for l in t.splitlines() if l.strip())
open('state/text/_<sha12>_merged.txt','w',encoding='utf-8').write(m)
print('merged', len(m), '| 参考文献起点', m.rfind('参考文献'))
EOF

# 3) 通读（分段 print，读到「参考文献」为止；>4万字符的可只读至讨论末）

# 4) 写 payload → state/agent_ingest_wip/payload-<sha12>.json（用 Write 工具，勿用 heredoc）
# 5) 写入
venv/Scripts/python.exe scripts/agent_ingest.py write --sha <sha12> --payload state/agent_ingest_wip/payload-<sha12>.json --force
# 6) 每 5–8 篇攒一批
venv/Scripts/python.exe scripts/agent_ingest.py commit --all-written --no-facet --force
venv/Scripts/python.exe scripts/build_facet_index.py
```

## 1.5 上下文管理（长程连续入库的关键，必须遵守）

一次要跑几十上百篇，上下文会爆。规定**上下文里只允许存在三样东西**：

1. **本执行卡**（原始提示词，常驻，不随篇数增长）；
2. **更新后的待办列表**（只保留 `sha12 + 期刊 + 标题` 一行一条，最多 10 行）；
3. **当前这一篇的正文切片**（读几段就够，读完即弃）。

硬规则：

- **写完 commit 完一篇，就在上下文里把这一篇彻底扔掉**——不再回顾它的
  正文、不再回头改它的 payload、不把它带进下一篇的思考。只对「当前篇」保持注意力。
- **正文切片按段读、按段消化**，不要整篇一次性灌入；>4 万字符的只读到讨论末。
- **队列列表每次重新生成**：用 §6 的「查队首」命令重新打印，而不是复制上一次的输出。
  列表输出严格限制在 10 行以内（前 10 篇），不要一次列出几十条。
- **不要累积中间产出**：payload 写进文件就不再在对话里复述；`write` 的输出只看最后一行
  （`written <sha12> | entry + N kp | 校验通过`）。
- 若上下文已明显变长，**不回顾历史**，只按「执行卡 + 新列表 + 当前篇」继续往下跑。

## 2. payload 字段（缺一不可）

`title / title_zh / lang / tags / summary / summary_en / abstract / key_content / key_content_en /
core_methods / core_methods_en / study_area / study_area_en / scenarios / scenarios_en /
methods_zh / deposit_type_zh / notes / kps`

- 中文条目：`title_zh=""`、`summary_en=""`、`key_content_en=""`、`core_methods_en=[]`、`scenarios_en=[]`；
  **只有 `study_area_en` 写英文**（地名拼音/英文名）。
- 英文条目：反之，`*_en` 必填且**英文原文抽出**。
- `kps[]`：`title / body / type / reuse / why / value(0-10) / evidence / tags`
  - `type` ∈ 定量结果｜找矿标志｜成因机制｜参数条件｜方法技术｜时空格架｜区域实例
  - `reuse` ∈ 解释借鉴｜方法迁移｜类比参照｜标定参照｜本区事实
  - `evidence`：能定位就写 `L120-L135`；中文刊无行号可写「内容提要（关键词）」，只告警不拦
  - `value`：**必须拉开差距**，不要全给 8 分（9–10 必会被引用｜7–8 存档备查｜5–6 有点参考｜≤4 局部事实）

## 3. 知识点取舍判据（用户的口径）

> **只问一句：这条能否用于团队今后的研究？换一个研究区、换一个矿床，还用得上吗？**

- **0–8 条，没有下限**。凑数 = 不合格；2~3 条很正常；无价值的内容一条都不写。
- 「同类最高分 ×0.60」以下的是填充，不要。
- `本区事实`（样品描述、测试流程、设备参数）**每篇最多 1 条**，测试流程通常 0 条。
- 输出前自问：这条删掉会损失什么？答不上来 → 删。

## 4. 质量红线

- 数值必须**照抄原文**（含 ±、范围、单位、有效位），不得换算或四舍五入。
- 一句Dedicated着一结论：一个年龄、一个品位、一个机制、一个判据，不要一锅烩。
- body 100–260 字，含「对象 + 属性 + 数值或条件」，脱离本文仍能读懂。
- 禁「本文研究了…」「具有重要意义…」这类元叙述。
- 引用 Slovakia 表格数据要小心：`verify_numbers` 已支持表格裸小数，但若报 `unverified_number`，
  先 grep 原文确认原文写法，多数是换了单位或被 PDF 抽碎，改文案不要改数字。

## 4.5 质量门禁（2026-10-24 审计后新增，写前 + 写后双检）

> 起因：2026-10-24 对 134 篇 agent 条目按 8 项要求打分，**沿用旧 payload 的 3 篇综合分仅 0.59–0.73，
> 而本轮新读新写的 2 篇为 1.00**。差距全部来自下面 7 条，必须写前自检、写后复核。

| # | 门禁 | 硬要求 | 不合格实测表现 |
|---|---|---|---|
| G1 | **禁止复用旧 payload** | 每次 `prep` 的模板必须重新填写；目录里已有 `payload-<sha>.json` 时，**先逐字段重写或删除**，不得直接 `write` | 旧 payload 缺 `why`/`value` → `kp_why: null`、`kp_ai_value: null` |
| G2 | **kp 四要素齐全** | 每条必须有 `type` / `reuse` / `why` / `value`，缺一不可 | `kp_why: null`、`kp_ai_value: null` |
| G3 | **evidence 纯行号** | 只写 `L<起>-L<止>`，多条用英文逗号 `L10-L20,L35-L40`；**禁括号、章节名、中文注释** | `L74-L117（ABSTRACT）；L168-L217（INTRODUCTION）` |
| G4 | **分面非空** | `commit` 后 `facets` 至少 1 维非空；为空立即 `agent_facet_patch.py` 或补 tags 后重跑 `build_facet_index.py` | `facets: {}`（36dc92527d46） |
| G5 | **摘要长度** | 中文 `summary` 200–400 字；英文 `summary` **≤1000 字符且不得整段照抄 Abstract**；`summary_en` ≤300 字符按句截断 | 1659 / 1159 / 541 字 |
| G6 | **正文长度** | 每条 kp body **100–260 字**，超 260 必删、低于 100 必补 | 部分 280–330 字 |
| G7 | **圈号不贴数字** | 序号**不要**用 ①–⑧ 紧贴数字（`⑥950–900℃` 会被数字分词读成 `6950` 直接 FAIL）；用「（6）」「第6步」或加中文隔开 | 68754dfb65ac kp6 曾 FAIL |

**写前自检**（payload 落盘前）：G1 G2 G3 G5 G6 G7 全过。

**写后复核**（commit 前）：
```bash
grep -E "kp_why|kp_ai_value|evidence" content/kpoints/kp-<sha>-*.md   # 不得为 null；evidence 必须纯行号
grep -A3 "^facets:" content/papers/paper-<sha>.md                      # commit 后不得是 {}
```

## 5. 已知坑（改一处同步四处 / 环境）

- **复用旧 payload（最常见）**：见 G1/G2 —— 旧 payload 无 `why`/`value`，直接 `write` 会落
  `kp_why: null`、`kp_ai_value: null`，综合分被拉到 0.6 档。
- **evidence 写成中文注释**：见 G3 —— `L74-L117（ABSTRACT）` 这类格式检索可读性差。
- **圈号紧贴数字**：见 G7 —— `⑥950–900℃` 被数字分词读成 `6950`，硬闸门直接 FAIL。
- **summary 整段抄 Abstract**：见 G5 —— 英文 summary 达 1659 字符，远超要求。

- **本地有批量删除守卫**（单轮 50 次）：改知识点不许批量 `os.remove`，脚本内部已改为 rename 到
  `state/kp_stale/<sha12>/`。
- **Bash heredoc 里 `\s` `\d` 会被改写成 `/s` `/d`**：改 Python 文件一律用 Edit/Write，不要 heredoc。
- **JSON 里禁裸英文双引号**（会报 `Expecting ',' delimiter`）：中文引号「」或 `\"`。
- Windows 路径、注册表不要写 heredoc 里 —— 同上用 Write。
- `FOR PEER REVIEW` 稿、征稿简则、书评、编读往来 → `skip`。
- **payload 里不得出现原文没有的数值**——包括自己归纳的区间、阈值、上下界。
  硬闸门 `verify_numbers` 会逐个数比对，命中即 `FAIL ... 数值在原文未找到`，整篇不入库。
  典型踩坑：写「A/CNK 落在 1.05~1.15 的模糊区间」而原文只有 1.08~1.11 与 1.05~1.06 → 1.15 被判编造。
  **凡是想写「一般」「通常」「多介于」的概括性数值，一律改成不含数字的表述**（如「A/CNK 落在 1.1 附近」）。
- **补充材料坏档**：少数条目下载到的是 Supplementary Table 的独立 PDF（4 页全是数据表、无正文）。
  判据：`_night_text.py` 抽出的文本里找不到「摘要」，且开头就是「附表1 / Supplementary Table」。
  处理：`agent_ingest.py skip --sha <x> --reason "PDF为补充材料(无正文)"`。
  已发现实例：`c7d78ce4d258`（福州加良山叶蜡石矿）。建议定期用 PyMuPDF 扫全队列页数+首页文字批量筛查。
- **短篇栏目文章**（如《地球科学》「关键科学问题」，3 页）可入库，但要克制：
  只提取可迁移的成因判据，一般 3 条即可，不要为了凑数写背景介绍。
- 索引报「不全」时先重跑 `index_one_entry.py --prune <sha12>`，再看 Battery `commit` 日志。

## 6. 可用工具速查

| 命令 | 作用 |
|---|---|
| `scripts/agent_ingest.py status` | 进度水位（P0/P1/P2 分桶、committed 数） |
| `scripts/agent_ingest.py prep --sha <x>` | 生成工作笔记（路径/台账行） |
| `scripts/agent_ingest.py write --sha <x> --payload <p> --force` | 写条目 + 知识点（走 check_kp 质检） |
| `scripts/agent_ingest.py kps --sha <x> --payload <p> --force` | **只重写知识点**，不动条目/台账 |
| `scripts/agent_ingest.py commit --all-written --no-facet --force` | 攒批提交（分面+索引+台账） |
| `scripts/agent_ingest.py skip --sha <x> --reason "…"` | 跳过不入库 |
| `scripts/build_facet_index.py` | 全库分面回写（攒批后跑一次） |
| `scripts/kp_select.py audit-existing` | 存量知识点打分审计 |
| `scripts/kp_parity.py --sha <x>` | 对拍：API 模式与 Agent 模式产出是否一致 |

## 7. 汇报口径

**只有 P0 才增加论文条目数**。补知识点（P1）、补分面（P2）要分开说：
「新增条目 N 篇 / 补知识点 M 篇」，不要合并成一个数字。
