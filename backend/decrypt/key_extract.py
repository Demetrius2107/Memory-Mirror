"""密钥提取骨架（Windows，R18 实验性）—— 内存取证思路，非破解算法

方法 A（wx3 偏移法）：GetModuleHandle("WeChatWin.dll") 基址 + KEY_OFFSET 读指针 → 32B
方法 B（内存扫描法）：
  - wx3：扫 WeChatWin.dll 内存范围找设备串 "android"/"iphone"，key 在其前（向前逐字节扫 32B 并校验）
  - wx4：扫 Weixin.exe 内存匹配 `x'<64hex_enc_key><32hex_salt>'` 模式 → HMAC page1 校验

运行需本机已登录运行微信；未检测到微信进程时仅打印提示，不报错。
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wt

PROCESS_VM_READ = 0x0010
PROCESS_QUERY_INFORMATION = 0x0400
TH32CS_SNAPPROCESS = 0x2
TH32CS_SNAPMODULE = 0x8


class PROCESSENTRY32(ctypes.Structure):
    _fields_ = [
        ("dwSize", wt.DWORD), ("cntUsage", wt.DWORD), ("th32ProcessID", wt.DWORD),
        ("th32DefaultHeapID", ctypes.POINTER(wt.ULONG)), ("th32ModuleID", wt.DWORD),
        ("cntThreads", wt.DWORD), ("th32ParentProcessID", wt.DWORD),
        ("pcPriClassBase", wt.LONG), ("dwFlags", wt.DWORD),
        ("szExeFile", ctypes.c_char * 260),
    ]


class MODULEENTRY32(ctypes.Structure):
    _fields_ = [
        ("dwSize", wt.DWORD), ("th32ModuleID", wt.DWORD), ("th32ProcessID", wt.DWORD),
        ("GlblcntUsage", wt.DWORD), ("ProccntUsage", wt.DWORD),
        ("modBaseAddr", wt.LPVOID), ("modBaseSize", wt.DWORD),
        ("hModule", wt.HMODULE), ("szModule", ctypes.c_char * 256), ("szExePath", ctypes.c_char * 260),
    ]


def find_process_pid(name: bytes) -> int | None:
    """按进程名（如 b"WeChat.exe" / b"Weixin.exe"）找 PID。"""
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    snap = kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    if snap == wt.HANDLE(-1).value:
        return None
    try:
        entry = PROCESSENTRY32()
        entry.dwSize = ctypes.sizeof(PROCESSENTRY32)
        if not kernel32.Process32First(snap, ctypes.byref(entry)):
            return None
        while True:
            if entry.szExeFile.lower() == name.lower():
                return entry.th32ProcessID
            if not kernel32.Process32Next(snap, ctypes.byref(entry)):
                return None
    finally:
        kernel32.CloseHandle(snap)


def module_base(pid: int, module_name: bytes) -> tuple[int, int] | None:
    """返回 (模块基址, 模块大小)（按模块名）。"""
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    snap = kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPMODULE, pid)
    if snap == wt.HANDLE(-1).value:
        return None
    try:
        entry = MODULEENTRY32()
        entry.dwSize = ctypes.sizeof(MODULEENTRY32)
        if not kernel32.Module32First(snap, ctypes.byref(entry)):
            return None
        while True:
            if entry.szModule.lower() == module_name.lower():
                return entry.modBaseAddr, entry.modBaseSize
            if not kernel32.Module32Next(snap, ctypes.byref(entry)):
                return None
    finally:
        kernel32.CloseHandle(snap)


def read_mem(pid: int, addr: int, size: int) -> bytes | None:
    """ReadProcessMemory 读任意地址。"""
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    h = kernel32.OpenProcess(PROCESS_VM_READ | PROCESS_QUERY_INFORMATION, False, pid)
    if not h:
        return None
    try:
        buf = ctypes.create_string_buffer(size)
        nread = wt.SIZE_T(0)
        ok = kernel32.ReadProcessMemory(h, ctypes.c_void_p(addr), buf, size, ctypes.byref(nread))
        return buf.raw[: nread.value] if ok else None
    finally:
        kernel32.CloseHandle(h)


def scan_key_by_pattern(pid: int, base: int, size: int, needle: bytes, chunk: int = 0x10000) -> list[int]:
    """在 [base, base+size) 内存范围内扫描 needle（跨 chunk 重叠 1 字节滑窗）。"""
    hits = []
    overlap = len(needle) - 1
    prev_tail = b""
    for off in range(0, size, chunk - overlap):
        buf = read_mem(pid, base + off, min(chunk, size - off))
        if not buf:
            continue
        data = prev_tail + buf
        idx = data.find(needle)
        while idx != -1:
            hits.append(base + off - len(prev_tail) + idx)
            idx = data.find(needle, idx + 1)
        prev_tail = data[-overlap:]
    return hits


def extract_keys_wx4(pid: int, base: int, size: int) -> list[bytes]:
    """wx4：匹配 `x'<64hex><32hex>'` 模式，返回 (enc_key_hex, salt_hex) 候选列表。"""
    import re

    raw = bytearray()
    for off in range(0, size, 0x20000):
        buf = read_mem(pid, base + off, min(0x20000, size - off))
        if buf:
            raw += buf
    # 匹配 x'<64 hex><32 hex>'
    results = []
    for m in re.finditer(rb"x'([0-9a-fA-F]{64})([0-9a-fA-F]{32})'", bytes(raw)):
        results.append((bytes.fromhex(m.group(1).decode()), bytes.fromhex(m.group(2).decode())))
    return results


def main() -> None:
    print("[key_extract] R18 实验性密钥提取骨架（需本机登录运行微信）")
    for pname, mname in ((b"WeChat.exe", b"WeChatWin.dll"), (b"Weixin.exe", b"Weixin.exe")):
        pid = find_process_pid(pname)
        if not pid:
            continue
        print(f"  找到进程 {pname.decode()} pid={pid}")
        mb = module_base(pid, mname)
        if not mb:
            print("  未能获取模块基址"); continue
        base, size = mb
        print(f"  模块 {mname.decode()} base=0x{base:x} size=0x{size:x}")
        hits = scan_key_by_pattern(pid, base, size, b"x'")
        print(f"  扫描到 {len(hits)} 个 'x'' 模式候选点（真实验证需配合 crypto.verify_page1_hmac）")
        return
    print("  未检测到微信进程——请先登录运行微信后再试。离线自测请运行 selftest.py")


if __name__ == "__main__":
    main()
