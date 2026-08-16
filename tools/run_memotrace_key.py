"""用 MemoTrace 原生 wx_info_v4.dump_wechat_info_v4 提取 wx4 passphrase（R18 实验性）。

用法（需管理员权限）：
  python tools/run_memotrace_key.py
输出：找到的 key 打印到 stdout。
"""

import sys
from pathlib import Path

SRC = Path(__file__).resolve().parent / "memotrace_src" / "MemoTrace-master"
sys.path.insert(0, str(SRC))

import psutil
from wxManager.decrypt.wx_info_v4 import dump_wechat_info_v4


def find_main_weixin_pid() -> int | None:
    """主 Weixin.exe = 内存占用最大的那个。"""
    cands = []
    for p in psutil.process_iter(["pid", "name", "memory_info"]):
        name = (p.info["name"] or "").lower()
        if name in ("weixin.exe",):
            try:
                cands.append((p.info["pid"], p.info["memory_info"].rss or 0))
            except Exception:
                continue
    if not cands:
        return None
    cands.sort(key=lambda x: -x[1])
    return cands[0][0]


def main() -> None:
    pid = find_main_weixin_pid()
    print(f"主 Weixin.exe pid: {pid}")
    if not pid:
        print("未找到 Weixin.exe 进程")
        return
    info = dump_wechat_info_v4(pid)
    if info is None:
        print("dump_wechat_info_v4 返回 None（未提取到 key）")
        return
    key = getattr(info, "key", None)
    print(f"⭐ 提取成功！")
    print(f"   key = {key}")
    print(f"   wxid = {getattr(info, 'wxid', '?')}")
    print(f"   nickname = {getattr(info, 'nickname', '?')}")


if __name__ == "__main__":
    main()
