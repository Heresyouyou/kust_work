# Phase 7：PDF 语料知识化设计方案

> 目标：用本地模型把 `D:/PostNews/papers_repo` 全部 PDF 论文筛选、提取、整理为 GeoKB 知识库条目。
> 原则：**可以慢，不能乱** —— 断点续跑、每步落盘、失败可回溯、不污染现有索引。

## 1. 语料事实（已盘点）

| 项 | 值 |
|---|---|
| 唯一 PDF 数 | **749**（`by_journal/`，`by_date/` 为硬链接同一 inode，不重复处理） |
| 磁盘占用 | 16 GB（含硬链接视图） |
| 主题分布 | 以斑岩 Cu-Mo 为主，覆盖 LITHOS / MINDEP / GEOLOGY / PRECAMB 等 14 个期刊目录 |
| 元数据 | `papers_repo/index.jsonl`（856 行，含 title/journal/date/repo_path） |

> **2026-10-14 模型决策变更**：本地 Qwen3.5-4B 方案已废弃（曾发生 llama-server 崩溃导致 47 篇降级条目的事故，已全部清除）。**统一改用 DeepSeek API**（`deepseek-chat` = V4-Flash 非思考模式），Key 复用 `D:/PostNews/Translate/.deepseek_key`。749 篇成本估算 ≈ ¥3.5（输入 ~2.8M + 输出 ~0.37M token）。提取速度 ~2 s/篇，质量优于本地 4B。本地模型不再参与任何入库内容生成；检索嵌入模型 bge-small-zh 属检索基础设施，不在此列。

## 2. 分类架构（三层）

### 2.1 模块层（module）
全部论文条目归入新模块 **`papers`**，与既有 11 个模块并列。
- 理由：论文是「文献证据」型知识，与 `economic-geology` 等「概念知识」性质不同；独立模块便于 AI 按 `module=papers` 精确过滤，也避免 749 条冲散概念模块的检索排序。
- 论文中提炼出的**通用概念**不进本管线（后续可由人工/Agent 从论文条目二次提炼到概念模块）。

### 2.2 主题标签层（tags，两级体系）
每篇 3–8 个标签，从受控词表选取，保证可聚合：

| 一级类 | 受控词（示例） |
|---|---|
| 矿床类型 | 斑岩铜矿、斑岩钼矿、矽卡岩、浅成低温热液、IOCG、造山型金、VMS、SEDEX、伟晶岩 |
| 成矿过程 | 岩浆-热液演化、流体包裹体、蚀变分带、成矿物质来源、成矿时代、构造控制、岩浆起源 |
| 方法技术 | 锆石U-Pb、Re-Os、Lu-Hf、Sr-Nd同位素、微量元素、流体包裹体测温、遥感、物探、机器学习、数值模拟、实验岩石学 |
| 矿种 | Cu、Mo、Au、Ag、W、Sn、Pb-Zn、REE、Li |
| 研究区 | 冈底斯、青藏高原、秦岭、长江中下游、安第斯、中亚造山带、特提斯、环太平洋 |

LLM 输出标签后过受控词表映射（同义归并），词表外新标签保留但记入 `new_tags` 供人工审。

### 2.3 元数据层（frontmatter）
```yaml
id: paper-<sha1前12位>
title: 论文标题
module: papers
tags: [受控标签...]
summary: 120字内中文摘要
confidence: B（LLM成功）/ C（规则兜底）
status: active
updated: YYYY-MM-DD
sources: [{name: 期刊名, path: 仓库相对路径}]
journal: 期刊目录名
year: 发表年
extraction: llm | fallback
```

## 3. 每篇提取 Schema（LLM 输出契约）

```json
{
  "title_en": "英文原标题",
  "title_zh": "中文译名",
  "key_content": "关键内容：2-4句，本文解决了什么问题、得到什么结论",
  "core_methods": ["核心方法1", "核心方法2"],
  "study_area": "研究区域（无则填'全球/综述'）",
  "application_scenarios": ["应用场景：找矿预测/成矿理论/方法参考..."],
  "deposit_types": ["斑岩铜矿"],
  "commodities": ["Cu","Mo"],
  "tags": ["..."]
}
```
正文 Markdown 结构：`## 关键内容 / ## 核心方法 / ## 研究区域 / ## 应用场景 / ## 原文摘要(截断) / ## 来源`。

## 4. 处理流水线（6 阶段，全部可断点续跑）

```
S1 文本提取   by_journal/*.pdf → PyMuPDF → state/text/<hash>.txt
              字数<1500 → 标记 needs_ocr，跳过（扫描版，不入库）
S2 去重登记   内容SHA1去重；关联index.jsonl取journal/date；
              state/pdf_state.jsonl 每篇一行（append-only）
S3 智能截断   定位 Abstract/Introduction/Conclusions 段落 +
              首页/末页文本 → 压缩至 ≤3500 tokens
S4 LLM 提取   llama-server(Qwen3.5-4B, GPU, enable_thinking=false)
              /v1/chat/completions → JSON 强校验；
              失败2次 → 规则兜底（标题+摘要首段）extraction=fallback
S5 条目生成   content/papers/paper-<hash12>.md（幂等覆盖写）
S6 索引+质检  全部完成后调 rebuild_index.py（单实例锁）；
              抽样10篇人工可读性检查；输出统计报告
```

**断点机制**：`state/pdf_state.jsonl` 记录 `{hash, status, ts}`；重跑时 status=done 直接跳过。中断后重跑同一条命令即可续跑。

**llama-server 生命周期**：由管线脚本作为子进程拉起（`--port 58120`，仅监听 127.0.0.1），批处理结束或异常时 `terminate()`。属于任务内部临时进程，不做常驻守护。

**并发与限速**：单 worker 串行（GPU 单卡，串行即满负载）；每篇约 15–40 s，749 篇预估 **4–8 小时**。

## 5. 质量与一致性保障

1. **不污染检索**：失败篇目不入库；fallback 条目标 confidence=C，检索排序天然降权。
2. **索引一致性**：只在全部写完后重建一次索引（rebuild 带文件锁），不在批处理中途重建。
3. **幂等**：条目文件名由内容 hash 决定，重跑覆盖写同一文件，不产生重复条目。
4. **可审计**：LLM 原始输出落盘 `state/llm_raw/<hash>.json`；统计报告含 llm/fallback/needs_ocr/failed 四类计数。
5. **零干扰**：管线只写 `content/papers/`、`state/`、`logs/`，不碰其他模块；索引重建在 5556 运行期间安全（ingest 重建为删表重建，秒级，检索短暂空窗可接受；若需零影响可在 active=0 窗口执行 S6）。

## 6. 验收标准

- 749 篇全部有终态（done / fallback / needs_ocr / failed），无悬挂状态。
- LLM 提取成功率 ≥ 85%（剩余走 fallback）。
- 抽样 10 篇：标题、方法、研究区与原文一致，无幻觉硬伤。
- 检索验证：`module=papers` 过滤可用；「斑岩铜矿 锆石U-Pb」类查询 Top-5 命中相关论文。

## 7. 执行结果（2026-10-14 04:21 完成，DeepSeek 版）

| 指标 | 结果 |
|---|---|
| 处理 | 746 篇（+此前样本 3 篇），耗时 36.8 分钟，~1218 篇/时 |
| 终态 | **llm=749，fallback=0，needs_ocr=0，failed=0**，成功率 100% |
| 入库条目 | **748 条**（749 篇中 1 对内容完全重复，SHA1 合并） |
| 全量质检 | 748/748 无问题（无 JSON 残留/乱码/重复/字段缺失）；key_content 均值 240 字 |
| 抽样比对 | 3 篇标题/方法/研究区与原文一致，含量化结论（年龄 ±Ma、矿体数量等） |
| 索引 | papers 模块 748 条已重建入库；`module=papers` 过滤与混合检索验证通过 |
| 成本 | 实测约 36.8 分钟完成，符合 ¥3.5 量级估算 |
| 高频标签 | Cu(120)、Au(102)、锆石U-Pb(72)、斑岩铜矿(46)、Zn(41)、Mo(40) |
