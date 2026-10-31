# -*- coding: utf-8 -*-
"""科研配色模块

收录权威期刊与地学标准配色，供出图时挑色。按「用在哪」分为 7 个大类
（原「期刊分类 / 分类型 / 期刊推荐 / 地学专属 / 地质图 / 地形」的交叉归类已合并），
并按矿床学研究的实际使用频率从高到低排列：

  · 地质图填色 —— 岩性·蚀变·地层单元多类别填色：ColorBrewer（Set3·Dark2·Set1
                  ·Accent·Pastel1·Pastel2）/ Paul Tol（柔和·明亮·浅色·深色·中对比
                  ·鲜明·地表覆盖）/ CARTO（Safe·Bold·Antique·Pastel·Vivid·Prism）
                  / Tableau 10
  · 地质年代标准色 —— 按年代设色：ICS/CGMW 国际年代地层表 2024/12 版（纪、代，
                    以及按纪拆分的 12 张世色卡）/ USGS 美国地质图年代色系
                    （American color system，排在组末）
  · 连续色阶 —— viridis / cmocean（thermal·haline·oxy）/ Crameri（batlow
                ·lajolla·oslo·devon）/ plasma·magma·inferno·cividis / Blues / Turbo
  · 发散色阶 —— RdBu / Crameri（vik·roma）/ cmocean delta / Spectral / PiYG
  · 通用分类配色 —— 跨学科通用离散色板：Okabe-Ito（色盲友好）/ ColorBrewer
                    Set2·Paired / D3 Category10 / UChicago / COSMIC Hallmarks
  · 地形与地球物理 —— GMT 官方色标 relief / topo / earth / etopo1 / seis / mag /
                    globe / haxby / seafloor / gebco，另含 cmocean topo 与地形高程
  · 期刊出版配色 —— ggsci 期刊社自有配色：Nature(npg) / Science(aaas) / Frontiers
                    / BMJ / Lancet / JAMA / NEJM / JCO（按投稿期刊换皮用）

色值来源：
  期刊分类色为 ggsci 官方公布定值；
  连续与发散色阶由 matplotlib 按 9~11 点均匀采样得到精确 hex；
  Crameri 色标取自 cmcrameri 官方 256 点 RGB 表；
  cmocean 色标取自 matplotlib/cmocean 官方 RGB 表；
  年代色取自 ICS/CGMW 年代地层表官方色码；
  ICS v2024/12 与 USGS 色值由其官方色卡 PDF 解析；
  GMT 色标取自 GMT 官方 share/cpt/*.cpt；
  ColorBrewer / Paul Tol / CARTO / Tableau 取自各自官方发布数据。
  全部为「定值快照」，不依赖运行时绘图库。

★ 扩充色卡请跑 `D:\PostNews\_build_palettes.py`：色值一律从权威源抓取并存档到
  `_palette_src/`，不手工抄 hex；抓不到的色板会被跳过并告警，不拿近似色凑数。

页面：GET /palette
接口：GET /api/palette/list
"""
import json
from pathlib import Path

from flask import Blueprint, jsonify

from portal.templating import render_html

palette_bp = Blueprint('palette', __name__)

_DIR = Path(__file__).parent
_TEMPLATE = _DIR / 'templates' / 'palette.html'
_DATA = _DIR / 'palettes.json'


def init_palette(app, **_ignored):
    """注册科研配色模块（无外部依赖，data_dir 用不上）。"""
    app.register_blueprint(palette_bp)
    try:
        n = len(_load())
        print(f'[模块] 科研配色 → GET /palette（{n} 组色板）')
    except Exception:
        print('[模块] 科研配色 → GET /palette（色板数据读取异常，请检查 palettes.json）')


def _load():
    with open(_DATA, 'r', encoding='utf-8') as f:
        data = json.load(f)
    return data.get('palettes') or []


def _summary(palettes):
    groups, order = {}, []
    for p in palettes:
        g = p.get('group') or '其他'
        if g not in groups:
            groups[g] = 0
            order.append(g)
        groups[g] += 1
    return {
        'palette_count': len(palettes),
        'color_count': sum(len(p.get('colors') or []) for p in palettes),
        'groups': order,
    }


@palette_bp.route('/palette')
def palette_page():
    return render_html(_TEMPLATE)


@palette_bp.route('/api/palette/list')
def api_palette_list():
    try:
        palettes = _load()
    except Exception:
        return jsonify({'ok': False, 'error': 'palettes.json 读取失败'}), 500

    payload = {'ok': True, 'palettes': palettes}
    payload.update(_summary(palettes))
    return jsonify(payload)
