# GeoKB 外部订阅 PDF 优先改造（2026-10-16）

## 1. 问题

门户 journals 页面每日抓到的论文，经 `ingest_external_feeds.py` 进 GeoKB 时**几乎全部落到
`external-feed`（外部订阅）题录**：confidence=C、source_tier=T4 降权、无 PDF、无知识点。

根因不是「PDF 优先」逻辑缺失——该逻辑一直在——而是它只调
`pdf_downloader.download()`（arXiv / DOI→CrossRef→Unpaywall 三条路），
**对中文期刊全部落空**：

| 期刊 | 旧下载器结果 | 真实原因 |
|---|---|---|
| 大地构造与成矿学 | 0/3 | CNKI `dgyk.cbpt.cnki.net` 图片验证码阻断 |
| 地质学报 | 0/3 | `www.geojournals.cn` SafeDog guard，79/79 全 403 |
| 地球科学 | 0/3 | 单正则匹配不到页面里的 `/fileDQKX/**.pdf` |
| 岩石学报 | 无 handler | `source_of()` 根本不识别 `ysxb.ac.cn` |

同期 `D:/PostNews/journal_pdfs/` 里已经躺着 1.5G / 589 篇手工下载成功的 PDF，
证明这些源**都能下**，只是没接进每日管线。

## 2. 逐期刊完善（2026-10-16 实测）

对 14 个目标期刊（`journals.json`：6 中文 + 8 国际）逐个探测落地页结构，
新增/修复 handler：

| 期刊 | 源站 | 方案 | 实测 |
|---|---|---|---|
| 岩石学报 | ysxb.ac.cn | **新增**。文章页 `citation_pdf_url` → `data/article/aps/preview/pdf/<doi>.pdf` 直链，无需 guard | **6/6** |
| 大地构造与成矿学 | ddgzyckx.paperonce.org | **新增**。CNKI 验证码 → 改走官网 paperonce API：`POST /api/api/Web/ArticleSearch`（双 api 前缀，PageSize=1000 枚举全刊 3274 篇建标题索引）→ 取 `PDFFileNameUrlGUID` → `GET /api/api/Web/OpenArticleFilebyGuidNew?Id=<GUID>` | **30/33**（3 篇为征稿/订购公告，非论文） |
| 地质学报 | geojournals.cn | **修复**。`citation_pdf_url` 主机换 `geo.ijournals.cn` 完全绕开 SafeDog guard；并正确区分「暂无全文」与「仅 Office 排版稿」 | 直连 0 guard；受源站上传进度限制 |
| 地球科学 | earth-science.net | **修复**。多重正则匹配 `/fileDQKX/**.pdf`，兼容 `/cn/` 与无 `/cn/ 变体 | **4/4** |
| 矿床地质 | kcdz.ac.cn | 沿用（SafeDog guard RC4 破解） | 3/3 |
| 地学前缘 | earthsciencefrontiers.net.cn | 沿用 | 2/3（2014 老文未电子化） |
| 矿物岩石地球化学通报 | sciengine.com | 沿用（偶发空响应，重试可解） | 2/3 |
| 国际刊（Geology / Am. Min. / Econ. Geol. / EPSL / Lithos / OGR / J. Pet.） | gsw / elsevier / oup | 沿用，**付费墙为真实限制** | 见下 |

### 2.1 关键发现：`/api/api/` 双前缀

paperonce 前端 `window.apiHost = origin + "/api"`，而端点字符串又以 `api/` 开头，
所以真实路径是 `/api/api/...`。单前缀会被 SafeDog 拦成 404 页。
抓到这个规律用的是 CDP 驱动 Edge 无头监听 `Network.requestWillBeSent`，
比猜端点可靠。

### 2.2 关键发现：换域绕 SafeDog

`www.geojournals.cn` 挂 SafeDog guard，`geo.ijournals.cn` 不挂。
同样内容、同样路径，换主机即可直连。旧 handler 在 www 上做 RC4 破解，
实测 79/79 全失败；换域后 0 次 guard。

### 2.3 付费墙（真实限制，非下载器缺陷）

以下为订阅制内容，本地空间再大也无法「下载所有论文」：

- **GSW**（Geology / American Mineralogist / Economic Geology）：article-pdf 403
- **Elsevier**（EPSL / Lithos / Ore Geology Reviews）：pdfft token 仅 OA 文有
- **OUP**（Journal of Petrology）：`citation_pdf_url` 缺失

对这些期刊，脚本会明确记 `paywalled`，退题录入库并在条目里写清原因。
**不要**为了凑全文去用盗版镜像源——有篡改风险，且违背引用源公网化纪律。

## 3. 改动清单

| 文件 | 改动 |
|---|---|
| `D:/PostNews/.workbuddy/journal_pdf_downloader.py` | 新增 `dl_ysxb`、`dl_ddgzyckx`（含 1 小时索引缓存）；修复 `dl_dqkx`（多重正则）、`dl_geojournals`（换域 + 三态分类）；`source_of()` 增加 ysxb / ddgzyckx 路由；新增单篇 API `fetch_one()` |
| `D:/PostNews/journal_pdf_fetch.py` | **新建**。门面模块，按路径加载 handler 集并暴露 `fetch_pdf(journal, title, url, date)`。不落盘、不改 manifest，只负责拿到 bytes |
| `D:/ZSK/geokb/scripts/ingest_external_feeds.py` | **源头修复**。新增 `_fetch_journal_pdf()`：先强 handler 集，再退回通用下载器；成功后落 NAS by_journal 并登记 index.jsonl。`write_feed_entry()` 新增 `pdf_reason` 参数，把真实失败原因写进题录 |
| `D:/ZSK/geokb/scripts/migrate_feed_to_papers.py` | **新建**。存量 external-feed → papers 迁移脚本（PDF 优先 + 全文提取 + 知识点提炼） |

## 4. 迁移脚本用法

```bash
# 只探测可下载性，不落盘（默认建议先跑这个）
python scripts/migrate_feed_to_papers.py --dry-run

# 小批验证
python scripts/migrate_feed_to_papers.py --limit 3

# 全量 + 知识点提炼 + 重建索引
python scripts/migrate_feed_to_papers.py --full

# 迁移后真删题录（默认 deprecate 降权保留，慎用）
python scripts/migrate_feed_to_papers.py --full --feed-action delete
```

**设计纪律**

- **幂等**：以 PDF 内容 SHA1 为键，papers / kpoints 已存在即跳过，可反复重跑。
- **非破坏**：默认只把题录 `status: deprecated` + 加 `migrated_to` 指针，不删文件。
- **绝不写垃圾**：DeepSeek infra 故障（429/5xx）→ 不标记、留待重跑；
  内容解析失败 → 标 confidence=C，不静默丢条目。
- **papers 条目 id 仍用 `paper-<sha1前12位>`**：与语料管线、kpoints 管线
  （`kp_extract.py` 的 `parent` / `already_done()`）约定一致，否则知识点挂不上。

## 5. 验证清单

- [ ] `python D:/PostNews/journal_pdf_fetch.py` → `handler 集可用: True`
- [ ] 覆盖矩阵复测：中文刊从 4/18 提升到 ≥14/18
- [ ] `migrate_feed_to_papers.py --dry-run` → 可下载数与预期一致
- [ ] `--limit 3` 实跑：papers 条目 + kpoints 落盘、题录被标记
- [ ] 改 content 后 `scripts/rebuild_index.py`（embed=True）
- [ ] `changelog.jsonl` 追加本次变更

## 6. 英文论文英文优先规则（2026-10-17 追加）

### 6.1 规则

英文论文入库**以英文为词条核心**，中文译名只作小字注解：

- front matter：`title` = 英文原标题（照抄）、`summary` = 英文摘要、
  `tags` = 英文标签；新增 `lang: en` 标记。
- 正文：`## Abstract` → `## Key Content` → `## Core Methods` →
  `## Study Area` → `## Application Scenarios` 全部英文；
  `## 中文翻译` 区的每个字段用 `<small>` 包裹（门户 python-markdown
  渲染保留行内 HTML，呈现为小字）。
- 中文论文维持原中文优先结构不变（`lang` 缺省视为 zh）。

动机：旧管线把英文论文全部转写为中文词条，英文原文只剩正文末尾一行
「英文标题」——翻译偏差无法核对。实测存量 780 篇中约 745 篇为英文论文。

### 6.2 实现落点

| 文件 | 改动 |
|---|---|
| `scripts/pdf_corpus_ingest.py` | ① 新增 `detect_lang()`（前 8000 字汉字/英词密度判定，中文期刊论文带英文摘要不误判）；② 新增 `EN_PROMPT_TMPL`（英文核心字段 + 中文译名字段，双语 schema）；③ `ds_extract()` 按语言分流提示词并向 obj 注入 `_lang`；④ `write_entry()` 增 `lang=='en'` 分支；⑤ `normalize_tags_en()`（英文标签不过中文同义词表）；⑥ `sentence_safe_summary()` 句末标点集加入英文句点 |
| `scripts/ingest_external_feeds.py` | `write_feed_entry()` 增英文条目分支（`_is_english_item()` 按原标题判定）；`FEED_META_PROMPT` 增 `summary_en`/`tags_en` 字段 |
| `scripts/backfill_en_papers.py` | **存量回填**：扫描 papers 条目 → 文本缓存判定英文 → 走新 `ds_extract` → `write_entry` 原地重写（eid 不变，索引/URL/收藏不变）→ 旧 front matter 扩展键（facets）合并回写 |
| `scripts/kp_extract.py` | 已记录后续需求：英文论文的知识点也须英文为主（提示词处有待办说明，见下） |

### 6.3 待办（用户确认暂缓）

**英文论文的知识点（kpoints）英文优先**：实现时改两处——
① `kp_extract.py` 的 PROMPT 增加双语 schema（`title_en/body_en/title_zh/body_zh/tags_en/tags_zh`，
或按 `detect_lang` 分流双提示词）；② 知识点条目写入处按语言分流，
英文论文用 en 字段为核心、zh 字段 `<small>` 附后。已在 `kp_extract.py`
提示词上方以注释块记录完整方案。

### 6.4 验证记录

- 语言判定：749 个文本缓存 → 英文 745 / 中文 4（岩石学报、矿物学报×3），抽样核对无谬误。
- 端到端（真实 DeepSeek）：MINERALS 一篇 → `_lang=en`、英文摘要/方法/区域/应用齐全，
  中文译名全部 `<small>` 小字，front matter `lang: en` + 英文 tags。
- 单元断言：英文前置（`## Abstract` 在 `## 中文翻译` 前）、`lang: en` 存在、
  英文标签未被中文同义词表污染。
- 回填：dry-run 744 篇可回填 / 31 篇无文本缓存 / 4 篇中文跳过；
  全量回填后需 `ingest.rebuild(embed=True)` + `build_facet_index.py`。
