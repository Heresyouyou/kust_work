"""用独立 profile 打开昆工 CAS 登录页，dump 表单结构 + 验证码图片。

用受控的 `sciencedirect_auth` profile（不是用户真实的 Default），
避免干扰用户正在用的 Edge 窗口。
"""
import sys
from pathlib import Path

BASE = Path('D:/ZSK/geokb')
sys.path.insert(0, str(BASE / 'scripts'))

from browser_verify_fetch import LAUNCH_ARGS, get_sync_playwright  # noqa: E402

PROFILE = BASE / 'state' / 'browser_profiles' / 'sciencedirect_auth'
OUT = BASE / 'state' / 'cas_probe'
CAND = ['https://cas.kmust.edu.cn/lyuapServer/login',
        'https://cas.kmust.edu.cn/lyuapServer/login?service=https%3A%2F%2Fidplib.kust.edu.cn%2Fidp%2Fprofile%2FSAML2%2FRedirect%2FSSO']


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    sync_playwright = get_sync_playwright('auto')
    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(
            user_data_dir=str(PROFILE), channel='msedge', headless=False,
            args=LAUNCH_ARGS, chromium_sandbox=True, no_viewport=True,
            locale='zh-CN', timezone_id='Asia/Shanghai')
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        for url in CAND:
            try:
                page.goto(url, wait_until='domcontentloaded', timeout=45000)
            except Exception as e:
                print(f'[goto] {url} → {type(e).__name__}: {e}')
                continue
            page.wait_for_timeout(3500)
            # 切到「账号登录」标签
            for sel in ('text=账号登录', '.el-tabs__item:has-text("账号登录")',
                        '#tab-account', 'li:has-text("账号登录")'):
                try:
                    el = page.query_selector(sel)
                except Exception:
                    el = None
                if el:
                    try:
                        el.click()
                        page.wait_for_timeout(1800)
                        print(f'  切到账号登录（{sel}）')
                        break
                    except Exception as e:
                        print(f'  tab 点击失败 {sel}: {e}')
            title = (page.title() or '')[:80]
            cur = page.url[:130]
            print(f'[page] {url}\n  → {cur}\n  title={title!r}')

            info = page.evaluate("""() => {
                const out = {inputs: [], imgs: [], forms: [], buttons: []};
                document.querySelectorAll('input').forEach(el => out.inputs.push({
                    name: el.name || '', id: el.id || '', type: el.type || '',
                    placeholder: el.placeholder || '',
                    visible: !!(el.offsetWidth || el.offsetHeight)
                }));
                document.querySelectorAll('img').forEach(el => {
                    const s = el.src || '';
                    if (/code|captcha|verify|kaptcha/i.test(s + el.id + el.className))
                        out.imgs.push({src: s.slice(0, 160), id: el.id || '',
                                       w: el.width, h: el.height});
                });
                document.querySelectorAll('form').forEach(el => out.forms.push({
                    action: (el.action || '').slice(0, 120), id: el.id || ''
                }));
                document.querySelectorAll('button,input[type=submit],a.btn').forEach(el => {
                    if (el.offsetWidth) out.buttons.push({
                        tag: el.tagName, text: (el.innerText || el.value || '').trim().slice(0, 30),
                        id: el.id || '', cls: (el.className || '').toString().slice(0, 60)
                    });
                });
                return out;
            }""")
            import json
            print('  inputs :', json.dumps(info['inputs'][:10], ensure_ascii=False))
            print('  forms  :', json.dumps(info['forms'], ensure_ascii=False))
            print('  buttons:', json.dumps(info['buttons'][:6], ensure_ascii=False))
            print('  captcha:', json.dumps(info['imgs'], ensure_ascii=False))

            # 截图存档，便于人工判断形态
            try:
                shot = OUT / 'cas_login.png'
                page.screenshot(path=str(shot))
                print(f'  screenshot → {shot}')
            except Exception as e:
                print('  screenshot fail:', e)
            break
        try:
            ctx.close()
        except Exception:
            pass


if __name__ == '__main__':
    main()
