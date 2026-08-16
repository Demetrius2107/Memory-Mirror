"""RAG 向量索引（Week 4，PRD §4.4）：切片 → embedding → FAISS 存储 + 检索

- 切片：1000 字 / 100 字重叠
- 存储：FAISS IndexFlatIP（data/faiss_index.bin）+ 边车元数据
  （data/faiss_meta.jsonl，行号 = FAISS id）
- embedding：backend.app.embedder（TF-IDF 512 维，L2 归一化，
  内积 = 余弦相似度；R4 后续可切 bge-small-zh ONNX）

后端选型（2026-08-16）：chroma 1.5.9 在 50 万向量规模下原生层
间歇性挂起/崩溃（读 id、count、query 均曾触发），换 FAISS——
纯 C++ 实现、成熟稳定，120 万 × 512 维仅约 2.4GB 内存，支持
跨进程分段续跑（read_index → add → write_index）。
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import faiss
import numpy as np

from backend.app.demo_data import DB_PATH
from backend.app.embedder import DIM, embed_texts, fit

CHUNK_SIZE = 1000  # 1000 字切片（原 500）：长消息片段数减半，RAG 粒度仍够
CHUNK_OVERLAP = 100
BATCH = 256  # 批量加大：减少 FAISS add 调用次数
FAISS_INDEX_FILE = "faiss_index.bin"
FAISS_META_FILE = "faiss_meta.jsonl"
CHECKPOINT_FILE = "index_checkpoint.txt"


def chunk_text(text: str) -> list[tuple[int, str]]:
    """按 CHUNK_SIZE 字带重叠切片。返回 [(start_idx, text)]。"""
    text = text or ""
    if len(text) <= CHUNK_SIZE:
        return [(0, text)]
    chunks = []
    start = 0
    while start < len(text):
        end = start + CHUNK_SIZE
        chunks.append((start, text[start:end]))
        if end >= len(text):
            break
        start = end - CHUNK_OVERLAP
    return chunks


def _paths(db_path: Path) -> tuple[Path, Path, Path]:
    d = db_path.parent
    return d / FAISS_INDEX_FILE, d / FAISS_META_FILE, d / CHECKPOINT_FILE


def get_collection(db_path, create: bool = True):
    """chroma 时代兼容存根（main.py /api/debug/embed 仍引用）。

    FAISS 后端无集合概念，恒返回 None——调用方按"集合不存在"分支处理。
    """
    return None


def _read_meta(meta_path: Path) -> list[dict]:
    """读全部元数据（行号 = FAISS id）。"""
    out = []
    if not meta_path.exists():
        return out
    with meta_path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                out.append(json.loads(line))
    return out


def _append_meta(meta_path: Path, rows: list[dict]) -> None:
    with meta_path.open("a", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


def build_index(db_path: str | Path | None = None, progress_cb=None, incremental: bool = False, checkpoint: Path | None = None) -> int:
    """全量/断点续跑构建 FAISS 索引（两遍流式，低内存）。

    - checkpoint=None：全量重建（/api/index"重建索引"按钮），删除旧索引
    - checkpoint=Path：断点续跑——按 messages.id 记录进度文件，
      read_index 已有索引后追加嵌入，跨进程分段安全
    返回片段总数。
    """
    db_path = Path(db_path) if db_path else DB_PATH
    index_path, meta_path, cp_path = _paths(db_path)

    def cb(**kw):
        if progress_cb:
            progress_cb(**kw)

    SQL = "SELECT id, msg_id, talker, time_utc, content, year_month FROM messages ORDER BY id"

    def iter_chunk_texts(conn):
        for _mid, _m, _t, _ts, content, _ym in conn.execute(SQL):
            for _start, seg in chunk_text(content):
                if seg.strip() and len(seg.strip()) >= 2:  # 跳过无意义短片段
                    yield seg

    start_id = 0
    if checkpoint and checkpoint.exists():
        try:
            start_id = int(checkpoint.read_text().strip())
        except ValueError:
            start_id = 0

    cb(status="running", phase="read", message="正在读取消息", current=0, total=0)
    conn = sqlite3.connect(db_path)
    try:
        n_msgs = conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
        cb(phase="chunk", message=f"正在切片（{n_msgs} 条消息，断点 {start_id}）", current=0, total=n_msgs)
        # Pass 1：fit（迭代器，大语料不驻留）
        fit(iter_chunk_texts(conn))
        cb(phase="embed", message="正在向量化...", current=0, total=0)

        # Pass 2：打开/创建索引
        if checkpoint:
            if index_path.exists():
                index = faiss.read_index(str(index_path))
                existing = len(_read_meta(meta_path))
            else:
                index = faiss.IndexFlatIP(DIM)
                existing = 0
            cb(phase="embed", message=f"续跑：已有 {existing} 个片段，从断点 {start_id} 继续", current=existing, total=0)
        else:
            for p in (index_path, meta_path, cp_path):
                try:
                    p.unlink()
                except FileNotFoundError:
                    pass
            index = faiss.IndexFlatIP(DIM)
            existing = 0

        done = existing
        last_id = start_id
        batch: list = []
        meta_batch: list[dict] = []

        def flush():
            nonlocal done
            if not batch:
                return
            segs = [b[1] for b in batch]
            vecs = embed_texts(segs)
            index.add(np.ascontiguousarray(vecs, dtype=np.float32))
            _append_meta(meta_path, meta_batch)
            done += len(batch)
            cb(current=done)
            batch.clear()
            meta_batch.clear()

        for mid, msg_id, talker, ts, content, ym in conn.execute(SQL):
            if checkpoint and mid <= start_id:
                continue
            for start, seg in chunk_text(content):
                if not (seg.strip() and len(seg.strip()) >= 2):
                    continue
                batch.append((f"{msg_id}#{start}", seg))
                meta_batch.append(
                    {
                        "doc_id": f"{msg_id}#{start}",
                        "seg": seg,
                        "msg_id": msg_id,
                        "talker": talker,
                        "time_utc": ts,
                        "year_month": ym,
                        "start_idx": start,
                    }
                )
                if len(batch) >= BATCH:
                    flush()
            last_id = mid
            if checkpoint and (mid - start_id) % 20000 == 0:
                checkpoint.write_text(str(last_id))  # 周期性落盘，被杀也能续
        flush()
        faiss.write_index(index, str(index_path))
        if checkpoint:
            checkpoint.write_text(str(last_id))
    finally:
        conn.close()
    cb(status="done", phase="done", message=f"索引构建完成：{done} 个片段", current=done, total=done)
    return done


_INDEX_CACHE: tuple[str, float, "faiss.Index", list[dict]] | None = None  # (路径, mtime, 索引, 元数据)


def _hits(scores, ids, meta: list[dict], n: int) -> list[dict]:
    """把 FAISS 检索结果组装为统一命中结构（distance 越小越相关）。"""
    out = []
    for sc, i in zip(scores[0], ids[0]):
        if i < 0 or i >= len(meta):
            continue
        m = meta[int(i)]
        out.append(
            {
                "content": m["seg"],
                "metadata": {
                    "msg_id": m["msg_id"],
                    "talker": m["talker"],
                    "time_utc": m["time_utc"],
                    "year_month": m["year_month"],
                    "start_idx": m["start_idx"],
                },
                "distance": round(float(1.0 - sc), 6),  # 内积 = 余弦（向量 L2 归一化），转距离语义
            }
        )
        if len(out) >= n:
            break
    return out


def search(query: str, top_k: int = 20, db_path: str | Path | None = None, scope_talker: str | None = None) -> list[dict]:
    """向量检索 Top-K，返回片段及元数据（distance 越小越相关）。

    scope_talker：按消息归属过滤（单聊=好友 wxid；群聊=群 ID）。None = 全量。
    FAISS 索引不支持库内过滤，采用**后过滤 + 候选集迭代扩大**：先取放大候选
    （top_k×4），按 talker 过滤后不足则翻倍重取，直到凑够 top_k 或取遍全索引。
    """
    db_path = Path(db_path) if db_path else DB_PATH
    index_path, meta_path, _ = _paths(db_path)
    if not index_path.exists():
        return []

    global _INDEX_CACHE
    key = str(index_path)
    try:
        mtime = index_path.stat().st_mtime
    except OSError:
        mtime = 0.0
    if _INDEX_CACHE is None or _INDEX_CACHE[0] != key or abs(_INDEX_CACHE[1] - mtime) > 1e-6:
        _INDEX_CACHE = (key, mtime, faiss.read_index(str(index_path)), _read_meta(meta_path))
    index, meta = _INDEX_CACHE[2], _INDEX_CACHE[3]
    if index.ntotal == 0 or not meta:
        return []

    v = embed_texts([query])
    vec = np.ascontiguousarray(v, dtype=np.float32)
    if scope_talker is None:
        scores, ids = index.search(vec, top_k)
        return _hits(scores, ids, meta, top_k)

    # 范围过滤：后过滤 + 候选集迭代扩大
    fetch = max(top_k * 4, 64)
    while True:
        scores, ids = index.search(vec, min(fetch, index.ntotal))
        cand = _hits(scores, ids, meta, fetch)
        out = [h for h in cand if (h["metadata"] or {}).get("talker") == scope_talker]
        if len(out) >= top_k or fetch >= index.ntotal:
            return out[:top_k]
        fetch = min(fetch * 2, index.ntotal)
