"""记忆镜像 MemoryMirror — 后端引擎（FastAPI）

接口：
- GET  /health             健康检查
- GET  /api/contacts       联系人/群列表（R17 选择面板数据源）
- GET  /api/stats          消息总量等统计
- POST /api/import         导入任务（Week 2：格式探测+清洗+UPSERT 入库，异步 task_id）
- GET  /api/tasks/{id}     导入任务状态查询
- WS   /ws/progress        处理进度推送（WebSocket，R5，轮询 TASKS 实时状态）
- GET  /api/chat/stream    AI 问答流式输出（SSE，R5）

启动：uvicorn backend.app.main:app --host 127.0.0.1 --port 8787
"""

from __future__ import annotations

import asyncio
import json
import shutil
import sqlite3
import threading
import time
import traceback
from pathlib import Path

from fastapi import FastAPI, HTTPException, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from backend.app.analyzer import analyze_scope
from backend.app.demo_data import DB_PATH, generate_demo_data
from backend.app.importer import run_import
from backend.app.llm import build_rag_prompt, has_key, load_config, save_config, stream_chat
from backend.app.rag_index import build_index, search

app = FastAPI(title="MemoryMirror Engine", version="0.1.0")

# 仅本地访问；CORS 收窄到调试用途（R12：展示端仅嵌 Webview，浏览器仅调试）
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://127.0.0.1:*", "tauri://localhost"],
    allow_methods=["*"],
    allow_headers=["*"],
)


def _db() -> sqlite3.Connection:
    if not DB_PATH.exists():
        generate_demo_data()
    return sqlite3.connect(DB_PATH)


# ---------------- 导入任务状态（真实进度，R5 走 WebSocket 推送） ----------------
TASKS: dict[str, dict] = {}
TASKS_LOCK = threading.Lock()
# 并行分析任务的 RAG 段串行锁：embedder 为全局单例，多个任务同时 fit 会互相踩
# （统计段仍并行，仅 fit+search 段互斥）
ANALYZE_RAG_LOCK = threading.Lock()


class ImportRequest(BaseModel):
    path: str                         # 消息文件（CSV/JSONL）
    contact_path: str | None = None   # 联系人/群文件（R17 配对导入）
    members_path: str | None = None   # 群成员文件


class ConfigRequest(BaseModel):
    api_key: str | None = None        # 空字符串 = 清空
    base_url: str | None = None
    model: str | None = None


@app.get("/health")
def health():
    return {"status": "ok", "service": "memorymirror-engine", "version": "0.1.0"}


@app.get("/api/contacts")
def list_contacts():
    """联系人/群列表（R17 选择面板数据源），按互动量降序。"""
    conn = _db()
    rows = conn.execute(
        """
        SELECT c.wxid, c.nickname, c.remark, c.type,
               COUNT(m.id) AS cnt,
               MAX(m.time_utc) AS last_ts
        FROM contacts c
        LEFT JOIN messages m ON m.talker = c.wxid
        GROUP BY c.wxid
        ORDER BY cnt DESC
        """
    ).fetchall()
    conn.close()
    return {
        "items": [
            {
                "wxid": r[0],
                "nickname": r[1],
                "remark": r[2],
                "display_name": r[2] or r[1] or r[0],  # 备注名 > 昵称 > wxid（R17）
                "type": r[3],
                "message_count": r[4],
                "last_ts": r[5],
            }
            for r in rows
        ]
    }


@app.get("/api/stats")
def stats():
    conn = _db()
    total = conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
    friends = conn.execute("SELECT COUNT(*) FROM contacts WHERE type='friend'").fetchone()[0]
    groups = conn.execute("SELECT COUNT(*) FROM contacts WHERE type='group'").fetchone()[0]
    conn.close()
    return {"total_messages": total, "friends": friends, "groups": groups}


@app.post("/api/import")
async def start_import(req: ImportRequest):
    """启动真实导入任务（后台线程）：格式探测 → 清洗 → UPSERT 入库（Week 2）。"""
    src = Path(req.path)
    if not src.is_file():
        raise HTTPException(status_code=400, detail=f"消息文件不存在: {req.path}")
    for p in (req.contact_path, req.members_path):
        if p and not Path(p).is_file():
            raise HTTPException(status_code=400, detail=f"辅助文件不存在: {p}")

    task_id = f"imp_{int(time.time() * 1000)}"
    with TASKS_LOCK:
        TASKS[task_id] = {
            "task_id": task_id, "status": "queued", "phase": "queued",
            "current": 0, "total": 0, "message": "任务已创建，等待执行",
        }

    def _run():
        def cb(**kw):
            with TASKS_LOCK:
                TASKS[task_id].update(kw)

        try:
            run_import(
                task_id, src,
                contact_path=req.contact_path, members_path=req.members_path,
                progress_cb=cb,
            )
        except Exception as e:  # 记录错误，避免后台线程静默死亡
            cb(status="error", phase="error", message=f"导入失败: {e}")
            traceback.print_exc()

    threading.Thread(target=_run, daemon=True).start()
    return {"task_id": task_id, "status": "accepted"}


@app.get("/api/tasks/{task_id}")
def get_task(task_id: str):
    with TASKS_LOCK:
        task = TASKS.get(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail=f"任务不存在: {task_id}")
    return task


@app.post("/api/index")
async def start_index():
    """全量重建向量索引（后台线程，进度走 /ws/progress，Week 4）。"""
    task_id = f"idx_{int(time.time() * 1000)}"
    with TASKS_LOCK:
        TASKS[task_id] = {
            "task_id": task_id, "status": "queued", "phase": "queued",
            "current": 0, "total": 0, "message": "索引任务已创建，等待执行",
        }

    def _run():
        def cb(**kw):
            with TASKS_LOCK:
                TASKS[task_id].update(kw)

        try:
            build_index(progress_cb=cb)
        except Exception as e:
            cb(status="error", phase="error", message=f"索引构建失败: {e}")
            traceback.print_exc()

    threading.Thread(target=_run, daemon=True).start()
    return {"task_id": task_id, "status": "accepted"}


@app.get("/api/rag/search")
def rag_search(q: str = "", top_k: int = 20, scope_talker: str | None = None):
    """RAG 向量检索（Week 4）：问题 → Top-K 相关片段（含元数据，距离越小越相关）。

    scope_talker：范围过滤（某人 wxid / 某群 ID；None=全量）。
    说明：reranker 依赖 bge-reranker ONNX（当前网络无法下载），暂时以向量距离排序，
    模型可用后在此接入重排（PRD §4.4）。
    """
    q = q.strip()
    if not q:
        raise HTTPException(status_code=400, detail="q 不能为空")
    hits = search(q, top_k=max(1, min(top_k, 50)), scope_talker=scope_talker)
    return {"query": q, "hits": hits}


@app.get("/api/debug/embed")
def debug_embed(q: str = "吃饭了吗"):
    """调试（Week 4 排障）：查询向量状态 vs 索引中已存储向量的范数，定位检索失效根因。"""
    import numpy as np

    from backend.app import embedder as emb
    from backend.app.rag_index import get_collection

    v = emb.embed_texts([q])
    col = get_collection(DB_PATH, create=False)
    out = {
        "query": q,
        "query_vec_norm": round(float(np.linalg.norm(v)), 3),
        "fitted": emb._fitted,
        "vocab_size": len(emb.get_embedder().vocab),
        "vocab_first5": list(emb.get_embedder().vocab.items())[:5],
        "query_nonzero_idx": [int(i) for i, x in enumerate(np.ravel(v)) if x > 0][:8],
    }
    if col is None:
        out["note"] = "collection 不存在"
        return out
    # A) chroma 原生 query（同一查询向量）
    r = col.query(query_embeddings=v.tolist(), n_results=3, include=["documents", "distances"])
    out["chroma_query"] = [
        {"doc": d, "dist": round(float(x), 4)}
        for d, x in zip(r["documents"][0], r["distances"][0])
    ]
    # B) 手动余弦对照：取前 2000 条存储向量与查询向量逐条 dot（向量均已 L2 归一化）
    got = col.get(limit=2000, include=["embeddings", "documents"])
    embs = np.asarray(got.get("embeddings"), dtype=np.float32)
    docs = got.get("documents") or []
    if embs.ndim == 2 and len(embs):
        sims = embs @ np.ravel(v).astype(np.float32)
        order = np.argsort(-sims)[:3]
        out["manual_cosine_top3"] = [
            {"doc": docs[int(i)], "cos": round(float(sims[int(i)]), 4)}
            for i in order
        ]
    else:
        out["manual_cosine_top3"] = "无向量"
    return out


@app.websocket("/ws/progress")
async def ws_progress(ws: WebSocket):
    """推送最新导入任务的实时进度（R5）：轮询 TASKS 状态，变化即推送。"""
    await ws.accept()
    last_sig = None
    try:
        while True:
            with TASKS_LOCK:
                task = TASKS[max(TASKS.keys())] if TASKS else None
            if task is not None:
                sig = (task["status"], task.get("phase"), task.get("current"), task.get("total"), task.get("message"))
                if sig != last_sig:
                    last_sig = sig
                    total = task.get("total", 0)
                    current = task.get("current", 0)
                    if task["status"] == "done":
                        evt_type = "done"
                    elif task["status"] == "error":
                        evt_type = "error"
                    else:
                        evt_type = "progress"
                    await ws.send_text(
                        json.dumps(
                            {
                                "type": evt_type,
                                "task_id": task["task_id"],
                                "status": task["status"],
                                "phase": task.get("phase"),
                                "phase_label": task.get("message", ""),
                                "current": current,
                                "total": total,
                                "percent": int(current / total * 100) if total else 0,
                                "message": task.get("message", ""),
                            },
                            ensure_ascii=False,
                        )
                    )
            await asyncio.sleep(0.3)
    except WebSocketDisconnect:
        pass
    finally:
        await ws.close()


def _scope_label(scope_talker: str | None) -> str:
    """分析范围显示名（备注名 > 昵称 > wxid；None = 全部数据）。"""
    if not scope_talker:
        return "全部数据"
    conn = _db()
    row = conn.execute(
        "SELECT nickname, remark FROM contacts WHERE wxid=?", (scope_talker,)
    ).fetchone()
    conn.close()
    if row:
        return f"「{row[1] or row[0] or scope_talker}」({scope_talker})"
    return f"「{scope_talker}」"


def _build_emotion_summary(scope_talker: str | None) -> str | None:
    """构建情感统计摘要，供 LLM 理解情绪全貌。

    返回格式化的文本块，包含：
    - 总消息量、情感均值、正/负/中性分布
    - 最近6个月的情绪趋势
    - 极端情绪片段（最强正向/负向）
    """
    try:
        conn = _db()
        where = "WHERE talker=?" if scope_talker else ""
        params = (scope_talker,) if scope_talker else ()

        # 总体统计
        total = conn.execute(
            f"SELECT COUNT(*) FROM messages {where}", params
        ).fetchone()[0]
        if total == 0:
            conn.close()
            return None

        avg = conn.execute(
            f"SELECT ROUND(AVG(emotion_score), 3) FROM messages {where}", params
        ).fetchone()[0]

        pos = conn.execute(
            f"SELECT COUNT(*) FROM messages {where} AND emotion_score > 0.1", params
        ).fetchone()[0]
        neg = conn.execute(
            f"SELECT COUNT(*) FROM messages {where} AND emotion_score < -0.1", params
        ).fetchone()[0]
        neu = total - pos - neg

        # 最近6个月趋势
        trend_rows = conn.execute(
            f"SELECT year_month, COUNT(*), ROUND(AVG(emotion_score), 3) "
            f"FROM messages {where} AND year_month >= ? "
            f"GROUP BY year_month ORDER BY year_month",
            params + ("2026-03",) if scope_talker else ("2026-03",),
        ).fetchall()
        trend_lines = []
        for m, c, e in trend_rows:
            bar = "😊" * max(1, int(e * 10)) if e and e > 0 else "😢" * max(1, int(abs(e) * 10)) if e else "😐"
            trend_lines.append(f"  {m}: {c}条, 平均{e or 0:.3f}")

        # 极端情绪样例
        extremes = conn.execute(
            f"SELECT content, emotion_score FROM messages {where} AND emotion_score IS NOT NULL "
            f"ORDER BY ABS(emotion_score) DESC LIMIT 3", params
        ).fetchall()
        extreme_lines = [f"  「{r[0]}」({r[1]:+.2f})" for r in extremes]

        conn.close()

        lines = [
            f"共 {total} 条消息，情感均值 {avg if avg else 0:.3f}（+为正/-为负）。",
            f"正向 {pos} 条 ({pos*100//max(total,1)}%) / 负向 {neg} 条 ({neg*100//max(total,1)}%) / 中性 {neu} 条 ({neu*100//max(total,1)}%)",
        ]
        if trend_lines:
            lines.append(f"最近趋势：\n" + "\n".join(trend_lines))
        if extreme_lines:
            lines.append("极端情绪片段：\n" + "\n".join(extreme_lines))
        return "\n".join(lines)
    except Exception:
        return None


@app.get("/api/chat/stream")
async def chat_stream(question: str = "我们哪一年吵架最多？", scope_talker: str | None = None):
    """AI 问答流式输出（SSE，R5 / Week 5）：RAG 检索 → Prompt 组装 → LLM 流式；
    范围可选（scope_talker：某人 wxid / 某群 ID；None=全量）；
    未配置 Key 或调用失败时降级为模拟回答（附检索片段预览，保持可用）。"""

    hits = search(question, top_k=8, scope_talker=scope_talker)

    # 构建情感统计摘要（注入 LLM 上下文，提升回答质量）
    emotion_summary = _build_emotion_summary(scope_talker)

    messages = build_rag_prompt(
        question, hits,
        scope_label=_scope_label(scope_talker),
        emotion_summary=emotion_summary,
    )
    llm_on = has_key()

    async def event_gen():
        yield (
            "data: "
            + json.dumps(
                {"delta": f"（已检索 {len(hits)} 条相关片段）\n", "done": False},
                ensure_ascii=False,
            )
            + "\n\n"
        )
        if llm_on:
            q: asyncio.Queue = asyncio.Queue()

            def feed(delta: str):
                q.put_nowait(delta)

            def run():
                try:
                    stream_chat(messages, feed)
                except Exception as e:
                    q.put_nowait(f"\n\n[LLM 调用失败: {e}]")
                finally:
                    q.put_nowait(None)

            asyncio.get_running_loop().run_in_executor(None, run)
            while True:
                d = await q.get()
                if d is None:
                    break
                yield "data: " + json.dumps({"delta": d, "done": False}, ensure_ascii=False) + "\n\n"
        else:
            # 降级：展示本地检索片段 + 模拟回答（无 Key 可用；设置 Key 后走真实 LLM）
            preview = "\n".join(
                f"[{i}] ({h.get('metadata', {}).get('year_month', '?')}) {h.get('content', '')}"
                for i, h in enumerate(hits[:3], 1)
            )
            answer = (
                f"关于「{question}」，本地检索到的最相关片段：\n{preview}\n\n"
                f"（未配置 API Key，当前为降级模拟回答。POST /api/config 设置 Key 后获得真实 AI 回答）"
            )
            for ch in answer:
                yield "data: " + json.dumps({"delta": ch, "done": False}, ensure_ascii=False) + "\n\n"
                await asyncio.sleep(0.005)
        yield "data: " + json.dumps({"delta": "", "done": True}, ensure_ascii=False) + "\n\n"

    return StreamingResponse(
        event_gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.post("/api/upload")
async def upload_file(file: UploadFile):
    """上传导入文件到 data/uploads/，返回可导入的本地路径（向导页用）。"""
    upload_dir = DB_PATH.parent / "uploads"
    upload_dir.mkdir(parents=True, exist_ok=True)
    dest = upload_dir / Path(file.filename or "upload.bin").name
    with open(dest, "wb") as f:
        shutil.copyfileobj(file.file, f)
    return {"path": str(dest), "name": file.filename, "size": dest.stat().st_size}


class AnalyzeRequest(BaseModel):
    scope_talker: str | None = None   # 范围：某人 wxid / 某群 ID；None=全量
    question: str | None = None       # 可选：对范围做 RAG 片段检索
    top_k: int = 8


@app.post("/api/analyze")
async def start_analyze(req: AnalyzeRequest):
    """分析任务（范围可选 + 可并行）：对解密数据区（demo.db）按范围聚合统计，
    可选附带 RAG 片段检索。每个任务一个后台线程，TASKS 为多任务并存——
    可同时发起多个不同范围的分析。结果随 GET /api/tasks/{id} 返回。"""
    task_id = f"ana_{int(time.time() * 1000)}"
    with TASKS_LOCK:
        TASKS[task_id] = {
            "task_id": task_id, "status": "queued", "phase": "queued",
            "current": 0, "total": 0, "message": "分析任务已创建，等待执行",
        }

    def _run():
        def cb(**kw):
            with TASKS_LOCK:
                TASKS[task_id].update(kw)

        try:
            cb(status="running", phase="stats", message="正在统计范围数据", current=1, total=3)
            stats = analyze_scope(DB_PATH, scope_talker=req.scope_talker)
            result = {
                "scope_talker": req.scope_talker,
                "scope_label": _scope_label(req.scope_talker),
                "stats": stats,
                "hits": [],
            }
            if req.question and req.question.strip():
                cb(phase="rag", message="正在检索相关片段", current=2, total=3)
                from backend.app.rag_index import chunk_text, search
                from backend.app.embedder import fit

                def all_chunk_texts():
                    conn = sqlite3.connect(DB_PATH)
                    try:
                        for (content,) in conn.execute("SELECT content FROM messages"):
                            for _s, seg in chunk_text(content):
                                s = seg.strip()
                                if s and len(s) >= 2:
                                    yield s
                    finally:
                        conn.close()

                with ANALYZE_RAG_LOCK:  # 并行任务互斥：embedder 为全局单例
                    fit(all_chunk_texts())  # 与索引同词表，保证检索一致
                    result["hits"] = search(
                        req.question.strip(),
                        top_k=max(1, min(req.top_k, 20)),
                        scope_talker=req.scope_talker,
                    )
            cb(status="done", phase="done", message="分析完成", current=3, total=3, result=result)
        except Exception as e:
            cb(status="error", phase="error", message=f"分析失败: {e}")
            traceback.print_exc()

    threading.Thread(target=_run, daemon=True).start()
    return {"task_id": task_id, "status": "accepted"}


@app.get("/api/talker/{wxid}/stats")
def talker_stats(wxid: str):
    """单联系人/群统计：总数、最近时间、月度消息量（前端详情/Week 6 曲线数据基础）。"""
    conn = _db()
    row = conn.execute("SELECT COUNT(*), MAX(time_utc) FROM messages WHERE talker=?", (wxid,)).fetchone()
    monthly = conn.execute(
        "SELECT year_month, COUNT(*) FROM messages WHERE talker=? GROUP BY year_month ORDER BY year_month",
        (wxid,),
    ).fetchall()
    conn.close()
    return {
        "wxid": wxid,
        "total": row[0],
        "last_ts": row[1],
        "monthly": [{"month": m, "count": c} for m, c in monthly],
    }


@app.get("/api/talker/{wxid}/emotions")
def talker_emotions(wxid: str):
    """情绪日历：按天的平均情感分与消息数（Week 6 仪表盘热力图数据源）。"""
    conn = _db()
    rows = conn.execute(
        "SELECT date(time_utc, 'unixepoch', 'localtime') AS day, COUNT(*), AVG(emotion_score) "
        "FROM messages WHERE talker=? AND emotion_score IS NOT NULL "
        "GROUP BY day ORDER BY day",
        (wxid,),
    ).fetchall()
    conn.close()
    return {
        "wxid": wxid,
        "days": [{"day": r[0], "count": r[1], "avg": round(r[2], 3)} for r in rows],
    }


@app.get("/api/talker/{wxid}/emotions/monthly")
def talker_emotions_monthly(wxid: str):
    """情绪月趋势：按月的平均情感分（Week 6 仪表盘情绪趋势柱状图数据源）。"""
    conn = _db()
    rows = conn.execute(
        "SELECT year_month, COUNT(*), AVG(emotion_score) "
        "FROM messages WHERE talker=? AND emotion_score IS NOT NULL "
        "GROUP BY year_month ORDER BY year_month",
        (wxid,),
    ).fetchall()
    conn.close()
    return {
        "wxid": wxid,
        "months": [{"month": r[0], "count": r[1], "avg_emo": round(r[2], 3)} for r in rows],
    }


@app.get("/api/talker/{wxid}/words")
def talker_words(wxid: str, top: int = 100):
    """词云：文本消息高频词（快速分词器 + 停用词过滤，Week 6 仪表盘词云数据源）。"""
    import re
    from collections import Counter

    from backend.app.embedder import _tokenize

    # 过滤非词残渣：二进制转义（\x00）、单字符 ASCII、群消息发送者标识（wxid_xxx:）
    _ARTIFACT = re.compile(r"^x[0-9a-fA-F]{2}$|^[a-zA-Z0-9]$|^wxid_[a-z0-9_]+$")

    conn = _db()
    rows = conn.execute(
        "SELECT content FROM messages WHERE talker=? AND content_type='text' AND content != ''",
        (wxid,),
    ).fetchall()
    conn.close()
    c: Counter = Counter()
    for (content,) in rows:
        c.update(t for t in _tokenize(content) if not _ARTIFACT.match(t))
    return {"wxid": wxid, "words": [{"word": w, "count": n} for w, n in c.most_common(top)]}


@app.post("/api/demo")
def make_demo():
    """重新生成演示数据集（向导"演示数据集"入口）。"""
    from backend.app.demo_data import generate_demo_data

    generate_demo_data()
    return {"status": "ok", "message": "演示数据集已重新生成"}


@app.get("/api/config")
def get_config():
    """读取 LLM 配置（Key 不返回明文，仅返回是否已配置）。"""
    cfg = load_config()
    return {"base_url": cfg["base_url"], "model": cfg["model"], "has_key": has_key()}


@app.post("/api/config")
def set_config(req: ConfigRequest):
    """设置 LLM 配置（api_key/base_url/model），存 data/config.json（不入库）。"""
    cfg = save_config({"api_key": req.api_key, "base_url": req.base_url, "model": req.model})
    return {"status": "ok", "base_url": cfg["base_url"], "model": cfg["model"], "has_key": has_key()}


# 静态 UI 挂载必须在所有 API 路由之后（保证 /api/* 优先匹配）
app.mount(
    "/",
    StaticFiles(directory=str(Path(__file__).resolve().parents[2] / "ui"), html=True),
    name="ui",
)
