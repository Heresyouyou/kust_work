# -*- coding: utf-8 -*-
"""知识点提示词：真源校验 + skill 同步。

真源（唯一权威）：config/prompts/kp_candidates.md
镜像（人读版）：  ~/.workbuddy/skills/geokb-kp-writing/SKILL.md 的受管区块

用法：
  venv/Scripts/python.exe scripts/kp_prompt.py --check          # 校验（改完提示词必跑）
  venv/Scripts/python.exe scripts/kp_prompt.py --sync-skill     # 渲染真源 → skill 受管区块
  venv/Scripts/python.exe scripts/kp_prompt.py --show main      # 打印某一段（main/repair/lang:zh/lang:en）

退出码：0 一致 / 1 有问题（可挂到日检脚本）。
"""
import os
import re
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(BASE, "scripts"))

PROMPT_FILE = (os.environ.get("GEOKB_KP_PROMPT")
               or os.path.join(BASE, "config", "prompts", "kp_candidates.md"))
SKILL_FILE = (os.environ.get("GEOKB_KP_SKILL")
              or os.path.expanduser("~/.workbuddy/skills/geokb-kp-writing/SKILL.md"))

BEGIN = "<!-- BEGIN:auto-prompt（由 scripts/kp_prompt.py --sync-skill 生成，勿手改）-->"
END = "<!-- END:auto-prompt -->"

# 平台侧需要保证存在的关键语义（防止有人改提示词时把判据改没了）
MUST_HAVE = [
    ("可复用性判据", "换一个研究区、换一个矿床，还用得上吗"),
    ("五种复用方式", "本区事实"),
    ("60/40 分工", "只占最终决定的 **60%**"),
    ("value 区分度要求", "大多数论文只有 3–6 条能到 7 分以上"),
    ("must 特权", '"verdict":"must"'),
    ("drop 否决", '"verdict":"drop"'),
    ("禁止凑数", "不要考虑条数上限"),
    ("逐字引文", "从原文**逐字**抄"),
]


def load_sections(path=None):
    """解析真源 → {main, repair, lang:{zh,en}}（与 kp_extract.parse_prompt_file 同规则）。"""
    path = path or PROMPT_FILE
    if not os.path.exists(path):
        return {}
    txt = open(path, encoding="utf-8").read()
    out, cur, buf = {}, None, []
    for line in txt.splitlines():
        m = re.match(r"^##\s+(main|repair|lang:zh|lang:en)\s*$", line)
        if m:
            if cur:
                out[cur] = "\n".join(buf).strip("\n")
            cur, buf = m.group(1), []
        elif cur is not None:
            buf.append(line)
    if cur:
        out[cur] = "\n".join(buf).strip("\n")
    lang = {k.split(":", 1)[1]: v for k, v in out.items()
            if k.startswith("lang:") and v.strip()}
    return {"main": (out.get("main") or "").strip(),
            "repair": (out.get("repair") or "").strip(), "lang": lang}


def render(sections, which="main", plain=True):
    """把模板渲染成人可读文本（填掉占位符，可选去掉 format 转义）。"""
    import kp_select
    types = " / ".join(kp_select.TYPE_ORDER)
    dt = kp_select.TYPE_ORDER[0]
    body = sections.get(which) or ""
    body = (body.replace("{types}", types).replace("{default_type}", dt)
            .replace("{text}", "{{论文全文，带 L<n> 行号前缀}}")
            .replace("{gaps}", "{{缺失的定量结论清单，每行一条}}")
            .replace("{lang_rule}", (sections.get("lang") or {}).get("zh", "")))
    if plain:
        body = body.replace("{{", "{").replace("}}", "}")
    return body


def build_block(sections):
    """生成 skill 受管区块（含中英语言规则说明）。"""
    lang = sections.get("lang") or {}
    parts = [
        BEGIN,
        "",
        "> 以下内容由 `D:/ZSK/geokb/config/prompts/kp_candidates.md` 渲染而来，",
        "> 与 12:01 / 18:01 的 API 入库链路（`kp_extract.py`）**同一份提示词**。",
        "> 修改请在真源改，然后跑 `python scripts/kp_prompt.py --sync-skill`。",
        "",
        "### 主抽取提示词",
        "",
        "```text",
        render(sections, "main").strip(),
        "```",
        "",
        "### 补抽提示词（仅在覆盖不足时追加，命中前缀缓存）",
        "",
        "```text",
        render(sections, "repair").strip(),
        "```",
        "",
        "### 语言规则",
        "",
        "- 中文论文（zh）：" + (lang.get("zh") or "(缺失)"),
        "- 英文论文（en）：" + (lang.get("en") or "(缺失)"),
        "- **英文论文的知识点必须用英文原文写，禁止回译**（与条目「英文原文为主」一致）。",
        "",
        END,
    ]
    return "\n".join(parts)


def sync_skill(write=True):
    """把真源渲染进 skill 受管区块；返回 (ok, msg)。"""
    sections = load_sections()
    if not sections.get("main"):
        return False, "真源不可解析：%s" % PROMPT_FILE
    block = build_block(sections)
    if not os.path.exists(SKILL_FILE):
        if not write:
            return False, "skill 不存在：%s" % SKILL_FILE
        os.makedirs(os.path.dirname(SKILL_FILE), exist_ok=True)
        open(SKILL_FILE, "w", encoding="utf-8").write(
            "# GeoKB 知识点撰写（Agent 模式）\n\n" + block + "\n")
        return True, "已新建 skill 并写入受管区块：%s" % SKILL_FILE
    txt = open(SKILL_FILE, encoding="utf-8").read()
    if BEGIN in txt and END in txt:
        new = re.sub(re.escape(BEGIN) + r"[\s\S]*?" + re.escape(END), lambda _m: block, txt)
    else:
        new = txt.rstrip("\n") + "\n\n" + block + "\n"
    if not write:
        return new == txt, ("一致" if new == txt else "受管区块与真源不一致，请跑 --sync-skill")
    if new != txt:
        open(SKILL_FILE, "w", encoding="utf-8").write(new)
        return True, "已更新受管区块：%s" % SKILL_FILE
    return True, "已是最新：%s" % SKILL_FILE


def check():
    """全量校验，返回 (ok, [问题…], [提示…])。"""
    problems, notes = [], []
    if not os.path.exists(PROMPT_FILE):
        return False, ["真源不存在：%s" % PROMPT_FILE], []
    sections = load_sections()
    for key in ("main", "repair"):
        if not sections.get(key):
            problems.append("真源缺少 `## %s` 段或其内容为空" % key)
    for key in ("zh", "en"):
        if not (sections.get("lang") or {}).get(key):
            problems.append("真源缺少 `## lang:%s` 段或其内容为空" % key)

    main = sections.get("main") or ""
    for name, needle in MUST_HAVE:
        if needle not in main:
            problems.append("主提示词丢失关键判据「%s」（找不到 %r）" % (name, needle))
    for ph in ("{text}", "{types}", "{default_type}", "{lang_rule}"):
        if ph not in main:
            problems.append("主提示词缺少占位符 %s（代码注入会失效）" % ph)
    if "{gaps}" not in (sections.get("repair") or ""):
        problems.append("补抽提示词缺少占位符 {gaps}")
    if "{{" not in main or "}}" not in main:
        notes.append("主提示词里没有 {{ }} 转义——若改了 JSON 示例，请确认 format 不会崩")

    # 代码侧是否真的读到了真源
    try:
        import kp_extract
        if kp_extract.PROMPT_SOURCE != "file":
            problems.append("kp_extract 未从真源加载（source=%s），当前用的是内置回退副本"
                            % kp_extract.PROMPT_SOURCE)
        if kp_extract._file_prompt.get("main") != main:  # noqa: SLF001
            problems.append("kp_extract 解析结果与 kp_prompt 解析结果不一致（解析逻辑漂移）")
        notes.append("kp_extract：source=%s，类目 %d 种" %
                     (kp_extract.PROMPT_SOURCE, len(kp_extract.kp_select.TYPE_ORDER)))
    except Exception as e:  # noqa: BLE001
        problems.append("导入 kp_extract 失败：%s" % e)

    # skill 镜像是否同步
    if not os.path.exists(SKILL_FILE):
        problems.append("skill 不存在：%s（跑 --sync-skill）" % SKILL_FILE)
    else:
        txt = open(SKILL_FILE, encoding="utf-8").read()
        if BEGIN not in txt or END not in txt:
            problems.append("skill 缺少受管区块标记（跑 --sync-skill）")
        else:
            cur = re.search(re.escape(BEGIN) + r"[\s\S]*?" + re.escape(END), txt)
            if not cur or cur.group(0).strip() != build_block(sections).strip():
                problems.append("skill 受管区块与真源不一致（跑 --sync-skill）")
            else:
                notes.append("skill 镜像一致：%s" % SKILL_FILE)
    return (not problems), problems, notes


def main():
    argv = sys.argv[1:] or ["--check"]
    if "--sync-skill" in argv:
        ok, msg = sync_skill(write=True)
        print(("[OK] " if ok else "[FAIL] ") + msg)
        return 0 if ok else 1
    if "--show" in argv:
        i = argv.index("--show")
        which = argv[i + 1] if i + 1 < len(argv) else "main"
        sections = load_sections()
        if which.startswith("lang:"):
            print((sections.get("lang") or {}).get(which.split(":", 1)[1], ""))
        else:
            print(render(sections, which))
        return 0
    ok, problems, notes = check()
    print("真源：%s" % PROMPT_FILE)
    print("镜像：%s" % SKILL_FILE)
    for n in notes:
        print("  · %s" % n)
    for p in problems:
        print("  [FAIL] %s" % p)
    print("结论：%s" % ("全部一致" if ok else "发现 %d 个问题" % len(problems)))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
