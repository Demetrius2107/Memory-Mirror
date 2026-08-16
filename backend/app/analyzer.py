"""分析引擎（Week 6 扩展）：按范围（全部 / 某人 / 某群）聚合统计。

数据基座：解密后入库的 messages 表（demo.db，即"解密之后的数据区"）。
范围 = talker 过滤：单聊=好友 wxid；群聊=群 ID；None=全量。
RAG 片段检索由调用方在完成 fit 后调 rag_index.search(scope_talker=...) 完成。
"""

from __future__ import annotations

import re
import sqlite3
from collections import Counter

from backend.app.embedder import _tokenize

# 过滤非词残渣：二进制转义（\x00）、单字符 ASCII、群消息发送者标识（wxid_xxx:）
_ARTIFACT = re.compile(r"^x[0-9a-fA-F]{2}$|^[a-zA-Z0-9]$|^wxid_[a-z0-9_]+$")


def analyze_scope(db_path, scope_talker: str | None = None) -> dict:
    """按范围聚合统计。返回：总数 / 时间跨度 / 月度分布 / 情绪均值 /
    高频词 top20 / 活跃时段 top5。"""
    conn = sqlite3.connect(db_path)
    try:
        where = "WHERE talker=?" if scope_talker else ""
        params = [scope_talker] if scope_talker else []
        # where 为空时用 " WHERE" 接条件，非空时用 " AND" 接条件
        cond = " AND" if where else " WHERE"

        total = conn.execute(f"SELECT COUNT(*) FROM messages {where}", params).fetchone()[0]
        span = conn.execute(
            f"SELECT MIN(time_utc), MAX(time_utc) FROM messages {where}", params
        ).fetchone()
        monthly = conn.execute(
            f"SELECT year_month, COUNT(*) FROM messages {where} GROUP BY year_month ORDER BY year_month",
            params,
        ).fetchall()
        emo = conn.execute(
            f"SELECT AVG(emotion_score) FROM messages {where}{cond} emotion_score IS NOT NULL",
            params,
        ).fetchone()[0]
        words = conn.execute(
            f"SELECT content FROM messages {where}{cond} content_type='text' AND content != ''",
            params,
        ).fetchall()
        hours = conn.execute(
            f"SELECT strftime('%H', time_utc, 'unixepoch', 'localtime') AS h, COUNT(*) "
            f"FROM messages {where} GROUP BY h ORDER BY COUNT(*) DESC LIMIT 5",
            params,
        ).fetchall()

        c: Counter = Counter()
        for (content,) in words:
            c.update(t for t in _tokenize(content) if not _ARTIFACT.match(t))

        return {
            "total": total,
            "first_ts": span[0],
            "last_ts": span[1],
            "monthly": [{"month": m, "count": n} for m, n in monthly],
            "avg_emotion": round(emo, 3) if emo is not None else None,
            "top_words": [{"word": w, "count": n} for w, n in c.most_common(20)],
            "active_hours": [{"hour": h, "count": n} for h, n in hours],
        }
    finally:
        conn.close()
