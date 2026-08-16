"""导入通道（Week 2）：格式探测 → 字段映射 → 清洗 → UPSERT 入库（PRD §4.1/§4.2/§4.3）

支持格式：
- wechatmsg_csv : WeChatMsg(留痕) 导出 CSV（utf-8-sig，列名宽松别名映射）
- jsonl         : chatlog 旧版导出的 JSON Lines
- generic_csv   : 通用 CSV（同别名映射）
- contacts_csv  : 联系人/群文件（wxid/nickname/remark/type），与消息文件配对导入（R17）
- members_csv   : 群成员文件（group_wxid/member_wxid/member_name）

统一行模型（对应 §4.3 messages 表）：
  msg_id, talker, time_utc(UTC秒), content, content_type, emotion_score, year_month

幂等：按 msg_id UPSERT（无 id 列则用 sha1(talker|time|content) 派生），重复导入不产生脏数据（R8）。
"""

from __future__ import annotations

import csv
import hashlib
import json
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from backend.app.demo_data import DB_PATH, rule_sentiment

# ---------------- 字段别名（宽松映射，兼容 WeChatMsg / chatlog / 通用导出） ----------------
ALIAS_TIME = ["createtime", "time", "ts", "date", "timestamp", "strtime", "create_time", "datetime"]
ALIAS_TALKER = ["talker", "talkerid", "from", "fromuser", "wxid", "sender", "username", "user"]
ALIAS_CONTENT = ["content", "strcontent", "msg", "message", "text"]
ALIAS_TYPE = ["type", "msgtype", "ctype", "message_type"]
ALIAS_ID = ["msg_id", "msgid", "id", "message_id", "localid"]

# 联系人文件列别名
ALIAS_CONTACT_WXID = ["wxid", "username", "user", "id"]
ALIAS_CONTACT_NICK = ["nickname", "nick", "name"]
ALIAS_CONTACT_REMARK = ["remark", "remarkname", "备注", "备注名"]
ALIAS_CONTACT_TYPE = ["type", "kind"]

# WeChatMsg msgType 数字 → 统一 content_type（PRD §4.3）
MSG_TYPE_MAP = {
    "1": "text", "3": "image", "34": "voice", "43": "video",
    "47": "sticker", "49": "file", "10000": "system",
}

TYPES_OK = {"text", "image", "voice", "video", "file", "system", "sticker"}

# ---------------- 脱敏（R1：手机号/身份证/邮箱；姓名由 contacts 展示层替换） ----------------
RE_PHONE = re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)")
RE_IDCARD = re.compile(r"(?<!\d)\d{17}[\dXx](?!\d)")
RE_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+")


def desensitize(text: str) -> str:
    text = RE_PHONE.sub(lambda m: m.group(0)[:3] + "****" + m.group(0)[-4:], text)
    text = RE_IDCARD.sub(lambda m: m.group(0)[:6] + "********" + m.group(0)[-4:], text)
    text = RE_EMAIL.sub(lambda m: m.group(0)[0] + "***@" + m.group(0).split("@")[1], text)
    return text


def parse_time_utc(v) -> int | None:
    """解析为 UTC 秒。支持秒/毫秒时间戳、'2024-01-01 12:00:00' 等常见格式。
    微信本地时间为 Asia/Shanghai（无夏令时，固定 UTC+8）。"""
    if v is None or str(v).strip() == "":
        return None
    s = str(v).strip()
    if s.replace(".", "", 1).isdigit():
        f = float(s)
        return int(f / 1000) if f > 1e12 else int(f)
    s = s.replace("T", " ").split(".")[0].strip()
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y/%m/%d %H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            dt = datetime.strptime(s, fmt)
            return int(dt.replace(tzinfo=timezone.utc).timestamp()) - 8 * 3600
        except ValueError:
            continue
    return None


def norm_type(v) -> str:
    if v is None or str(v).strip() == "":
        return "text"
    s = str(v).strip().lower()
    if s.isdigit():
        return MSG_TYPE_MAP.get(s, "text")
    return s if s in TYPES_OK else "text"


def detect_format(path: Path) -> str:
    """按扩展名 + 内容嗅探格式。"""
    suffix = path.suffix.lower()
    if suffix == ".jsonl":
        return "jsonl"
    if suffix == ".json":
        # 数组或逐行 JSON
        return "jsonl"
    if suffix == ".csv":
        return "wechatmsg_csv"
    if suffix == ".html":
        raise ValueError("HTML 导出暂不支持，请用 WeChatMsg 导出 CSV/JSON，或 chatlog JSONL")
    raise ValueError(f"不支持的导入格式: {suffix or '(无扩展名)'}")


def _pick(row: dict, aliases: list[str]):
    """按别名优先级取第一个非空值。"""
    for a in aliases:
        v = row.get(a)
        if v is not None and str(v).strip() != "":
            return v
    return None


def _read_csv_rows(path: Path):
    """读取 CSV（utf-8-sig），键统一小写。"""
    with open(path, "r", encoding="utf-8-sig", errors="replace", newline="") as f:
        reader = csv.DictReader(f)
        if reader.fieldnames is None:
            raise ValueError(f"CSV 无表头: {path}")
        for raw in reader:
            yield {str(k).strip().lower(): v for k, v in raw.items() if k is not None}


def _read_jsonl_rows(path: Path):
    with open(path, "r", encoding="utf-8-sig", errors="replace") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(obj, list):  # 容忍整行是数组
                for o in obj:
                    if isinstance(o, dict):
                        yield {str(k).lower(): v for k, v in o.items()}
                continue
            if isinstance(obj, dict):
                yield {str(k).lower(): v for k, v in obj.items()}


def clean_row(raw: dict) -> dict | None:
    """清洗单行：字段映射 → 时间标准化 → 脱敏 → 类型归一 → 情感打分。

    返回统一行（含 msg_id），或 None（缺关键字段/空内容）。
    """
    talker = _pick(raw, ALIAS_TALKER)
    content_raw = _pick(raw, ALIAS_CONTENT)
    ts = parse_time_utc(_pick(raw, ALIAS_TIME))
    msg_type = _pick(raw, ALIAS_TYPE)
    is_media = msg_type in ("3", "image", "34", "voice", "43", "video")
    if not talker or ts is None:
        return None
    content = desensitize(str(content_raw or "").strip())
    # 媒体消息（图片/语音/视频）允许空文本；非媒体消息空内容则丢弃（如系统消息）
    if not is_media and (content_raw is None or not content):
        return None

    msg_id = _pick(raw, ALIAS_ID)
    if msg_id is None:
        digest = hashlib.sha1(f"{talker}|{ts}|{content}".encode("utf-8")).hexdigest()[:16]
        msg_id = f"imp_{digest}"

    from datetime import datetime as _dt, timezone as _tz

    return {
        "msg_id": str(msg_id),
        "talker": str(talker),
        "time_utc": int(ts),
        "content": content,
        "content_type": norm_type(_pick(raw, ALIAS_TYPE)),
        "emotion_score": rule_sentiment(content),
        "year_month": _dt.fromtimestamp(ts, _tz.utc).strftime("%Y-%m"),
    }


def read_contacts(path: Path) -> list[dict]:
    """读取联系人/群文件 → contacts 表行。"""
    out = []
    for raw in _read_csv_rows(path):
        wxid = _pick(raw, ALIAS_CONTACT_WXID)
        if not wxid:
            continue
        out.append(
            {
                "wxid": str(wxid),
                "nickname": str(_pick(raw, ALIAS_CONTACT_NICK) or ""),
                "remark": str(_pick(raw, ALIAS_CONTACT_REMARK) or ""),
                "type": str(_pick(raw, ALIAS_CONTACT_TYPE) or ("group" if "chatroom" in str(wxid) else "friend")),
            }
        )
    return out


def read_members(path: Path) -> list[tuple]:
    """读取群成员文件 → group_members 表行。"""
    out = []
    for raw in _read_csv_rows(path):
        g, m = _pick(raw, ["group_wxid", "group"]), _pick(raw, ["member_wxid", "member", "wxid"])
        if g and m:
            out.append((str(g), str(m), str(_pick(raw, ["member_name", "name", "nickname"]) or "")))
    return out


# ---------------- 入库（UPSERT 幂等，R8） ----------------
_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY,
    msg_id TEXT UNIQUE,
    talker TEXT, time_utc INTEGER, content TEXT, content_type TEXT,
    emotion_score REAL, year_month TEXT
);
CREATE INDEX IF NOT EXISTS idx_time ON messages(time_utc);
CREATE INDEX IF NOT EXISTS idx_talker ON messages(talker);
CREATE INDEX IF NOT EXISTS idx_year_month ON messages(year_month);
CREATE TABLE IF NOT EXISTS contacts (
    wxid TEXT PRIMARY KEY, nickname TEXT, remark TEXT, type TEXT, avatar TEXT
);
CREATE TABLE IF NOT EXISTS group_members (
    group_wxid TEXT, member_wxid TEXT, member_name TEXT,
    PRIMARY KEY (group_wxid, member_wxid)
);
CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT);
"""


def _init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(_SCHEMA_SQL)


def run_import(
    task_id: str,
    src_path: str | Path,
    contact_path: str | Path | None = None,
    members_path: str | Path | None = None,
    db_path: str | Path | None = None,
    progress_cb=None,
) -> int:
    """执行完整导入流水线。返回导入的消息条数。

    progress_cb(**kw) 接收: status/phase/current/total/message。
    """
    db_path = Path(db_path) if db_path else DB_PATH
    src = Path(src_path)

    def cb(**kw):
        if progress_cb:
            progress_cb(**kw)

    cb(status="running", phase="detect", message="正在解析导入文件", current=0, total=0)
    kind = detect_format(src)

    # 1) 读取原始行
    if kind == "wechatmsg_csv":
        raw_iter = _read_csv_rows(src)
    else:
        raw_iter = _read_jsonl_rows(src)
    raws = list(raw_iter)
    cb(phase="clean", message=f"正在清洗数据（{len(raws)} 条原始记录）", current=0, total=len(raws))

    # 联系人自动构建：消息文件自带 Remark/NickName 列时，无需单独联系人文件（R17/MemoTrace 兼容）
    auto_contacts: dict[str, dict] = {}
    if contact_path is None:
        for raw in raws:
            tk = _pick(raw, ALIAS_TALKER)
            if not tk:
                continue
            nick = _pick(raw, ALIAS_CONTACT_NICK) or ""
            remark = _pick(raw, ALIAS_CONTACT_REMARK) or ""
            if nick or remark:
                auto_contacts[str(tk)] = {
                    "wxid": str(tk), "nickname": str(nick), "remark": str(remark),
                    "type": "group" if "chatroom" in str(tk) else "friend",
                }
        if auto_contacts:
            print(f"  [importer] 从消息文件自动构建 {len(auto_contacts)} 个联系人")

    # 2) 清洗
    cleaned = []
    for i, raw in enumerate(raws):
        row = clean_row(raw)
        if row:
            cleaned.append(row)
        if i % 5000 == 0:
            cb(current=i)
    cb(phase="store", message=f"清洗完成 {len(cleaned)} 条，正在写入数据库", current=0, total=len(cleaned))

    # 3) 入库（UPSERT，按 msg_id 幂等）
    conn = sqlite3.connect(db_path)
    try:
        _init_db(conn)
        before = conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
        conn.executemany(
            "INSERT OR REPLACE INTO messages "
            "(msg_id, talker, time_utc, content, content_type, emotion_score, year_month) "
            "VALUES (?,?,?,?,?,?,?)",
            [(r["msg_id"], r["talker"], r["time_utc"], r["content"], r["content_type"], r["emotion_score"], r["year_month"]) for r in cleaned],
        )
        # 联系人导入（R17）：显式联系人文件优先，否则用消息文件自带的 Remark/NickName 自动构建
        if contact_path or auto_contacts:
            contacts_rows = list(read_contacts(Path(contact_path))) if contact_path else []
            merged = {c["wxid"]: c for c in contacts_rows}
            for wxid, c in auto_contacts.items():
                merged.setdefault(wxid, c)
            for c in merged.values():
                conn.execute(
                    "INSERT OR REPLACE INTO contacts (wxid, nickname, remark, type) VALUES (?,?,?,?)",
                    (c["wxid"], c["nickname"], c["remark"], c["type"]),
                )
        if members_path:
            conn.executemany(
                "INSERT OR REPLACE INTO group_members VALUES (?,?,?)", read_members(Path(members_path))
            )
        conn.executemany(
            "INSERT OR REPLACE INTO metadata (key, value) VALUES (?,?)",
            [
                ("last_import_task", task_id),
                ("last_import_source", str(src)),
                ("last_import_time", datetime.now(timezone.utc).isoformat()),
                ("last_import_count", str(len(cleaned))),
            ],
        )
        conn.commit()
        after = conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
    finally:
        conn.close()

    added = after - before
    cb(
        status="done", phase="done",
        message=f"导入完成：处理 {len(cleaned)} 条，净新增 {added} 条",
        current=added, total=added,
    )
    return added
