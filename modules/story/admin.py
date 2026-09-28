# -*- coding: utf-8 -*-
"""汇报说明书 · 后台管理（/story/admin）—— 像发文一样加一份汇报

页面：GET  /story/admin                  管理页（清单 + 发布表单）
页面：GET  /story/admin?edit=<sid>       同一张表单切到「编辑」模式
接口：GET  /api/story/admin/list          清单 JSON（给页面的「刷新」用）
接口：POST /api/story/admin/create        发布一份新汇报（multipart：pptx / STORY.md 可选）
接口：POST /api/story/admin/update/<sid>  改字段 / 换 pptx / 换 md
接口：POST /api/story/admin/rebuild/<sid> 重跑页图（换了 pptx 之后用）
接口：POST /api/story/admin/delete/<sid>  摘掉一条（目录移进 _trash，不真删）
接口：GET  /api/story/admin/job/<jid>     后台转换任务进度（前端轮询）

两条使用路径，正好对应用户的两句话：
  ① 有 PPT + STORY.md → 一份完整汇报（正文 + 放映 + 下载）
  ② 只有 PPT          → 「转成网页 PPT」：出页图、出放映入口，正文留空

设计上的三个取舍：
  · **发布与转换解耦**：登记条目是毫秒级的，页图转换要十几秒。所以创建接口立刻返回，
    转换丢给 deckbuild.job_start 跑线程，前端轮询进度 —— 否则浏览器要空转半分钟。
  · **删除是「移走」不是「删掉」**：目录移到 docs/_trash/<sid>-<时间戳>/。
    与项目里「归档只移不删」的既有铁律一致，误点也能捞回来。
  · **内置条目只读**：BUILTIN 里的条目随仓库走，改它要动代码；页面上给出提示而不是按钮。
"""
import re
import shutil
import time
from pathlib import Path
from urllib.parse import quote

from flask import Blueprint, Response, jsonify, request

from portal.templating import load_html, render_html

from . import deckbuild
from . import stories as REG

admin_bp = Blueprint('story_admin', __name__)

_DIR = Path(__file__).resolve().parent
_TEMPLATE = _DIR / 'templates' / 'admin.html'
_TRASH = REG.DOCS / '_trash'

# 上传文件名的清理：Windows 非法字符 + 路径分隔 + 控制字符
_BAD_CHARS = re.compile(r'[\\/:*?"<>|\x00-\x1f]')
MAX_NAME = 120


# ── 页面 ────────────────────────────────────────────────────────────────

@admin_bp.route('/story/admin')
def admin_page():
    edit = (request.args.get('edit') or '').strip()
    return render_html(_TEMPLATE, {
        '<!--ADM_TITLE-->': '编辑汇报 · 后台管理 · kust.work' if edit else '汇报管理 · kust.work',
        '<!--ADM_CRUMB-->': '编辑汇报' if edit else '汇报管理',
        '<!--ADM_FORM-->': _form_html(edit),
        '<!--ADM_ROWS-->': _rows_html(),
        '<!--ADM_ERRORS-->': _errors_html(),
    })


# ── 清单 ────────────────────────────────────────────────────────────────

@admin_bp.route('/api/story/admin/list')
def admin_list():
    out = []
    for i, s in enumerate(REG.all(), 1):
        sid = s['id']
        pptx, md = REG.pptx_path(s), REG.md_path(s)
        pages = REG.deck_files(s)
        jobs = deckbuild.jobs_of(sid)
        out.append({
            'no': i,
            'id': sid,
            'tab': s.get('tab') or sid,
            'title': s.get('title') or '',
            'builtin': REG.is_builtin(sid),
            'md': md.name if md else None,
            'md_size': REG.human_size(md),
            'pptx': pptx.name if pptx else None,
            'pptx_size': REG.human_size(pptx),
            'pages': len(pages),
            'job': jobs[0] if jobs else None,
            'url': '/story/%s' % quote(sid),
            'deck_url': '/story/%s/deck' % quote(sid) if pages else None,
        })
    return jsonify({'code': 0, 'message': 'ok',
                    'data': {'total': len(out), 'stories': out}})


# ── 发布 / 编辑 ─────────────────────────────────────────────────────────

def _field(name, default=''):
    return (request.form.get(name) or default).strip()


def _tags_of(raw):
    """标签串 → 列表。中英文逗号、顿号、竖线、换行都当分隔符。"""
    parts = re.split(r'[,，、|;\n]+', raw or '')
    return [p.strip() for p in parts if p.strip()][:8]


def _safe_name(upload_name):
    """上传文件名 → 可落盘的文件名（去路径、去非法字符、限长）。

    刻意不强制 ASCII：中文文件名是这个项目的常态，而下载接口已经用
    RFC 5987（filename*）把它完整传出去，`_ascii_fallback` 另有兜底。
    """
    name = _BAD_CHARS.sub('_', Path(upload_name or '').name).strip(' .')
    if len(name) > MAX_NAME:
        stem, dot, ext = name.rpartition('.')
        name = (stem[:MAX_NAME - len(ext) - 1] + dot + ext) if dot else name[:MAX_NAME]
    return name


def _common_fields():
    return {
        'tab': _field('tab'),
        'tab_note': _field('tab_note'),
        'title': _field('title'),
        'eyebrow': _field('eyebrow'),
        'subtitle': _field('subtitle'),
        'meta': _field('meta'),
        'brand': _field('brand') or '汇报说明书',
        'tags': _tags_of(_field('tags')),
        'note': _field('note'),
    }


def _save_uploads(docdir, entry, replace=False):
    """把表单里的 pptx / md 落盘。返回 (是否换了 pptx, 提示列表)。

    replace=True 时（编辑模式）会先把老的同名文件删掉，避免 docs/ 里堆版本。
    只要传了新 pptx 就算「换了」—— 哪怕文件名没变（用户在 WPS 里改完又存了一次，
    文件名通常不变，但页图必须重生成）。所以判据是「有没有传」，不是「名字变没变」。
    """
    msgs = []
    swapped = False

    up = request.files.get('pptx')
    if up and up.filename:
        name = _safe_name(up.filename)
        if not name.lower().endswith('.pptx'):
            name += '.pptx'
        if replace and entry.get('pptx') and entry['pptx'] != name:
            (docdir / entry['pptx']).unlink(missing_ok=True)
        up.save(str(docdir / name))
        entry['pptx'] = name
        swapped = True
        msgs.append('已保存 PPT：%s' % name)

    md_file = request.files.get('md')
    md_text = (request.form.get('md_text') or '')
    if md_file and md_file.filename:
        raw = md_file.read()
        for enc in ('utf-8-sig', 'utf-8', 'gbk'):
            try:
                text = raw.decode(enc)
                break
            except UnicodeDecodeError:
                continue
        else:
            raise ValueError('STORY.md 编码无法识别（试过 UTF-8 / GBK）—— 另存为 UTF-8 再传')
        (docdir / 'STORY.md').write_text(text, encoding='utf-8')
        entry['md'] = 'STORY.md'
        msgs.append('已保存 STORY.md（%.1f KB）' % (len(raw) / 1024.0))
    elif md_text.strip():
        (docdir / 'STORY.md').write_text(md_text, encoding='utf-8')
        entry['md'] = 'STORY.md'
        msgs.append('已保存 STORY.md（粘贴正文 %.1f KB）' % (len(md_text.encode('utf-8')) / 1024.0))
    elif replace and request.form.get('md_clear'):
        # 「移除正文」是显式勾选的动作 —— 空粘贴框只表示「这次不动它」，
        # 否则编辑一次标题就会顺手把 STORY.md 删掉，太容易误伤。
        if entry.pop('md', None):
            (docdir / 'STORY.md').unlink(missing_ok=True)
            msgs.append('已移除 STORY.md（本份只剩放映入口与 PPT 下载）')

    return swapped, msgs


@admin_bp.route('/api/story/admin/create', methods=['POST'])
def admin_create():
    fields = _common_fields()
    if not fields['tab']:
        return jsonify({'code': 1, 'message': '「标签名」必填 —— 它就是标签栏上那枚的名字'}), 400

    has_pptx = bool(request.files.get('pptx') and request.files['pptx'].filename)
    has_md = bool((request.files.get('md') and request.files['md'].filename)
                  or (request.form.get('md_text') or '').strip())
    if not has_pptx and not has_md:
        return jsonify({'code': 1,
                        'message': '至少要给一样：传 PPT（自动转网页放映版），'
                                   '或传/粘贴 STORY.md（只有正文）'}), 400

    sid = _field('sid') or REG.suggest_id()
    ok, why = REG.check_id(sid)
    if not ok:
        return jsonify({'code': 1, 'message': why}), 400

    if not fields['title']:
        fields['title'] = fields['tab']

    docdir = REG.DOCS / sid
    if docdir.exists():
        return jsonify({'code': 1, 'message': '目录已存在：docs/%s/' % sid}), 400
    docdir.mkdir(parents=True)

    entry = {'id': sid, **fields}
    try:
        _swapped, msgs = _save_uploads(docdir, entry)
    except ValueError as e:
        shutil.rmtree(docdir, ignore_errors=True)      # 落盘失败就收拾干净，别留半个目录
        return jsonify({'code': 1, 'message': str(e)}), 400
    except OSError as e:
        shutil.rmtree(docdir, ignore_errors=True)
        return jsonify({'code': 1, 'message': '写盘失败：%s' % e}), 500

    if not entry.get('pptx') and not entry.get('md'):
        shutil.rmtree(docdir, ignore_errors=True)
        return jsonify({'code': 1, 'message': 'PPT 与 STORY.md 都没落盘成功'}), 400
    try:
        REG.add_custom(entry)
    except ValueError as e:
        shutil.rmtree(docdir, ignore_errors=True)
        return jsonify({'code': 1, 'message': str(e)}), 400

    job = _start_deck_job(sid, entry, msgs)
    return jsonify({'code': 0, 'message': '已发布', 'data': {
        'sid': sid, 'tab': entry['tab'], 'msgs': msgs,
        'job': job['jid'] if job else None,
        'url': '/story/%s' % quote(sid),
    }})


@admin_bp.route('/api/story/admin/update/<sid>', methods=['POST'])
def admin_update(sid):
    s = REG.get(sid)
    if not s:
        return jsonify({'code': 1, 'message': '没有这份汇报：%s' % sid}), 404
    if REG.is_builtin(sid):
        return jsonify({'code': 1, 'message': '内置条目随仓库走，请改 stories.py 后重启'}), 403

    fields = _common_fields()
    if not fields['tab']:
        return jsonify({'code': 1, 'message': '「标签名」必填'}), 400
    if not fields['title']:
        fields['title'] = fields['tab']

    docdir = REG.DOCS / sid
    entry = dict(s)
    msgs = []
    try:
        swapped, msgs = _save_uploads(docdir, entry, replace=True)
    except ValueError as e:
        return jsonify({'code': 1, 'message': str(e)}), 400
    except OSError as e:
        return jsonify({'code': 1, 'message': '写盘失败：%s' % e}), 500

    patch = dict(fields, pptx=entry.get('pptx'), md=entry.get('md'))
    REG.update_custom(sid, patch)

    # 只有真的传了新 pptx 才重建页图；纯改字段不该让用户等十几秒
    job = _start_deck_job(sid, entry, msgs, force=True) if swapped else None
    return jsonify({'code': 0, 'message': '已保存', 'data': {
        'sid': sid, 'msgs': msgs, 'job': job['jid'] if job else None}})


@admin_bp.route('/api/story/admin/rebuild/<sid>', methods=['POST'])
def admin_rebuild(sid):
    """重跑页图 —— 换了 pptx（哪怕文件名没变）之后手动触发。"""
    s = REG.get(sid)
    if not s:
        return jsonify({'code': 1, 'message': '没有这份汇报：%s' % sid}), 404
    pptx = REG.pptx_path(s)
    if not pptx:
        return jsonify({'code': 1, 'message': '这份没有 PPT 原文件，无法生成页图'}), 400
    job = _start_deck_job(sid, s, [], force=True)
    return jsonify({'code': 0, 'message': '已开始重建', 'data': {'job': job['jid']}})


@admin_bp.route('/api/story/admin/delete/<sid>', methods=['POST'])
def admin_delete(sid):
    """摘掉一条运行期条目：注册表删掉，目录移进 docs/_trash/（可回捞）。"""
    if REG.is_builtin(sid):
        return jsonify({'code': 1, 'message': '内置条目不能在页面上删（改 stories.py 可移除）'}), 403
    hit = REG.drop_custom(sid)
    if not hit:
        return jsonify({'code': 1, 'message': '没有这份汇报：%s' % sid}), 404

    moved = None
    src = REG.DOCS / sid
    if src.is_dir():
        _TRASH.mkdir(parents=True, exist_ok=True)
        dst = _TRASH / ('%s-%s' % (sid, time.strftime('%Y%m%d-%H%M%S')))
        try:
            shutil.move(str(src), str(dst))
            moved = str(dst.relative_to(REG.DOCS)).replace('\\', '/')
        except OSError as e:
            # 目录没搬走不算失败 —— 条目已经下线，页面不再引用它
            return jsonify({'code': 0, 'message': '条目已移除，但目录搬运失败：%s' % e,
                            'data': {'sid': sid, 'trash': None}})
    return jsonify({'code': 0, 'message': '已移除',
                    'data': {'sid': sid, 'trash': moved}})


@admin_bp.route('/api/story/admin/job/<jid>')
def admin_job(jid):
    j = deckbuild.job_get(jid)
    if not j:
        return jsonify({'code': 1, 'message': '任务不存在或已过期'}), 404
    return jsonify({'code': 0, 'data': j})


# ── 内部 ────────────────────────────────────────────────────────────────

def _start_deck_job(sid, entry, msgs, force=True):
    """有 pptx 就把「生成页图」丢进后台线程；没有就什么都不做。"""
    pptx = REG.pptx_path(entry)
    if not pptx:
        return None
    deckdir = REG.DOCS / sid / 'deck'

    def work(log):
        for m in msgs:
            log(m)
        return deckbuild.convert(str(pptx), str(deckdir), force=force, log=log)

    return deckbuild.job_start('生成页图', sid, work)


def _js_str(s):
    return '"' + str(s).replace('\\', '\\\\').replace('"', '\\"') + '"'


def _errors_html():
    """把「不给 JS 也能看懂」的一条提示放在表单上方。"""
    return ('<p class="hint">需要脚本支持：发布后的页图转换进度会实时刷新。'
            '若浏览器禁用了 JS，发布仍会生效，只是要手动刷新看结果。</p>')


# ── HTML 片段 ───────────────────────────────────────────────────────────

def _form_html(edit_sid):
    """发布 / 编辑表单。编辑模式下把已有值填回去（服务端预填，不靠 JS）。"""
    s = REG.get(edit_sid) if edit_sid else None
    if edit_sid and not s:
        return '<div class="admon">找不到 id 为 <b>%s</b> 的汇报。</div>' % edit_sid
    if s and REG.is_builtin(edit_sid):
        return ('<div class="admon">「%s」是<b>内置条目</b>（写在 stories.py 里，随仓库走），'
                '页面上不提供编辑。要改它请直接改代码后重启 Flask；'
                '想另存一份改过的版本，就在下面新建一个。</div>' % edit_sid)

    def val(k, d=''):
        return _attr((s or {}).get(k) or d)

    tags = '，'.join((s or {}).get('tags') or [])
    sid_field = (
        '<div class="row"><label>目录名 / id<span>小写字母数字与 . _ -；留空自动生成'
        '（如 story-20260918-1800）。它同时是 URL 与目录名 <code>docs/&lt;id&gt;/</code>，'
        '建好后不可改</span></label>'
        '<input name="sid" value="%s" placeholder="留空自动生成" pattern="[a-z0-9][a-z0-9._\\-]*"></div>'
        % val('id')
    ) if s else (
        '<div class="row"><label>目录名 / id<span>可留空自动生成（story-日期-时间）</span></label>'
        '<input name="sid" placeholder="留空自动生成"></div>'
    )

    md_note = ''
    md_clear = ''
    if s:
        has_md = bool(REG.md_path(s))
        md_note = ('<span>编辑模式：传新文件会覆盖；粘贴框留空则<b>保持原样</b>'
                   '（本份%s）</span>' % ('已有 STORY.md' if has_md else '当前没有 STORY.md'))
        if has_md:
            md_clear = ('<label class="ck"><input type="checkbox" name="md_clear" value="1">'
                        '移除 STORY.md（本份只保留放映入口与下载）</label>')

    return '''
<form class="pub" id="pub" data-mode="%s" data-sid="%s" autocomplete="off">
  <div class="grid">
    <div class="row"><label>标签名 <b>*</b><span>标签栏上那枚的名字，如「组会汇报」</span></label>
      <input name="tab" value="%s" placeholder="组会汇报" required></div>
    <div class="row"><label>标签副注<span>标签上的小字，如「21 页 · 11 min」</span></label>
      <input name="tab_note" value="%s" placeholder="21 页 · 11 min"></div>
  </div>
  <div class="row"><label>标题 <b>*</b><span>hero 的大标题；留空则用标签名</span></label>
    <input name="title" value="%s" placeholder="组会汇报：近期学习总结 + 工作进展"></div>
  <div class="grid">
    <div class="row"><label>眉标（eyebrow）<span>标题上方那行小字</span></label>
      <input name="eyebrow" value="%s" placeholder="课题组组会 · 2026-09"></div>
    <div class="row"><label>时间/来源（meta）<span>顶栏右侧</span></label>
      <input name="meta" value="%s" placeholder="21 页 · 约 11 分钟"></div>
  </div>
  <div class="row"><label>概要<span>标题下那段说明，一两句即可</span></label>
    <textarea name="subtitle" rows="2" placeholder="一次组会、两个部分、连续讲述，合计约 11 分钟。">%s</textarea></div>
  <div class="grid">
    <div class="row"><label>标签片（tags）<span>逗号分隔，最多 8 个</span></label>
      <input name="tags" value="%s" placeholder="第一部分 · 钨矿研究现状，第二部分 · kust.work"></div>
    <div class="row"><label>备注（给自己看）<span>只存在 stories.json 里，不上页面</span></label>
      <input name="note" value="%s" placeholder=""></div>
  </div>
  %s

  <div class="drop">
    <div class="row"><label>PPT（.pptx）<span>选了就自动转成网页放映版：出页图 + 放映入口</span></label>
      <input type="file" name="pptx" accept=".pptx,application/vnd.openxmlformats-officedocument.presentationml.presentation"></div>
    <div class="row"><label>STORY.md<span>可选。不给也能发布 —— 那样就只有放映入口，正文留空</span></label>
      <input type="file" name="md" accept=".md,.txt,.markdown"></div>
    <div class="row"><label>或直接粘贴 STORY.md 正文<span>%s</span></label>
      <textarea name="md_text" rows="6" placeholder="## 数据索引&#10;&#10;| 数字 | 出处 |&#10| ... |"></textarea>
      %s</div>
  </div>

  <div class="acts">
    <button type="submit" class="primary" id="pubBtn">%s</button>
    <a class="ghost" href="/story/admin">重置</a>
    <span class="status" id="pubStatus"></span>
  </div>
</form>
<div class="prog-box" id="progBox" hidden>
  <div class="pb-h"><b id="pbName">生成页图</b><span id="pbState">进行中…</span></div>
  <pre id="pbLog"></pre>
  <div class="pb-f" id="pbFoot"></div>
</div>
''' % ('edit' if s else 'create',
       _attr(edit_sid or ''),
       val('tab'), val('tab_note'), val('title'), val('eyebrow'), val('meta'),
       _esc((s or {}).get('subtitle') or ''), _attr(tags), val('note'),
       sid_field, md_note, md_clear,
       '保存修改' if s else '发布')


def _attr(v):
    return (str(v).replace('&', '&amp;').replace('"', '&quot;')
            .replace('<', '&lt;').replace('>', '&gt;'))


def _esc(v):
    return (str(v).replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;'))


def _rows_html():
    rows = []
    for i, s in enumerate(REG.all(), 1):
        sid = s['id']
        builtin = REG.is_builtin(sid)
        pptx, md = REG.pptx_path(s), REG.md_path(s)
        pages = REG.deck_files(s)
        jobs = deckbuild.jobs_of(sid)
        job = jobs[0] if jobs else None

        badges = ['<span class="b b-in">内置</span>'] if builtin else \
                 ['<span class="b b-own">自建</span>']
        if pages:
            badges.append('<span class="b b-on">放映 %d 页</span>' % len(pages))
        else:
            badges.append('<span class="b b-off">无页图</span>')
        if not md:
            badges.append('<span class="b b-off">无正文</span>')
        if job and job['state'] == 'running':
            badges.append('<span class="b b-run">转换中…</span>')
        elif job and job['state'] == 'error':
            badges.append('<span class="b b-err">转换失败</span>')

        links = ['<a href="/story/%s" target="_blank">说明书</a>' % quote(sid)]
        if pages:
            links.append('<a href="/story/%s/deck" target="_blank">放映</a>' % quote(sid))
        if pptx:
            links.append('<a href="/api/story/ppt/%s">下载</a>' % quote(sid))
        if pptx:
            links.append('<button type="button" class="lk" data-act="rebuild" data-sid="%s">'
                         '重建页图</button>' % _attr(sid))
        if builtin:
            links.append('<span class="dim" title="内置条目随仓库走，改它要动 stories.py">—</span>')
        else:
            links.append('<a href="/story/admin?edit=%s">编辑</a>' % quote(sid))
            links.append('<button type="button" class="lk danger" data-act="delete" data-sid="%s" '
                         'data-tab="%s">移除</button>' % (_attr(sid), _attr(s.get('tab') or sid)))

        detail = []
        if md:
            detail.append('MD %s <span class="dim">%s</span>'
                          % (_esc(md.name), REG.human_size(md) or ''))
        if pptx:
            detail.append('PPTX %s <span class="dim">%s</span>'
                          % (_esc(pptx.name), REG.human_size(pptx) or ''))
        if not detail:
            detail.append('<span class="dim">目录里没有文件</span>')

        rows.append(
            '<tr data-sid="%s">'
            '<td class="no">%02d</td>'
            '<td><div class="tab-name">%s</div><div class="sub">%s</div></td>'
            '<td class="id"><code>%s</code></td>'
            '<td><div class="badges">%s</div><div class="sub">%s</div></td>'
            '<td class="ops">%s</td>'
            '</tr>'
            % (_attr(sid), i, _esc(s.get('tab') or sid),
               _esc(s.get('title') or ''), _esc(sid),
               ' '.join(badges), ' ｜ '.join(detail), ' '.join(links)))

    if not rows:
        return '<tr><td colspan="5" class="empty">还没有任何汇报。</td></tr>'
    return ''.join(rows)
