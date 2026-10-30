# -*- coding: utf-8 -*-
"""南亚 / 东南亚区域定向「待抓取」增量清单（只列清单，不爬取）

为什么单独一支
--------------
现有 `history_manifest_10y.py` 的区域词表只有三江 / 青藏 / 特提斯一带（团队原方向），
完全没有南亚东南亚。实测现有清单 3149 条里，泰国 2、缅甸 4、印尼 5、菲律宾 4、
马来西亚 1、婆罗洲 2 —— 合计不到 30 条，等于这块是空白。

CrossRef 的全域关键词检索**不可用**：查 `Myanmar porphyry copper` 会把 Myanmar
整个丢掉，返回全球铜矿论文（实测）。所以只能走「区域刊全量枚举 + 本地标题精确过滤」。

两条口径
--------
1. **必须同时命中「区域词」与「学科词」**：只提地名（印度、缅甸）会把地震台网、
   水文、滑坡、遥感这类完全无关的论文捞进来；只有学科词就是全球论文。
2. **剔除非正刊稿**：评论/回复/勘误/撤稿/专辑导读/书评/讣告/总目次/征稿简则，
   判据唯一来源 `D:/PostNews/journal_filter.classify_article`。

去重五路：台账有 PDF｜content/papers 词条｜现有待爬清单｜已完成清单｜10 年暂存区。

输出（独立暂存区，绝不覆盖正在消费的 history_manifest.jsonl）
----
· state/history_manifest_region.jsonl      增量（可 --merge 注入）
· state/history_manifest_region.html       人读清单（可搜索/分栏）
· state/history_manifest_region_stats.json
· state/history_manifest_region_skipped.jsonl  剔除非正刊稿明细
· logs/清单南亚东南亚_YYYYMMDD.md

用法
----
    python scripts/history_manifest_region.py                 # 生成暂存区
    python scripts/history_manifest_region.py --dry-run       # 只统计
    python scripts/history_manifest_region.py --refresh       # 重抓题录
    python scripts/history_manifest_region.py --merge         # 并入待爬队列
    python scripts/history_manifest_region.py --journals      # 只看刊表
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import time
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path

BASE = Path('D:/ZSK/geokb')
SCRIPTS = BASE / 'scripts'
STATE = BASE / 'state'
LOGS = BASE / 'logs'
MANIFEST = STATE / 'history_manifest.jsonl'
STAGE = STATE / 'history_manifest_region.jsonl'
STAGE_HTML = STATE / 'history_manifest_region.html'
STAGE_STATS = STATE / 'history_manifest_region_stats.json'
SKIPPED = STATE / 'history_manifest_region_skipped.jsonl'
STAGE10Y = STATE / 'history_manifest_add10y.jsonl'
RAW_CACHE = STATE / 'manifest_raw'

sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, 'D:/PostNews')

import history_manifest as hm                      # noqa: E402
import history_manifest_10y as h10                 # noqa: E402  复用去重/打分/绘图内核
from journal_filter import classify_article        # noqa: E402

# ── 目标期刊（南亚东南亚区域刊 + 构造/地化/定年高价值刊）───────────────
# 全部经 CrossRef /journals 接口核过：ISSN 有效、近 10 年有 data。
# reach 由 hm.reach_of(URL) 自动判，SD 系（10.1016）会自动标 sd=True 沉底。
REGION_JOURNALS = [
    # —— 区域核心：南亚东南亚论文主阵地 ——
    ('Journal of Asian Earth Sciences', '1367-9120'),
    ('Journal of Asian Earth Sciences: X', '2590-0560'),
    ('Gondwana Research', '1342-937X'),
    ('Island Arc', '1038-4871'),
    ('Resource Geology', '1344-1698'),
    ('Journal of the Geological Society of India', '0016-7622'),
    ('Vietnam Journal of Earth Sciences', '0866-7187'),
    ('Indonesian Journal on Geoscience', '2355-9314'),
    ('Bulletin of the Geological Society of Malaysia', '0126-6187'),
    ('Acta Geologica Sinica-English Edition', '1000-9515'),
    # —— 构造 / 地化 / 定年：区域研究大量投这些刊 ——
    ('Tectonophysics', '0040-1951'),
    ('Chemical Geology', '0009-2541'),
    ('Contributions to Mineralogy and Petrology', '0010-7999'),
    ('Journal of Metamorphic Geology', '0263-4929'),
    ('Geoscience Frontiers', '1674-9871'),
    ('International Geology Review', '0020-6814'),
    ('Journal of the Geological Society', '0016-7649'),
    ('Geological Magazine', '0016-7568'),
    ('Journal of Mineralogical and Petrological Sciences', '1345-6296'),
    ('Geochimica et Cosmochimica Acta', '0016-7037'),
    ('Precambrian Research', '0301-9268'),
    ('Terra Nova', '0954-4879'),
    ('Geological Journal', '0072-1050'),
    ('Mineralogy and Petrology', '0930-0708'),
]

# ── 区域词：南亚 / 东南亚 ────────────────────────────────────────────
SEA_REGION = [
    # 南亚 · 国家与山系
    ('Himalaya', 7), ('Himalayan', 7), ('喜马拉雅', 7),
    ('Trans-Himalaya', 7), ('Transhimalaya', 7), ('Karakoram', 7), ('喀喇昆仑', 7),
    ('Kohistan', 8), ('Chitral', 7), ('Hindu Kush', 6), ('Ladakh', 6),
    ('Nanga Parbat', 6), ('Kashmir', 5), ('Uttarakhand', 6), ('Garhwal', 6),
    ('Pakistan', 5), ('巴基斯坦', 6), ('Balochistan', 6), ('Makran', 7),
    ('Indian Shield', 8), ('Dharwar', 8), ('Singhbhum', 8), ('Aravalli', 7),
    ('Bastar', 7), ('Bundelkhand', 7), ('Deccan', 4), ('Deccan Trap', 6),
    ('Eastern Ghats', 6), ('Western Ghats', 5), ('Nilgiri', 5),
    ('Southern Granulite', 6), ('India', 3), ('印度', 4),
    ('Nepal', 6), ('尼泊尔', 6), ('Bhutan', 6), ('不丹', 6),
    ('Bangladesh', 6), ('孟加拉', 5), ('Sri Lanka', 7), ('斯里兰卡', 7),
    ('Andaman', 7), ('Nicobar', 7), ('安达曼', 7),
    # 东南亚 · 国家
    ('Myanmar', 8), ('Burma', 8), ('Burman', 6), ('缅甸', 8),
    ('Thailand', 7), ('Thai', 4), ('泰国', 7),
    ('Laos', 7), ('Lao PDR', 7), ('老挝', 7),
    ('Cambodia', 7), ('Kampuchea', 6), ('柬埔寨', 7),
    ('Vietnam', 7), ('Viet Nam', 7), ('Vietnamese', 5), ('越南', 7),
    ('Malaysia', 7), ('Malay Peninsula', 7), ('马来西亚', 7), ('Singapore', 4),
    ('Indonesia', 7), ('Indonesian', 5), ('印度尼西亚', 7), ('印尼', 7),
    ('Philippines', 7), ('Philippine', 5), ('菲律宾', 7),
    ('Indochina', 8), ('中南半岛', 8), ('印度支那', 6),
    ('Sundaland', 8), ('Sunda Shelf', 7), ('Sunda Arc', 6), ('Sunda Block', 7),
    # 东南亚 · 地体与构造单元（这些词本身就是"研究区"强信号）
    ('Sibumasu', 9), ('Inthanon', 8), ('Loei', 7), ('Sukhothai', 8),
    ('Nan-Uttaradit', 8), ('Phetchabun', 7), ('Truong Son', 8), ('Kontum', 8),
    ('Da Lat', 6), ('Song Ma', 8), ('Song Chay', 7),
    ('Mogok', 8), ('Slate Belt', 6), ('Mergui', 7), ('Wuntho', 8),
    ('Popa', 6), ('Monywa', 7), ('Bawdwin', 8), ('Shan Plateau', 8),
    ('Arakan', 7), ('Indo-Burman', 8), ('Chin Hills', 6), ('Sagaing', 7),
    ('Chindwin', 6), ('Irrawaddy', 6), ('Salween', 6), ('Red River Fault', 7),
    ('Mekong', 5), ('Tonle Sap', 5),
    ('Sumatra', 8), ('Sumatera', 7), ('Java', 5), ('Sulawesi', 8),
    ('Celebes', 6), ('Borneo', 8), ('Kalimantan', 8), ('Bali', 4),
    ('Lombok', 5), ('Sumbawa', 6), ('Flores', 5), ('Timor', 7),
    ('Halmahera', 8), ('Molucca', 7), ('Banda Arc', 7), ('Irian Jaya', 7),
    ("Bird's Head", 8), ('Papua', 5),
    ('Luzon', 7), ('Mindanao', 7), ('Palawan', 7), ('Visayan', 6), ('Negros', 5),
    ('Woyla', 8), ('Meratus', 8), ('Luk Ulo', 8), ('Karangsambung', 8),
    ('Makassar', 5), ('Sulu Sea', 5), ('Celebes Sea', 5), ('Banda Sea', 6),
    ('Java Sea', 6), ('Andaman Sea', 7), ('Gulf of Thailand', 6), ('Natuna', 6),
    # 区域构造-成矿标识（跨南亚东南亚）
    ('Neo-Tethys', 6), ('Meso-Tethys', 6), ('Cimmerian', 6), ('Cimmeria', 6),
    ('Tethyan', 6), ('Tethyan Metallogenic', 8), ('Southeast Asian Tin', 9),
    ('东南亚锡', 9), ('Bangong-Nujiang', 7), ('Indus-Tsangpo', 7),
    ('Yarlung', 6), ('Lhasa', 5), ('Qiangtang', 5),
    ('Central Myanmar Basin', 8), ('jadeite tract', 6),
]

# ── 学科词：地质 / 构造 / 地化 / 定年 / 成矿带 / 俯冲带 ──────────────────
# 分两档：CORE 必须命中 ≥1 个才算"这是地质学论文"，AUX 只加分不单独成立。
# 为什么不用一张平表 + 阈值：`Myanmar` + `basin` 这种组合会把水文盆地研究
# 捞进来；但 `Myanmar` + `tectonics` 是实打实的地质论文。用门槛而非分数区分。
SEA_DISC_CORE = [
    # —— 岩石 / 矿物 ——
    ('igneous', 4), ('pluton', 4), ('plutonic', 4), ('volcanic', 4),
    ('volcanism', 4), ('volcaniclastics', 4), ('granitoid', 4), ('granite', 4),
    ('granitic', 4), ('basalt', 3), ('basaltic', 3), ('andesite', 4),
    ('rhyolite', 4), ('ophiolite', 5), ('serpentinite', 5), ('melange', 5),
    ('metamorphic', 4), ('metamorphism', 4), ('gneiss', 5), ('schist', 5),
    ('granulite', 5), ('amphibolite', 5), ('eclogite', 5), ('migmatite', 5),
    ('skarn', 5), ('hornfels', 5), ('phyllite', 5),
    ('mineralogy', 4), ('amphibole', 5), ('feldspar', 5), ('muscovite', 5),
    ('biotite', 5), ('garnet', 5), ('pyroxene', 5), ('olivine', 5),
    ('spinel', 5), ('tourmaline', 5), ('cassiterite', 6), ('wolframite', 6),
    ('scheelite', 6), ('molybdenite', 6), ('chalcopyrite', 6),
    ('zircon', 4), ('monazite', 5), ('allanite', 5), ('apatite', 5),
    ('baddeleyite', 5), ('titanite', 5), ('rutile', 4),
    ('blueschist', 6), ('greenschist', 6), ('ultrahigh-pressure', 6),
    ('high-pressure', 4), ('ultramafic', 5), ('mafic', 3), ('felsic', 3),
    ('intrusive', 4), ('extrusive', 4), ('dyke', 4), ('dike', 4), ('sill', 3),
    ('xenolith', 5), ('phenocryst', 5), ('stratovolcano', 5),
    ('petrogenesis', 5), ('petrogenetic', 5), ('petrology', 4),
    ('petrochemical', 4), ('petrotectonic', 5), ('lithology', 4),
    ('lithostratigraphy', 4), ('lithospheric', 4),
    # —— 构造 ——
    ('tectonics', 4), ('tectonic', 4), ('tectonism', 4),
    ('deformation', 4), ('deformed', 3), ('structural', 3),
    ('thrust', 4), ('thrusting', 4), ('nappe', 5), ('klippe', 5),
    ('imbricate', 5), ('fold', 3), ('folding', 4), ('cleavage', 4),
    ('fault', 3), ('faulting', 4), ('strike-slip', 4), ('detachment', 4),
    ('shear zone', 4), ('suture', 5), ('terrane', 5), ('tectonostratigraphic', 5),
    ('massif', 5), ('crystalline complex', 5), ('metamorphic complex', 5),
    ('rift', 4), ('rifting', 4), ('graben', 4), ('extensional', 3),
    ('collision', 4), ('collisional', 4), ('convergence', 3),
    ('accretion', 4), ('accretionary', 4), ('amalgamation', 5),
    ('assembly', 3), ('orogen', 4), ('orogenic', 4), ('orogeny', 4),
    ('basement', 3), ('craton', 3), ('cratonic', 4), ('shield', 3),
    ('uplift', 4), ('exhumation', 4), ('denudation', 4), ('unroofing', 4),
    ('cooling history', 4), ('thermochronology', 4), ('cooling age', 4),
    ('paleostress', 5), ('palaeostress', 5), ('stress field', 4),
    ('kinematic', 3), ('paleogeography', 4), ('palaeogeography', 4),
    ('paleogeographic', 4), ('reconstruction', 3), ('plate', 2),
    ('subduction', 5), ('obduction', 5), ('slab', 3), ('trench', 4),
    ('forearc', 4), ('back-arc', 4), ('arc', 2), ('island arc', 4),
    ('seismotectonic', 5), ('neotectonic', 4), ('active tectonics', 4),
    ('geodynamic', 4), ('mantle', 3), ('crustal', 3), ('lithosphere', 4),
    ('asthenosphere', 4), ('moho', 4), ('geophysical', 3), ('tomograph', 4),
    ('anisotropy', 3), ('lattice preferred orientation', 5),
    ('crustal evolution', 5), ('continental growth', 5), ('tectonic evolution', 5),
    ('stratigraphy', 3), ('stratigraphic', 3), ('sedimentary', 3),
    ('formation', 3), ('group', 2), ('member', 2),
    ('sedimentology', 4), ('provenance', 4), ('detrital', 4),
    ('sandstone', 4), ('mudstone', 4), ('limestone', 4), ('conglomerate', 4),
    ('radiolarian', 4), ('foraminifera', 4), ('conodont', 4),
    ('paleozoic', 3), ('palaeozoic', 3), ('mesozoic', 3), ('cenozoic', 3),
    ('precambrian', 3), ('archean', 3), ('archaean', 3), ('proterozoic', 3),
    ('triassic', 2), ('jurassic', 2), ('cretaceous', 2), ('permian', 2),
    # —— 地化 ——
    ('geochemistry', 4), ('geochemical', 4), ('geochemist', 4),
    ('isotope', 4), ('isotopic', 4), ('trace element', 4), ('rare earth', 4),
    ('major element', 4), ('elemental', 3), ('REE', 4),
    ('fluid inclusion', 5), ('melt inclusion', 5),
    ('thermobarometry', 5), ('geothermometry', 5), ('geobarometry', 5),
    ('oxygen fugacity', 5), ('sulfur fugacity', 5), ('salinity', 3),
    ('P-T', 3), ('P–T', 3), ('pressure-temperature', 4),
    ('mass spectrometry', 3), ('LA-ICP-MS', 4), ('XRF', 3), ('EPMA', 4),
    ('electron microprobe', 4), ('cathodoluminescence', 3),
    # —— 定年 ——
    ('geochronology', 5), ('geochronological', 5), ('geochronometer', 5),
    ('dating', 3), ('U-Pb', 5), ('U–Pb', 5), ('Pb-Pb', 5), ('Ar-Ar', 5),
    ('Ar/Ar', 5), ('40Ar/39Ar', 5), ('K-Ar', 4), ('Re-Os', 5), ('Rb-Sr', 4),
    ('Sm-Nd', 5), ('Lu-Hf', 5), ('fission track', 5), ('(U-Th)/He', 5),
    ('U-Th-He', 5), ('baddeleyite age', 5), ('isochron', 5),
    ('emplacement age', 5), ('crystallization age', 5), ('inherited zircon', 5),
    # —— 成矿 / 矿床 ——
    ('metallogeny', 5), ('metallogenic', 5), ('metallogenesis', 5),
    ('mineralization', 4), ('mineralisation', 4), ('ore-forming', 5),
    ('ore', 3), ('orebody', 5), ('deposit', 2), ('hydrothermal', 4),
    ('porphyry', 4), ('epithermal', 4), ('skarn-type', 5),
    ('VMS', 4), ('MVT', 4), ('IOCG', 4), ('orogenic gold', 5),
    ('placer', 4), ('placer gold', 5), ('supergene', 4), ('hypogene', 4),
    ('alteration', 3), ('vein', 3), ('lode', 4), ('gossan', 5),
    ('mineral system', 5), ('mineral prospectivity', 4),
    ('gold', 3), ('copper', 3), ('tin', 3), ('tungsten', 3),
    ('molybdenum', 3), ('silver', 3), ('lead-zinc', 3), ('lithium', 2),
    ('niobium', 3), ('tantalum', 3), ('bauxite', 3), ('nickel', 2),
    ('chromite', 4), ('jadeite', 4), ('ruby', 3), ('sapphire', 3),
    ('gemstone', 4), ('gem', 2),
    # —— 岩浆过程（成矿直接相关） ——
    ('magma', 3), ('magmatic', 4), ('magmatism', 4), ('melt', 3),
    ('fractional crystallization', 5), ('partial melting', 5),
    ('assimilation', 4), ('mixing', 3), ('differentiation', 4),
    ('degassing', 4), ('volatile', 3), ('sulfur', 2), ('sulphur', 2),
]

SEA_DISC_AUX = [
    ('geology', 4), ('geological', 4), ('地质', 4), ('构造', 4),
    ('地球化学', 4), ('年代学', 5), ('定年', 5), ('成矿', 5),
    ('俯冲', 5), ('造山', 4), ('增生', 4), ('变质', 3), ('岩浆', 3),
    ('岩石成因', 4), ('矿物学', 3), ('地层', 3), ('同位素', 3),
    ('蛇绿岩', 5), ('混杂岩', 5), ('锆石', 4), ('流体包裹体', 4),
    ('盆地', 2), ('变形', 3), ('物源', 3), ('矿床', 3), ('矿田', 4),
    ('basin', 2), ('basinal', 3), ('fluid', 2), ('geologic', 4),
    ('age', 2), ('ages', 2), ('evolution', 3), ('history', 2),
    ('setting', 2), ('implication', 1), ('evidence', 1), ('record', 2),
    ('deposits', 2), ('mineral', 2), ('minerals', 3), ('rock', 3),
    ('rocks', 3), ('unit', 2), ('belt', 3), ('zone', 2), ('complex', 2),

    ('plate', 2), ('arc', 2), ('triassic', 2), ('jurassic', 2),
    ('cretaceous', 2), ('permian', 2), ('deposit', 2), ('nickel', 2),
    ('lithium', 2), ('gem', 2), ('sulfur', 2), ('sulphur', 2),
]

# ── 负向短语：明确非地质学（水文/环境/灾害/农业/遥感）。只在无强信号时生效。
# 用短语不用单词：`flood basalt` 是地质词，`flood` 不是；`erosion` 在剥露研究里
# 是核心概念——单词级负向会误伤，短语级不会。
SEA_NEG = [
    'groundwater', 'water quality', 'water resource', 'hydrogeolog',
    'hydrochemistr', 'surface water', 'drinking water', 'irrigation',
    'soil contamination', 'heavy metal pollution', 'pollution assessment',
    'air quality', 'pm2.5', 'pm10', 'carbon emission', 'greenhouse gas',
    'landslide susceptibility', 'landslide hazard', 'landslide risk',
    'slope stability', 'debris flow', 'soil erosion', 'suspended sediment',
    'seismic hazard', 'earthquake hazard', 'seismic risk', 'hazard assessment',
    'risk assessment', 'susceptibility mapping', 'vulnerability',
    'land use', 'land cover', 'urban sprawl', 'city planning',
    'remote sensing', 'satellite imagery', 'gis-based', 'gps',
    'crop yield', 'rice production', 'agricultur', 'fishery', 'aquaculture',
    'public health', 'epidemiolog', 'covid',
    'machine learning', 'deep learning', 'neural network',
    'traffic', 'road construction', 'tunnel', 'dam safety', 'reservoir',
]

# 强信号：命中则忽略负向词（说明确实是矿床/岩石/地化研究）
SEA_STRONG = [
    'ore', 'deposit', 'mineraliz', 'mineralis', 'metallogenic', '成矿', '矿床',
    'geochronolog', 'zircon', 'isotope', 'geochemi', 'fluid inclusion',
    'ophiolite', '蛇绿岩', 'petrogenesis', 'granitoid', 'pluton',
]


# 重音字符归一：CrossRef 标题里 mélange / Paléozoïque 这类写法很常见，
# 按字面匹配会漏。
ACCENT = {'é': '[eé]', 'è': '[eè]', 'ê': '[eê]', 'á': '[aá]', 'à': '[aà]',
          'ó': '[oó]', 'ö': '[oö]', 'ü': '[uü]', 'ñ': '[nñ]', 'ç': '[cç]',
          'å': '[aå]', 'ø': '[oø]', 'ï': '[iï]', 'ô': '[oô]'}


def _rx(term):
    """关键词 → 正则。

    三处必须做归一，否则实测漏掉大量条目（2026-10-27 抽查暴露）：
    1. **复数**：`amphibole` 匹配不到 `amphiboles`，`granitoid` 匹配不到
       `granitoids` —— 补 `(?:e?s)?`。这是漏判最多的一处。
    2. **破折号**：标题里 `U–Pb`（en dash）与 `U-Pb`（hyphen）混用。
    3. **重音**：`mélange` / `Paléozoïque`。
    中文词直接按字面包含匹配（无词边界概念）。
    """
    t = term.strip()
    if re.search(r'[\u4e00-\u9fff]', t):
        return re.compile(re.escape(t))
    out = []
    for ch in t:
        if ch in ' -–—_':
            out.append(r'[\s\-–—]?')
        elif ch in ACCENT:
            out.append(ACCENT[ch])
        else:
            out.append(re.escape(ch))
    body = ''.join(out)
    return re.compile(r'(?<![a-z])' + body + r'(?:e?s)?(?![a-z])', re.I)


REGION_RX = [(_rx(t), w, t) for t, w in SEA_REGION]
CORE_RX = [(_rx(t), w, t) for t, w in SEA_DISC_CORE]
AUX_RX = [(_rx(t), w, t) for t, w in SEA_DISC_AUX]
NEG_RX = [_rx(t) for t in SEA_NEG]
STRONG_RX = [_rx(t) for t in SEA_STRONG]

# 「核心学科词」= CORE 表里权重 ≥3 的项。weight<3 的（plate/arc/deposit/年代名…）
# 只加分不立门槛 —— 否则 `Myanmar` + `basin` 这种组合会把水文盆地研究放进清单。
CORE_GATE = 3

JUNK_TITLE = h10.JUNK_TITLE


def log(msg):
    print(msg, flush=True)


def verdict(title):
    """→ (keep, reason, region_score, disc_score, hits)。reason 供统计丢弃原因。

    短路设计：先匹配区域词（~150 个），命中才匹配学科词（~300 个）。
    3.8 万条 × 450 个正则全跑一遍要几十秒，短路后只对 1/6 的条目做完整匹配。
    """
    rs, rh = 0, []
    for rx, w, t in REGION_RX:
        if rx.search(title):
            rs += w
            rh.append(t)
    if not rh:
        return False, 'no_region', 0, 0, []

    ds, dh, ncore = 0, [], 0
    for rx, w, t in CORE_RX:
        if rx.search(title):
            ds += w
            dh.append(t)
            if w >= CORE_GATE:
                ncore += 1
    for rx, w, t in AUX_RX:
        if rx.search(title):
            ds += w
            dh.append(t)
    if ncore == 0:
        return False, 'no_discipline', rs, ds, rh

    if not any(rx.search(title) for rx in STRONG_RX):
        for rx in NEG_RX:
            if rx.search(title):
                return False, 'negative:%s' % rx.pattern, rs, ds, rh + dh
    return True, '', rs, ds, rh + dh


# ── 枚举 ─────────────────────────────────────────────────────────────
def cache_get(key):
    p = RAW_CACHE / ('%s.json' % key)
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding='utf-8'))
    except Exception:
        return None


def cache_put(key, data):
    RAW_CACHE.mkdir(parents=True, exist_ok=True)
    p = RAW_CACHE / ('%s.json' % key)
    tmp = p.with_suffix('.tmp')
    tmp.write_text(json.dumps(data, ensure_ascii=False), encoding='utf-8')
    tmp.replace(p)


def fetch_all(frm, until, refresh=False, only=''):
    raw, fails = [], []
    for name, issn in REGION_JOURNALS:
        if only and only.lower() not in name.lower() and only != issn:
            continue
        # 缓存键必须带 ISSN：刊名截断到 24 字符会让
        # "Journal of Asian Earth Sciences" 与 "...: X" 撞成同一个键，
        # 实测把 JAES 的 3603 条当成 JAST-X 的数据复用（2026-10-27 发现）。
        key = 'rg2_%s_%s_%s' % (re.sub(r'\W+', '', issn), frm.strftime('%Y%m%d'),
                                until.strftime('%Y%m%d'))
        items = None if refresh else cache_get(key)
        if items is not None:
            log('  [缓存] %-46s %5d 篇' % (name, len(items)))
        else:
            t0 = time.time()
            items = []
            for attempt in range(3):
                try:
                    items = hm.fetch_crossref(issn, frm.isoformat(), until.isoformat(),
                                              cap=9000)
                except Exception as e:
                    log('  [失败] %-42s 第%d次 %s: %s'
                        % (name, attempt + 1, type(e).__name__, str(e)[:60]))
                    items = []
                if items:
                    break
                time.sleep(3 * (attempt + 1))    # CrossRef 偶发 500/空返回，退避重试
            if not items:
                log('  [失败] %-42s 三次均无数据' % name)
                fails.append({'journal': name, 'error': 'crossref 重试三次仍无数据'})
            cache_put(key, items)
            log('  [CrossRef] %-40s %5d 篇  %.0fs' % (name, len(items), time.time() - t0))
        for it in items:
            it['journal'] = name
            it['source'] = 'crossref'
        raw.extend(items)
    return raw, fails


# ── 去重 ─────────────────────────────────────────────────────────────
def load_stage_keys():
    """10 年暂存区的键。

    不读本脚本自己的 STAGE —— 它每次都被整体覆盖写，与自己比会把全部条目
    判成「暂存区已有」，重跑直接归零（2026-10-27 踩过）。
    """
    titles, dois, urls = set(), set(), set()
    n = {'add10y': 0}
    if STAGE10Y.exists():
        for line in STAGE10Y.read_text(encoding='utf-8', errors='ignore').splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
            except Exception:
                continue
            n['add10y'] += 1
            if r.get('title'):
                titles.add(hm.norm_title(r['title']))
            if r.get('doi'):
                dois.add(hm.norm_doi(r['doi']))
            if r.get('url'):
                urls.add(r['url'].strip().lower())
    return {'titles': titles, 'dois': dois, 'urls': urls}, n


def hit(rec, keys):
    if rec.get('title') and hm.norm_title(rec['title']) in keys['titles']:
        return True
    if rec.get('doi') and hm.norm_doi(rec['doi']) in keys['dois']:
        return True
    if rec.get('url') and rec['url'].strip().lower() in keys['urls']:
        return True
    return False


# ── 主流程 ───────────────────────────────────────────────────────────
def main():
    ap = argparse.ArgumentParser(description='南亚东南亚区域定向待爬清单（只列不爬）')
    ap.add_argument('--years', type=int, default=10)
    ap.add_argument('--min-score', type=int, default=7, help='入列阈值（区域分+学科分）')
    ap.add_argument('--core-score', type=int, default=11, help='core 层阈值')
    ap.add_argument('--only', default='', help='只跑指定刊（名称片段或 ISSN）')
    ap.add_argument('--refresh', action='store_true')
    ap.add_argument('--dry-run', action='store_true')
    ap.add_argument('--merge', action='store_true', help='并入待爬队列')
    ap.add_argument('--merge-10y', action='store_true',
                    help='同时并入 10 年暂存区（上次遗留未并）')
    ap.add_argument('--force', action='store_true')
    ap.add_argument('--journals', action='store_true')
    args = ap.parse_args()

    if args.journals:
        log('目标期刊 %d 种：' % len(REGION_JOURNALS))
        for n, i in REGION_JOURNALS:
            log('  %-48s %s' % (n, i))
        return 0

    if args.merge:
        return do_merge(args)

    until = datetime.now().date()
    frm = until - timedelta(days=365 * args.years)
    log('窗口 %s ~ %s ｜ 刊 %d 种 ｜ 入列>=%d / core>=%d'
        % (frm, until, len(REGION_JOURNALS), args.min_score, args.core_score))

    raw, fails = fetch_all(frm, until, args.refresh, args.only)
    log('原始题录 %d 条（%d 刊失败）' % (len(raw), len(fails)))

    kept = [r for r in raw if not (r.get('date') and r['date'][:7] < frm.isoformat()[:7])]
    log('时间窗外剔除 %d → %d' % (len(raw) - len(kept), len(kept)))

    nonarticle, junk, drop = [], 0, Counter()
    cand = []
    for r in kept:
        t = (r.get('title') or '').strip()
        if len(t) < 8 or JUNK_TITLE.search(t):
            junk += 1
            continue
        keep, why, rs, ds, hits = verdict(t)
        if not keep:
            drop['negative' if why.startswith('negative') else why] += 1
            continue
        kind, kwhy = classify_article(t)
        if kind != 'article':
            nonarticle.append(dict(r, kind=kind, why=kwhy))
            continue
        total = rs + ds
        if total < args.min_score:
            drop['low_score'] += 1
            continue
        r['region_score'] = rs
        r['disc_score'] = ds
        r['score'] = total
        r['matched'] = hits[:10]
        r['tier'] = 'core' if total >= args.core_score else 'ext'
        cand.append(r)

    log('非正刊稿 %d ｜ 垃圾标题 %d ｜ 丢弃 %s ｜ 候选 %d'
        % (len(nonarticle), junk, dict(drop), len(cand)))
    log('  分层：core %d ｜ ext %d'
        % (sum(1 for x in cand if x['tier'] == 'core'),
           sum(1 for x in cand if x['tier'] == 'ext')))

    # 五路去重
    kb, kbstats = h10.build_dedup_full()
    mk, mkstats = h10.load_manifest_keys()
    sk, skn = load_stage_keys()
    log('去重：台账有PDF %d ｜ papers %d ｜ 清单 %d ｜ 已完成 %d ｜ 10年暂存区 %d'
        % (kbstats['ledger_pdf'], kbstats['papers_titles'], mkstats['manifest'],
           mkstats['done'], skn['add10y']))

    todo, seen = [], set()
    dup = Counter()
    for r in cand:
        nk = hm.norm_title(r['title'])
        if nk in seen:
            dup['本批重复'] += 1
            continue
        seen.add(nk)
        where = h10.hit_keys(r, kb)
        if where == 'meta_titles':
            dup['台账仅题录'] += 1
            continue
        if where:
            dup['知识库有PDF'] += 1
            continue
        if h10.hit_manifest(r, mk):
            dup['现有清单'] += 1
            continue
        if hit(r, sk):
            dup['暂存区已有'] += 1
            continue
        rid = 'rg-' + hashlib.sha1(
            (hm.norm_doi(r.get('doi')) or nk).encode('utf-8')).hexdigest()[:12]
        reach, reach_p = hm.reach_of(r['url'])
        todo.append({
            'id': rid,
            'journal': r['journal'],
            'title': r['title'],
            'url': r['url'],
            'doi': r.get('doi') or '',
            'date': r.get('date') or '',
            'year': (r.get('date') or '')[:4],
            'volume': r.get('volume') or '',
            'issue': r.get('issue') or '',
            'score': r['score'],
            'region_score': r['region_score'],
            'disc_score': r['disc_score'],
            'matched': r['matched'],
            'tier': r['tier'],
            'source': r['source'],
            'reach': reach,
            'reach_p': reach_p,
            'sd': hm.is_sd(r.get('doi') or '', r['url'], r['journal']),
            'status': 'pending',
            'attempts': 0,
            'last_error': '',
            'batch': 'region',
            'added_at': datetime.now().isoformat(timespec='seconds'),
        })
    log('去重剔除 %s → 新增 %d 条' % (dict(dup), len(todo)))

    todo.sort(key=lambda x: (x['tier'] != 'core', x['sd'], -x['reach_p'],
                             -x['score'], x['date']), reverse=False)

    stats = {
        'generated_at': datetime.now().isoformat(timespec='seconds'),
        'window': [frm.isoformat(), until.isoformat()],
        'journals': len(REGION_JOURNALS),
        'raw': len(raw),
        'in_window': len(kept),
        'nonarticle_removed': len(nonarticle),
        'junk_title': junk,
        'drop_reason': dict(drop),
        'candidates': len(cand),
        'by_tier': dict(Counter(x['tier'] for x in todo)),
        'dup': dict(dup),
        'todo': len(todo),
        'sd_todo': sum(1 for x in todo if x['sd']),
        'fetch_failed': fails,
        'by_journal': dict(Counter(x['journal'] for x in todo).most_common()),
        'by_year': dict(sorted(Counter(x['year'] for x in todo).items())),
        'by_reach': dict(Counter(x['reach'] for x in todo).most_common()),
        'top_matched': dict(Counter(k for x in todo for k in x['matched']).most_common(30)),
    }

    if args.dry_run:
        log('\n[dry-run] 未写盘。抽样 15 条：')
        for x in todo[:15]:
            log('  %-3s %-38s %s' % (x['tier'], x['journal'][:38], x['title'][:76]))
        log('\n按刊：')
        for j, v in list(stats['by_journal'].items())[:15]:
            log('  %-48s %d' % (j[:48], v))
        return 0

    STAGE.write_text(''.join(json.dumps(x, ensure_ascii=False) + '\n' for x in todo),
                     encoding='utf-8')
    SKIPPED.write_text(''.join(json.dumps(x, ensure_ascii=False) + '\n' for x in nonarticle),
                       encoding='utf-8')
    STAGE_STATS.write_text(json.dumps(stats, ensure_ascii=False, indent=2), encoding='utf-8')
    log('\n已写 %s（%d 条）' % (STAGE, len(todo)))
    log('已写 %s（%d 条非正刊稿）' % (SKIPPED, len(nonarticle)))
    write_report(stats, todo)
    return 0


def write_report(stats, todo):
    try:
        hm.write_html(todo, stats, out=str(STAGE_HTML),
                      heading='南亚 / 东南亚区域定向 · 待抓取清单')
        log('已写 %s' % STAGE_HTML)
    except Exception as e:
        log('[warn] HTML 生成失败 %s: %s' % (type(e).__name__, str(e)[:80]))

    LOGS.mkdir(parents=True, exist_ok=True)
    p = LOGS / ('清单南亚东南亚_%s.md' % datetime.now().strftime('%Y%m%d'))
    L = ['# 南亚 / 东南亚区域定向待抓清单', '',
         '生成时间：%s ｜ 窗口 %s ~ %s' % (stats['generated_at'], *stats['window']), '',
         '## 总量', '',
         '| 指标 | 数值 |', '|---|---|',
         '| 目标期刊 | %d 种 |' % stats['journals'],
         '| 原始题录 | %d |' % stats['raw'],
         '| 非正刊稿剔除 | %d |' % stats['nonarticle_removed'],
         '| 候选（区域+学科双命中） | %d |' % stats['candidates'],
         '| **新增待抓取** | **%d** |' % stats['todo'],
         '| 其中 SD 政策停抓 | %d |' % stats['sd_todo'], '',
         '## 分层', '', '| 层 | 条数 |', '|---|---|']
    for k, v in stats['by_tier'].items():
        L.append('| %s | %d |' % (k, v))
    L += ['', '## 按期刊', '', '| 期刊 | 条数 |', '|---|---|']
    for k, v in list(stats['by_journal'].items()):
        L.append('| %s | %d |' % (k, v))
    L += ['', '## 命中最多的问题词', '', '| 关键词 | 次数 |', '|---|---|']
    for k, v in list(stats['top_matched'].items())[:25]:
        L.append('| %s | %d |' % (k, v))
    if stats['fetch_failed']:
        L += ['', '## 失败的源', '']
        for f in stats['fetch_failed']:
            L.append('- %s：%s' % (f['journal'], f['error']))
    p.write_text('\n'.join(L) + '\n', encoding='utf-8')
    log('已写 %s' % p)


def do_merge(args):
    """把暂存区并入待爬队列。

    必须独占清单：调度器父进程把清单整份读进内存、每次收工全量写回，
    直接改文件会被它用旧内存覆盖回去（外面看着像"改了没生效"）。
    做法沿用 park_frozen 已验证的那套：操作锁 → 停调度器确认死透 → 改盘 → 释放锁。
    """
    import history_queue as hq
    import park_frozen as pf

    srcs = []
    if STAGE.exists():
        srcs.append(('region', STAGE))
    else:
        log('[warn] 区域暂存区不存在：%s' % STAGE)
    if args.merge_10y:
        if STAGE10Y.exists():
            srcs.append(('add10y', STAGE10Y))
        else:
            log('[warn] 10 年暂存区不存在：%s' % STAGE10Y)
    if not srcs:
        log('没有可并入的暂存区')
        return 2

    rows = hq.load_manifest(repair=True)
    have_t, have_d, have_u = set(), set(), set()
    for r in rows:
        if r.get('title'):
            have_t.add(hm.norm_title(r['title']))
        if r.get('doi'):
            have_d.add(hm.norm_doi(r['doi']))
        if r.get('url'):
            have_u.add(r['url'].strip().lower())

    add, dups = [], Counter()
    for tag, p in srcs:
        for line in p.read_text(encoding='utf-8', errors='ignore').splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
            except Exception:
                continue
            nt = hm.norm_title(r.get('title') or '')
            nd = hm.norm_doi(r.get('doi') or '')
            nu = (r.get('url') or '').strip().lower()
            if (nt and nt in have_t) or (nd and nd in have_d) or (nu and nu in have_u):
                dups[tag] += 1
                continue
            if nt:
                have_t.add(nt)
            if nd:
                have_d.add(nd)
            if nu:
                have_u.add(nu)
            add.append(r)

    sd = sum(1 for x in add if x.get('sd'))
    log('清单现有 %d ｜ 待并入 %d（其中 SD 暂停 %d）｜ 重复跳过 %s'
        % (len(rows), len(add), sd, dict(dups)))
    if not add:
        return 0
    if args.dry_run:
        log('[dry-run] 未写盘。并入后清单将变为 %d 条' % (len(rows) + len(add)))
        return 0

    if pf.queue_procs() and not args.force:
        log('⚠️ 调度器正在运行，并入后会**立即开爬**。确认请加 --force。')
        return 3

    try:
        pf.take_lock()
    except RuntimeError as e:
        log('⚠️ %s' % e)
        return 4

    try:
        n = pf.stop_queue()
        if n:
            log('已停调度器 %d 个进程，等守护拉起（锁在不会抢跑）' % n)
        merged = rows + add
        hq.save_manifest(merged, backup=True)
        log('已写清单 %d → %d 条' % (len(rows), len(merged)))
    finally:
        pf.drop_lock()
        log('操作锁已释放，守护 10s 内会用新清单拉起')
    return 0


if __name__ == '__main__':
    sys.exit(main())
