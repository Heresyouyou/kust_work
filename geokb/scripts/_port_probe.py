# -*- coding: utf-8 -*-
"""列出占用指定端口的进程（纯 ctypes，避开被拉黑的 wmic/tasklist）。"""
import ctypes
import ctypes.wintypes as wt
import socket
import struct
import sys

k32 = ctypes.WinDLL("kernel32", use_last_error=True)
psapi = ctypes.WinDLL("psapi", use_last_error=True)

PROCESS_QUERY_INFORMATION = 0x0400
PROCESS_VM_READ = 0x0010


class PROCESSENTRY32(ctypes.Structure):
    _fields_ = [
        ("dwSize", wt.DWORD), ("cntUsage", wt.DWORD), ("th32ProcessID", wt.DWORD),
        ("th32DefaultHeapID", ctypes.POINTER(ctypes.c_ulong)),
        ("th32ModuleID", wt.DWORD), ("cntThreads", wt.DWORD),
        ("th32ParentProcessID", wt.DWORD), ("pcPriClassBase", ctypes.c_long),
        ("dwFlags", wt.DWORD), ("szExeFile", ctypes.c_char * 260),
    ]


def enum_processes():
    snap = k32.CreateToolhelp32Snapshot(0x00000002, 0)
    if snap == ctypes.c_void_p(-1).value:
        return {}
    pe = PROCESSENTRY32()
    pe.dwSize = ctypes.sizeof(PROCESSENTRY32)
    out = {}
    if k32.Process32First(snap, ctypes.byref(pe)):
        while True:
            out[pe.th32ProcessID] = (pe.th32ParentProcessID,
                                     bytes(pe.szExeFile).split(b"\x00")[0].decode("utf-8", "ignore"))
            if not k32.Process32Next(snap, ctypes.byref(pe)):
                break
    k32.CloseHandle(snap)
    return out


def cmdline_of(pid):
    h = k32.OpenProcess(PROCESS_QUERY_INFORMATION | PROCESS_VM_READ, False, pid)
    if not h:
        return "<无法打开>"
    try:
        # NtQueryInformationProcess, class 60 -> PEB 只读近似：改用 Win32_CommandLine 不可用时降级
        buf = ctypes.create_unicode_buffer(4096)
        # QueryFullProcessImageName
        size = wt.DWORD(4096)
        try:
            k32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size))
            return buf.value
        except Exception:
            return "<未知>"
    finally:
        k32.CloseHandle(h)


def listening_table():
    """用 GetExtendedTcpTable 拿到 (pid, local_addr, local_port, state)。"""
    iphlp = ctypes.WinDLL("iphlpapi", use_last_error=True)
    TCP_TABLE_OWNER_PID_ALL = 5
    buf = ctypes.create_string_buffer(0)
    size = wt.DWORD(0)
    r = iphlp.GetExtendedTcpTable(None, ctypes.byref(size), False, 2,
                                  TCP_TABLE_OWNER_PID_ALL, 0)
    buf = ctypes.create_string_buffer(size.value)
    if iphlp.GetExtendedTcpTable(buf, ctypes.byref(size), False, 2,
                                 TCP_TABLE_OWNER_PID_ALL, 0):
        return []
    n = struct.unpack("<I", buf[:4])[0]
    rows = []
    off = 4
    for _ in range(n):
        st, la, lp, ra, rp, pid = struct.unpack("<IIIIII", buf[off:off + 24])
        ip = socket.inet_ntoa(struct.pack("<I", la))
        rows.append((pid, ip, socket.ntohs(lp), socket.ntohs(rp), st))
        off += 24
    return rows


if __name__ == "__main__":
    procs = enum_processes()
    want = {5554, 5555, 5556}
    print("=" * 78)
    print("端口监听情况（来自 GetExtendedTcpTable）")
    print("=" * 78)
    rows = [r for r in listening_table() if r[2] in want]
    if not rows:
        print("未发现 5554/5555/5556 任何监听")
    for pid, ip, lp, rp, st in rows:
        state = {2: "LISTENING", 5: "ESTABLISHED"}.get(st, str(st))
        parent, name = procs.get(pid, (0, "<已退出>"))
        print(f"  {ip}:{lp:<6} {state:<12} pid={pid:<7} exe={name}")
        if st == 2:
            print(f"        路径: {cmdline_of(pid)}")
            if parent:
                pn, pex = parent, procs.get(parent, (0, "?"))[1]
                print(f"        父进程: {pex} (pid={pn})")
    print()
    print("当前 python/supervisor 相关进程：")
    for pid, (ppid, name) in sorted(procs.items()):
        low = name.lower()
        if "python" in low or "supervis" in low:
            print(f"  {pid:<7} ppid={ppid:<7} {name}")
