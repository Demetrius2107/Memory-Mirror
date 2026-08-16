"""批量解密 db_storage 全部库（wx4：passphrase → PBKDF2-HMAC-SHA512 256000 → 各库密钥）。

用法:
  python tools/batch_decrypt.py <passphrase_hex> <db_storage_dir> [out_dir]
输出:
  out_dir（默认 data/decrypted/）下保持子目录结构的明文 sqlite，仅解密成功者。

说明（R18 实验性，本机自用）：wx4 每库 salt 独立、passphrase 共用；
用各库文件头 16 字节 salt 派生密钥，HMAC 校验通过才写出。密钥勿入 git/云端。
"""

from __future__ import annotations

import argparse
import hashlib
import hmac
import struct
import sys
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

from Crypto.Cipher import AES

KEY_SIZE = 32
ROUND_COUNT = 256000
PAGE_SIZE = 4096
SALT_SIZE = 16
IV_SIZE = 16
HMAC_SZ = 64
RESERVE = IV_SIZE + HMAC_SZ  # 80


def decrypt_db_v4(passphrase_hex: str, in_path: str, out_path: str) -> bool:
    """解密单个库：HMAC 校验通过返回 True 并写出明文，否则 False。"""
    with open(in_path, "rb") as f_in, open(out_path, "wb") as f_out:
        salt = f_in.read(SALT_SIZE)
        if len(salt) != SALT_SIZE:
            return False
        mac_salt = bytes(b ^ 0x3A for b in salt)
        key = hashlib.pbkdf2_hmac("sha512", bytes.fromhex(passphrase_hex), salt, ROUND_COUNT, dklen=KEY_SIZE)
        mac_key = hashlib.pbkdf2_hmac("sha512", key, mac_salt, 2, dklen=KEY_SIZE)

        # 先校验 page1 HMAC（快速判断 passphrase 是否正确）
        page1 = salt + f_in.read(PAGE_SIZE - SALT_SIZE)
        if len(page1) < PAGE_SIZE:
            return False
        h1 = hmac.new(mac_key, page1[SALT_SIZE : PAGE_SIZE - RESERVE + IV_SIZE], hashlib.sha512)
        h1.update(struct.pack("<I", 1))
        if not hmac.compare_digest(h1.digest(), page1[PAGE_SIZE - HMAC_SZ : PAGE_SIZE]):
            return False

        # 逐页解密写出
        f_in.seek(SALT_SIZE)
        f_out.write(b"SQLite format 3\x00")
        cur = 0
        while True:
            page = salt + f_in.read(PAGE_SIZE - SALT_SIZE) if cur == 0 else f_in.read(PAGE_SIZE)
            if not page:
                break
            offset = SALT_SIZE if cur == 0 else 0
            iv = page[PAGE_SIZE - RESERVE : PAGE_SIZE - RESERVE + IV_SIZE]
            cipher = AES.new(key, AES.MODE_CBC, iv)
            dec = cipher.decrypt(page[offset : PAGE_SIZE - RESERVE])
            f_out.write(dec)
            cur += 1
    return True


def _worker(args):
    """ProcessPoolExecutor 顶层 worker（Windows spawn 下 lambda 不可 pickle）。"""
    return decrypt_db_v4(*args)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("passphrase_hex", help="wx4 passphrase（64 位 hex，由 wx_key/降级法获得）")
    ap.add_argument("db_dir", help="db_storage 目录（递归找 *.db）")
    ap.add_argument("out_dir", nargs="?", default="data/decrypted")
    args = ap.parse_args()

    if len(args.passphrase_hex) != 64:
        print("passphrase 必须是 64 位 hex")
        sys.exit(1)

    src = Path(args.db_dir)
    out = Path(args.out_dir)
    targets = sorted(src.rglob("*.db"))
    print(f"待解密库: {len(targets)} 个（{src}）")

    tasks = []
    for db in targets:
        rel = db.relative_to(src)
        dest = out / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        tasks.append((args.passphrase_hex, str(db), str(dest)))

    ok_cnt = 0
    with ProcessPoolExecutor(max_workers=8) as ex:
        results = list(ex.map(_worker, tasks))
    for db, ok in zip(targets, results):
        tag = "✅" if ok else "❌"
        print(f"  {tag} {db.relative_to(src)}")
        ok_cnt += ok
    print(f"完成: {ok_cnt}/{len(targets)} 个库解密成功 → {out}")


if __name__ == "__main__":
    main()
