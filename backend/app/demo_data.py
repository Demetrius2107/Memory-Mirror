"""演示数据集生成器 — 按 PRD §4.3 schema 生成模拟聊天数据（R16：内置演示数据）。

用法: python -m backend.app.demo_data
生成: data/demo.db（messages / contacts / group_members / metadata）
"""

from __future__ import annotations

import random
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parents[2] / "data"
DB_PATH = DATA_DIR / "demo.db"

# --- 模拟联系人（含备注名 remark，供 R17 选择面板演示：备注名 > 昵称 > wxid） ---
CONTACTS = [
    ("wxid_lin", "林小满", "林小满", "friend"),
    ("wxid_chen", "陈默", "陈默", "friend"),
    ("wxid_zhang", "Zhang Wei", "老张", "friend"),
    ("wxid_wang", "王思远", "思远", "friend"),
    ("wxid_li", "李一诺", "一诺", "friend"),
    ("wxid_zhao", "赵小刀", "小刀", "friend"),
    ("wxid_ma", "马也", "马也", "friend"),
    ("wxid_sun", "孙莉", "Sunny", "friend"),
    ("wxid_zhou", "周凯", "凯哥", "friend"),
    ("wxid_wu", "吴悠", "小悠", "friend"),
    ("wxid_tang", "唐果", "唐唐", "friend"),
    ("wxid_he", "何明远", "何总", "friend"),
    ("wxid_official", "微信团队", "微信团队", "official"),
    ("chatroom_family", "相亲相爱一家人", "相亲相爱一家人", "group"),
    ("chatroom_project", "项目攻坚小组", "项目攻坚小组", "group"),
    ("chatroom_oldclass", "大学同学会", "大学同学会", "group"),
]

# 群成员（group_wxid, member_wxid, 群内昵称）
GROUP_MEMBERS = [
    ("chatroom_family", "wxid_lin", "林小满"),
    ("chatroom_family", "wxid_zhang", "老张"),
    ("chatroom_family", "wxid_sun", "Sunny"),
    ("chatroom_project", "wxid_zhou", "凯哥"),
    ("chatroom_project", "wxid_wu", "小悠"),
    ("chatroom_project", "wxid_he", "何总"),
    ("chatroom_project", "wxid_li", "一诺"),
    ("chatroom_oldclass", "wxid_chen", "陈默"),
    ("chatroom_oldclass", "wxid_wang", "思远"),
    ("chatroom_oldclass", "wxid_zhao", "小刀"),
    ("chatroom_oldclass", "wxid_tang", "唐唐"),
]

# 小词库：话题池（日常 / 工作 / 正向情绪 / 负向情绪）
TOPICS = {
    "daily": ["吃饭了吗", "下班没", "周末去哪", "晚安", "今天天气不错", "快递到了", "在忙吗", "晚上一起吃饭", "看到一家新店", "记得带伞"],
    "work": ["方案改好了吗", "下午开会", "需求评审", "这个 bug 修了吗", "上线了吗", "数据报表发我", "客户反馈很好", "明天出差", "代码合并了", "测试通过了吗"],
    "emotion_hi": ["太开心了", "哈哈哈笑死", "好棒", "爱你", "终于搞定了", "太感动了", "想你了", "真不错", "太好了吧", "加油加油"],
    "emotion_lo": ["好累", "有点难过", "烦死了", "气死我了", "不想上班", "心情不好", "压力好大", "失眠了", "唉", "有点失望"],
}

CONTENT_TYPE = ["text", "text", "text", "image", "voice", "video", "file", "text", "text"]

# 简易规则情绪打分（对应 PRD R9：词典/规则流水线产出 emotion_score，非逐条 LLM）
POS_WORDS = ["开心", "哈哈", "好棒", "爱你", "感动", "想", "好", "不错", "太", "终于", "加油"]
NEG_WORDS = ["累", "难过", "烦", "气", "不想", "压力", "失眠", "唉", "失望", "哭"]


def rule_sentiment(text: str) -> float:
    """词典规则打分：-1 ~ 1。"""
    pos = sum(1 for w in POS_WORDS if w in text)
    neg = sum(1 for w in NEG_WORDS if w in text)
    total = pos + neg
    if total == 0:
        return round(random.uniform(-0.2, 0.2), 3)
    return round((pos - neg) / total, 3)


def generate_messages(rng: random.Random, total: int = 20000) -> list[tuple]:
    """生成 total 条消息，跨 2023-01 ~ 2026-08（UTC），私聊 70% / 群聊 30%。"""
    start = datetime(2023, 1, 1, tzinfo=timezone.utc)
    end = datetime(2026, 8, 16, tzinfo=timezone.utc)
    span = (end - start).total_seconds()

    friend_wxids = [c[0] for c in CONTACTS if c[3] == "friend"]
    group_wxids = [c[0] for c in CONTACTS if c[3] == "group"]
    rows = []
    for i in range(total):
        talker = rng.choice(friend_wxids) if rng.random() < 0.7 else rng.choice(group_wxids)
        ts = start + timedelta(seconds=span * rng.random())
        pool_name = rng.choices(
            ["daily", "work", "emotion_hi", "emotion_lo"], weights=[4, 2, 2, 2], k=1
        )[0]
        content = rng.choice(TOPICS[pool_name])
        rows.append(
            (
                f"demo_msg_{i:07d}",      # msg_id
                talker,                    # talker（单聊=好友wxid；群聊=群ID）
                int(ts.timestamp()),       # time_utc
                content,                   # content
                rng.choice(CONTENT_TYPE),  # content_type
                rule_sentiment(content),   # emotion_score
                ts.strftime("%Y-%m"),      # year_month
            )
        )
    return rows


def generate_demo_data(total: int = 20000) -> Path:
    """重建演示数据库（幂等：每次覆盖重建）。"""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    rng = random.Random(42)  # 固定种子，结果可复现

    if DB_PATH.exists():
        DB_PATH.unlink()

    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.executescript(
        """
        CREATE TABLE messages (
            id INTEGER PRIMARY KEY,
            msg_id TEXT UNIQUE,
            talker TEXT,
            time_utc INTEGER,
            content TEXT,
            content_type TEXT,
            emotion_score REAL,
            year_month TEXT
        );
        CREATE INDEX idx_time ON messages(time_utc);
        CREATE INDEX idx_talker ON messages(talker);
        CREATE INDEX idx_year_month ON messages(year_month);
        CREATE TABLE contacts (
            wxid TEXT PRIMARY KEY,
            nickname TEXT,
            remark TEXT,
            type TEXT,
            avatar TEXT
        );
        CREATE TABLE group_members (
            group_wxid TEXT,
            member_wxid TEXT,
            member_name TEXT,
            PRIMARY KEY (group_wxid, member_wxid)
        );
        CREATE TABLE metadata (
            key TEXT PRIMARY KEY,
            value TEXT
        );
        """
    )
    cur.executemany(
        "INSERT OR REPLACE INTO contacts (wxid, nickname, remark, type) VALUES (?,?,?,?)",
        [(c[0], c[1], c[2], c[3]) for c in CONTACTS],
    )
    cur.executemany("INSERT OR REPLACE INTO group_members VALUES (?,?,?)", GROUP_MEMBERS)
    cur.executemany(
        "INSERT OR REPLACE INTO messages (msg_id, talker, time_utc, content, content_type, emotion_score, year_month) VALUES (?,?,?,?,?,?,?)",
        generate_messages(rng, total),
    )
    cur.executemany(
        "INSERT OR REPLACE INTO metadata (key, value) VALUES (?,?)",
        [
            ("source", "demo"),
            ("total_messages", str(total)),
            ("generated_at", datetime.now(timezone.utc).isoformat()),
        ],
    )
    conn.commit()
    conn.close()
    print(f"[demo_data] 已生成 {total} 条演示消息 -> {DB_PATH}")
    return DB_PATH


if __name__ == "__main__":
    generate_demo_data()
