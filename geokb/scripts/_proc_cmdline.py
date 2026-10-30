# -*- coding: utf-8 -*-
"""读取进程完整命令行（PEB + ReadProcessMemory），并列出自启入口现状。"""
import ctypes
import ctypes.wintypes as wt
import os
import struct

k32 = ctypes.WinDLL("kernel32", use_last_error=True)
PROCESS_QUERY_INFORMATION = 0x0400
PROCESS_VM_READ = 0x0010


def cmdline(pid):
    class PROCESS_BASIC_INFORMATION(ctypes.Structure):
        _fields_ = [("ExitStatus", ctypes.c_long), ("PebBaseAddress", ctypes.c_void_p),
                    ("AffinityMask", ctypes.c_size_t), ("BasePriority", ctypes.c_long),
                    ("UniqueProcessId", ctypes.c_size_t),
                    ("InheritedFromUniqueProcessId", ctypes.c_size_t)]

    ntdll = ctypes.WinDLL("ntdll")
    h = k32.OpenProcess(PROCESS_QUERY_INFORMATION | PROCESS_VM_READ, False, pid)
    if not h:
        return "<无法打开 pid=%s>" % pid
    try:
        pbi = PROCESS_BASIC_INFORMATION()
        if ntdll.NtQueryInformationProcess(h, 0, ctypes.byref(pbi),
                                           ctypes.sizeof(pbi), None):
            return "<NtQuery 失败>"
        # PEB + 0x20 -> ProcessParameters ; +0x70 -> CommandLine (UNICODE_STRING)
        addr = ctypes.c_void_p()
        if not k32.ReadProcessMemory(h, ctypes.c_void_p(pbi.PebBaseAddress + 0x20),
                                     ctypes.byref(addr), ctypes.sizeof(addr), None):
            return "<读 PEB 失败>"
        base = addr.value or 0
        buf = ctypes.create_string_buffer(16)
        if not k32.ReadProcessMemory(h, ctypes.c_void_p(base + 0x70), buf, 16, None):
            return "<读参数块失败>"
        length, maximum, ptr = struct.unpack("<HHQ", buf.raw[:12])
        raw = ctypes.create_string_buffer(length + 2)
        if k32.ReadProcessMemory(h, ctypes.c_void_p(ptr), raw, length + 2, None):
            return raw.raw[:length].decode("utf-16-le", "ignore")
        return "<读命令行失败>"
    finally:
        k32.CloseHandle(h)


if __name__ == "__main__":
    for pid in (2540, 43932, 45888, 47816, 49676, 12768, 42088, 41724):
        print(f"[{pid}] {cmdline(pid)[:220]}")
    print()
    print("=" * 70)
    print("自启入口现状")
    print("=" * 70)
    tasks = r"C:\Windows\System32\Tasks"
    hits = []
    for root, _, files in os.walk(tasks):
        for f in files:
            low = f.lower()
            if any(k in low for k in ("geokb", "postnews", "kust", "portal", "web")):
                hits.append(os.path.join(root, f))
    print("计划任务命中:", hits or "无")
    for base in (os.path.expandvars(r"%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup"),
                 r"C:\ProgramData\Microsoft\Windows\Start Menu\Programs\StartUp"):
        try:
            print(f"启动目录 {base}: {os.listdir(base)}")
        except Exception as e:
            print(f"启动目录 {base}: <读取失败 {e}>")
    for reg_hint in (r"HKLM Run", r"HKCU Run"):
        print(reg_hint, "-> 见下一步")
