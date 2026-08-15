# 解密 Spike 技术方案（R18：实验性自实现，wx3/wx4）

> 定位：原型实验模块，默认关闭、不进分发版（合规红线见 PRD §4.1 R18）。
> 目标：验证「进程内存提取密钥 → SQLCipher 解密 → 导出统一 CSV/JSONL → 喂入现有导入通道」全链路可行性。

## 1. 调研结论（2026-08-16）

### 微信 PC 版数据库加密事实

| 版本 | 加密方案 | 密钥形态 | 提取方式 |
|---|---|---|---|
| wx3.x | SQLCipher（AES-256-CBC + HMAC，PBKDF2 派生） | 32 字节 enc_key，WCDB 缓存于进程内存 | ① 版本偏移法（KEY_OFFSET 相对 WeChatWin.dll 基址，老版本维护偏移表）② **内存扫描法**（搜 "android"/"iphone" 设备串，key 在其前，向前逐字节扫 32B 并校验）——兼容性更好 |
| wx4.0 | **SQLCipher 4**（AES-256-CBC + HMAC-SHA512，KDF PBKDF2-HMAC-SHA512 256,000 次；**每库独立 salt + enc_key**） | WCDB 缓存派生后的 raw key，内存中形如 `x'<64hex_enc_key><32hex_salt>'` | 扫 Weixin.exe 内存匹配该模式 → 用 HMAC 校验 page 1 确认密钥正确 |

### 关键原理（内存取证，非破解算法）

- 核心矛盾：**数据在使用时必须是明文**——密钥必然驻留进程内存，提取内存即得密钥（ylytdeng/wechat-decrypt 明确表述此思路）。
- 校验：取候选 key 后，mac_key = PBKDF2-HMAC-SHA512(enc_key, salt⊕0x3a, iter=2, dklen=32)，对 page1 的 [16:4032] 数据 + 页号做 HMAC-SHA512，与文件尾部 64B 比对，通过即正确。
- **wx4 实时性发现（对 R8 增量更新有启发）**：微信用 SQLite WAL 模式，WAL 预分配固定 4MB；**新消息只写 WAL**，可用 30ms 轮询 mtime（或 Windows ReadDirectoryChangesW）检测变化并增量解密 frame——比 PRD 当前「全量重扫 + msg_id 水位线」更优，作为增量通道升级方向（Week 4+ 再评估）。

## 2. Spike 验证步骤（本机自测）

1. 本机需已登录运行微信（wx3.9.x 或 4.0）
2. `python -m backend.decrypt.key_extract` —— 内存扫描提取候选 key，HMAC page1 校验
3. `python -m backend.decrypt.decrypt_db --db <MSG.db 路径> --key <hex> --out out.db`
4. 用 sqlite3 打开 out.db，核对表结构（wx3: MSG/Contact；wx4: 新版 schema）
5. 导出 CSV/JSONL → 喂现有导入通道 → 走 §4.2 清洗流水线（两条通道统一中间格式，解密失败不影响主链路）
6. 失败回退：退回 MemoTrace 导出文件导入通道

## 3. 技术风险

- 版本兼容性：wx3 偏移随版本变化（内存扫描法兼容性更好）；wx4 需验证本机版本可命中模式
- 权限：ReadProcessMemory 读同用户进程通常无需管理员
- 杀软告警：读他进程内存可能触发 Windows Defender 提示
- 合规：见 PRD R18——实验性自用，不进分发版

## 4. 参考来源（已核实可获取）

- zhimian/decrypt-PC-WeChat-db（C，wx2.8 偏移法：KEY_OFFSET 0x161cc50）
- zxki.cn「PC端微信数据库搜索密钥key」（wx3 设备串内存扫描法，测试至 3.9.7.28）
- ylytdeng/wechat-decrypt（wx4.0 SQLCipher4 方案 + WAL 实时监听，2026 活跃）
- WeChatMsg（LC044）仓库仍公开（wx3/wx4 schema 文档）

## 5. 本仓库实验代码

- `backend/decrypt/crypto.py` —— SQLCipher4 纯 Python 原语（HMAC 校验 + 页解密），wx3 较新版本同构
- `backend/decrypt/key_extract.py` —— Windows 内存扫描骨架（ctypes，未登录微信时仅打印提示）
- `backend/decrypt/selftest.py` —— 加密→解密往返自测（无真实微信也能验证密码学管线正确性）
