"""独立进程构建索引（脱离 bash 超时存活，日志写 stdout 供轮询）。"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.app.rag_index import build_index


def cb(**kw):
    print(f"{kw.get('phase', '?')} {kw.get('current', 0)} / {kw.get('total', 0)}", flush=True)


if __name__ == "__main__":
    n = build_index(progress_cb=cb)
    print(f"DONE {n}", flush=True)
