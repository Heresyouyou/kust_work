# -*- coding: utf-8 -*-
r"""GeoKB 外部数据路径统一入口。

2026-10-14 起，论文 PDF 仓库整体迁至 NAS：
    S: 盘 = \\192.168.10.1\KUST_SJCK_PUBLIC（公共共享）
    实际目录 = S:/GeoKB/papers_repo

注意踩过的坑：
- 不要把文件放到 \\192.168.10.1\Youyou（那是个人目录，用户的 Y: 盘）。
- kust.work 域名解析到 10.109.0.222，局域网内访问不到，必须用 192.168.10.1。
- 服务若跑在 SYSTEM 身份下看不到 S: 盘符，故这里保留 UNC 作为兜底候选。

所有脚本一律通过这里取路径，禁止再硬编码 D:/PostNews/papers_repo。
"""
import os

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG = os.path.join(BASE, "config", "paths.yaml")

DEFAULT_ROOT = "S:/GeoKB/papers_repo"
# 盘符未映射时（例如服务跑在别的会话里）的 UNC 兜底
UNC_ROOT = r"//192.168.10.1/KUST_SJCK_PUBLIC/GeoKB/papers_repo"
LEGACY_ROOT = "D:/PostNews/papers_repo"


def pdf_root():
    env = os.environ.get("GEOKB_PDF_ROOT")
    if env and os.path.isdir(env):
        return env.replace("\\", "/")
    try:
        import yaml
        if os.path.exists(CONFIG):
            cfg = yaml.safe_load(open(CONFIG, encoding="utf-8").read()) or {}
            p = cfg.get("pdf_root")
            if p and os.path.isdir(p):
                return str(p).replace("\\", "/")
    except Exception:
        pass
    for cand in (DEFAULT_ROOT, UNC_ROOT, LEGACY_ROOT):
        try:
            if os.path.isdir(cand):
                return cand.replace("\\", "/")
        except Exception:
            continue
    return DEFAULT_ROOT


def repo(*parts):
    return os.path.join(pdf_root(), *parts)


def by_journal():
    return repo("by_journal")


def addition():
    """用户自定义上传文献的专属目录（2026-10-21 新增）。

    归属规则：上传文献**不归属任何期刊**，只放 Addition/<年>/ 下，
    与 by_journal 平级。台账与条目 frontmatter 的 journal 字段均为 "Addition"。
    """
    return repo("Addition")


def by_date():
    return repo("by_date")


def index_jsonl():
    return repo("index.jsonl")


def text_cache():
    return os.path.join(BASE, "state", "text")


if __name__ == "__main__":
    print("pdf_root   :", pdf_root())
    print("by_journal :", by_journal())
    print("by_date    :", by_date())
    print("index.jsonl:", index_jsonl())
    print("text_cache :", text_cache())
