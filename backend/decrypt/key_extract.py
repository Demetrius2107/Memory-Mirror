"""密钥提取（Windows，R18 实验性）—— wx4：全进程内存扫描 + HMAC 实库校验

WCDB（SQLCipher 4）会把派生后的 raw key 缓存于进程内存。本工具：
1. 枚举 Weixin.exe / WeChatAppEx.exe / WeChat.exe 全部可读内存区域
2. 匹配两种 key 驻留形态：
   - 主模式 `x'<64hex_enc_key><32hex_salt>'`
   - 宽松模式：任意 96 位连续 hex（key+salt，形制随版本可能变化）
3. `--db <加密库>` 时用该库 page1 HMAC 校验候选，输出正确 key

运行需微信已登录运行；建议以管理员身份运行（微信进程有防篡改 DACL）。
"""

from __future__ import annotations

import argparse
import ctypes
import ctypes.wintypes as wt
import re
from pathlib import Path

from backend.decrypt.crypto import PAGE_SZ, verify_page1_hmac

PROCESS_VM_READ = 0x0010
PROCESS_QUERY_INFORMATION = 0x0400
MEM_COMMIT = 0x1000

# 可读内存页保护属性（低 8 位；PAGE_NOACCESS=0x01 / PAGE_GUARD=0x100 已排除）
READABLE_PROTECT = {0x02, 0x04, 0x08, 0x20, 0x40, 0x80, 0x100}

# 主模式：WCDB 缓存的 `x'<64hex_enc_key><32hex_salt>'`
PATTERN_WX4 = re.compile(rb"x'([0-9a-fA-F]{64})([0-9a-fA-F]{32})'")
# 宽松模式：任意 96 位连续 hex（前 64 = enc_key，后 32 = salt）
PATTERN_LOOSE = re.compile(rb"(?<![0-9a-fA-F])([0-9a-fA-F]{96})(?![0-9a-fA-F])")
# MemoTrace GetKeyAddrStub：指针结构（前 8B=指向 32B passphrase 的指针，后跟 \x20 长度 + \x2f 标记）
PATTERN_KEYADDR = re.compile(rb".{6}\x00{2}\x00{8}\x20\x00{7}\x2f\x00{7}", re.S)


class PROCESSENTRY32(ctypes.Structure):
    _fields_ = [
        ("dwSize", wt.DWORD), ("cntUsage", wt.DWORD), ("th32ProcessID", wt.DWORD),
        ("th32DefaultHeapID", ctypes.POINTER(wt.ULONG)), ("th32ModuleID", wt.DWORD),
        ("cntThreads", wt.DWORD), ("th32ParentProcessID", wt.DWORD),
        ("pcPriClassBase", wt.LONG), ("dwFlags", wt.DWORD),
        ("szExeFile", ctypes.c_char * 260),
    ]


class MEMORY_BASIC_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("BaseAddress", ctypes.c_void_p),
        ("AllocationBase", ctypes.c_void_p),
        ("AllocationProtect", wt.DWORD),
        ("PartitionId", wt.DWORD),
        ("RegionSize", ctypes.c_size_t),
        ("State", wt.DWORD),
        ("Protect", wt.DWORD),
        ("Type", wt.DWORD),
    ]


def _k32():
    """kernel32 句柄并显式声明签名（默认 restype=c_int 会截断 64 位 HANDLE）。"""
    k = ctypes.WinDLL("kernel32", use_last_error=True)
    k.CreateToolhelp32Snapshot.restype = wt.HANDLE
    k.CreateToolhelp32Snapshot.argtypes = [wt.DWORD, wt.DWORD]
    k.Process32First.restype = wt.BOOL
    k.Process32First.argtypes = [wt.HANDLE, ctypes.POINTER(PROCESSENTRY32)]
    k.Process32Next.restype = wt.BOOL
    k.Process32Next.argtypes = [wt.HANDLE, ctypes.POINTER(PROCESSENTRY32)]
    k.OpenProcess.restype = wt.HANDLE
    k.OpenProcess.argtypes = [wt.DWORD, wt.BOOL, wt.DWORD]
    k.VirtualQueryEx.restype = ctypes.c_size_t
    k.VirtualQueryEx.argtypes = [
        wt.HANDLE, wt.LPVOID, ctypes.POINTER(MEMORY_BASIC_INFORMATION), ctypes.c_size_t,
    ]
    k.ReadProcessMemory.restype = wt.BOOL
    k.ReadProcessMemory.argtypes = [
        wt.HANDLE, wt.LPVOID, ctypes.c_void_p, ctypes.c_size_t, ctypes.POINTER(ctypes.c_size_t),
    ]
    k.CloseHandle.restype = wt.BOOL
    k.CloseHandle.argtypes = [wt.HANDLE]
    return k


def enable_debug_privilege() -> bool:
    """启用 SeDebugPrivilege（管理员提权后读取他人进程内存所需，微信进程有防篡改 DACL）。"""
    k32 = _k32()
    adv = ctypes.WinDLL("advapi32", use_last_error=True)
    k32.GetCurrentProcess.restype = wt.HANDLE
    k32.OpenProcessToken.restype = wt.BOOL
    k32.OpenProcessToken.argtypes = [wt.HANDLE, wt.DWORD, ctypes.POINTER(wt.HANDLE)]
    adv.LookupPrivilegeValueW.restype = wt.BOOL
    adv.LookupPrivilegeValueW.argtypes = [wt.LPCWSTR, wt.LPCWSTR, ctypes.POINTER(ctypes.c_longlong)]
    adv.AdjustTokenPrivileges.restype = wt.BOOL
    adv.AdjustTokenPrivileges.argtypes = [
        wt.HANDLE, wt.BOOL, ctypes.c_void_p, wt.DWORD, ctypes.c_void_p, ctypes.c_void_p,
    ]

    class LUID_AND_ATTRIBUTES(ctypes.Structure):
        _fields_ = [("Luid", ctypes.c_longlong), ("Attributes", wt.DWORD)]

    class TOKEN_PRIVILEGES(ctypes.Structure):
        _fields_ = [("PrivilegeCount", wt.DWORD), ("Privileges", LUID_AND_ATTRIBUTES * 1)]

    TOKEN_ADJUST_PRIVILEGES, TOKEN_QUERY = 0x0020, 0x0008
    h_token = wt.HANDLE()
    if not k32.OpenProcessToken(k32.GetCurrentProcess(), TOKEN_ADJUST_PRIVILEGES | TOKEN_QUERY, ctypes.byref(h_token)):
        return False
    try:
        luid = ctypes.c_longlong()
        if not adv.LookupPrivilegeValueW(None, "SeDebugPrivilege", ctypes.byref(luid)):
            return False
        tp = TOKEN_PRIVILEGES()
        tp.PrivilegeCount = 1
        tp.Privileges[0].Luid = luid
        tp.Privileges[0].Attributes = 0x00000002  # SE_PRIVILEGE_ENABLED
        return bool(adv.AdjustTokenPrivileges(h_token, False, ctypes.byref(tp), 0, None, None))
    finally:
        k32.CloseHandle(h_token)


def find_all_pids(name: bytes) -> list[int]:
    kernel32 = _k32()
    snap = kernel32.CreateToolhelp32Snapshot(0x2, 0)
    if snap in (wt.HANDLE(-1), None):
        return []
    pids = []
    try:
        entry = PROCESSENTRY32()
        entry.dwSize = ctypes.sizeof(PROCESSENTRY32)
        ok = kernel32.Process32First(snap, ctypes.byref(entry))
        while ok:
            if entry.szExeFile.lower() == name.lower():
                pids.append(entry.th32ProcessID)
            ok = kernel32.Process32Next(snap, ctypes.byref(entry))
    finally:
        kernel32.CloseHandle(snap)
    return pids


def scan_process(pid: int) -> list[tuple[bytes, bytes]]:
    """扫描进程全部可读内存区域，返回去重后的 (enc_key, salt) 候选列表。"""
    kernel32 = _k32()
    h = kernel32.OpenProcess(PROCESS_VM_READ | PROCESS_QUERY_INFORMATION, False, pid)
    if not h:
        print(f"    [scan] pid={pid}: OpenProcess 失败 err={ctypes.get_last_error()}")
        return []
    found: dict[tuple[str, str], tuple[bytes, bytes]] = {}
    regions = 0
    bytes_read = 0
    try:
        mbi = MEMORY_BASIC_INFORMATION()
        addr = ctypes.c_void_p(0)
        while kernel32.VirtualQueryEx(h, addr, ctypes.byref(mbi), ctypes.sizeof(mbi)):
            if (
                mbi.State == MEM_COMMIT
                and (mbi.Protect & 0xFF) in READABLE_PROTECT
                and mbi.RegionSize
            ):
                regions += 1
                buf = ctypes.create_string_buffer(mbi.RegionSize)
                nread = ctypes.c_size_t(0)
                ok = kernel32.ReadProcessMemory(
                    h, mbi.BaseAddress, buf, mbi.RegionSize, ctypes.byref(nread)
                )
                if ok and nread.value:
                    bytes_read += nread.value
                    data = buf.raw[: nread.value]
                    for m in PATTERN_WX4.finditer(data):
                        enc = bytes.fromhex(m.group(1).decode())
                        salt = bytes.fromhex(m.group(2).decode())
                        found[(enc.hex(), salt.hex())] = (enc, salt)
                    for m in PATTERN_LOOSE.finditer(data):
                        raw = m.group(1)
                        enc = bytes.fromhex(raw[:64].decode())
                        salt = bytes.fromhex(raw[64:].decode())
                        found[(enc.hex(), salt.hex())] = (enc, salt)
            # 首个区域常为 BaseAddress=0 的 MEM_FREE 空闲区（不可 break）；
            # VirtualQueryEx 返回 0 即到达地址空间末端，由 while 条件自然结束
            addr = ctypes.c_void_p((mbi.BaseAddress or 0) + mbi.RegionSize)
    finally:
        kernel32.CloseHandle(h)
    print(f"    [scan] pid={pid}: {regions} 区域, 读取 {bytes_read / 1024 / 1024:.1f} MB, 候选 {len(found)}")
    return list(found.values())


def scan_salt_anchor(pid: int, salt: bytes, page: bytes) -> list[tuple[int, str]]:
    """二进制锚点扫描：在进程内存中搜已知 salt，命中处 ±1KB 窗口内
    逐个 32 字节偏移做 HMAC 校验，返回 [(偏移, enc_key_hex), ...]。"""
    kernel32 = _k32()
    h = kernel32.OpenProcess(PROCESS_VM_READ | PROCESS_QUERY_INFORMATION, False, pid)
    if not h:
        return []
    hits: list[tuple[int, str]] = []
    regions = 0
    salt_hits = 0
    first_ctx_printed = False
    try:
        mbi = MEMORY_BASIC_INFORMATION()
        addr = ctypes.c_void_p(0)
        while kernel32.VirtualQueryEx(h, addr, ctypes.byref(mbi), ctypes.sizeof(mbi)):
            if (
                mbi.State == MEM_COMMIT
                and (mbi.Protect & 0xFF) in READABLE_PROTECT
                and mbi.RegionSize
            ):
                regions += 1
                buf = ctypes.create_string_buffer(mbi.RegionSize)
                nread = ctypes.c_size_t(0)
                ok = kernel32.ReadProcessMemory(
                    h, mbi.BaseAddress, buf, mbi.RegionSize, ctypes.byref(nread)
                )
                if ok and nread.value:
                    data = buf.raw[: nread.value]
                    pos = 0
                    while True:
                        idx = data.find(salt, pos)
                        if idx < 0:
                            break
                        salt_hits += 1
                        if not first_ctx_printed:
                            lo0 = max(0, idx - 64)
                            print(f"      [首个 salt 命中上下文 ±64B] {data[lo0: idx + 80].hex()}")
                            first_ctx_printed = True
                        # ±1KB 窗口，逐个 32 字节偏移试 HMAC
                        lo = max(0, idx - 1024)
                        hi = min(len(data), idx + 1024 + 16)
                        window = data[lo:hi]
                        for off in range(0, len(window) - 32 + 1):
                            key = window[off : off + 32]
                            if verify_page1_hmac(page, key):
                                hits.append((mbi.BaseAddress + lo + off, key.hex()))
                        pos = idx + 16
            addr = ctypes.c_void_p((mbi.BaseAddress or 0) + mbi.RegionSize)
    finally:
        kernel32.CloseHandle(h)
    print(f"    [salt-scan] pid={pid}: {regions} 区域, salt 命中 {salt_hits} 处, 校验出 {len(hits)} 个 key")
    return hits


def scan_keyaddr(pid: int) -> list[bytes]:
    """MemoTrace GetKeyAddrStub 方法：扫描内存中"指向 32B passphrase 的指针结构"。

    结构（32B）：前 8B = 指针（低 6B 任意 + 高 2B 零），随后 8B 零、0x20（=32 长度）、
    7B 零、0x2f、7B 零。命中后读指针处 32B 即为 passphrase 候选。
    """
    kernel32 = _k32()
    h = kernel32.OpenProcess(PROCESS_VM_READ | PROCESS_QUERY_INFORMATION, False, pid)
    if not h:
        return []
    found: set[bytes] = set()
    regions = 0
    try:
        mbi = MEMORY_BASIC_INFORMATION()
        addr = ctypes.c_void_p(0)
        while kernel32.VirtualQueryEx(h, addr, ctypes.byref(mbi), ctypes.sizeof(mbi)):
            if (
                mbi.State == MEM_COMMIT
                and (mbi.Protect & 0xFF) in READABLE_PROTECT
                and mbi.RegionSize
            ):
                regions += 1
                buf = ctypes.create_string_buffer(mbi.RegionSize)
                nread = ctypes.c_size_t(0)
                ok = kernel32.ReadProcessMemory(
                    h, mbi.BaseAddress, buf, mbi.RegionSize, ctypes.byref(nread)
                )
                if ok and nread.value:
                    data = buf.raw[: nread.value]
                    for m in PATTERN_KEYADDR.finditer(data):
                        ptr = int.from_bytes(data[m.start() : m.start() + 8], "little")
                        if not ptr:
                            continue
                        # 读指针处的 32 字节（可能在别的区域，用独立 ReadProcessMemory）
                        kbuf = ctypes.create_string_buffer(32)
                        kread = ctypes.c_size_t(0)
                        if kernel32.ReadProcessMemory(h, ctypes.c_void_p(ptr), kbuf, 32, ctypes.byref(kread)) and kread.value == 32:
                            found.add(bytes(kbuf.raw[:32]))
            addr = ctypes.c_void_p((mbi.BaseAddress or 0) + mbi.RegionSize)
    finally:
        kernel32.CloseHandle(h)
    print(f"    [keyaddr-scan] pid={pid}: {regions} 区域, 指针结构命中出 {len(found)} 个 passphrase 候选")
    return list(found)


def main() -> None:
    import json

    ap = argparse.ArgumentParser(description="wx4 密钥内存扫描（R18 实验性）")
    ap.add_argument("--db", help="单个目标加密库（如 message_0.db），用于 HMAC 校验候选 key")
    ap.add_argument("--db-dir", help="db_storage 根目录：遍历其中所有 *.db 校验候选 key（覆盖多账号）")
    ap.add_argument("--salt-anchor", help="用库文件头 salt 做二进制锚点扫描内存（key 以原始字节驻留时适用）")
    ap.add_argument("--keyaddr", action="store_true", help="MemoTrace GetKeyAddrStub 指针结构扫描（wx4.1 真实方法）")
    args = ap.parse_args()

    print("[key_extract] wx4 全进程内存扫描（R18 实验性，需微信已登录运行）")
    print(f"  SeDebugPrivilege 启用: {enable_debug_privilege()}")

    all_cand: dict[tuple[str, str], tuple[bytes, bytes]] = {}
    if args.salt_anchor or args.keyaddr:
        print("  （--salt-anchor / --keyaddr 模式：跳过文本模式扫描，直接做内存结构定位）")
    else:
        for pname in (b"Weixin.exe", b"WeChatAppEx.exe", b"WeChat.exe"):
            pids = find_all_pids(pname)
            print(f"  {pname.decode()}: {len(pids)} 个进程 {pids}")
            for pid in pids:
                for enc, salt in scan_process(pid):
                    all_cand[(enc.hex(), salt.hex())] = (enc, salt)

        print(f"  全部候选（去重）: {len(all_cand)}")
        try:
            Path("data/key_candidates.json").write_text(
                json.dumps([{"enc": e.hex(), "salt": s.hex()} for e, s in all_cand.values()], indent=1),
                encoding="utf-8",
            )
            print("  候选已保存: data/key_candidates.json")
        except Exception as e:
            print(f"  候选保存失败: {e}")

        targets: list[Path] = []
        if args.db:
            targets = [Path(args.db)]
        elif args.db_dir:
            targets = sorted(Path(args.db_dir).rglob("*.db"))
            print(f"  待校验库: {len(targets)} 个（{args.db_dir} 下递归）")

        matched_any = False
        for db in targets:
            try:
                page = db.read_bytes()[:PAGE_SZ]
            except OSError as e:
                print(f"    读取失败 {db}: {e}")
                continue
            if len(page) < PAGE_SZ:
                continue
            for (eh, sh), (enc, salt) in all_cand.items():
                if verify_page1_hmac(page, enc):
                    matched_any = True
                    print(f"  ⭐ HMAC 校验通过: {db}")
                    print(f"    enc_key={eh}")
                    print(f"    salt   ={sh}")
        if targets and not matched_any:
            print(f"  HMAC 校验: {len(targets)} 个库均无匹配——key 形制/进程/加密参数仍需排查")

    # salt 二进制锚点扫描（key 以原始字节驻留时）
    if args.salt_anchor:
        anchor_db = Path(args.salt_anchor)
        db_data = anchor_db.read_bytes()
        salt = db_data[:16]
        page = db_data[:PAGE_SZ]
        print(f"  salt 锚点: {salt.hex()}（来自 {anchor_db.name}），扫描全部进程…")
        found_keys: set[str] = set()
        for pname in (b"Weixin.exe", b"WeChatAppEx.exe", b"WeChat.exe"):
            for pid in find_all_pids(pname):
                for off, kh in scan_salt_anchor(pid, salt, page):
                    if kh not in found_keys:
                        found_keys.add(kh)
                        print(f"    ⭐⭐ salt 锚点命中: 进程 {pname.decode()} pid={pid} 偏移 0x{off:x}")
                        print(f"       enc_key={kh}")


    # MemoTrace GetKeyAddrStub 指针结构扫描（wx4.1 真实方法）：收集 passphrase 候选
    if args.keyaddr:
        print("  （--keyaddr：GetKeyAddrStub 指针结构扫描 → passphrase 候选 → 保存）")
        pp_cands: set[bytes] = set()
        for pname in (b"Weixin.exe", b"WeChatAppEx.exe", b"WeChat.exe"):
            for pid in find_all_pids(pname):
                for k in scan_keyaddr(pid):
                    pp_cands.add(k)
        print(f"  passphrase 候选（去重）: {len(pp_cands)}")
        try:
            Path("data/passphrase_candidates.json").write_text(
                json.dumps([{"enc": k.hex(), "salt": ""} for k in pp_cands], indent=1),
                encoding="utf-8",
            )
            print("  已保存: data/passphrase_candidates.json（用 tools/validate_v4_keys.py 做 PBKDF2 256000 校验）")
        except Exception as e:
            print(f"  保存失败: {e}")


if __name__ == "__main__":
    main()
