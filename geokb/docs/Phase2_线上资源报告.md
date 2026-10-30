# GeoKB Phase 2 线上资源获取报告

- 日期：2026-10-13
- 原则：仅抓取公开、允许学术使用的资源；每条记录来源 URL / 机构 / 获取日期 / 可信度。

## 1. 连通性探测矩阵（已验证，原始记录 `raw_sources/connectivity_probe_20261013.json`）

| 站点 | 结果 | 结论与处置 |
|---|---|---|
| CrossRef API | ✅ 200（用户此前已验证，本次复测一致） | 可用 → Phase 3 为 819 篇本地 PDF 补全 DOI 题录的官方通道 |
| USGS pubs.usgs.gov | ✅ 200 | 可用 → 已下载 MCS2025 |
| ICS stratigraphy.org | ✅ 200（页面为 Nuxt SPA） | 可用 → 直链下载地层表 v2024-12 |
| hf-mirror.com | ✅ 200（1.4s） | 可用 → Phase 4 嵌入模型经此镜像下载（huggingface.co 主站经当前出口 502） |
| huggingface.co | ❌ 502 Bad Gateway | 不可直连，走镜像 |
| IMA ima-mineralogy.org | ❌ 证书链失败 + 跳过校验后 403；**用户已明确拒绝该域的网络请求** | 不采用，不再重试 |
| RRUFF（IMA 名录镜像） | ❌ 302 死循环 | 不可用 |
| Mindat / OpenGeology / oapen | ❌（Phase 1 先验：403 / 反爬门 / 502） | 不可用，未重复尝试 |

## 2. 本轮下载的权威资源（`raw_sources/`，登记于 `sources_registry.json`）

| 资源 | 机构 | 大小 | 授权 | 可信度 | 用途 |
|---|---|---|---|---|---|
| Mineral Commodity Summaries 2025 | USGS（美国联邦政府出版物，公共领域） | 10.4MB | 公共领域 | A | data-sheets 模块：90+ 矿种的产量/储量/用途数据速查；economic-geology 佐证 |
| 国际年代地层表 v2024-12 | ICS / IUGS | 333KB | 学术公开 | A | geochronology 模块：标准年代地层框架（官网 SPA 下最新可得直链版本） |

## 3. 学科框架来源的补齐策略（结合 Phase 1 本地资源）

| 模块 | 权威框架来源 | 状态 |
|---|---|---|
| mineralogy | Earle 教材 + USGS Bulletin 2012 + 本地 12 矿种鉴定手册（IMA 名录不可得，以其教材级命名为准） | 齐备 |
| petrology / structural / field-methods / geochemistry 基础 | Earle《Physical Geology 2e》（CC BY，668 条术语表） | 齐备 |
| economic-geology | USGS Bulletin 1693（85 个矿床模型）+ Huston & Gutzmer 同位素经济地质 + MCS2025 | 齐备 |
| geochronology | ICS 地层表 v2024-12 + Huston & Gutzmer 定年章节 | 齐备 |
| geophysics | Earle 地球物理章节（基础框架）；**专项权威源待后续补充** | 部分 |
| glossary | glossary_verified 293 条 + Earle 668 条 + USGS 2012 | 齐备 |
| 前沿（近 5 年） | 本地 819 篇 2025–2026 期刊论文（斑岩 Cu-Mo / 矽卡岩 / 萤石 / 关键金属） | 齐备 |

**结论**：除 geophysics 仅具基础框架外，其余模块的权威框架来源已齐备；geophysics 的专项补强（如 SEG/USGS 开放课件）列为 Phase 3 后的可选增量，不阻塞主线。

## 4. Phase 2 小结

- **完成项**：7 站点连通性矩阵（含用户确认的 CrossRef 复测）；下载 USGS MCS2025 + ICS 地层表两份 A 级资源并登记来源；确认 hf-mirror 可作为嵌入模型下载通道；按用户指示放弃 IMA。
- **产出文件**：`raw_sources/usgs_mcs2025.pdf`、`raw_sources/ics_chronostrat_chart_v2024-12.pdf`、`raw_sources/sources_registry.json`、`raw_sources/connectivity_probe_20261013.json`、`scripts/probe_connectivity.py`、`scripts/probe_retry.py`、`scripts/fetch_authoritative.py`。
- **已验证结论**：见 §1 矩阵（全部有本次实测记录）。
- **未验证/待办**：819 篇 PDF 的 CrossRef 题录批量匹配（Phase 3 执行，需限速合规调用）；geophysics 专项权威源。
- **下一步**：Phase 3 —— 知识库内容构建：模块目录落盘、四部书籍按模块切分为 Markdown 条目、术语词典合并、手册 HTML 抽取、论文元数据级摄取。
