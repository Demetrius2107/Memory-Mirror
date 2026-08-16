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
# V4 — jieba 分词增强版（2026-08-16）：
# 1) 双引擎架构：子串匹配（标准词）+ jieba 分词精确匹配（短语级）
# 2) 短语级情感词典：覆盖网络用语/口语/成语（"一脸懵逼"/"破防"/"上头"等）
# 3) 分词精确匹配优先：jieba 分词后的整词匹配，避免子串误判
# 4) 其余特性继承 V3：词边界匹配/否定词抵消/问候语豁免/表情符号/长文本校准

import jieba
# 主动初始化 jieba（惰性加载，首次 lcut 可能耗时）
jieba.initialize()

# ========== 情感词典 ==========

# 正向词（多字词优先，子串匹配，权重1.0）
POS_WORDS = [
    "太开心了", "哈哈哈", "开心", "哈哈", "好棒", "爱你", "感动", "太好了", "真不错",
    "加油", "想你了", "不错", "好开心", "太棒了", "太感动",
]
# 短正向词（单/双字，子串匹配，权重0.35）
POS_SHORT = ["好", "太", "想", "终于", "棒", "赞"]

# 负向词（多字词优先，子串匹配，权重1.0）
NEG_WORDS = [
    "有点难过", "烦死了", "气死我了", "不想上班", "心情不好", "压力好大",
    "难过", "烦", "失眠", "失望", "哭", "压力", "崩溃", "无语", "心态崩了",
    "太坑了", "醉了", "服了", "受不了", "搞不定", "算了吧",
]
# 短负向词（单/双字，子串匹配，权重0.35）
NEG_SHORT = ["累", "唉", "气", "坑", "没意思", "不好", "醉了", "烦"]

# ========== 短语级情感词典（jieba 分词精确匹配） ==========
# key: 情感短语, value: (情感值, 情感强度)
# 情感值: +1.0~-1.0, 情感强度: 1-3（1=轻微, 2=中等, 3=强烈）
# 这些短语必须通过 jieba 分词精确匹配，避免子串误判

# 强烈负向短语（-1.0 ~ -0.8）
PHRASE_NEG_STRONG = {
    "心态炸了": -1.0, "心态崩了": -1.0, "心态崩": -1.0,
    "破防了": -1.0, "整破防了": -1.0,
    "绷不住了": -1.0, "蚌埠住了": -1.0,
    "笑不活了": -0.9, "笑拉了": -0.9, "笑yue了": -0.9, "笑到头掉": -0.9,
    "一脸懵逼": -0.8, "一脸懵": -0.8,
    "泪崩了": -0.9, "泪崩": -0.9, "泪目了": -0.8,
    "天塌了": -0.9, "天塌": -0.9,
    "离大谱": -0.8, "太离谱了": -0.8,
}

# 中等负向短语（-0.7 ~ -0.4）
PHRASE_NEG_MEDIUM = {
    "无语了": -0.6, "无语": -0.5,
    "心态炸": -0.7, "心态崩": -0.7,
    "裂开了": -0.6, "裂开": -0.6,
    "绝了": -0.5, "真的绝": -0.5,
    "麻了": -0.5, "人麻了": -0.5, "看麻了": -0.5, "听麻了": -0.5, "整不会了": -0.5,
    "整不会": -0.5,
    "不是吧": -0.4, "天呐": -0.4,
    "就这": -0.4, "就这？": -0.4,
    "下头": -0.5, "真下头": -0.5, "下头男": -0.5,
    "emo了": -0.6, "emo": -0.5, "深夜emo": -0.6,
    "好家伙": -0.4,
    "栓Q": -0.4, "我真的会谢": -0.5,
    "一整个无语住": -0.6,
    "离大谱": -0.6,
    "离谱": -0.5,
}

# 强烈正向短语（+0.8 ~ +1.0）
PHRASE_POS_STRONG = {
    "绝绝子": 0.8, "针不戳": 0.8, "真不戳": 0.8,
    "yyds": 0.9, "永远的神": 0.9,
    "上头": 0.7, "狠狠上头": 0.8, "有点上头": 0.6,
}

# 合并所有短语词典
PHRASE_DICT = {}
PHRASE_DICT.update(PHRASE_NEG_STRONG)
PHRASE_DICT.update(PHRASE_NEG_MEDIUM)
PHRASE_DICT.update(PHRASE_POS_STRONG)

# 将短语级情感词注册到 jieba 词典，确保被分词为一个整体
for pw in PHRASE_DICT:
    jieba.add_word(pw, freq=100, tag="x")

# 表情符号 -> 情感值
EMOJI_MAP = {
    "😊": 0.5, "😄": 0.6, "😂": 0.5, "🤣": 0.5, "😍": 0.7, "🥰": 0.6, "❤️": 0.4, "💕": 0.4,
    "😢": -0.5, "😭": -0.6, "😡": -0.6, "😤": -0.4, "😞": -0.4, "😔": -0.3, "💔": -0.4,
    "😅": 0.2, "🤔": 0.0, "🙄": -0.2,
}

# 问候语模式（单字"好"在这些模式中不匹配）
GREETING_PATTERNS = {"早上好", "上午好", "中午好", "下午好", "晚上好", "大家好", "大家好呀", "你好", "你好呀"}

# 确认语模式（单字"好"在这些模式中不匹配）
CONFIRM_PATTERNS = {"好的", "好吧", "好了", "好的吧", "好的好的", "好啊", "好啦", "好嘞", "好滴"}

# 否定词（前 3 字内出现则抵消该情感词）
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
            if word == "好" and is_cjk_after and after in ("累", "烦", "气", "难", "哭", "惨", "痛", "晕"):
                idx = text.find(word, idx + 1)
                continue
            # 特别规则：单字"好"在问候语/确认语中不匹配
            if word == "好":
                # 检查是否在问候语模式中
                for pat in GREETING_PATTERNS:
                    if pat in text:
                        return False
                # 检查是否在确认语模式中
                for pat in CONFIRM_PATTERNS:
                    if pat in text:
                        return False
            return True
        idx = text.find(word, idx + 1)
    return False


def _has_negator_before(text: str, match_pos: int) -> bool:
    """检查 match_pos 前 3 字内是否有否定词。"""
    start = max(0, match_pos - 3)
    prefix = text[start:match_pos]
    return any(n in prefix for n in NEGATORS)


def rule_sentiment(text: str) -> float:
    """词典规则打分（V4 jieba 分词增强版）：-1 ~ 1。
    
    双引擎架构：
    1) jieba 分词精确匹配（短语级情感词典，优先）
    2) 子串匹配（标准情感词，兜底）
    3) 表情符号情感
    4) 情感强度校准（长度调节 + 密度调节 + 长句分段）
    """
    if not text or not text.strip():
        return 0.0

    pos_score = 0.0
    neg_score = 0.0

    # ---- 引擎1：jieba 分词精确匹配（短语级） ----
    words = jieba.lcut(text)
    phrase_matched = {}  # phrase -> score
    for w in words:
        if w in PHRASE_DICT:
            phrase_matched[w] = PHRASE_DICT[w]

    # 补充：子串匹配也检查短语词典（防止 jieba 未识别的新词）
    # 对未匹配到的短语做子串兜底
    for phrase, score in PHRASE_DICT.items():
        if phrase in text and phrase not in phrase_matched:
            # 检查是否已被其他短语覆盖
            already_covered = False
            for existing in phrase_matched:
                if existing in phrase or phrase in existing:
                    already_covered = True
                    break
            if not already_covered:
                phrase_matched[phrase] = score
    # 短语贡献
    for phrase, score in phrase_matched.items():
        if score > 0:
            pos_score += abs(score)
        else:
            neg_score += abs(score)

    # ---- 引擎2：子串匹配（标准词，仅当短语引擎未匹配到该部分时） ----
    # 已匹配的短语所覆盖的字符范围，避免双重计数
    covered = set()
    for phrase in phrase_matched:
        idx = text.find(phrase)
        while idx != -1:
            for i in range(idx, idx + len(phrase)):
                covered.add(i)
            idx = text.find(phrase, idx + 1)

    def _is_covered(start: int, end: int) -> bool:
        """检查字符区间 [start, end) 是否已被短语引擎覆盖。"""
        for i in range(start, end):
            if i in covered:
                return True
        return False

    def _find_matches(word_list, is_short: bool):
        """子串匹配（仅在未被短语引擎覆盖的位置）。"""
        nonlocal pos_score, neg_score
        for w in word_list:
            if not _word_boundary_match(text, w):
                continue
            idx = 0
            while True:
                idx = text.find(w, idx)
                if idx == -1:
                    break
                # 如果该词已被短语引擎覆盖，跳过
                if _is_covered(idx, idx + len(w)):
                    idx += 1
                    continue
                # 检查是否被否定词修饰
                has_neg = _has_negator_before(text, idx)
                if has_neg:
                    idx += len(w)
                    continue
                wgt = 0.35 if is_short else 1.0
                if word_list is POS_WORDS or word_list is POS_SHORT:
                    pos_score += wgt
                else:
                    neg_score += wgt
                idx += len(w)

    # 子串匹配
    _find_matches(POS_WORDS, is_short=False)
    _find_matches(NEG_WORDS, is_short=False)
    _find_matches(POS_SHORT, is_short=True)
    _find_matches(NEG_SHORT, is_short=True)

    # ---- 引擎3：表情符号情感 ----
    emoji_score = 0.0
    for ch in text:
        if ch in EMOJI_MAP:
            emoji_score += EMOJI_MAP[ch]

    total = pos_score + neg_score + abs(emoji_score)

    # 只有表情符号/只有短语的情况
    if total == 0:
        if emoji_score != 0:
            raw = max(-0.6, min(0.6, emoji_score))
        else:
            return round(random.uniform(-0.15, 0.15), 3)
        return round(raw, 3)

    # 基础分数
    raw = (pos_score - neg_score + emoji_score) / total

    # 表情符号弱化
    if pos_score + neg_score < 0.5 and abs(emoji_score) > 0:
        raw = max(-0.6, min(0.6, raw))

    # --- 情感强度校准（继承 V3） ---
    if total <= 1.5:
        raw = raw * 0.65
    elif total <= 2.5:
        raw = raw * 0.85
    if len(text) <= 4 and total > 0:
        raw = raw * 1.3
    if len(text) > 10:
        raw = raw * 0.85
    if len(text) > 30:
        words_only = text.replace(" ", "").replace("　", "")
        if total > 0 and total / len(words_only) > 0.15:
            raw = raw * 0.95
        else:
            raw = raw * 0.75
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
