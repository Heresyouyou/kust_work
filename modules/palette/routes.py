# -*- coding: utf-8 -*-
"""科研配色模块

收录权威期刊与地学标准配色，供出图时挑色：
  · 期刊分类配色 —— Nature(npg) / Science(aaas) / NEJM / Lancet / JAMA / JCO
                    / BMJ / Frontiers
  · 期刊推荐色标 —— Crameri 科学色标（batlow / lajolla / oslo / devon / roma / vik）
                    Nature 系期刊明确推荐的感知均匀色标
  · 地学专属 —— 国际年代地层表 ICS/CGMW 官方年代色、地形高程色阶、
                 cmocean 海洋地学色标（thermal / haline / oxy / topo / delta）
  · 连续色阶 —— viridis / cividis / plasma / magma / inferno / Blues / Turbo
  · 发散型 —— RdBu / Spectral / PiYG
  · 分类型 —— D3 category10 / ColorBrewer Set2、Paired / UChicago
               / Okabe-Ito（色盲友好）/ COSMIC Hallmarks

色值来源：
  期刊分类色为 ggsci 官方公布定值；
  连续与发散色阶由 matplotlib 按 9~11 点均匀采样得到精确 hex；
  Crameri 色标取自 cmcrameri 官方 256 点 RGB 表；
  cmocean 色标取自 matplotlib/cmocean 官方 RGB 表；
  年代色取自 ICS/CGMW 年代地层表官方色码。
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
