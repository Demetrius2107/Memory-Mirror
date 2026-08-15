"""SQLCipher 4 纯 Python 解密原语（R18 实验性；wx4 方案，wx3 较新版本同构）

布局（页 4096，reserve 80 = IV 16 + HMAC-SHA512 64）：
- page1 : [0:16) salt | [16:4016) AES-256-CBC 密文 | [4016:4032) IV | [4032:4096) HMAC
- 其他页: [0:4016) AES-256-CBC 密文 | [4016:4032) IV | [4032:4096) HMAC
- mac_key = PBKDF2-HMAC-SHA512(enc_key, salt XOR 0x3a, iter=2, dklen=32)
- HMAC 数据 = 页文件 [16:4032)（页1）/ [0:4032)（其他页） + 页号(小端 4B)

enc_key 直接取自进程内存（WCDB 已缓存派生后的 raw key，无需 256k 次 KDF）。
参照：ylytdeng/wechat-decrypt + zhimian/decrypt-PC-WeChat-db。
"""

from __future__ import annotations

import hashlib
import hmac as hmac_mod
import struct

PAGE_SZ = 4096
RESERVE_SZ = 80
SALT_SZ = 16
IV_SZ = 16
HMAC_SZ = 64
KEY_SZ = 32
SQLITE_HDR = b"SQLite format 3\x00"


def derive_mac_key(enc_key: bytes, salt: bytes) -> bytes:
    """由 enc_key 派生 HMAC 密钥（salt 逐字节异或 0x3a）。"""
    mac_salt = bytes(b ^ 0x3A for b in salt)
    return hashlib.pbkdf2_hmac("sha512", enc_key, mac_salt, 2, dklen=KEY_SZ)


def _aes(enc_key: bytes):
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

    return Cipher(algorithms.AES(enc_key), modes.CBC(b"\x00" * IV_SZ))  # 每页自换 IV


def verify_page1_hmac(page1: bytes, enc_key: bytes) -> bool:
    """用 HMAC 校验 page1，确认候选 enc_key 是否正确。"""
    salt = page1[:SALT_SZ]
    mac_key = derive_mac_key(enc_key, salt)
    data = page1[SALT_SZ : PAGE_SZ - RESERVE_SZ + IV_SZ] + struct.pack("<I", 1)
    stored = page1[PAGE_SZ - HMAC_SZ : PAGE_SZ]
    return hmac_mod.compare_digest(hmac_mod.new(mac_key, data, hashlib.sha512).digest(), stored)


def decrypt_page(page: bytes, enc_key: bytes, pgno: int) -> bytes:
    """解密单个 4096 字节页，输出标准 SQLite 页。"""
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

    iv = page[PAGE_SZ - RESERVE_SZ : PAGE_SZ - RESERVE_SZ + IV_SZ]
    cipher = Cipher(algorithms.AES(enc_key), modes.CBC(iv))
    decryptor = cipher.decryptor()
    if pgno == 1:
        encrypted = page[SALT_SZ : PAGE_SZ - RESERVE_SZ]  # 4000B
        decrypted = decryptor.update(encrypted) + decryptor.finalize()
        return bytes(SQLITE_HDR + decrypted + b"\x00" * RESERVE_SZ)
    encrypted = page[: PAGE_SZ - RESERVE_SZ]  # 4016B
    decrypted = decryptor.update(encrypted) + decryptor.finalize()
    return decrypted + b"\x00" * RESERVE_SZ


def decrypt_database(db_bytes: bytes, enc_key: bytes, verify_hmac: bool = True) -> bytes:
    """解密整个数据库文件（内存读取），返回明文 SQLite 字节。"""
    size = len(db_bytes)
    total_pages = (size + PAGE_SZ - 1) // PAGE_SZ
    out = bytearray()
    for pgno in range(1, total_pages + 1):
        page = db_bytes[(pgno - 1) * PAGE_SZ : pgno * PAGE_SZ]
        if len(page) < PAGE_SZ:
            page = page + b"\x00" * (PAGE_SZ - len(page))
        if verify_hmac and pgno == 1 and not verify_page1_hmac(page, enc_key):
            raise ValueError("page1 HMAC 校验失败：候选 key 不正确")
        out += decrypt_page(page, enc_key, pgno)
    return bytes(out)
