"""wx_key.dll 直调取钥（pythonw 无窗口版，R18 实验性，需管理员）。

交互设计：无控制台窗口，关键节点用 MessageBox 弹窗提示用户——
  ① 注入成功 → 弹窗：【请操作微信退出登录→重登（或关闭重开）】
  ② 重新注入成功 → 弹窗：【已重新注入，现在可以扫码登录】
  ③ 捕获成功 → 弹窗：passphrase（同时写入 data/wx_passphrase.txt）
  ④ 超时 → 弹窗提示

流程：等待微信进程 → InitializeHook 注入 → 用户重登 → setCipherKey 触发 → PollKeyData 捕获。
重注入前先 CleanupHook（否则 DLL 单例报"Hook已经初始化"）。
"""

import ctypes
import sys
import time
from pathlib import Path

DLL = Path(__file__).resolve().parent / "weflow51" / "WeFlow-5.1.0" / "resources" / "key" / "win32" / "x64" / "wx_key.dll"
OUT = Path(__file__).resolve().parent.parent / "data" / "wx_passphrase.txt"
LOG = Path(__file__).resolve().parent.parent / "data" / "call_wxkey_out.txt"
TIMEOUT = 240          # 总轮询秒数
WAIT_PROC = 120        # 若微信未运行，等待其出现的秒数


def log(msg: str = ""):
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(msg + "\n")


def msgbox(text: str, title: str = "记忆镜像 · 取钥工具"):
    log(text)
    try:
        ctypes.windll.user32.MessageBoxW(0, text, title, 0x40)  # MB_ICONINFORMATION
    except Exception:
        pass


def find_main_pid():
    import psutil

    cands = []
    for p in psutil.process_iter(["pid", "name", "memory_info"]):
        if (p.info["name"] or "").lower() in ("weixin.exe", "wechat.exe"):
            try:
                cands.append((p.info["pid"], p.info["memory_info"].rss or 0))
            except Exception:
                continue
    if not cands:
        return None
    cands.sort(key=lambda x: -x[1])
    return cands[0][0]


def main() -> int:
    log("=" * 50)
    log("wx_key 取钥工具启动（pythonw 无窗口版）")
    log("=" * 50)
    if not DLL.exists():
        msgbox(f"wx_key.dll 不存在:\n{DLL}\n\n请确认 WeFlow 5.1.0 包已解压到 tools/weflow51/", "❌ 错误")
        return 1

    dll = ctypes.WinDLL(str(DLL))
    dll.InitializeHook.argtypes = [ctypes.c_uint32]
    dll.InitializeHook.restype = ctypes.c_bool
    dll.PollKeyData.argtypes = [ctypes.c_char_p, ctypes.c_int]
    dll.PollKeyData.restype = ctypes.c_bool
    dll.GetStatusMessage.argtypes = [ctypes.c_char_p, ctypes.c_int, ctypes.POINTER(ctypes.c_int)]
    dll.GetStatusMessage.restype = ctypes.c_bool
    dll.CleanupHook.restype = ctypes.c_bool
    dll.GetLastErrorMsg.restype = ctypes.c_char_p

    # 等待微信进程出现（支持"先关微信再重开"）
    pid = find_main_pid()
    if not pid:
        log(f"微信未运行，等待其出现（最长 {WAIT_PROC}s）...")
        msgbox("未检测到微信进程。\n请打开微信并登录，脚本将自动注入 Hook。")
        deadline_wait = time.time() + WAIT_PROC
        while time.time() < deadline_wait:
            pid = find_main_pid()
            if pid:
                break
            time.sleep(0.5)
        if not pid:
            msgbox("等待超时：未检测到微信进程。", "❌ 超时")
            return 1
        log(f"检测到微信进程: PID {pid}")

    # 注入 Hook
    if not dll.InitializeHook(pid):
        err = (dll.GetLastErrorMsg() or b"").decode("utf-8", "replace")
        msgbox(f"InitializeHook 失败:\n{err}\n\n若提示权限/ACCESS_DENIED，请以管理员身份运行。", "❌ 错误")
        return 1
    log(f"✅ Hook 已注入 (PID {pid})")
    msgbox("✅ Hook 已注入！\n\n请在微信里操作（二选一）：\n"
           "  方式A：微信【设置 → 退出登录】→ 重新扫码（进程不关）\n"
           "  方式B：关闭微信再重新打开（脚本会自动重新注入）\n\n"
           "⚠️ 重开后【先别扫码】，等下一个弹窗提示【已重新注入】再扫码。\n"
           "登录瞬间将自动捕获密钥，请稍候...")

    buf = ctypes.create_string_buffer(256)
    deadline = time.time() + TIMEOUT
    last_status = ""
    last_remind = 0
    while time.time() < deadline:
        if time.time() - last_remind > 20:
            last_remind = time.time()
            remain = int(deadline - time.time())
            log(f"⏰ 等待重登中，剩余 {remain}s...")
        cur = find_main_pid()
        if cur and cur != pid:
            pid = cur
            log(f"检测到微信重启 (PID {pid})，CleanupHook 后重新注入...")
            try:
                dll.CleanupHook()
            except Exception:
                pass
            if not dll.InitializeHook(pid):
                err = (dll.GetLastErrorMsg() or b"").decode("utf-8", "replace")
                log(f"❌ 重新注入失败: {err}")
                msgbox(f"重新注入失败:\n{err}", "❌ 错误")
                break
            log(f"✅ 已重新注入 (PID {pid})")
            msgbox("✅ 已重新注入新进程！\n现在可以扫码登录微信了。")
        stbuf = ctypes.create_string_buffer(256)
        lvl = ctypes.c_int(0)
        if dll.GetStatusMessage(stbuf, len(stbuf), ctypes.byref(lvl)):
            m = stbuf.value.decode("utf-8", "replace").strip()
            if m and m != last_status:
                last_status = m
                log(f"[状态 L{lvl.value}] {m}")
        if dll.PollKeyData(buf, len(buf)):
            key = buf.value.decode("utf-8", "replace").strip()
            if len(key) == 64:
                log(f"🎉 密钥捕获成功: {key}")
                OUT.parent.mkdir(parents=True, exist_ok=True)
                OUT.write_text(key, encoding="utf-8")
                msgbox(f"🎉 密钥捕获成功！\n\npassphrase = {key}\n\n已保存到 data/wx_passphrase.txt")
                try:
                    dll.CleanupHook()
                except Exception:
                    pass
                return 0
        time.sleep(0.3)

    try:
        dll.CleanupHook()
    except Exception:
        pass
    msgbox("超时未捕获。\n若全程照做仍失败，可能是此 wx_key.dll 与 4.1.12.55 不完全兼容\n"
           "（WeFlow 文档建议降级微信到 4.1.8.100 最稳定）。", "⏰ 超时")
    return 1


if __name__ == "__main__":
    sys.exit(main())
