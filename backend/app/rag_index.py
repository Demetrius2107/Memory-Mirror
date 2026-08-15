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

CHUNK_SIZE = 500
CHUNK_OVERLAP = 50
BATCH = 128
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
    """全量重建索引：读 messages → 切片 → embedding → ChromaDB upsert。返回片段数。"""
    db_path = Path(db_path) if db_path else DB_PATH

    def cb(**kw):
        if progress_cb:
            progress_cb(**kw)

    cb(status="running", phase="read", message="正在读取消息", current=0, total=0)
    conn = sqlite3.connect(db_path)
    try:
        rows = conn.execute(
            "SELECT msg_id, talker, time_utc, content, year_month FROM messages ORDER BY id"
        ).fetchall()
    finally:
        conn.close()
    cb(phase="chunk", message=f"正在切片（{len(rows)} 条消息）", current=0, total=len(rows))

    chunks: list[tuple[str, str, dict]] = []  # (doc_id, text, metadata)
    for msg_id, talker, ts, content, ym in rows:
        for start, seg in chunk_text(content):
            if not seg.strip():
                continue
            chunks.append(
                (
                    f"{msg_id}#{start}",
                    seg,
                    {"msg_id": msg_id, "talker": talker, "time_utc": ts, "year_month": ym, "start_idx": start},
                )
            )
    cb(phase="embed", message=f"正在向量化（{len(chunks)} 个片段）", current=0, total=len(chunks))

    # 全量文本 fit：保证 query 与 doc 处于同一词表/向量空间
    fit([c[1] for c in chunks])

    client = _client(db_path)
    try:
        client.delete_collection(COLLECTION_NAME)
    except Exception:
        pass
    col = client.create_collection(COLLECTION_NAME, metadata={"hnsw:space": "cosine"})

    done = 0
    for i in range(0, len(chunks), BATCH):
        batch = chunks[i : i + BATCH]
        vecs = embed_texts([c[1] for c in batch])
        col.upsert(
            ids=[c[0] for c in batch],
            embeddings=vecs.tolist(),
            documents=[c[1] for c in batch],
            metadatas=[c[2] for c in batch],
        )
        done += len(batch)
        cb(current=done)
    cb(status="done", phase="done", message=f"索引构建完成：{len(chunks)} 个片段", current=done, total=done)
    return len(chunks)


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
