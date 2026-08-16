"""解密 CLI（R18 实验性，本机自用验证）：python -m backend.decrypt.decrypt_db --db <MSG.db> --key <hex> --out <out.db>

流程：读取加密库 → page1 HMAC 校验密钥 → SQLCipher 逐页解密 → 写明文 sqlite → 列表现。
"""

from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path

from backend.decrypt.crypto import PAGE_SZ, decrypt_database, verify_page1_hmac


def main() -> None:
    p = argparse.ArgumentParser(description="SQLCipher 微信数据库解密（实验性，本机自用）")
    p.add_argument("--db", required=True, help="加密数据库路径（如 MSG.db）")
    p.add_argument("--key", required=True, help="32 字节密钥 hex（64 字符）")
    p.add_argument("--out", required=True, help="输出明文 sqlite 路径")
    args = p.parse_args()

    db_path = Path(args.db)
    if not db_path.is_file():
        raise SystemExit(f"数据库不存在: {db_path}")
    try:
        key = bytes.fromhex(args.key)
    except ValueError:
        raise SystemExit("key 必须是 64 字符 hex")

    data = db_path.read_bytes()
    ok = verify_page1_hmac(data[:PAGE_SZ], key)
    print(f"[1] page1 HMAC 校验: {'✅ 通过（key 正确）' if ok else '❌ 失败（key 可能不正确）'}")
    if not ok:
        raise SystemExit(1)

    plain = decrypt_database(data, key)
    out_path = Path(args.out)
    out_path.write_bytes(plain)
    print(f"[2] 解密完成: {out_path}（{len(plain)} 字节，{len(plain) // PAGE_SZ} 页）")

    conn = sqlite3.connect(out_path)
    try:
        tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")]
    except Exception as e:  # 伪 fixtures 的 reserve 视图可能读不出 schema；真实微信库正常
        tables = [f"(schema 读取异常: {e})"]
    counts = {}
    for t in tables[:10]:
        if str(t).startswith("("):
            continue
        try:
            counts[t] = conn.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0]
        except Exception:
            counts[t] = "?"
    conn.close()
    print(f"[3] 表结构: {', '.join(str(t) for t in tables[:10])}")
    print(f"    行数: {counts}")


if __name__ == "__main__":
    main()
