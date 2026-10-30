# GeoKB 地矿学科知识库 —— Agent 完整使用手册

> 只读 API · 无需凭据 · 限流 120 次/60 秒/每 IP
> 条目总数 862（生效 862）· 索引构建于 2026-10-14T07:40:33

## 0. 一句话流程

`检索拿 id` → `按 id 取全文` → `引用并标注来源`。

```
# 1) 检索（要正文必须 format=full）
curl "https://sjck.kust.work/api/search?q=斑岩铜矿&format=full&limit=3"

# 2) 用返回的 id 取单条全文
curl "https://sjck.kust.work/api/entry/paper-6011ea779131"

# 3) 不确定有哪些模块/标签时
curl "https://sjck.kust.work/api/modules"
curl "https://sjck.kust.work/api/tags"
curl "https://sjck.kust.work/api/facets"
```

## 1. 端到端示例（含真实字段）

请求：

```
GET https://sjck.kust.work/api/search?q=紫金山铜矿&format=full&limit=1
```

响应要点（已裁剪）：

```json
{
  "ok": true,
  "data": {
    "expanded_terms": ["紫金山", "铜矿"],
    "hits": [
      {
        "id": "paper-6011ea779131",
        "title": "中国紫金山斑岩-浅成低温热液Cu-Au-Mo-Ag矿床铜硫化物组成演化及其找矿意义",
        "module": "papers",
        "summary": "本文研究紫金山矿田斑岩-浅成低温热液系统中铜硫化物矿物…",
        "body_markdown": "---\nid: paper-6011ea779131\ntitle: …\n（完整 Markdown 正文）",
        "body_chars": 705,
        "confidence": "B",
        "status": "active",
        "source_tier": "T3",
        "sources": [{"name": "MINERALS", "path": "papers_repo/…/xxx.pdf"}],
        "score": 0.1808,
        "score_detail": {"bm25": 1.0, "vector": 0.3291},
        "matched_by": ["bm25", "vector"]
      }
    ]
  }
}
```

**关键字段读法**

| 字段 | 含义 |
|---|---|
| `id` | 条目唯一标识，供 `/api/entry/<id>` 使用 |
| `summary` | 摘要（约 200 字以内） |
| `body_markdown` | **完整正文（含 YAML frontmatter）**，仅 `format=full` 时返回 |
| `body_chars` | 正文字符数，可用于预估上下文占用 |
| `confidence` | A/B/C/D 可信度 |
| `source_tier` | T1~T4 来源分级，影响排序权重 |
| `sources` | 来源清单（期刊名 / 仓库路径） |
| `score` / `score_detail` | 融合得分与 BM25、向量分项 |
| `matched_by` | 命中通道（bm25 / vector） |

## 2. 检索参数全表

| 参数 | 默认 | 说明 |
|---|---|---|
| `q` | 空 | 查询词；留空则退化为纯元数据过滤 |
| `format` | `summary` | **`full` 才返回 `body_markdown`** |
| `limit` | `0` | `0` = 按相关度动态截断；否则固定条数（≤100） |
| `min_results` | 3 | 自适应模式最少返回条数 |
| `global_cap` | 50 | 自适应模式全局上限 |
| `min_score` | — | 绝对分数阈值（0~1） |
| `score_ratio` | — | 相对最高分的截断比例 |
| `modules` | — | 逗号分隔模块白名单 |
| `exclude_modules` | — | 逗号分隔排除模块 |
| `tags` | — | 逗号分隔标签（满足任一） |
| `exclude_tags` | — | 逗号分隔排除标签 |
| `confidence_min` | — | `A/B/C/D` 最低可信度 |
| `date_from` / `date_to` | — | 更新日期区间 `YYYY-MM-DD` |
| `sort` / `order` | — | 排序字段与方向 |
| `offset` | 0 | 分页偏移 |
| `fields` | — | 返回字段白名单，如 `id,title,score` |
| `expand` | true | 是否启用同义词/概念扩展 |
| `fuzzy` | true | 主结果不足时是否模糊兜底 |

> 检索采用 **BM25 + 向量归一化 → RRF 融合 → 可信度/状态/来源分级加权 → 动态截断**。
> 因此"结果条数不固定"是设计行为：相关才返回，不相关不硬凑。

## 3. 模块清单（实时）

| 模块 | 条目数 |
|---|---|
| `papers` | 748 |
| `glossary` | 43 |
| `economic-geology` | 23 |
| `mineralogy` | 13 |
| `external-feed` | 12 |
| `field-methods` | 10 |
| `petrology` | 4 |
| `geochronology` | 3 |
| `geophysics` | 2 |
| `structural` | 2 |
| `data-sheets` | 1 |
| `geochemistry` | 1 |

## 4. 高频标签（实时，Top 40）

- `U`（425 条）
- `Au`（244 条）
- `Pb`（237 条）
- `W`（180 条）
- `Fe`（172 条）
- `Cu`（167 条）
- `锆石U-Pb`（151 条）
- `微量元素`（110 条）
- `地幔过程`（98 条）
- `Mo`（94 条）
- `数值模拟`（94 条）
- `Co`（89 条）
- `Ni`（87 条）
- `Ta`（86 条）
- `稀土元素`（82 条）
- `Li`（81 条）
- `Ag`（79 条）
- `REE`（78 条）
- `变质作用`（74 条）
- `Zn`（74 条）
- `岩浆起源`（70 条）
- `特提斯`（68 条）
- `Lu-Hf`（64 条）
- `LA-ICP-MS`（63 条）
- `矽卡岩`（58 条）
- `Sn`（58 条）
- `安第斯`（56 条）
- `斑岩铜矿`（55 条）
- `Nb`（55 条）
- `电子探针`（49 条）
- `Sr-Nd同位素`（48 条）
- `蚀变分带`（47 条）
- `填图`（47 条）
- `术语`（43 条）
- `中英对照`（43 条）
- `青藏高原`（42 条）
- `油气藏`（41 条）
- `油气`（40 条）
- `浅成低温热液`（36 条）
- `遥感`（35 条）

## 受控分面（检索时可作为强过滤词）

- **矿床类型（deposit_type）**：矽卡岩、斑岩铜矿、油气藏、浅成低温热液、造山型金矿、热液脉型、伟晶岩、煤矿、VMS、MVT、沉积型矿床、IOCG、砂岩铀矿、红土镍矿
- **矿种（commodity）**：U、Au、W、Fe、Cu、Mo、Pb、Co、Ni、Ta、REE、Li、Ag、Zn
- **成矿过程（process）**：地幔过程、变质作用、岩浆起源、蚀变分带、流体包裹体、构造控制、成矿时代、岩浆-热液演化、成矿物质来源、沉积成岩作用、矿床保存、表生作用
- **方法（method）**：锆石U-Pb、微量元素、数值模拟、稀土元素、Lu-Hf、LA-ICP-MS、电子探针、Sr-Nd同位素、填图、遥感、机器学习、Pb同位素、S同位素、地球物理勘探
- **研究区（region）**：特提斯、安第斯、青藏高原、华北克拉通、北美、天山、澳大利亚、中亚造山带、欧洲、喜马拉雅、秦岭、冈底斯、东非裂谷、柴达木

## 6. 可信度与来源分级

| `confidence` | 含义 |
|---|---|
| A | 权威机构 / 经典教材 |
| B | 同行评议 / 已验证 |
| C | 可信二手汇编 |
| D | 未核实草稿 |

| `source_tier` | 含义 | 排序权重 |
|---|---|---|
| T1 | 官方 / 权威源 | 1.00 |
| T2 | 权威二手 | 0.82 |
| T3 | **论文来源（统一降权）** | 0.62 |
| T4 | 待核实 | 0.45 |

结论优先级：优先采信 T1/T2、confidence A/B 的条目；`papers` 模块（T3）可作线索，不宜单独作为定论依据。

## 7. 端点全表

| 方法 | 路径 | 用途 |
|---|---|---|
| GET | `/health` | 健康检查 |
| GET | `/manifest` | 总清单（模块、条目数、近期更新） |
| GET | `/openapi.json` | OpenAPI 规范（机器可读，权威） |
| GET | `/llms.txt` | 精简用法说明（本手册的摘要版） |
| GET | `/llms-full.txt` | 本完整手册 |
| GET/POST | `/api/search` | 检索主入口 |
| GET/POST | `/api/entries` | 无关键词元数据过滤/列表 |
| GET | `/api/entry/<id>` | 按 id 取单条全文 |
| GET | `/api/entry/<id>/related` | 相似条目推荐 |
| GET | `/api/module/<name>` | 模块内条目列表 |
| GET | `/api/modules` | 模块目录 |
| GET | `/api/tags` | 标签总表 |
| GET | `/api/facets` | 受控分面词表 |
| GET | `/api/synonyms/<term>` | 同义词展开 |
| GET | `/api/expand` | 术语/概念扩展 |
| GET | `/api/graph/<id>` | 条目关联图谱 |

## 8. 使用纪律

1. 引用库内内容请标注条目 `id` 与 `sources`。
2. 检索不到时**明确声明超出知识库范围**，不得编造地学内容冒充库内结论。
3. 合理缓存：同一 id 的全文短时间内不要重复拉取。
4. 尊重限流（120 次 / 60 秒 / 每 IP），批量任务请串行或降速。

## 9. 排错

| 现象 | 原因 | 处理 |
|---|---|---|
| 只有 `id` 和 `title`，没有正文 | 用了默认的 `format=summary` | 加 `format=full` 或改调 `/api/entry/<id>` |
| 返回条目数变化 | 动态截断是设计行为 | 用 `limit` 固定条数，或用 `min_results`/`global_cap` 调阈值 |
| 检索结果为空 | 关键词过窄或模块过滤过严 | 放宽 `q`、去掉 `modules`、开启 `fuzzy` |
| 429 | 触发限流 | 降速重试 |
| 论文条目排在前面但想要权威结论 | 排序已按 source_tier 加权 | 用 `confidence_min=B` 进一步过滤 |
