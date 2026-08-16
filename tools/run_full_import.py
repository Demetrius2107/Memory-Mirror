"""wx_key 路线一键闭环：passphrase → 全量解密 → 导出 → 导入 → 索引。

用法:
  python tools/run_full_import.py <passphrase_hex> <db_storage_dir>

流程（对应 wx_key 路线拿到 64 位 hex passphrase 后）：
  1. batch_decrypt      ：解密 db_storage 下全部库到 data/decrypted/
  2. export_wx4_messages：解密后的 contact.db + message_*.db → 导入 CSV
  3. run_import         ：CSV → SQLite（自动建联系人/脱敏）
  4. build_index        ：向量索引 → 可检索
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

# 脚本位于 tools/ 下，需把项目根加入 sys.path 才能 import backend.app.*
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.app.importer import run_import
from backend.app.rag_index import build_index

DEC_OUT = "data/decrypted"
CSV_DIR = "data/wx4_csv"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("passphrase_hex", help="wx4 数据库 passphrase（64 位 hex，wx_key 获取）")
    ap.add_argument("db_dir", help="db_storage 目录（E:/.../xwechat_files/<wxid>/db_storage）")
    ap.add_argument("--dec-out", default=DEC_OUT, help="解密输出目录")
    args = ap.parse_args()

    # 1) 批量解密
    r = subprocess.run(
        [sys.executable, "tools/batch_decrypt.py", args.passphrase_hex, args.db_dir, args.dec_out],
        capture_output=True, text=True,
    )
    print(r.stdout)
    if r.returncode != 0:
        print("❌ 解密失败:", r.stderr[-500:])
        sys.exit(1)
    dec = Path(args.dec_out)

    # 2) 定位解密后的 contact.db 与 message 库
    contact_dbs = sorted(dec.rglob("contact.db"))
    if not contact_dbs:
        print("❌ 未找到解密后的 contact.db")
        sys.exit(1)
    contact_db = str(contact_dbs[0])
    msg_dbs = sorted(dec.rglob("message_*.db"))
    if not msg_dbs:
        print("❌ 未找到解密后的 message_*.db")
        sys.exit(1)
    print(f"联系人库: {contact_db}\n消息库: {len(msg_dbs)} 个")

    # 3) 逐个导出 + 导入（importer 按 msg_id UPSERT 幂等）
    csv_dir = Path(CSV_DIR)
    csv_dir.mkdir(parents=True, exist_ok=True)
    total = 0
    for i, msg_db in enumerate(msg_dbs):
        csv_path = csv_dir / f"wx4_messages_{i}.csv"
        r = subprocess.run(
            [
                sys.executable, "tools/export_wx4_messages.py",
                "--contact", contact_db, "--message", str(msg_db), "-o", str(csv_path),
            ],
            capture_output=True, text=True,
        )
        print(r.stdout.strip())
        if r.returncode != 0:
            print("⚠️ 导出失败:", msg_db.name, r.stderr[-300:])
            continue
        n = run_import(f"wx4_{i}", csv_path)
        total += n
    print(f"导入完成: 共 {total} 条（净新增）")

    # 4) 建索引
    n_idx = build_index()
    print(f"✅ 索引构建完成: {n_idx} 片段 —— 现在可以检索/AI 问答了")


if __name__ == "__main__":
    main()
