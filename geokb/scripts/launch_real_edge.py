"""用本机真实 Edge（Default profile）打开论文页 —— 不复制 profile、不注入调试参数。

用户明确要求：
    "C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe" --profile-directory=Default

要点：
  - 直接用安装目录里的 msedge.exe，走真实 Default profile（完整机构登录态）；
  - 不传 --remote-debugging-* / --no-sandbox / --disable-blink-features，零自动化痕迹；
  - DETACHED_PROCESS + CREATE_NEW_PROCESS_GROUP，让浏览器脱离本进程的作业对象，
    父进程退出后窗口仍在。
"""
import subprocess
import sys

MSEDGE = r'C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe'
DEFAULT_URL = 'https://www.sciencedirect.com/science/article/pii/S0169136823004328'

url = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_URL
new_window = '--new-window' in sys.argv

args = [MSEDGE, '--profile-directory=Default']
if new_window:
    args.append('--new-window')
args.append(url)

flags = 0x00000008 | 0x00000200          # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
p = subprocess.Popen(args, close_fds=True, creationflags=flags)
print('launched pid=%s' % p.pid)
print('cmd: %s' % ' '.join(args))
