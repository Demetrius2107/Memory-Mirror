# 产品需求文档 (PRD)
## 项目代号：记忆镜像 (MemoryMirror)
### 版本：V2.1（公测MVP · 性能优化版 · 代码评审修订版）
### 拟稿日期：2026-08-16

> **修订说明**：V2.1 由代码评审（AtomCode）对 V2.0 提出 15 项修订，P0/P1 已全部内联修订。文中以 `✏️ R#` 标注对应修订项，详见下方修订日志。

---

## 0. 修订日志（V2.0 → V2.1）

| 编号 | 级别 | 位置 | 修订内容 |
| :--- | :--- | :--- | :--- |
| R1 | P0 | §8 / §4.5 | 隐私与"RAG 片段上云"矛盾：**方向调整（用户决策 2026-08-16）**——原型阶段客户端集成：用户自填 Key 直连云模型，接受隐私由公共云厂商读取的取舍并明示披露；本地 Ollama 降为可选降级通道；后续分支迭代服务端版本 |
| R2 | P0 | §6 / §10 | "安装包 ≤25MB"不可行：改为双指标（Tauri 壳 ≤25MB / 完整安装包 ≤300MB）；模型推理走 **ONNX Runtime**（无 torch） |
| R3 | P0 | §5 / §9 | MVP 仅支持 **Windows**；Mac 从"自动读取"降级为"导入导出"；路线图移除 `.dmg` |
| R4 | P1 | §4.4 | Embedding 换中文模型 `bge-small-zh`（默认）/ `bge-m3`（可选），删除英文模型 all-MiniLM-L6-v2 |
| R5 | P1 | §3 / §4.5 / §7 | 流式方案统一：处理进度 = WebSocket，AI 流式输出 = SSE；移除 Socket.IO |
| R6 | P1 | §4.5 / §7 | 移除 Redis，改用 `diskcache` 嵌入式持久缓存；缓存 key 去掉无意义的 user_id |
| R7 | P1 | §5 / §8 | 反馈"截图上传"改为"生成本地反馈包 zip"（架构无云端服务，与"绝不联网"自洽） |
| R8 | P1 | §4.3 / §6 | 增量逻辑修正：从 last_sync_time 起增量（非"仅最近 3 天"，避免跨期丢数据）；msg_id 水位线 + time_utc 双过滤 |
| R9 | P2 | §4.2 / §5 | `emotion_score` 明确由**词典/规则流水线**产出（非逐条 LLM 打分），AI 只做窗口级情绪概括 |
| R10 | P2 | §7 | Python 锁定 **3.11/3.12**（3.13 对 PyWxDump/ChromaDB/ONNX 生态兼容风险） |
| R11 | P2 | §6 / §8 | API Key 存系统钥匙串（Windows 凭据管理器 / Rust keyring crate），不明文落盘 |
| R12 | P2 | §3 / §6 | 展示端**仅嵌 Webview**；浏览器仅作调试模式（避免扩大本地 API 暴露面） |
| R13 | P2 | §10 | 性能指标标注"ONNX 路线下"前提（torch 路线达不到） |
| R14 | P2 | §9 | 路线图：演示数据集提前至 Week 1（前端先行），解密周预留 1.5 周缓冲 |
| R15 | P2 | §4.1 / §7 | 工具名勘误：`chatlog`（sjzar/chatlog）、MemoTrace（= WeChatMsg 更名）、移除未核实的 EchoTrace |
| R16 | P0 | §4.1 / §9 / §11 | **市场调研新增（2026-08-16）**：PyWxDump、chatlog 已被微信官方律师函要求删库停更，腾讯确认向 GitHub 发函清场 30+ 项目；数据获取改为"**导入通道为主 + 演示数据集**"，解密降级为实验性可选（默认关闭，不进分发版），规避合规红线 |
| R17 | P1 | §4.3 / §5 / §6 | **用户需求补充（2026-08-16）**：新增联系人维度建模（contacts 表：wxid/昵称/备注名/类型 + group_members 群成员表）+ 联系人/群聊**选择面板**（按昵称或备注名搜索、多选对比、群聊分析）；数据获取定位为"格式适配器"（数据源 = MemoTrace 导出文件，备注名取自联系人文件配对导入） |
| R18 | P0 | §4.1 / §9 | **用户调整（2026-08-16）**：不放弃解密路线——**自实现解密逻辑**（学习 WeChatMsg/PyWxDump/chatlog 现有实现，先验证可行性，目标 wx3/wx4）；作为**实验性通道（默认关闭，不进分发版）**，导入通道仍为产品主通道 |

---

## 1. 项目背景与目标

- **背景**：  
  用户拥有大量历史微信聊天记录（通常跨越 3~5 年），但缺乏有效工具去挖掘其中蕴含的情感脉络、关系动态以及个人回忆。现有的大模型产品无法直接处理本地加密数据，且存在隐私顾虑。

- **目标**：  
  打造一款 **本地隐私优先** 的 AI 分析工具，帮助用户完成「社交关系体检」——通过数据可视化和智能问答，让用户看到自己与重要联系人之间的亲密度变化、情绪起伏以及关键转折点。

- **核心理念**：  
  **首次全量，之后增量；流式反馈，缓存加速；本地优先，云端增强。** ✏️ R1（方向调整 2026-08-16）：原型阶段**客户端集成**——下载即用、自填 API Key 直连云模型，接受公共云厂商读取的取舍（配置页明示）；完整版再通过分支迭代服务端（CS）版本。

## 2. 用户画像与场景

- **主要用户**：  
  - 对 AI 好奇但非技术背景的普通用户  
  - 重视个人数据隐私，不愿将聊天记录上传云端  
  - 希望通过「数据化自我认知」获得情感价值或社交反思

- **核心使用场景**：  
  1. **回忆挖掘**：快速搜索多年前的某次对话或承诺。  
  2. **关系复盘**：查看与伴侣/好友/同事的亲密度变化曲线。  
  3. **情绪观察**：发现长期情绪波动与生活事件的关联。  
  4. **社交提醒**：基于互动频率，接收「是否该联系一下」的温情提示。

## 3. 系统架构（三层分离）

| 层级 | 技术选型 | 职责 |
| :--- | :--- | :--- |
| **桌面客户端外壳** | **Tauri 2 (Rust + Webview2)** | 应用入口、进程管理、系统交互、本地服务器托管 |
| **数据处理引擎** | **Python 3.11 ✏️ R10 (FastAPI) + Sidecar** | 数据解密（PyWxDump）、清洗、向量化、AI 调用 |
| **Web 展示端** | **Vue3 + ECharts + Tailwind** | 仪表盘渲染、交互反馈、图表展示（嵌入 Webview ✏️ R12） |

**通信机制**：
- Tauri 通过 **Sidecar** 模式启动/停止 Python 进程
- Python 进程通过 **WebSocket** 实时推送处理进度 ✏️ R5
- AI 问答通过 **SSE** 流式输出（FastAPI StreamingResponse）✏️ R5
- Web 前端通过 **HTTP API** 获取数据和发起 AI 问答
- 展示端仅嵌入 Tauri Webview；浏览器访问仅作为调试模式 ✏️ R12

---

## 4. 数据流与性能优化设计（核心）

### 4.1 数据获取（不暴力破解）

**市场现状（2026-08 调研）**：✏️ R16 微信官方已大规模清场——`PyWxDump`、`chatlog` 均于 2025-10 收到律师函后**删库停更**；腾讯 2026-01 确认向 GitHub 发函、一次 DMCA 举报 30+ 项目；`WeChatMsg`（留痕）本体停更（官网/fork 仍支持微信 4.0）。**本项目不将"解密"作为产品功能**，规避同类合规红线。

**MVP 数据通道（按优先级）**：✏️ R16
1. **导入通道（主）**：导入用户自备的导出文件（WeChatMsg 留痕 CSV/HTML、chatlog 旧版 JSON、通用 JSONL）——解密由用户用自用工具完成，责任与风险边界清晰（与 wechat-insight 同路线）
2. **演示数据集（内置）**：内置模拟数据，保证无真实数据也能体验全部功能
3. **解密通道（实验性，默认关闭，自实现验证）**✏️ R18：学习现有开源实现——WeChatMsg 仓库仍公开、PyWxDump v3.1.46 wheel 与 chatlog v0.0.31 Go 模块仍可获取（MIT/Apache-2.0），**自实现** key 提取（进程内存）+ SQLCipher 解密，目标 **wx3/wx4**；先做可行性验证（spike），接受合规风险，不进入对外分发版本；仅 Windows

**关于自实现解密（✏️ R18 合规边界）**：**授权 ≠ 合规**——MIT/Apache-2.0 允许复制借鉴代码，但微信官方打击的是**功能本身**（绕过客户端加密），与代码是否原创无关（下架项目用的都是自己的代码）。故定位：原型实验模块（自用验证），默认关闭，不进分发版；对外公开前需再做法律评估。

**流程（导入通道）**：
1. 用户选择/拖入导出文件（CSV/JSON/JSONL/HTML）
2. 格式探测 → 字段映射 → 清洗流水线（§4.2）
3. 结构化入库（§4.3），增量逻辑见 §4.3

**可作导入数据源的成熟工具**：
- **MemoTrace（WeChatMsg，留痕）**：图形化工具，导出 CSV/HTML/Word，官网/fork 支持微信 4.0 ✏️ R15
- ~~PyWxDump / chatlog~~：已删库停更（2025-10），仅可本地旧版自用，不再作为集成依赖 ✏️ R16

**备注名/昵称映射（选择面板的数据基础）**：✏️ R17 好友备注名存在微信 `Contact` 表的 `Remark` 字段（昵称 `NickName`），MemoTrace 导出时通常附带联系人文件（contact.csv / contact.db）；导入器需支持"消息文件 + 联系人文件**配对导入**"，解析出 `contacts` 表（§4.3），否则选择面板只能显示原始 wxid。**产品定位 = "格式适配器"而非"解密器"**——数据获取与微信版本解耦，产品寿命不绑微信生态。

### 4.2 数据清洗（中等难度，需耐心处理细节）

**输入**：解密后的原始数据（JSON/CSV）
**输出**：结构化的消息记录表

**清洗流水线**：

```python
# 伪代码示例
def clean_pipeline(raw_df):
    # 1. 分块读取（避免内存溢出）
    for chunk in pd.read_csv(raw, chunksize=100000):
        # 2. 时间标准化：统一为UTC时间戳（记录导入时的时区假设）
        chunk['time_utc'] = pd.to_datetime(chunk['time_str']).dt.tz_localize('Asia/Shanghai').dt.tz_convert('UTC')
        # 3. XML内容解析（递归处理转发消息、系统通知）
        chunk['content_parsed'] = chunk['raw_xml'].apply(parse_xml_content)
        # 4. 去重：按msg_id（多库合并时叠加(时间+内容hash)）
        chunk = chunk.drop_duplicates(subset=['msg_id'])
        # 5. 脱敏：手机号、身份证、姓名 ✏️ R1
        chunk['content'] = chunk['content_parsed'].apply(desensitize)
        # 6. 规则情感打分（词典/情感词频，非LLM）✏️ R9
        chunk['emotion_score'] = chunk['content'].apply(rule_based_sentiment)
        # 7. 写入SQLite（UPSERT，幂等可重跑）✏️ R8
        upsert_to_sqlite(chunk)
```

**重点难点**：

- XML 解析：嵌套转发消息可能层层包裹，需递归解析
- 特殊字符：Emoji 需转成统一编码存储
- 时间时区：必须统一转 UTC，展示时再转本地；用户改过系统时区时需提示误差 ✏️ R9（时区假设记录到 metadata）
- 增量幂等：写入必须 UPSERT（msg_id 主键），重复导入不产生脏数据 ✏️ R8

### 4.3 数据存储（避免重复处理）

**方案**：本地 SQLite + 增量更新

**表结构设计**：

```sql
-- 消息表（主存储）
CREATE TABLE messages (
    id INTEGER PRIMARY KEY,
    msg_id TEXT UNIQUE,           -- 微信唯一ID，用于去重
    talker TEXT,                  -- 联系人微信号
    time_utc INTEGER,             -- UTC时间戳（索引）
    content TEXT,                 -- 清洗后文本
    content_type TEXT,            -- text/image/voice/video/file
    emotion_score REAL,           -- 情感打分（-1~1），由规则流水线产出 ✏️ R9
    year_month TEXT               -- 分区字段 '2024-01'
);

-- 索引（加速查询）
CREATE INDEX idx_time ON messages(time_utc);
CREATE INDEX idx_talker ON messages(talker);
CREATE INDEX idx_year_month ON messages(year_month);

-- 元数据表（记录处理状态）
CREATE TABLE metadata (
    key TEXT PRIMARY KEY,
    value TEXT                   -- 如 last_sync_msg_id, last_sync_time, tz_assumption
);

-- 联系人表（选择面板数据基础）✏️ R17
CREATE TABLE contacts (
    wxid TEXT PRIMARY KEY,        -- 微信号/群ID（wxid_xxx 或 群ID）
    nickname TEXT,                -- 昵称
    remark TEXT,                  -- 备注名（用户设置，展示优先）
    type TEXT,                    -- friend / group / official / stranger
    avatar TEXT                   -- 头像路径（可选）
);

-- 群成员表（群聊分析用）✏️ R17
CREATE TABLE group_members (
    group_wxid TEXT,              -- 群ID
    member_wxid TEXT,             -- 成员wxid
    member_name TEXT,             -- 群内昵称/备注
    PRIMARY KEY (group_wxid, member_wxid)
);
-- 注：messages.talker 语义——单聊=好友wxid；群聊=群ID（群内归属结合 group_members / 消息 sender 字段）
```

**增量更新逻辑**：✏️ R8
1. 首次运行：全量处理，记录 last_sync_time = 0、last_sync_msg_id = 0
2. 后续启动：解密后以 `time_utc > last_sync_time OR (time_utc = last_sync_time AND msg_id > last_sync_msg_id)` 过滤（msg_id 水位线 + 时间双过滤，覆盖离线消息/时间戳乱序）
3. 一律按 msg_id UPSERT 写入，天然幂等；间隔超过阈值（如 30 天）时提示"本次增量较大"

### 4.4 向量化与本地检索（RAG核心）

**方案**：ChromaDB（嵌入式，无需独立部署）

**流程**：

1. 清洗后的文本按 500 字切片（带 50 字重叠）
2. 调用本地中文 Embedding 模型 `bge-small-zh`（默认）/ `bge-m3`（高质量可选）生成向量 ✏️ R4（删除英文模型 all-MiniLM-L6-v2——对中文检索质量差）
3. 模型推理统一走 **ONNX Runtime**（int8 量化），避免引入 torch（体积/内存超标）✏️ R2
4. 存入 ChromaDB，元数据记录 msg_id, 时间, 联系人

**查询时**：

1. 用户问题 → 向量化 → ChromaDB 检索 Top 20
2. 用本地 Reranker（bge-reranker，ONNX 推理）重排 → 取 Top 3-5
3. 云端调用前对片段做**增强脱敏**（姓名 → A先生/B小姐）✏️ R1
4. 组装 Prompt 发送给 LLM（原型默认云端 DeepSeek/GPT-4o-mini；本地 Ollama 为可选降级）✏️ R1

### 4.5 AI问答（速度优化核心）

**策略一：流式输出（SSE）** ✏️ R5

- 后端 FastAPI `StreamingResponse` 流式返回，前端逐字渲染（打字机效果）
- 首字出现 0.5~1 秒内，感知速度提升约 10 倍
- 与进度推送（WebSocket）职责分离，不再使用 Socket.IO

**策略二：模型分层** ✏️ R1（方向调整 2026-08-16：原型默认云端）

- 原型默认（用户决策取舍）：用户自填 DeepSeek / GPT-4o-mini Key，客户端直连云模型——接受隐私由公共云厂商读取的取舍，配置页**明示"检索片段将发送至该模型服务商"**
- 可选降级：本地 Ollama（Qwen2.5-7B-Instruct / DeepSeek-R1-Distill-Qwen-7B），零上云、零费用，供无 Key/离线场景
- 完整版：分支迭代服务端（CS）版本时再评估本地化与隐私策略

**策略三：缓存命中**

- `diskcache`（纯 Python 嵌入式持久缓存，重启不丢、零运维）✏️ R6
- Key = question_hash（本地单用户，无需 user_id）✏️ R6
- 相同问题直接返回历史结果（0ms）

**策略四：后台预生成（异步）**

- 深度分析任务返回 task_id，前端显示"分析中，完成后通知您"
- 后台运行，完成后 WebSocket 推送

**数据传输量控制**：

- 只发 Top 3-5 段最相关片段（而非 Top 10）
- 要求 AI"50字以内概括"或做抽取式摘要
- Token 数从 5000 → 1000，耗时缩短约 60%

---

## 5. 核心功能模块（MoSCoW优先级）

| 模块 | 功能点 | 优先级 | 对应端 |
| :--- | :--- | :--- | :--- |
| 数据接入 | 1. 导入导出通道（WeChatMsg/留痕 CSV/JSON/HTML + 联系人文件配对）✏️ R16/R17 2. 演示数据集（内置模拟数据）3. 解密通道（实验性，默认关闭）✏️ R16 | Must | 客户端 |
| 清洗与存储 | 4. 自动脱敏（手机号/身份证/姓名）✏️ R1 5. 增量更新（从同步点起，幂等 UPSERT）✏️ R8 6. 进度实时反馈（WebSocket 推送） | Must | 客户端 |
| 关系洞察 | 7. 亲密度曲线（月互动频次 + 平均回复时长）8. 情绪起伏日历（规则打分 + 极值日标注）✏️ R9 9. 年度词汇云图 | Must | Web 端 |
| 会话选择（新增）✏️ R17 | 15. 联系人/群聊选择面板（昵称/备注名搜索、多选对比）16. 单聊分析（我 vs 单好友）17. 群聊分析（活跃度/成员排行/群内互动） | Must | Web 端 |
| AI 智能问答 | 10. 自然语言查询（带引用溯源）11. 流式输出（SSE 打字机效果）✏️ R5 12. 缓存复用（相同问题秒回） | Should | Web 端 |
| 反馈闭环 | 13. 一键评分（有帮助/无帮助）14. 生成本地反馈包（截图+脱敏日志 zip，不上传）✏️ R7 | Must | 双端 |

---

## 6. 用户体验流程（关键路径）

**首次启动（全量处理）**

1. 下载安装：Tauri 打包的 .exe（Tauri 壳 <25MB；Sidecar 依赖随安装器分发，完整安装包 ≤300MB）✏️ R2
2. 配置 AI（原型默认云端，用户决策取舍）✏️ R1：
   - 默认：填入云端 API Key（DeepSeek / OpenAI），**存系统钥匙串（Windows 凭据管理器）**，不明文落盘 ✏️ R11；填入即视为同意"检索片段将发送至所选模型服务商" ✏️ R1
   - 可选降级：本地 Ollama（自动检测/引导安装，无需 Key），供无 Key/离线场景
3. 点击"开始扫描"：
   - 选择导入文件（WeChatMsg 导出 CSV/HTML、JSONL）或使用演示数据集 ✏️ R16
   - Python 进程启动，通过 WebSocket 推送进度：
     - 正在解析导入文件...
     - 正在清洗数据...
     - 正在生成向量索引...
     - ✅ 分析完成！共 12,345 条消息，耗时 10 分 18 秒 ✏️ R13
4. 在客户端内嵌 Webview 中打开仪表盘（不自动跳转浏览器）✏️ R12
5. 打开**选择面板**：联系人/群聊列表（按昵称/备注名搜索）→ 单选进入单人分析，多选进入对比模式，群聊进入群分析 ✏️ R17

**后续启动（增量更新）** ✏️ R8

1. 启动客户端
2. 自动从上次同步点起检测新消息（msg_id 水位线 + 时间双过滤）
3. 进度提示："发现 23 条新消息，正在更新..."；间隔过大时提示"本次增量较大"
4. 秒级完成，直接打开仪表盘

**AI问答交互**

1. 用户在 Web 对话框输入："我们哪一年吵架最多？"
2. 后端先查 diskcache 缓存 → 命中则秒回 ✏️ R6
3. 未命中则本地检索 Top 3-5 片段（增强脱敏后）→ 调用 LLM（原型默认云端）✏️ R1，流式返回（SSE）✏️ R5
4. 前端逐字渲染，并标注引用来源（时间戳）

---

## 7. 技术栈全景

| 层级 | 技术选型 | 理由 |
| :--- | :--- | :--- |
| 桌面外壳 | Tauri 2 (Rust + Webview2) | 打包体积小，无需额外运行时 |
| 数据处理 | Python 3.11 ✏️ R10 (FastAPI) | 生态丰富（Pandas, PyWxDump, ChromaDB, ONNX Runtime） |
| 进程管理 | Tauri Sidecar | Rust 管理 Python 子进程生命周期 |
| 实时通信 | WebSocket（进度推送）+ SSE（AI 流式）✏️ R5 | 各自职责单一，避免 Socket.IO 复杂度 |
| Web前端 | Vue3 + ECharts + Tailwind | 图表渲染、响应式设计 |
| 本地缓存 | diskcache（嵌入式）✏️ R6 | 重启不丢、零运维；Redis 对本地单用户过重 |
| 向量数据库 | ChromaDB (嵌入式) | 无需独立部署，纯本地 |
| Embedding / Reranker | bge-small-zh / bge-m3 / bge-reranker（ONNX int8）✏️ R4 | 中文检索质量命门；ONNX 避免 torch 体积与内存 ✏️ R2 |
| 大模型 | 原型默认云端：DeepSeek / GPT-4o-mini（自填 Key）；本地 Ollama 可选降级 ✏️ R1 | 原型接受云端取舍（配置页明示）；完整版分支迭代服务端 |
| 数据解密 | PyWxDump（仅 Windows，版本锁定）✏️ R3 | 成熟方案，无需暴力破解 |

> **备注**：全链路模型推理（Embedding/Reranker）走 ONNX Runtime；本地 LLM 由 Ollama 管理。**不引入 torch**，否则安装包与内存指标失效（R2/R13）。

## 8. 数据安全与隐私声明

- **原型取舍（用户决策 2026-08-16）**：默认客户端集成、用户自填 Key 直连云模型——检索片段（经增强脱敏）将发送至所选模型服务商，配置页明示披露；数据导入、清洗、存储、向量化均在本地完成 ✏️ R1
- **云端明示披露**：启用云端模型后，检索到的聊天片段（经增强脱敏）将发送至所选模型服务商，配置页明示 ✏️ R1
- **用完即焚**：临时密钥在客户端关闭时由 Rust 彻底销毁
- **脱敏展示**：Web 端姓名自动替换为「A先生/B小姐」；导入清洗阶段对手机号/身份证/姓名统一脱敏 ✏️ R1
- **反馈本地化**：反馈日志仅含版本号、处理条数、错误码，反馈包（截图+日志）本地生成 zip，不上传 ✏️ R7

---

## 9. V2.0 迭代路线图 ✏️ R3/R14

| 阶段 | 时间 | 里程碑 |
| :--- | :--- | :--- |
| Week 1 | 基础框架 | Tauri + FastAPI 互通，WebSocket/SSE 打通，**演示数据集 + 仪表盘静态页先行** ✏️ R14 |
| Week 2-3 | 数据获取 | **导入通道为主**（WeChatMsg 导出 CSV/HTML、JSONL 格式探测 + 字段映射）+ 演示数据集 ✏️ R16；**解密自实现验证**（key 提取 + SQLCipher，wx3/wx4，实验性默认关闭）✏️ R18 |
| Week 3 | 清洗与存储 | Pandas 清洗流水线（含规则情感打分 ✏️ R9）+ SQLite 增量存储（UPSERT） |
| Week 4 | 向量检索 | bge-small-zh（ONNX）+ ChromaDB + bge-reranker 本地重排 |
| Week 5 | AI接入 | 云端默认通道（DeepSeek/GPT-4o-mini，自填 Key）+ 本地 Ollama 可选降级 + SSE 流式 + diskcache 缓存 + 异步预生成 ✏️ R1 |
| Week 6 | Web仪表盘 | ECharts 图表（曲线/日历/词云）+ 问答界面 + 反馈包生成 |
| Week 7 | 打包公测 | 编译 Windows .exe 安装器，收集反馈 |

> **后续规划（分支迭代）**：原型验证完成后，通过独立分支迭代**服务端版本（CS 架构）**——形态参考市面 chatlog-new（agent-web / datasync 微服务 + 向量库 + 消息队列）✏️ R16；届时再评估本地化与隐私策略

## 10. 附录：性能指标目标 ✏️ R13（以下指标均基于 ONNX Runtime 推理路线，torch 路线无法达标）

| 指标 | 目标值 |
| :--- | :--- |
| 首次全量处理（10万条，ONNX int8 + 大 batch） | ≤ 10 分钟 |
| 增量更新（1000条） | ≤ 5 秒 |
| AI问答首字出现时间 | ≤ 1 秒 |
| AI问答全文生成（本地 7B / 云端快模型） | ≤ 5 秒 |
| 缓存命中响应时间 | ≤ 50 ms |
| Tauri 壳安装包 | ≤ 25 MB |
| 完整安装包（含 Sidecar 依赖） | ≤ 300 MB ✏️ R2 |
| 运行时内存占用（无 torch） | ≤ 300 MB |

## 11. 给开发者的"快速启动清单" ✏️ R10/R6

☐ 安装 Rust + Tauri CLI（Tauri 2）
☐ 安装 Python 3.11 + 虚拟环境（勿用 3.13，ChromaDB/ONNX 兼容风险）✏️ R10
☐ 安装依赖：pip install chromadb onnxruntime fastapi "uvicorn[standard]" diskcache ✏️ R6（**不再依赖 pywxdump——已删库** ✏️ R16）
☐ 生成/内置演示数据集（模拟 10 万条消息），打通"导入通道"格式探测 ✏️ R16
☐ 可选：安装 Ollama 并拉取本地模型（降级通道）✏️ R1
☐ 启动 FastAPI 服务，测试 /health 接口
☐ Tauri 通过 Sidecar 启动 Python 进程
☐ WebSocket 推送一条测试进度消息；SSE 测试一条流式回复 ✏️ R5
☐ 前端 Vue3 显示"Hello World"
☐ 加载演示数据集，输出第一个 ECharts 图表（模拟数据）

---

## 附录 B：市面同类项目调研（2026-08-16）✏️ R16

| 项目 | 形态 | 数据获取 | AI / 隐私 | 状态（2026-08） | 对我们的启示 |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **PyWxDump** | Python 库 + CLI + Web | 进程内存提 key + 解密 | 无 AI；本地 | ❌ 2025-10 律师函后删库停更 | 解密通道不可依赖；仅本地旧版自用 |
| **chatlog（sjzar）** | Go TUI + HTTP API + Web | 进程内存提 key + 解密（Win/Mac） | 无 AI；本地 | ❌ 2025-10 律师函后删库停更 | HTTP API + Web 形态可参考 |
| **WeChatMsg / 留痕（MemoTrace）** | PyQt 桌面 GUI | 进程提 key + 解密 | AI 问答/年报（用户自填 Key） | ⚠️ 本体停更；官网/fork 支持微信 4.0 | 导出 CSV/HTML = 我们导入通道的数据源 |
| **wechat-insight**（caigee-cmd） | CLI + 单文件 HTML 年报 | 已导出 JSONL（不自解密） | 零联网规则分析；关系指数 = 时间衰减公式 + OHLC K线 | ✅ 活跃（macOS） | 验证"导入 + 规则分析"路线；亲密度算法可直接借鉴（72h 半衰期）|
| **welink**（thedawn3） | Docker 前后端平台 | 解密（自研） | 情感趋势 / 词云 / MCP 查询 | ✅ 活跃 | 关系分析 + 词云形态参考 |
| **ChatLab** | Electron + Vue3 桌面 | 多平台导入（不支持微信） | SQL + AI Agent（24+ 工具）；本地优先 + 可选云端 | ✅ 活跃（7K+ stars） | 验证"本地优先 + AI 问答 + 可视化"产品形态；Tauri 版更轻 |
| **Memoreei** | MCP server（Python） | 13 平台导入 | 混合检索 BM25 + 向量（RRF）；FastEmbed ONNX 离线 embedding | ✅ 活跃 | 验证 ONNX 本地 embedding 路线（约 23MB 模型） |
| **chatlog-new**（WechatRagAgent fork） | Java/Spring 微服务 | chatlog 旧版 | ES + Redis + SiliconFlow（bge-m3 / bge-reranker）+ OpenRouter，SSE 流式 | ✅ 活跃 | "服务端版本"的现实形态（远期分支迭代参考） |

**调研结论**：

1. **合规是行业级红线**：微信官方已对解密类项目清场（律师函 + DMCA，一次 30+ 项目），MVP 必须走**导入通道为主**，解密不做产品功能——与 wechat-insight 同路线，风险最低
2. **产品形态成立且有先例**：ChatLab（7K+ stars）证明"本地优先 + AI 问答 + 可视化"成立；我们的差异化 = 微信生态 + 关系洞察（亲密度/情绪日历/词云）+ RAG 问答 + Tauri 轻客户端
3. **技术选型被市场验证**：RAG 链路（bge embedding + rerank + 向量库 + LLM + SSE）与 chatlog-new 一致；ONNX 本地 embedding 被 Memoreei 验证；关系指数公式可借鉴 wechat-insight（规则打分、时间衰减，非逐条 LLM）
4. **"客户端集成 + 自填 Key 接受云端"与市场主流一致**（ChatLab / wechat-insight 同款取舍），原型阶段成本最低；远期服务端版本可参考 chatlog-new 微服务形态

---

"记忆镜像"不仅是一个工具，更是每个人与过去自己的对话桥梁。

—— 让数据有温度，让回忆可追溯。
