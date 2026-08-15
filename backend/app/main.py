"""记忆镜像 MemoryMirror — 后端引擎（FastAPI）Week 1 骨架

接口：
- GET  /health             健康检查
- GET  /api/contacts       联系人/群列表（R17 选择面板数据源）
- GET  /api/stats          消息总量等统计
- POST /api/import         导入任务占位（异步 task_id）
- WS   /ws/progress        处理进度推送（WebSocket，R5）
- GET  /api/chat/stream    AI 问答流式输出（SSE，R5）

启动：uvicorn backend.app.main:app --host 127.0.0.1 --port 8787
"""

from __future__ import annotations

import asyncio
import json
import random
import sqlite3

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse

from backend.app.demo_data import DB_PATH, generate_demo_data

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
async def start_import():
    """导入任务占位：返回 task_id，真实进度经 /ws/progress 推送（Week 2-3 实现）。"""
    task_id = f"imp_{random.randint(100000, 999999)}"
    return {"task_id": task_id, "status": "accepted", "note": "Week 2-3 实现真实导入"}


@app.websocket("/ws/progress")
async def ws_progress(ws: WebSocket):
    """模拟导入进度推送：phase/current/total，完成后发 done（R5：进度走 WebSocket）。"""
    await ws.accept()
    try:
        phases = [
            ("parsing", "正在解析导入文件", 100),
            ("cleaning", "正在清洗数据", 100),
            ("vectorizing", "正在生成向量索引", 100),
        ]
        for name, label, total in phases:
            for i in range(0, total + 1, 10):
                await ws.send_text(
                    json.dumps(
                        {
                            "type": "progress",
                            "phase": name,
                            "phase_label": label,
                            "current": i,
                            "total": total,
                            "percent": i,
                        },
                        ensure_ascii=False,
                    )
                )
                await asyncio.sleep(0.05)
        await ws.send_text(
            json.dumps({"type": "done", "message": "分析完成，共 20,000 条消息"}, ensure_ascii=False)
        )
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
