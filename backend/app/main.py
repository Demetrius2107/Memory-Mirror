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
import sqlite3
import threading
import time
import traceback
from pathlib import Path

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from backend.app.demo_data import DB_PATH, generate_demo_data
from backend.app.importer import run_import

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


class ImportRequest(BaseModel):
    path: str                         # 消息文件（CSV/JSONL）
    contact_path: str | None = None   # 联系人/群文件（R17 配对导入）
    members_path: str | None = None   # 群成员文件


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


@app.get("/api/chat/stream")
async def chat_stream(question: str = "我们哪一年吵架最多？"):
    """AI 问答流式输出（SSE，R5）。Week 5 接入真实 RAG + LLM，当前为模拟打字机。"""

    async def event_gen():
        yield (
            "data: "
            + json.dumps({"delta": "（SSE 流式通道已打通）", "done": False}, ensure_ascii=False)
            + "\n\n"
        )
        answer = f"关于「{question}」，检索到相关片段后我会逐字返回答案。当前为 Week 1 模拟响应。"
        for ch in answer:
            yield "data: " + json.dumps({"delta": ch, "done": False}, ensure_ascii=False) + "\n\n"
            await asyncio.sleep(0.01)
        yield "data: " + json.dumps({"delta": "", "done": True}, ensure_ascii=False) + "\n\n"

    return StreamingResponse(
        event_gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
