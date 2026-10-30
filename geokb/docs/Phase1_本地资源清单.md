# GeoKB 本地资源清单（Phase 1 产出）

- 盘点日期：2026-10-13
- 扫描方式：**全程只读**（ls/find/du/head，未修改、移动、删除任何文件）
- 扫描范围：`D:/ZSK`、`D:/PostNews/pdfs`（用户口述 `D://pdfs`，实测不存在，已定位为 `D:/PostNews/pdfs`）、`D:/PostNews/Translate`

---

## 1. 资源总览

| # | 资源 | 路径 | 规模 | 类型 | 摄取优先级 |
|---|---|---|---|---|---|
| R1 | 权威开放书籍（已转纯文本） | `Translate/books/` + `books_text/` | 4 部书，文本合计约 3.8MB | CC BY / 公共领域，合法可分发 | **P0** |
| R2 | 已验证中英术语表 | `Translate/corpus/glossary_verified.json` 等 | 293 条已验证 + 多份候选集 | 双语术语 JSON，含置信度 | **P0** |
| R3 | 镜下矿物系统鉴定手册 | `Translate/modules/notes/assets/handbook/` | 14 个 HTML（12 矿种系统鉴定页 + 索引 + 打印版），1.0MB | 用户自整理笔记（HTML） | **P0** |
| R4 | 斑岩 Cu-Mo 主题期刊论文库 | `PostNews/pdfs/sjiang_porphyry_CuMo_2026/pdfs/` | 819 篇 PDF，约 18GB | 期刊论文（2025–2026 为主） | P1（先元数据级摄取） |
| R5 | 精选翻译论文（含中文对照版） | `PostNews/pdfs/` 顶层 | 20 个 PDF（含 3 篇 `zh.mono` 中译版） | 用户精译/精读论文 | P1 |
| R6 | 手册源工程与打印版 PDF | `/d/exam/岩矿综合鉴定/mineral-microscopy-handbook/` | HTML 源 + A4 打印 PDF v1–v6 | 同 R3 的源工程 | P2（以 R3 为准，仅溯源用） |
| R7 | 田坝心钨矿项目数据 | `/d/science/2田坝心钨矿/` | 采样表 xlsx×2、CL/BSE 照片、柱状图等 | 在研项目一手数据 | P2（仅作项目语境，不入公开知识库正文） |
| R8 | GeoKB 工作区 | `D:/ZSK` | 仅任务书与本项目文件 | — | — |

## 2. 重点资源详情

### R1 权威开放书籍（Phase 3 内容骨架的最佳种子）

| 书 | 授权 | 文本 | 价值 |
|---|---|---|---|
| Earle《Physical Geology 2e》(BCcampus 2019) | CC BY 4.0 | 1.1MB 全文 + 168KB 术语表（668 条） | 覆盖矿物/岩石/构造/年代学等基础模块的权威框架 |
| Huston & Gutzmer《Isotopes in Economic Geology》(Springer 2023) | CC BY 4.0 | 1.7MB | 矿床学 × 同位素地球化学 × 定年，与 R4 论文域高度同源 |
| USGS Bulletin 1693《Mineral Deposit Models》(1986) | 公共领域 | 760KB | 85 个描述性矿床模型，矿床学模块标准来源 |
| USGS Bulletin 2012 (U-Th 矿物术语会议文集, 1992) | 公共领域 | 604KB | 铀钍矿床术语 |

附带价值：`books/BOOKS_MANIFEST.md` 已记录**合法来源 URL、下载日期、被排除来源及原因**——可直接作为 Phase 2 的先验：
- 用户明确排除 **Wikipedia / 百度百科**作为术语依据（数据质量差）；
- Mindat 对脚本请求返回 403；OpenGeology 有 Anubis 反爬门；`library.oapen.org` 经当前网络出口 502 —— Phase 2 连通性探测时应绕开或换通道。

### R2 术语资源（glossary 模块 + 检索同义词扩展的种子）

- `corpus/glossary_verified.json`：293 条已人工/流程验证术语（en/zh/confidence/group/释义），质量最高；
- 另有 `book_terms*.json`、`glossary_candidates.json`、`glossary_review_final.json` 等中间产物，需在 Phase 3 梳理血缘后择优合并；
- `Earle_..._glossary.txt`（668 条）可交叉扩充。

### R3 镜下鉴定手册（用户自整理，权威性由用户背书）

- 12 个矿种系统鉴定页：apatite（磷灰石）、augite（辉石）、biotite（黑云母）、calcite（方解石）、fluorite（萤石）、garnet（石榴子石）、hornblende（角闪石）、muscovite（白云母）、olivine（橄榄石）、orthoclase（正长石）、plagioclase（斜长石）、quartz（石英）+ 总索引 + A4 打印版；
- HTML 结构规整（附共享样式），可程序化抽取为 Markdown 条目，归入 mineralogy 模块；
- 源工程在 `/d/exam/岩矿综合鉴定/mineral-microscopy-handbook/`，与 notes 内副本一致（**未逐字节比对，Phase 3 抽取前校验**）。

### R4 期刊论文库（819 篇，18GB）

- 命名规范：`日_年_期刊_作者_编号.pdf`（如 `11_2025_MINERALS_Fu_min15050447.pdf`）——**期刊、年份可直接从文件名解析，无需开 PDF**；
- 抽样期刊分布：Minerals(MDPI)、Frontiers in Earth Science、Int. J. Earth Sciences、Mineralogy and Petrology、Mineralium Deposita；
- 主题（据目录名与抽样）：三江斑岩 Cu-Mo、矽卡岩、萤石地球化学、Co/Au/Ag-Zn-Pb 矿床；
- 摄取策略：**先元数据级**（文件名 → 条目存根：标题/期刊/年份/主题标签），全文择需在 Phase 3 二次摄取；18GB 全文入库既不必要也拖垮索引。
- **未验证**：819 篇的完整期刊/年份分布表（Phase 3 用脚本解析文件名生成，纯只读）。

### R5 精选翻译论文

- 20 个顶层 PDF 中含 3 篇 `no_watermark.zh.mono.pdf`（用户流水线产出的中文精译版）：锂同位素卤水、San Albino 金矿、矽卡岩绿帘石——中英对照双版本，适合作为 economic-geology/geochemistry 模块的高质量条目来源；
- `_download_log.json` 可能含来源 URL（Phase 3 读取核对）。

## 3. 风险提示

| 风险 | 等级 | 说明 |
|---|---|---|
| R4 论文版权 | 中 | 期刊论文仅作本地检索与摘要引用，正文不入库原文分发；条目只存题录+要点摘要 |
| notes 手册与 exam 源工程版本漂移 | 低 | 抽取前做一次一致性校验 |
| corpus 中间产物血缘复杂 | 低 | Phase 3 以 `glossary_verified.json` 为唯一高置信源，其余作候选 |

## 4. Phase 1 小结

- **完成项**：三处目录只读盘点；资源分级（P0×3 / P1×2 / P2×2）；确认 `D://pdfs` 实为 `D:/PostNews/pdfs`；发现 BOOKS_MANIFEST 已含 Phase 2 所需的合规先验与用户排除项（Wikipedia/百度百科）。
- **已验证结论**：839 篇 PDF（819 主题库 + 20 顶层）；4 部合法开放书籍已全部有纯文本；293 条已验证双语术语；12 矿种鉴定手册齐全；5555 服务与所有被扫文件零改动。
- **未验证项**：819 篇完整题录解析（Phase 3 脚本化）；notes 与 exam 手册一致性；`_download_log.json` 内容。
- **下一步**：进入 Phase 2 —— 先做外网连通性探测（章程 F 项），按结果决定权威线上资源抓取或降级路径。
