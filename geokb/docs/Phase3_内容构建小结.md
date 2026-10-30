# GeoKB Phase 3 内容构建小结

- 日期：2026-10-13
- 校验记录：`raw_sources/phase3_validation.json`（82 条目 / 0 错误 / 82 唯一 id）

## 1. 成果总览

| 指标 | 数值 |
|---|---|
| 知识条目（content/*.md） | **82**（唯一 id 82，schema 校验零错误） |
| 论文题录（index/papers_index.jsonl） | **819**（解析成功率 100%，title/doi 待 CrossRef 补全） |
| 模块覆盖 | **10/10** 全部一级模块有条目 |
| 高置信来源 | USGS×2 + ICS + Earle(CC BY) + Huston(CC BY) + 用户手册 + 已验证术语表 |

## 2. 模块条目分布与来源

| 模块 | 条目 | 来源构成 |
|---|---|---|
| glossary | 43 | 已验证术语表 293 条→16 分组；Earle 术语表 601 条→27 字母组 |
| field-methods | 10 | Earle 第 1/5/13–19/21 章（概论、风化、地表过程、区域实例） |
| mineralogy | 13 | 12 矿种镜下鉴定手册（用户整理，B 级）+ Earle 第 2 章 |
| petrology | 4 | Earle 第 3/4/6/7 章（三大岩类+火山作用） |
| economic-geology | 3 | USGS 1693 矿床模型库 + Earle 第 20 章 + 819 篇论文语料说明 |
| geochronology | 3 | ICS 国际年代地层表 v2024-12 + Earle 第 8/22 章 |
| geophysics | 2 | Earle 第 9/11 章（地球内部、地震）——专项权威源仍薄弱（已知项） |
| geochemistry | 1 | Huston & Gutzmer 同位素经济地质（书级条目） |
| structural | 2 | Earle 第 10/12 章（板块构造、地质构造） |
| data-sheets | 1 | USGS MCS2025（90+ 矿种产储量速查） |

## 3. 已验证结论

1. 82 条目全部通过 frontmatter 必填字段 / module 一致性 / confidence / status 枚举校验，id 无重复；
2. Earle 教材 22 章 100% 切分（期间修复两处 bug：Summary 标记误匹配、目录区 Versioning History 边界误判，均有重跑证据）；
3. 819 篇论文文件名解析成功率 100%（21 期刊子目录结构）；
4. 术语层共沉淀 **894 条术语**（293 双语已验证 + 601 英文定义，其中 384 条为新增不重叠术语）。

## 4. 已知限制（诚实声明，均为后续增量项）

| 限制 | 影响 | 计划 |
|---|---|---|
| Earle/Huston 正文为英文原文 | 中文查询靠同义词映射命中 | 术语层已兜底；高频章节按需摘译 |
| USGS 1693 OCR 噪声，仅 11 个模型名稳定抽取 | 矿床模型精读受限 | 对照原 PDF 精校高频模型（矽卡岩 W/Sn、斑岩） |
| Huston 为书级条目未章节切分 | 同位素细粒度检索偏弱 | 优先切分「成矿年代学」「流体示踪」两块 |
| papers 题录缺 title/doi | 论文检索只能按期刊/年份/作者 | Phase 4 后经 CrossRef 批量补全 |
| Earle 601 术语暂无中文对照 | 该部分同义词映射未追加 | 3h 后按需半自动补全 |
| 手册转换两处小瑕疵（step-tag 并入标题、图题浮动行） | 可读性略降 | v1 接受，必要时修正脚本重生成 |

## 5. 下一步

Phase 4 —— 检索与服务层实现：4a 技术验证三件套（sqlite-vec / jieba / bge-small-zh-v1.5 经 hf-mirror）→ 4b venv → 4c ingest → 4d retriever → 4e Flask API → 4f 守护 → 4g 冒烟测试。清单见 `docs/PROGRESS.md`。
