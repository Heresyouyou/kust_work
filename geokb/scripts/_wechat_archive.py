# -*- coding: utf-8 -*-
"""微信 2026-10 批次 PDF → NAS papers_repo 规范归档 + index.jsonl 登记。

- 31 篇唯一论文复制到 by_journal/<JOURNAL>/<YYYY>/<date>_<slug>.pdf
- [14] 与 [13] 同文（Sun & Chen 2017, Shimensi, DOI 10.2138/am-2017-5654），跳过不入库
- 复制后逐字节 SHA1 校验
- 追加 index.jsonl（与 consolidate_pdfs 字段一致）
"""
import hashlib
import json
import shutil
import sys
from datetime import datetime
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))
from paths import by_journal, index_jsonl  # noqa: E402

SRC = Path(r"D:\WeChat\xwechat_files\wxid_5a7fxnscvh4h12_8ac3\msg\file\2026-10")
REPO = Path(by_journal())
INDEX = Path(index_jsonl())

# (idx, 目录, date, slug, journal)
PLAN = [
    (0,  "ECONGEO", "2026-05-14", "Sun_Magmatic_Volatile_Cu_Mo_Collisional_Porphyry", "Economic Geology"),
    (1,  "ECONGEO", "2026-01-01", "Altenberger_W_Eastern_Alps", "Economic Geology"),
    (2,  "OGR",     "2021-11-08", "Dai_Cuonadong_Sn_W_Be", "Ore Geology Reviews"),
    (3,  "AM",      "2020-01-01", "Han_Tongshankou_Scheelite", "American Mineralogist"),
    (4,  "OGR",     "2022-01-29", "Huang_Weijia_W_Skarn", "Ore Geology Reviews"),
    (5,  "AM",      "2024-04-01", "Ji_Fujiashan_Scheelite", "American Mineralogist"),
    (6,  "FRONTGEO","2023-01-01", "Li_Qiaomaishan_U_Pb", "Geoscience Frontiers"),
    (7,  "JGE",     "2025-01-28", "Liu_Dulong_Garnet", "Journal of Geochemical Exploration"),
    (8,  "CANMIN",  "2018-01-01", "Poulin_Scheelite_Discriminator", "The Canadian Mineralogist"),
    (9,  "ECONGEO", "2018-01-01", "Scanlan_Otago_Sr_Scheelite", "Economic Geology"),
    (10, "MINDEP",  "2020-01-01", "Sciuba_Orogenic_Gold_Scheelite", "Mineralium Deposita"),
    (11, "LITHOS",  "2016-09-01", "Guo_Honghuaerji_Sm_Nd", "Lithos"),
    (12, "AM",      "2014-01-01", "Song_Chizhou_Scheelite", "American Mineralogist"),
    (13, "AM",      "2017-01-01", "Sun_Shimensi_Scheelite", "American Mineralogist"),
    (15, "AM",      "2021-12-01", "Li_Yangjiashan_Scheelite", "American Mineralogist"),
    (16, "AM",      "2021-09-01", "Cao_Helukou_W", "American Mineralogist"),
    (17, "ECONGEO", "2026-02-07", "Wang_Scheelite_WallRock_Reaction", "Economic Geology"),
    (18, "CMP",     "2024-01-01", "Weng_Zhaishang_Scheelite", "Contributions to Mineralogy and Petrology"),
    (19, "GR",      "2024-01-01", "Zhao_Nanyangtian_Grt_U_Pb", "Gondwana Research"),
    (20, "岩石学报", "2026-10-01", "RenQinghui_Zhanpu_Zircon", "岩石学报"),
    (21, "矿床地质", "2022-08-01", "RenKangda_Guilinzheng_REE", "矿床地质"),
    (22, "岩石学报", "2026-10-01", "LiuJun_Bianjiadayuan_SnAg", "岩石学报"),
    (23, "岩石学报", "2026-10-01", "Zhou_Mao_XinXiadike_Sn", "岩石学报"),
    (24, "岩石学报", "2021-03-01", "LiuYi_Guanfang_Grt_UPb", "岩石学报"),
    (25, "岩石学报", "2026-10-01", "ZhuangSitong_CrustalMelting_Sn", "岩石学报"),
    (26, "岩石学报", "2026-10-01", "ZhangChenming_Aonaodaba_CuSn", "岩石学报"),
    (27, "岩石学报", "2026-10-01", "ZhangMinghang_RockPhysics", "岩石学报"),
    (28, "岩石学报", "2026-10-01", "PengYao_Xiaowulan_Muscovite", "岩石学报"),
    (29, "岩石学报", "2026-10-01", "YangShiwen_Xiatongling_Wolframite", "岩石学报"),
    (30, "岩石学报", "2026-10-01", "ChenXinkai_BayinChagan_Sn", "岩石学报"),
    (31, "岩石学报", "2026-10-01", "WeiJialin_Geshaogou_W_Sn", "岩石学报"),
]

meta = {r["sha1"]: r for r in json.loads(
    (BASE / "state" / "wechat_202610_meta" / "extract.json").read_text(encoding="utf-8"))}


def sha1_of(path, chunk=1 << 20):
    h = hashlib.sha1()
    with open(path, "rb") as f:
        while True:
            b = f.read(chunk)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


now = datetime.now().isoformat()
new_index = []
for idx, journal_dir, date, slug, journal in PLAN:
    rec = meta[[k for k in meta][idx]]
    assert list(meta.keys())[idx] == rec["sha1"], "顺序漂移"
    src = SRC / rec["file"]
    year = date[:4]
    dest_dir = REPO / journal_dir / year
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / f"{date}_{slug}.pdf"
    if not dest.exists():
        shutil.copy2(src, dest)
    s1, s2 = sha1_of(src), sha1_of(dest)
    ok = "OK" if s1 == s2 else "MISMATCH!"
    print(f"[{idx:02d}] {ok} {journal_dir}/{year}/{dest.name}")
    if s1 != s2:
        raise SystemExit(f"校验失败: {src}")
    new_index.append({
        "title": slug,
        "journal": journal,
        "date": date,
        "source_path": str(src),
        "size": dest.stat().st_size,
        "indexed_at": now,
        "repo_path": str(dest),
        "date_path": "",
    })

with open(INDEX, "a", encoding="utf-8") as f:
    for r in new_index:
        f.write(json.dumps(r, ensure_ascii=False) + "\n")
print(f"\nindex.jsonl 追加 {len(new_index)} 条")
