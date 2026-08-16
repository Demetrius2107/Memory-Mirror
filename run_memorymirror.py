"""MemoryMirror 一键启动入口
打包为 exe 后，双击即可启动后端服务并打开浏览器。
"""

import sys
import os
import webbrowser
import threading
import time
from pathlib import Path

# ---- 路径处理 ----
# 获取程序所在目录（兼容 exe 打包和源码运行）
if getattr(sys, "frozen", False):
    BASE_DIR = Path(sys.executable).parent
else:
    BASE_DIR = Path(__file__).resolve().parent

os.chdir(str(BASE_DIR))
sys.path.insert(0, str(BASE_DIR))

HOST = "127.0.0.1"
PORT = 8787


def open_browser():
    time.sleep(2.5)
    url = f"http://{HOST}:{PORT}/"
    print(f"🌐 正在打开浏览器: {url}")
    webbrowser.open(url)


def print_banner():
    # 确保控制台编码支持 Unicode（Windows GBK 兼容）
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    print("""
    ============================================
         MemoryMirror - 记忆镜像
         个人微信聊天记录分析引擎
    ============================================
    """)
    print(f"[INFO] 工作目录: {BASE_DIR}")
    print(f"[INFO] 启动服务: http://{HOST}:{PORT}")
    print("[INFO] 正在初始化，请稍候...")
    print("")


def main():
    print_banner()

    data_dir = BASE_DIR / "data"
    data_dir.mkdir(exist_ok=True)

    threading.Thread(target=open_browser, daemon=True).start()

    try:
        # 直接导入 app 对象，避免 uvicorn 内部 importlib 路径解析问题
        import backend.app.main as app_module
        app = app_module.app
        import uvicorn
        uvicorn.run(
            app,
            host=HOST,
            port=PORT,
            log_level="warning",
            reload=False,
        )
    except KeyboardInterrupt:
        print("\n👋 服务已停止")
    except Exception as e:
        print(f"\n[ERROR] 启动失败: {e}")
        import traceback
        traceback.print_exc()
        print("\n可能的原因：")
        print("  1. 端口 8787 被占用 → 关闭其他程序后重试")
        print("  2. 依赖缺失 → 运行 pip install -r requirements.txt")
        print("  3. 目录结构错误 → 确保 exe 在 MemoryMirror 根目录")
        input("\n按 Enter 键退出...")
        sys.exit(1)


if __name__ == "__main__":
    main()