"""RAG 向量索引（Week 4，PRD §4.4）：切片 → embedding → ChromaDB 存储 + 检索

- 切片：500 字 / 50 字重叠
- 存储：ChromaDB PersistentClient（data/chroma），集合 memorymirror_messages
  元数据：msg_id / talker / time_utc / year_month / start_idx
- embedding：backend.app.embedder（当前 TF-IDF 兜底；R4 后续可切 bge-small-zh ONNX）
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import chromadb

from backend.app.demo_data import DB_PATH
from backend.app.embedder import embed_texts, fit

CHUNK_SIZE = 1000  # 1000 字切片（原 500）：长消息片段数减半，RAG 粒度仍够
CHUNK_OVERLAP = 100
BATCH = 256  # 批量加大：减少 chroma 调用次数与序列化开销
COLLECTION_NAME = "memorymirror_messages"
CHROMA_DIR_NAME = "chroma"


def chunk_text(text: str) -> list[tuple[int, str]]:
    """按 500 字带 50 字重叠切片。返回 [(start_idx, text)]。"""
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


def _client(db_path: Path):
    chroma_dir = db_path.parent / CHROMA_DIR_NAME
    return chromadb.PersistentClient(path=str(chroma_dir))


def get_collection(db_path: Path, create: bool = True):
    client = _client(db_path)
    name = COLLECTION_NAME
    try:
        return client.get_collection(name)
    except Exception:
        if create:
            return client.create_collection(name, metadata={"hnsw:space": "cosine"})
        return None


def build_index(db_path: str | Path | None = None, progress_cb=None) -> int:
    """全量重建索引（两遍流式，低内存）：读消息 → 切片 → embedding → ChromaDB upsert。

    Pass 1：流式切片 → fit（生成器喂入，不驻留 1.88M 片段文本列表）
    Pass 2：重读 → 批量向量化 + upsert（元数据现用现建，不驻留片段列表）
    返回片段数。
    """
    db_path = Path(db_path) if db_path else DB_PATH

    def cb(**kw):
        if progress_cb:
            progress_cb(**kw)

    SQL = "SELECT msg_id, talker, time_utc, content, year_month FROM messages ORDER BY id"

    def iter_chunk_texts(conn):
        for msg_id, talker, ts, content, ym in conn.execute(SQL):
            for _start, seg in chunk_text(content):
                if seg.strip() and len(seg.strip()) >= 2:  # 跳过无意义短片段
                    yield seg

    cb(status="running", phase="read", message="正在读取消息", current=0, total=0)
    conn = sqlite3.connect(db_path)
    try:
        n_msgs = conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
        cb(phase="chunk", message=f"正在切片（{n_msgs} 条消息）", current=0, total=n_msgs)
        # Pass 1：fit（迭代器，大语料不驻留）
        fit(iter_chunk_texts(conn))
        cb(phase="embed", message="正在向量化...", current=0, total=0)

        # Pass 2：批量 upsert
        client = _client(db_path)
        try:
            client.delete_collection(COLLECTION_NAME)
        except Exception:
            pass
        col = client.create_collection(
            COLLECTION_NAME,
            metadata={"hnsw:space": "cosine", "hnsw:M": 12, "hnsw:construction_ef": 120},
        )

        done = 0
        batch: list = []

        def flush():
            nonlocal done
            if not batch:
                return
            ids = [b[0] for b in batch]
            segs = [b[1] for b in batch]
            metas = [b[2] for b in batch]
            vecs = embed_texts(segs)
            col.upsert(ids=ids, embeddings=vecs.tolist(), documents=segs, metadatas=metas)
            done += len(batch)
            cb(current=done)
            batch.clear()

        for msg_id, talker, ts, content, ym in conn.execute(SQL):
            for start, seg in chunk_text(content):
                if not (seg.strip() and len(seg.strip()) >= 2):
                    continue
                batch.append(
                    (
                        f"{msg_id}#{start}",
                        seg,
                        {"msg_id": msg_id, "talker": talker, "time_utc": ts, "year_month": ym, "start_idx": start},
                    )
                )
                if len(batch) >= BATCH:
                    flush()
        flush()
    finally:
        conn.close()
    cb(status="done", phase="done", message=f"索引构建完成：{done} 个片段", current=done, total=done)
    return done


def search(query: str, top_k: int = 20, db_path: str | Path | None = None) -> list[dict]:
    """向量检索 Top-K，返回片段及元数据（距离越小越相关）。"""
    db_path = Path(db_path) if db_path else DB_PATH
    col = get_collection(db_path, create=False)
    if col is None:
        return []
    res = col.query(
        query_embeddings=embed_texts([query]).tolist(),
        n_results=top_k,
        include=["documents", "metadatas", "distances"],
    )
    docs = (res.get("documents") or [[]])[0]
    metas = (res.get("metadatas") or [[]])[0]
    dists = (res.get("distances") or [[]])[0]
    return [
        {"content": d, "metadata": m, "distance": dist}
        for d, m, dist in zip(docs, metas, dists)
    ]
