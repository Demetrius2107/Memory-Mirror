"""用 MemoTrace decrypt_v4 的正确参数重新校验已提取候选（passphrase → PBKDF2 256000 → key）。

此前校验用错派生路径（把提取值当最终密钥），现按 wx4 真实协议：
  key     = PBKDF2-HMAC-SHA512(passphrase, salt, 256000, 32)
  mac_key = PBKDF2-HMAC-SHA512(key, salt^0x3a, 2, 32)
  HMAC-SHA512(page[16:4032] + pgno) == page[4032:4096]
"""

import hashlib
import hmac
import json
import struct
import sys
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

DB = sys.argv[1] if len(sys.argv) > 1 else (
    "E:/TencentWechatDataStorage/xwechat_files/wxid_v7r5xpy33z6m22_8f2f/db_storage/message/message_0.db"
)
CAND_FILE = sys.argv[2] if len(sys.argv) > 2 else "data/key_candidates.json"
PAGE = Path(DB).read_bytes()[:4096]
SALT = PAGE[:16]


def check(enc_hex: str):
    passphrase = bytes.fromhex(enc_hex)
    key = hashlib.pbkdf2_hmac("sha512", passphrase, SALT, 256000, dklen=32)
    mac_key = hashlib.pbkdf2_hmac("sha512", key, bytes(b ^ 0x3A for b in SALT), 2, dklen=32)
    data = PAGE[16:4032] + struct.pack("<I", 1)
    expect = hmac.new(mac_key, data, hashlib.sha512).digest()
    return enc_hex if hmac.compare_digest(expect, PAGE[4032:4096]) else None


if __name__ == "__main__":
    cands = json.loads(Path(CAND_FILE).read_text(encoding="utf-8"))
    print(f"候选文件: {CAND_FILE} | 候选数: {len(cands)} | 目标库: {Path(DB).name} | salt: {SALT.hex()}")
    with ProcessPoolExecutor(max_workers=16) as ex:
        results = list(ex.map(check, [c["enc"] for c in cands]))
    hits = [r for r in results if r]
    print("⭐⭐ v4 派生校验命中:", hits if hits else "无（候选不含 passphrase）")
