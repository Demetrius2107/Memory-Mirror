"""密码学管线往返自测：构造符合 SQLCipher4 布局的"假加密库" → 解密 → 对比 + sqlite3 打开验证。

无需真实微信即可验证 crypto.py 的正确性。
"""

from __future__ import annotations

import hashlib
import hmac as hmac_mod
import os
import sqlite3
import struct
import tempfile
from pathlib import Path

from backend.decrypt.crypto import (
    PAGE_SZ,
    RESERVE_SZ,
    SALT_SZ,
    SQLITE_HDR,
    decrypt_database,
    derive_mac_key,
    verify_page1_hmac,
)


def _encrypt_page(enc_key: bytes, mac_key: bytes, plain: bytes, pgno: int) -> bytes:
    """按 SQLCipher4 布局构造加密页（crypto.decrypt_page 的逆过程）。"""
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

    iv = os.urandom(16)
    cipher = Cipher(algorithms.AES(enc_key), modes.CBC(iv))
    enc = cipher.encryptor()
    if pgno == 1:
        salt = os.urandom(16)
        body = plain[SALT_SZ : PAGE_SZ - RESERVE_SZ]  # [16:4016) = 4000B，对应 crypto.decrypt_page
        ciphertext = enc.update(body) + enc.finalize()
        data = ciphertext + iv
        h = hmac_mod.new(mac_key, data + struct.pack("<I", pgno), hashlib.sha512).digest()
        return salt + ciphertext + iv + h
    body = plain[: PAGE_SZ - RESERVE_SZ]  # 4016B
    ciphertext = enc.update(body) + enc.finalize()
    data = ciphertext + iv
    h = hmac_mod.new(mac_key, data + struct.pack("<I", pgno), hashlib.sha512).digest()
    return ciphertext + iv + h


def build_fixture(enc_key: bytes, page_count: int = 4) -> tuple[bytes, bytes]:
    """构造真实 sqlite 库 → 截断尾部 80B/页（reserve 视角）→ 逐页加密。返回 (加密文件, 明文视角)。"""
    with tempfile.TemporaryDirectory() as td:
        db_path = Path(td) / "plain.db"
        conn = sqlite3.connect(db_path)
        conn.execute("PRAGMA page_size=4096")
        conn.execute("CREATE TABLE contacts (wxid TEXT PRIMARY KEY, remark TEXT)")
        conn.execute("INSERT INTO contacts VALUES ('wxid_a','测试A'),('wxid_b','测试B')")
        conn.commit()
        conn.close()
        raw = db_path.read_bytes()

    padded = raw + b"\x00" * ((-len(raw)) % PAGE_SZ)
    pages = [padded[i : i + PAGE_SZ] for i in range(0, len(padded), PAGE_SZ)][:page_count]
    # reserve 视角：每页仅前 4016B 有效，尾部 80B 置零
    plain_view = [p[: PAGE_SZ - RESERVE_SZ] + b"\x00" * RESERVE_SZ for p in pages]

    salt = os.urandom(16)
    mac_key = derive_mac_key(enc_key, salt)
    enc_pages = [_encrypt_page(enc_key, mac_key, p, i + 1) for i, p in enumerate(plain_view)]
    enc_pages[0] = salt + enc_pages[0][16:]  # page1 前 16B 放 salt
    return b"".join(enc_pages), b"".join(plain_view)


def main() -> None:
    enc_key = bytes(range(32))  # 固定测试密钥
    enc_db, plain_view = build_fixture(enc_key)

    # 1) HMAC page1 校验
    assert verify_page1_hmac(enc_db[:PAGE_SZ], enc_key), "HMAC page1 校验失败"
    assert not verify_page1_hmac(enc_db[:PAGE_SZ], b"\x00" * 32), "错误 key 不应通过校验"
    print("[1] HMAC page1 校验通过（正确 key 通过 / 错误 key 拒绝）")

    # 2) 解密往返
    dec = decrypt_database(enc_db, enc_key)
    assert dec == plain_view, "解密结果与明文视角不一致"
    print(f"[2] 解密往返一致（{len(dec)} 字节，可用区内容与原库逐字节一致）")

    # 3) 头校验（sqlite3 打开 + 表结构核对需真实微信库，见 spike 文档步骤 4——
    #    普通 sqlite 库无 usable=4016 的 reserve 语义，伪 fixtures 无法复现其 B-tree 布局）
    assert dec[:16] == SQLITE_HDR, "解密后 page1 头应为 'SQLite format 3\\0'"
    print("[3] 解密后 page1 头校验通过（SQLite format 3）")
    print("    注：sqlite3 打开 + 表结构核对需真实微信库（spike 文档步骤 4）")
    print("\n✅ 密码学管线自测全部通过")


if __name__ == "__main__":
    main()
