"""只读：检查是否有遗留的 ScienceDirect 闯关/浏览器进程在跑。不做任何终止动作。"""
import sys
import psutil

KEYS = ("browser_verify_fetch", "sd_login_once", "sd_print_pdf", "playwright", "patchright")
SELF = psutil.Process().pid

hits = []
for p in psutil.process_iter(["pid", "name", "cmdline"]):
    try:
        if p.info["pid"] == SELF:
            continue
        name = (p.info["name"] or "").lower()
        cmd = " ".join(p.info["cmdline"] or [])
        low = cmd.lower()
        if name.startswith(("msedge", "chrome")):
            if "sciencedirect_auth" in low or "browser_profiles" in low:
                hits.append((p.info["pid"], p.info["name"], cmd[:200]))
            continue
        if any(k in low for k in KEYS):
            hits.append((p.info["pid"], p.info["name"], cmd[:200]))
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        pass

if not hits:
    print("clean: 无遗留 sd/浏览器自动化进程")
else:
    for pid, name, cmd in hits:
        print(f"{pid}\t{name}\t{cmd}")
sys.exit(0)
