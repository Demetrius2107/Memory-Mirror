"""MemoryMirror 一键启动入口
打包为 exe 后，双击即可启动后端服务并打开浏览器。

使用方法：
  直接运行：python scripts/memorymirror_app.py
  打包后：  双击 memorymirror_app.exe
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
    # PyInstaller 打包后的 exe
    BASE_DIR = Path(sys.executable).parent
else:
    # 源码运行
    BASE_DIR = Path(__file__).resolve().parents[1]

os.chdir(str(BASE_DIR))
sys.path.insert(0, str(BASE_DIR))

HOST = "127.0.0.1"
PORT = 8787


def open_browser():
    """延迟打开浏览器，等服务启动。"""
    time.sleep(2.5)
    url = f"http://{HOST}:{PORT}/"
    print(f"🌐 正在打开浏览器: {url}")
    webbrowser.open(url)


def print_banner():
    print("""
    ╔══════════════════════════════════════════╗
    ║         MemoryMirror - 记忆镜像           ║
    ║        个人微信聊天记录分析引擎            ║
    ╚══════════════════════════════════════════╝
    """)
    print(f"📂 工作目录: {BASE_DIR}")
    print(f"🚀 启动服务: http://{HOST}:{PORT}")
    print("⏳ 正在初始化，请稍候...")
    print("")


def main():
    print_banner()

    # 确保 data 目录存在
    data_dir = BASE_DIR / "data"
    data_dir.mkdir(exist_ok=True)

    # 启动浏览器线程
    threading.Thread(target=open_browser, daemon=True).start()

    # 导入并启动 uvicorn（此调用会阻塞）
    try:
        import uvicorn
        uvicorn.run(
            "backend.app.main:app",
            host=HOST,
            port=PORT,
            log_level="warning",
            reload=False,
        )
    except KeyboardInterrupt:
        print("\n👋 服务已停止")
    except Exception as e:
        print(f"\n❌ 启动失败: {e}")
        print("\n可能的原因：")
        print("  1. 端口 8787 被占用 → 关闭其他程序后重试")
        print("  2. 依赖缺失 → 运行 pip install -r requirements.txt")
        print("  3. 目录结构错误 → 确保 exe 在 MemoryMirror 根目录")
        input("\n按 Enter 键退出...")
        sys.exit(1)


if __name__ == "__main__":
    main()