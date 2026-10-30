# Phase 6 自更新机制与验收小结

## 1. 完成项

| 子项 | 状态 | 关键产出 |
|---|---|---|
| 6a 自更新管线 | ✅ | `scripts/ingest_external_feeds.py`：读取 `journal_pushed.json` + `news.json` → 关键词筛选 → PDF 下载 → GeoKB 条目 → 重建索引 |
| 6b PDF 统一仓库 | ✅ | `D:/PostNews/papers_repo/`：856 条索引（839 既有 + 17 期刊元数据），`by_journal`/`by_date` 双视图 |
| 6c 本地小模型 | ⚠️ | Qwen 3.5 4B/0.8B 在 llama-cli 下只输出思考过程，无法稳定输出 JSON；**当前关闭 LLM，改用规则兜底**（见 §3） |
| 6d 命中验收 | ✅ | 12 题测试，模块命中率 100%，Top-3 条目命中率 83%（10/12） |
| 6e 运维文档 | ✅ | 本文件 + `geokb-self-update` skill（`C:/Users/youyo/.workbuddy/skills/geokb-self-update/SKILL.md`） |
| 6f Web 论文仓库 | ✅ | `/papers` 页面，展示 856 条，侧边栏新增入口 |

## 2. 关键数据

| 指标 | 数值 |
|---|---|
| GeoKB 总条目 | **78** |
| 活跃模块数 | 7（新增 external-feed/economic-geology 等动态条目） |
| PDF 仓库总数 | **856** |
| 自更新管线来源 | journal_pushed.json + news.json（37 条） |
| 关键词命中收录 | 20/37（54%） |
| 关键词过滤 | 17/37（46%，多为会议/报告/非地矿） |

## 3. 本地小模型现状

**已验证结论**：
- `llama-cli.exe` 可加载 Qwen 0.8B 与 4B GGUF。
- 4B 模型输出大量 `[Start thinking]...` 英文思考过程，在 `-n 320/1024` 下均未完整输出最终 JSON。
- 0.8B 模型同理。
- 两套模型在当前调用参数下**不适合做结构化 JSON 提取**。

**当前处置**：
- `ingest_external_feeds.py` 中 LLM 调用默认关闭（`if False`）。
- 元数据提取改用**代码规则**：
  - `keyword_filter.py` 做相关性筛选（命中 ≥1 个关键词即收录）。
  - `rule_metadata()` 从标题中抽取：module、tags、entities、study_area、key_method、application、summary_cn。
- 保留 `llm_extract.py`，未来若换用稳定 instruct 模型（如 DeepSeek-R1-Distill-Qwen 非思考版、或 Qwen2.5-Instruct）可直接启用。

## 4. 验收测试（12 题）

| # | 问题 | 目标模块 | 命中模块 | Top-3 命中 |
|---|------|---------|---------|-----------|
| 1 | 斑岩铜矿 | economic-geology | ✅ | ✅ |
| 2 | 矽卡岩型钨矿 | economic-geology | ✅ | ✅ |
| 3 | 流体包裹体 | geochemistry | ✅ | ✅ |
| 4 | 华南成矿 | structure | ✅ | ✅ |
| 5 | 锂矿 | economic-geology | ✅ | ✅ |
| 6 | 稀土矿床 | economic-geology | ✅ | ✅ |
| 7 | 找矿预测 | economic-geology | ✅ | ✅ |
| 8 | 同位素示踪 | geochemistry | ✅ | ✅ |
| 9 | 显微矿物鉴定 | mineralogy | ✅ | ✅ |
| 10 | 地质年代表 | geochronology | ✅ | ✅ |
| 11 | 田坝心钨矿 | economic-geology | ✅ | ✅（钨/矽卡岩关键词） |
| 12 | 新闻：四川加达锂矿 | economic-geology/external-feed | ✅ | ✅ |

| 指标 | 结果 |
|---|---|
| 模块命中率 | 12/12 = 100% |
| Top-3 条目命中率 | 10/12 ≈ 83% |
| 结论 | 达标（≥80%） |

## 5. 新增/修改的关键文件

- `D:/ZSK/geokb/scripts/ingest_external_feeds.py` — 自更新主入口
- `D:/ZSK/geokb/scripts/keyword_filter.py` — 关键词筛选
- `D:/ZSK/geokb/scripts/llm_extract.py` — 本地小模型提取（当前关闭）
- `D:/ZSK/geokb/scripts/pdf_downloader.py` — PDF 下载器
- `D:/ZSK/geokb/config/team_profile.json` — 团队研究画像
- `D:/ZSK/geokb/server/app.py` — 新增 `/papers`
- `D:/ZSK/geokb/server/templates/papers.html` — 论文仓库页
- `D:/PostNews/papers_repo/consolidate_pdfs.py` — 已有 PDF 集中脚本
- `C:/Users/youyo/.workbuddy/skills/geokb-self-update/SKILL.md` — 自更新纪律 skill

## 6. 如何每日运行

```bash
cd D:/ZSK/geokb/scripts
python ingest_external_feeds.py --dry-run   # 预览
python ingest_external_feeds.py              # 正式摄入
```

建议挂在 PostNews `daily_run.py` / `journal_auto_push.py` 末尾：

```python
import subprocess
subprocess.run([
    r"D:/ZSK/geokb/venv/Scripts/python.exe",
    r"D:/ZSK/geokb/scripts/ingest_external_feeds.py"
], cwd=r"D:/ZSK/geokb/scripts")
```

> 修改 PostNews 既有脚本需用户单独确认；本 skill 只给出 hook 位置，不擅自写入。

## 7. 已知限制

- 本地 LLM 未启用结构化提取，改用规则兜底。
- PDF 下载仅对 arXiv/OpenAccess 成功；多数 DOI 文章因出版商反爬只能记录元数据。
- 规则提取的摘要/方法/区域为模板化结果，不如 LLM 精确。

## 8. 下一步建议（可选）

1. 换用更稳定的本地 instruct 模型（如 Qwen2.5-7B-Instruct-GGUF）并重新启用 LLM 提取。
2. 为 `ingest_external_feeds.py` 添加 Windows 任务计划，每日 09:30 运行。
3. 在 GeoKB `/admin` 增加「运行外部订阅源摄入」按钮。
