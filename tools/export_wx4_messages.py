"""wx4 解密库 → 导入 CSV 导出器（wx_key 路线配套，R18 实验性，本机自用）。

用法:
  python tools/export_wx4_messages.py --contact <解密contact.db> --message <解密message_0.db> -o out.csv

wx4 结构（参考 MemoTrace db_v4 源码）：
- contact.db 的 `contact` 表：username(=wxid)/remark/nick_name
- message_0.db：每联系人一张 `Msg_<md5(wxid)>` 表 + Name2Id 关联表，
  列含 local_id/sort_seq/create_time(unix秒)/local_type/message_content

输出与 importer 别名兼容的 CSV（msg_id,talker,create_time,content,type），
可直接 POST /api/import。
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import sqlite3


def md5_hex(s: str) -> str:
    return hashlib.md5(s.encode("utf-8")).hexdigest()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--contact", required=True, help="解密后的 contact.db")
    ap.add_argument("--message", required=True, help="解密后的 message_0.db")
    ap.add_argument("-o", "--out", required=True, help="输出 CSV 路径")
    ap.add_argument("--min-rows", type=int, default=0, help="跳过行数少于该值的会话")
    args = ap.parse_args()

    # 1) 联系人 → {md5(username): (wxid, remark, nick)}
    cc = sqlite3.connect(args.contact)
    try:
        contacts: dict[str, tuple[str, str, str]] = {}
        for username, remark, nick in cc.execute(
            "SELECT username, remark, nick_name FROM contact WHERE username IS NOT NULL AND username != ''"
        ):
            contacts[md5_hex(username)] = (username, remark or "", nick or "")
    finally:
        cc.close()
    print(f"联系人映射: {len(contacts)} 个")

    # 2) 消息库逐表导出（Msg_ 前缀 = 每联系人一表）
    mc = sqlite3.connect(args.message)
    try:
        tables = [
            r[0]
            for r in mc.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name GLOB 'Msg_*'"
            )
        ]
        print(f"消息表: {len(tables)} 个（Msg_<md5(wxid)>）")

        rows_written = 0
        with open(args.out, "w", encoding="utf-8-sig", newline="") as f:
            w = csv.writer(f)
            w.writerow(["msg_id", "talker", "create_time", "content", "type"])
            for t in tables:
                suffix = t[4:]  # 去掉 "Msg_"
                talker = contacts.get(suffix, (suffix, "", ""))[0]  # 表名后缀=md5(wxid)
                rows = mc.execute(
                    f"SELECT local_id, create_time, local_type, message_content FROM \"{t}\" ORDER BY sort_seq"
                ).fetchall()
                if len(rows) < args.min_rows:
                    continue
                for local_id, create_time, local_type, content in rows:
                    w.writerow(
                        [
                            f"{suffix}_{local_id}",
                            talker,
                            create_time,
                            content or "",
                            local_type if local_type is not None else 1,
                        ]
                    )
                    rows_written += 1
    finally:
        mc.close()
    print(f"导出完成: {rows_written} 行 -> {args.out}")


if __name__ == "__main__":
    main()
