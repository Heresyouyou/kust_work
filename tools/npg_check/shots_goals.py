# -*- coding: utf-8 -*-
"""「学习目标」的视觉验收截图（跑骨架页，不碰线上数据）。

产出 tools/npg_check/_shots/*.png：
    00_home_dark.png / 00_home_light.png / 00_home_mobile.png  （RPG 时间轴首页里的面板）
    01_today_dark.png / 02_board_dark.png / 03_weekly_dark.png
    04_today_light.png / 05_mobile_dark.png
    06_heat_tip_dark.png / 07_heat_day_dark.png / 08_heat_mobile.png  （打卡方块交互）
    09_heat_month_dark.png / 10_heat_month_light.png / 11_heat_month_mobile.png（月视图）
    12_heat_year_mobile.png                                （手机上的年视图：守下限 + 横向滚到最右）
    13_duo_year_dark.png / 14_duo_month_dark.png           （宽屏并排：方块 ｜ 近 14 天柱状图）
    15_dash_dark.png / 16_dash_edit_dark.png               （首页仪表盘：默认布局 ｜ 编辑态工具条）
    17_dash_mobile.png / 18_dash_edit_mobile.png           （手机上的仪表盘：单列 ｜ 44px 编辑按钮）
"""
import pathlib
from playwright.sync_api import sync_playwright

ROOT = pathlib.Path(__file__).resolve().parents[2]
H = (ROOT / "tools" / "npg_check" / "harness.html").as_uri()
OUT = ROOT / "tools" / "npg_check" / "_shots"
OUT.mkdir(parents=True, exist_ok=True)


def shot(pg, name, full=True):
    p = OUT / name
    pg.screenshot(path=str(p), full_page=full)
    print("  -> %s  (%.0f KB)" % (p.name, p.stat().st_size / 1024))


with sync_playwright() as p:
    b = p.chromium.launch(channel="msedge")

    # PC · 深色
    pg = b.new_page(viewport={"width": 1440, "height": 940}, device_scale_factor=2)
    pg.goto(H, wait_until="load")
    pg.wait_for_timeout(1400)

    # RPG 时间轴首页：面板挂在「任务时间轴」卡片正下方（默认就是深色）
    shot(pg, "00_home_dark.png")
    pg.evaluate("document.documentElement.setAttribute('data-theme','light')")
    pg.wait_for_timeout(400)
    shot(pg, "00_home_light.png")
    pg.evaluate("document.documentElement.removeAttribute('data-theme')")
    pg.wait_for_timeout(400)

    pg.click('[data-view="goals"]')
    pg.wait_for_timeout(600)
    shot(pg, "15_dash_dark.png")                       # 首页仪表盘（进入视图的默认页签）

    pg.click("#npgDashBtn")                            # 编辑态：每张卡的工具条 + 下方货架
    pg.wait_for_timeout(500)
    shot(pg, "16_dash_edit_dark.png")
    pg.click("#npgDashBtn")
    pg.wait_for_timeout(500)

    pg.locator('.npg-subtab[data-npg-tab="today"]').click()   # 后面几张都建立在「今日」页签上
    pg.wait_for_timeout(500)
    shot(pg, "01_today_dark.png")

    pg.locator('.npg-subtab[data-npg-tab="board"]').click()
    pg.wait_for_timeout(500)
    shot(pg, "02_board_dark.png")

    # 宽屏并排：打卡方块 ｜ 近 14 天柱状图 —— 两张卡各占一列
    # （并排与否认容器宽度，这里量到的列宽要和真环境对得上，见 13/14 两张）
    pg.evaluate("document.querySelector('.npg-duo').scrollIntoView({block:'center'})")
    pg.wait_for_timeout(400)
    shot(pg, "13_duo_year_dark.png", full=False)

    # 打卡方块的两种操作反馈：悬停提示、点开当天明细
    pg.evaluate("document.getElementById('npgHeat').scrollIntoView({block:'center'})")
    pg.wait_for_timeout(400)
    hi = pg.evaluate('''(function(){
        var a = document.querySelectorAll('#npgHeat svg rect[data-npg-heat]');
        for (var i = 0; i < a.length; i++) { if (a[i].getAttribute('fill-opacity')) { return i; } }
        return -1; })()''')
    if hi >= 0:
        pg.locator("#npgHeat svg rect[data-npg-heat]").nth(hi).hover()
        pg.wait_for_timeout(400)
        shot(pg, "06_heat_tip_dark.png", full=False)
        pg.locator("#npgHeat svg rect[data-npg-heat]").nth(hi).click()
        pg.wait_for_timeout(500)
        shot(pg, "07_heat_day_dark.png", full=False)
        pg.keyboard.press("Escape")
        pg.wait_for_timeout(300)

    pg.locator('.npg-subtab[data-npg-tab="weekly"]').click()
    pg.wait_for_timeout(500)
    shot(pg, "03_weekly_dark.png")

    # 月视图（深色）：7 列大格、格子里带日号、带月份导航
    pg.locator('.npg-subtab[data-npg-tab="board"]').click()
    pg.wait_for_timeout(400)
    pg.click('#npgHeatCtl [data-npg-hmode="month"]')
    pg.wait_for_timeout(600)
    shot(pg, "09_heat_month_dark.png")
    pg.evaluate("document.querySelector('.npg-duo').scrollIntoView({block:'center'})")
    pg.wait_for_timeout(400)
    shot(pg, "14_duo_month_dark.png", full=False)

    # PC · 浅色：走真实的 html[data-theme] 切换，不给元素刷内联色
    pg.evaluate("document.documentElement.setAttribute('data-theme','light')")
    pg.wait_for_timeout(500)
    shot(pg, "10_heat_month_light.png")
    pg.click('#npgHeatCtl [data-npg-hmode="year"]')
    pg.wait_for_timeout(500)
    pg.locator('.npg-subtab[data-npg-tab="today"]').click()
    pg.wait_for_timeout(500)
    shot(pg, "04_today_light.png")
    pg.close()

    # 移动端 375（单列）。注意：窄屏首屏默认就是月视图（年视图 53 列在手机上必然横向滚）
    m = b.new_page(viewport={"width": 375, "height": 780}, device_scale_factor=2)
    m.goto(H, wait_until="load")
    m.wait_for_timeout(1400)
    shot(m, "00_home_mobile.png")
    m.click('[data-view="goals"]')
    m.wait_for_timeout(600)
    shot(m, "17_dash_mobile.png")                      # 手机上的仪表盘：退回单列
    m.click("#npgDashBtn")
    m.wait_for_timeout(500)
    shot(m, "18_dash_edit_mobile.png")                 # 手机上的编辑态：按钮撑到 44px
    m.click("#npgDashBtn")
    m.wait_for_timeout(400)
    m.locator('.npg-subtab[data-npg-tab="today"]').click()
    m.wait_for_timeout(500)
    shot(m, "05_mobile_dark.png")
    m.locator('.npg-subtab[data-npg-tab="board"]').click()
    m.wait_for_timeout(500)
    m.evaluate("document.getElementById('npgHeat').scrollIntoView({block:'center'})")
    m.wait_for_timeout(400)
    shot(m, "08_heat_mobile.png", full=False)
    shot(m, "11_heat_month_mobile.png", full=False)
    m.click('#npgHeatCtl [data-npg-hmode="year"]')
    m.wait_for_timeout(600)
    m.evaluate("document.getElementById('npgHeat').scrollIntoView({block:'center'})")
    m.wait_for_timeout(400)
    shot(m, "12_heat_year_mobile.png", full=False)
    m.close()

    b.close()
print("done")
