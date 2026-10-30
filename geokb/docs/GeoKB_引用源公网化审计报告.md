# GeoKB 全库引用源公网化审计报告

> **域名切换说明（2026-10-15）**：本报告修复时写入库内的 URL 为
> `https://lib.kust.work/...`；同日因该域名易被外部 AI 误判为
> "昆明理工大学图书馆"域名，公网域名已切换为 **sjck.kust.work**，
> 库内 5166 处 URL 已批量迁移（见 `scripts/_rename_origin.py`，
> 备份 `state/backup_rename_20261015/`）。文中 URL 按当时实况保留。

- 日期：2026-10-15
- 范围：全部 5277 个活跃条目、5277 条 sources（修复后 5325 条）
- 目标：**所有引用源在公网可访问**——任何外部 AI 或读者只凭 URL 即可取到原始材料，不依赖内网盘符、本机路径或私有协议

---

## 1. 审计方法

| 步骤 | 工具 | 说明 |
|---|---|---|
| 来源提取 | `scripts/_audit_sources.py` | 从 `index/geokb.db` 读出全部 sources，按类型分类 |
| 外链探测 | `scripts/_check_urls.py` | 8 线程并发，HEAD→GET(Range) 回退，跟随重定向，浏览器 UA |
| 修复执行 | `scripts/_fix_sources.py` | 五类修复，前置全量备份 |
| 正文校验 | `scripts/_verify_bodies.py` | 修复后文件与备份正文逐字节比对 |
| 索引重建 | `scripts/rebuild_index.py` | `ingest.rebuild(embed=True)`，5277 条目全部嵌入 |
| 线上抽查 | `https://lib.kust.work/api/entry/<id>` | papers / kpoints / glossary / mineralogy / economic-geology 各模块实拉 |

---

## 2. 修复前状态（5277 条来源）

| 类型 | 数量 | 占比 | 公网可达？ |
|---|---|---|---|
| internal_pdf（内部盘符路径） | 5163 | 97.8% | 否（`path: S:/GeoKB/papers_repo/...`，仅内网） |
| ext_url（外部 URL） | 85 | 1.6% | 是（37 个唯一 URL，见 §3） |
| local_fake（`local://` 伪协议） | 29 | 0.5% | 否（任何设备都无法解析） |
| empty（无来源） | 0 | 0% | — |

**结论：97.8% 的引用源对外部 AI 是死链。** 外部模型即使检索到条目，也拿不到原文 PDF。

## 3. 37 个外链可达性检测

- **32 个可达**（HTTP 200/206，含出版商页面、USGS、Springer、教材站等）。
- **5 个返回 403**，均为出版商 Cloudflare 挡爬虫，**经外部视角 WebFetch 实测内容完整可读**（普通浏览器/AI 抓取不受影响）：
  - stockhouse.com 新闻页
  - doi.org/10.5382/econgeo.5253
  - doi.org/10.2138/am-2025-9723（已撤稿论文，见 §4-E）
  - doi.org/10.2138/am-2025-9955
  - doi.org/10.2138/am-2026-e1111017（撤稿通知）

## 4. 五类修复明细（`scripts/_fix_sources.py`，已执行）

前置备份：`state/backup_content_20261015/`（5288 个文件，逐字节可回滚）。

| 类 | 对象 | 数量 | 修复内容 |
|---|---|---|---|
| A | papers + kpoints 的 path-only sources | 5163 | 追加 `url: https://lib.kust.work/api/entry/<id>/pdf` + `accessed: 2026-10-15`（**保留原 path**，内网溯源不丢） |
| B | mineralogy 的 `local://PostNews/Translate/modules/notes/assets/handbook/<f>` | 12 | 替换为门户真实资产 URL `https://kust.work/api/notes/assets/handbook/<f>`（实测 206） |
| C | glossary 的 `local://` 伪链接 | 16 | 替换为 4 个真实公开来源：Earle《Physical Geology》(opentextbc.ca)、Huston & Gutzmer 矿床学专著 (Springer)、USGS B1693、USGS B2012 |
| D | papers-corpus 汇总条目 | 1 | sources 指向 `https://lib.kust.work/api/module/papers`，正文追加"公开访问说明" |
| E | feed-521d5b18063d（引撤稿论文） | 1 | 正文前置撤稿警示 blockquote，引撤稿通知 `https://doi.org/10.2138/am-2026-e1111017`，注明"请勿作为证据引用" |

frontmatter 处理：PyYAML 往返，`width=100000` 防长折行破坏空格，日期字符串加引号保类型；正文除 D/E 外原样拼回。

## 5. 修复后状态（5325 条来源）

| 类型 | 数量 |
|---|---|
| ext_url（外部 URL） | **5325（100%）** |
| local_fake / internal_pdf / empty | **0** |

- 唯一外部 URL：5214 个
- 无任何来源的条目：0
- 每条内部 PDF 均有公网 API 入口：`https://lib.kust.work/api/entry/<id>/pdf`（免鉴权、支持 Range）

来源总数 5277→5325 系 C 类将 1 个伪链拆为多个真实来源所致。

## 6. 验证记录

| 验证项 | 方法 | 结果 |
|---|---|---|
| 正文完整性 | 修复后 vs 备份逐字节比对（5277 文件） | 5275 一致；仅 D/E 两个故意追加正文的条目变动（预期内） |
| 索引重建 | `rebuild_index.py`（embed=True） | 5277 条目全部嵌入，dim=512，built_at 2026-10-15T01:12:52 |
| 来源复审 | 重跑 `_audit_sources.py` | ext_url 5325 / 其余 0 |
| 公网线上抽查 | `https://lib.kust.work/api/entry/<id>` | papers/kpoints/glossary/mineralogy/economic-geology 五模块 sources 全部带 http URL |
| 新 URL 可达性 | curl Range 0-1024 | kust.work 资产 206；papers/kpoints PDF 路由均 206 |
| 整库 PDF 供数 | （本轮前序）删除本地副本后公网下载 200、MD5 与 NAS 一致 | 通过 |

## 7. 遗留事项（不阻塞）

1. **5 个 Cloudflare 403 链接**：出版商反爬机制，浏览器/AI 实际可读，保留原 URL 不作替换（换镜像源有篡改风险）。
2. **撤稿论文**（am-2025-9723）：条目保留（status: active）作为文献追踪对象，正文已有警示；后续如建"撤稿论文"专题可再聚合。
3. **备份目录** `state/backup_content_20261015/`：确认无回归后由用户决定是否清理。

---

## 附：复现命令

```bash
cd /d/ZSK/geokb
./venv/Scripts/python.exe scripts/_audit_sources.py    # 来源分类复审
./venv/Scripts/python.exe scripts/_check_urls.py       # 外链可达性
./venv/Scripts/python.exe scripts/_fix_sources.py      # 五类修复（已执行，勿重跑）
./venv/Scripts/python.exe scripts/_verify_bodies.py    # 正文完整性
./venv/Scripts/python.exe scripts/rebuild_index.py     # 索引重建（须 embed=True）
```
