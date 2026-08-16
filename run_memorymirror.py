"""MemoryMirror 桌面客户端
双击 exe 后：
  1. 显示原生窗口（pywebview）
  2. 后台启动 FastAPI 服务
  3. 自动扫描微信本地存储 → 提取密钥 → 解密 → 导入
  4. 窗口加载前端 UI，无需打开浏览器
"""

import sys
import os
import threading
import time
import json
import shutil
from pathlib import Path

# ---- 路径 ----
if getattr(sys, "frozen", False):
    BASE_DIR = Path(sys.executable).parent
else:
    BASE_DIR = Path(__file__).resolve().parent

os.chdir(str(BASE_DIR))
sys.path.insert(0, str(BASE_DIR))

DATA_DIR = BASE_DIR / "data"
DECRYPTED_DIR = DATA_DIR / "decrypted"
HOST = "127.0.0.1"
PORT = 8787


# ==================== 微信路径扫描 ====================

def find_wechat_dirs() -> list[dict]:
    docs = Path.home() / "Documents"
    results = []
    for root_name in ["WeChat Files", "WeChatAppEx Files", "Weixin Files"]:
        root = docs / root_name
        if not root.is_dir():
            continue
        for sub in root.iterdir():
            if not sub.is_dir() or sub.name.startswith("."):
                continue
            msg_db = sub / "Msg" / "MSG.db"
            micro_msg = sub / "Msg" / "MicroMsg.db"
            contact_db = sub / "Config" / "contact.db"
            session_db = sub / "Msg" / "Session.db"
            results.append({
                "wxid": sub.name, "path": str(sub),
                "has_msg_db": msg_db.is_file() or micro_msg.is_file(),
                "msg_db": str(msg_db) if msg_db.is_file() else str(micro_msg) if micro_msg.is_file() else None,
                "contact_db": str(contact_db) if contact_db.is_file() else None,
                "session_db": str(session_db) if session_db.is_file() else None,
            })
    alt = docs / "WeChat Files" / "WeChat"
    if alt.is_dir():
        for sub in alt.iterdir():
            if not sub.is_dir() or sub.name.startswith("."):
                continue
            msg_db = sub / "Msg" / "MSG.db"
            results.append({
                "wxid": sub.name, "path": str(sub),
                "has_msg_db": msg_db.is_file(), "msg_db": str(msg_db) if msg_db.is_file() else None,
                "contact_db": None, "session_db": None,
            })
    return results


# ==================== 密钥提取 ====================

def extract_key_from_memory() -> str | None:
    print("[2] 正在从微信进程内存提取密钥…")
    try:
        from backend.decrypt.key_extract import scan_process
        import psutil
        wx_pid = None
        for p in psutil.process_iter(["pid", "name"]):
            name = (p.info["name"] or "").lower()
            if name in ("weixin.exe", "wechat.exe", "wechatappex.exe"):
                wx_pid = p.info["pid"]
                break
        if not wx_pid:
            print("    ❌ 未找到运行中的微信进程")
            return None
        candidates = scan_process(wx_pid)
        if not candidates:
            print("    ❌ 未在内存中找到密钥")
            return None
        key = candidates[0][0].hex()
        print(f"    ✅ 成功提取密钥: {key[:16]}…{key[-8:]}")
        return key
    except Exception as e:
        print(f"    ❌ 密钥提取失败: {e}")
        return None


def extract_key_via_dll() -> str | None:
    print("    ↳ 尝试 DLL 注入取钥（需管理员权限）…")
    try:
        from tools.call_wxkey import main as dll_main
        t = threading.Thread(target=dll_main, daemon=True)
        t.start()
        t.join(timeout=10)
        out_file = DATA_DIR / "wx_passphrase.txt"
        if out_file.is_file():
            key = out_file.read_text(encoding="utf-8").strip()
            if key:
                print(f"    ✅ DLL 取钥成功: {key[:16]}…")
                return key
    except Exception as e:
        print(f"    ❌ DLL 取钥失败: {e}")
    return None


# ==================== 数据库解密 ====================

def decrypt_wechat_db(enc_path: str, key: str, out_path: str) -> bool:
    print(f"    ↳ 解密: {Path(enc_path).name}…")
    try:
        from backend.decrypt.crypto import decrypt_database, verify_page1_hmac
        data = Path(enc_path).read_bytes()
        key_bytes = bytes.fromhex(key)
        if not verify_page1_hmac(data[:4096], key_bytes):
            print(f"    ⚠ HMAC 校验失败")
            return False
        plain = decrypt_database(data, key_bytes)
        Path(out_path).write_bytes(plain)
        print(f"    ✅ 解密成功: {Path(enc_path).name}")
        return True
    except Exception as e:
        print(f"    ❌ 解密失败: {e}")
        return False


def decrypt_all_dbs(accounts: list[dict], key: str) -> list[dict]:
    results = []
    DECRYPTED_DIR.mkdir(parents=True, exist_ok=True)
    for acc in accounts:
        if not key:
            break
        acc_dir = DECRYPTED_DIR / acc["wxid"]
        acc_dir.mkdir(exist_ok=True)
        result = {"wxid": acc["wxid"], "decrypted": []}
        if acc["msg_db"]:
            out = acc_dir / "message.db"
            if decrypt_wechat_db(acc["msg_db"], key, str(out)):
                result["decrypted"].append({"type": "message", "path": str(out)})
        if acc["contact_db"]:
            out = acc_dir / "contact.db"
            if decrypt_wechat_db(acc["contact_db"], key, str(out)):
                result["decrypted"].append({"type": "contact", "path": str(out)})
        if acc["session_db"]:
            out = acc_dir / "session.db"
            if decrypt_wechat_db(acc["session_db"], key, str(out)):
                result["decrypted"].append({"type": "session", "path": str(out)})
        if result["decrypted"]:
            results.append(result)
    return results


# ==================== 数据导入 ====================

def import_decrypted_data(decrypted_results: list[dict]):
    print("[4] 正在导入解密数据到分析库…")
    try:
        from backend.app.importer import run_import
        from backend.app.demo_data import generate_demo_data
        total = 0
        for acc in decrypted_results:
            for item in acc["decrypted"]:
                if item["type"] == "message":
                    print(f"    ↳ 导入 {acc['wxid']} 的消息数据…")
                    run_import({"db_path": item["path"], "wxid": acc["wxid"]})
                    total += 1
        if total > 0:
            print(f"    ✅ 共导入 {total} 个数据库")
        else:
            print("    ℹ 无数据可导入，使用演示数据集")
            generate_demo_data()
    except Exception as e:
        print(f"    ❌ 数据导入失败: {e}")
        print("    ↳ 降级：使用演示数据集")
        try:
            from backend.app.demo_data import generate_demo_data
            generate_demo_data()
        except Exception:
            pass


# ==================== 后台初始化流程 ====================

def run_init_flow():
    """后台执行微信数据扫描→解密→导入流程"""
    print("[1] 正在扫描微信本地存储…")
    accounts = find_wechat_dirs()
    if accounts:
        for acc in accounts:
            print(f"    📁 发现微信账号: {acc['wxid']}")
    else:
        print("    ℹ 未找到微信存储目录，使用演示数据集")

    key = None
    if accounts:
        key = extract_key_from_memory()
        if not key:
            key = extract_key_via_dll()

    decrypted = []
    if key and accounts:
        print("[3] 正在解密微信数据库…")
        decrypted = decrypt_all_dbs(accounts, key)
    else:
        print("    ℹ 跳过解密，使用演示数据集")

    if decrypted:
        import_decrypted_data(decrypted)
    else:
        print("[4] 生成演示数据集…")
        try:
            from backend.app.demo_data import generate_demo_data
            generate_demo_data()
            print("    ✅ 演示数据集已生成")
        except Exception as e:
            print(f"    ❌ 演示数据集生成失败: {e}")

    print("[5] 正在构建向量索引…")
    try:
        from backend.app.rag_index import build_index
        build_index()
        print("    ✅ 向量索引构建完成")
    except Exception as e:
        print(f"    ⚠ 索引构建跳过: {e}")


# ==================== 启动后端服务 ====================

def start_backend():
    """启动 FastAPI 后端服务"""
    import backend.app.main as app_module
    app = app_module.app
    import uvicorn
    uvicorn.run(app, host=HOST, port=PORT, log_level="warning", reload=False)


# ==================== 主入口 ====================

def main():
    # 确保数据目录存在
    DATA_DIR.mkdir(exist_ok=True)
    DECRYPTED_DIR.mkdir(exist_ok=True)

    # 后台线程：初始化数据（扫描微信→解密→导入→建索引）
    init_thread = threading.Thread(target=run_init_flow, daemon=True)
    init_thread.start()

    # 后台线程：启动 FastAPI 服务
    server_thread = threading.Thread(target=start_backend, daemon=True)
    server_thread.start()

    # 等待后端服务就绪
    import urllib.request
    for _ in range(50):
        try:
            urllib.request.urlopen(f"http://{HOST}:{PORT}/health", timeout=1)
            break
        except Exception:
            time.sleep(0.2)

    # 使用 pywebview 创建原生窗口加载前端
    try:
        import webview
        window = webview.create_window(
            title="MemoryMirror · 记忆镜像",
            url=f"http://{HOST}:{PORT}/",
            width=1200,
            height=800,
            min_size=(900, 600),
            resizable=True,
            text_select=False,
            frameless=False,
            easy_drag=False,
        )
        webview.start(
            debug=False,
            private_mode_off=True,
            storage_path=str(DATA_DIR / "webview_cache"),
        )
    except ImportError:
        # pywebview 未安装，回退到系统浏览器
        print("[INFO] pywebview 未安装，使用系统浏览器打开")
        import webbrowser
        webbrowser.open(f"http://{HOST}:{PORT}/")
        # 保持进程运行
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            pass


if __name__ == "__main__":
    main()