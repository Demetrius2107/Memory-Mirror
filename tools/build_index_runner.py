"""独立进程构建索引（pythonw 无控制台版：直接写日志文件，不依赖 stdout）。

启动：powershell Start-Process pythonw.exe tools\\build_index_runner.py
轮询：grep '^DONE' data/index_build.log
"""

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.app.rag_index import build_index

LOG = ROOT / "data" / "index_build.log"


def log(msg: str):
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(msg + "\n")


def cb(**kw):
    log(f"{kw.get('phase', '?')} {kw.get('current', 0)} / {kw.get('total', 0)}")


if __name__ == "__main__":
    log(f"== build start {time.strftime('%H:%M:%S')} (checkpoint resume) ==")
    cp = ROOT / "data" / "index_checkpoint.txt"
    try:
        n = build_index(progress_cb=cb, checkpoint=cp)
        log(f"DONE {n}")
    except Exception:
        import traceback

        log("CRASH " + traceback.format_exc())
        raise
