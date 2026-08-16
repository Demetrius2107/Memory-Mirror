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
#
# V2 — 增强版规则（2026-08-16）：
# 1) 词边界匹配：避免「好」匹配「好累/心情不好」等子串误判
# 2) 否定词反转：前 3 字内出现「不/没/别」时反转该词情感极性
# 3) 情感强度校准：根据消息长度和情感词密度调整分数
# 4) 多词/长词优先：优先匹配「不想」而非「想」

# 正向词（优先匹配长词/多字词）
POS_WORDS = ["太开心了", "哈哈哈", "开心", "哈哈", "好棒", "爱你", "感动", "加油", "想你了", "太好了", "真不错"]
# 短正向词（仅当不被前 3 字否定词修饰时生效）
POS_SHORT = ["好", "太", "想", "终于", "不错"]

# 负向词（优先匹配长词/多字词）
NEG_WORDS = ["有点难过", "烦死了", "气死我了", "不想上班", "心情不好", "压力好大", "难过", "烦", "失眠", "失望", "哭", "压力"]
# 短负向词（仅当不被前 3 字否定词修饰时生效）
NEG_SHORT = ["累", "唉", "气", "没意思", "不好"]

# 否定词（前 3 字内出现则反转情感极性）
NEGATORS = {"不", "没", "别"}


def _word_boundary_match(text: str, word: str) -> bool:
    """词边界匹配：word 在 text 中作为独立语义单元出现。
    对于多字词（>=2），直接子串匹配即可；
    对于单字词，要求至少一侧是边界（开头/结尾/非中文字符）。
    """
    if len(word) >= 2:
        return word in text
    # 单字词：至少一侧是边界
    idx = text.find(word)
    while idx != -1:
        before = text[idx - 1] if idx > 0 else ""
        after = text[idx + len(word)] if idx + len(word) < len(text) else ""
        is_cjk_before = "\u4e00" <= before <= "\u9fff"
        is_cjk_after = "\u4e00" <= after <= "\u9fff"
        # 至少一侧是边界（非中文/开头/结尾）
        if not is_cjk_before or not is_cjk_after:
            # 特别规则：单字"好"后面跟常见的负向关联字时不匹配
            # 如"好累"中的"好"（"累"是负向词）
            if word == "好" and is_cjk_after and after in ("累", "烦", "气", "难", "哭", "惨", "痛", "晕"):
                idx = text.find(word, idx + 1)
                continue
            # 特别规则：单字"好"在"你好"中作为问候语不匹配
            if word == "好" and is_cjk_before and before == "你":
                idx = text.find(word, idx + 1)
                continue
            return True
        idx = text.find(word, idx + 1)
    return False


def _has_negator_before(text: str, match_pos: int) -> bool:
    """检查 match_pos 前 3 字内是否有否定词。"""
    start = max(0, match_pos - 3)
    prefix = text[start:match_pos]
    return any(n in prefix for n in NEGATORS)


def rule_sentiment(text: str) -> float:
    """词典规则打分（V2 增强版）：-1 ~ 1。
    
    增强特性：
    - 词边界匹配避免子串误判
    - 否定词感知（前 3 字内出现"不/没/别"时反转极性）
    - 多词/长词优先匹配
    - 情感强度校准（长度调节 + 密度调节）
    """
    if not text or not text.strip():
        return 0.0

    pos_score = 0.0
    neg_score = 0.0

    def _find_matches(word_list, is_short: bool):
        """在 text 中查找匹配，返回 (总得分, 已匹配索引集合)。"""
        nonlocal pos_score, neg_score
        for w in word_list:
            if not _word_boundary_match(text, w):
                continue
            # 找该词所有出现位置
            idx = 0
            while True:
                idx = text.find(w, idx)
                if idx == -1:
                    break
                # 检查是否被否定词修饰
                has_neg = _has_negator_before(text, idx)
                if has_neg:
                    # 否定词修饰：该情感词被抵消，正负都不计
                    # 如"别烦我"中"烦"的负向被"别"抵消
                    idx += len(w)
                    continue
                # 短词权重降低，长词权重满
                wgt = 0.35 if is_short else 1.0
                if word_list is POS_WORDS or word_list is POS_SHORT:
                    pos_score += wgt
                else:
                    neg_score += wgt
                idx += len(w)

    # 先匹配长词（多字词优先），再匹配短词
    _find_matches(POS_WORDS, is_short=False)
    _find_matches(NEG_WORDS, is_short=False)
    _find_matches(POS_SHORT, is_short=True)
    _find_matches(NEG_SHORT, is_short=True)

    total = pos_score + neg_score
    if total == 0:
        # 无情感词：随机微幅波动（-0.15 ~ 0.15），避免全零
        return round(random.uniform(-0.15, 0.15), 3)

    # 基础分数
    raw = (pos_score - neg_score) / total

    # --- 情感强度校准 ---
    # 1) 情感词数量饱和：只有1个情感词时，分数不应饱和到±1.0
    if total <= 1.5:
        raw = raw * 0.65
    elif total <= 2.5:
        raw = raw * 0.85
    # 2) 短句拉伸：短消息（<=4字）若有情感词，强度放大
    if len(text) <= 4 and total > 0:
        raw = raw * 1.3
    # 3) 长句压缩：长消息（>10字）情感摊薄
    if len(text) > 10:
        raw = raw * 0.85
    # 4) 情感密度校准：如果情感词占比高，增强
    density = total / max(len(text), 1)
    if density > 0.3:
        raw = raw * 1.15
    elif density < 0.1 and total > 1.5:
        raw = raw * 0.9

    return round(max(-1.0, min(1.0, raw)), 3)


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
