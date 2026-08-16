"""LLM 客户端（Week 5，PRD §4.5）：OpenAI 兼容流式接口（DeepSeek / 通用）。

- Key 存 data/config.json（位于 gitignore 的 data/ 下，不入库）
- 本机 HTTPS 被中间人拦截：httpx verify=False 绕过证书（与 curl -k 同思路，
  仅用于用户自填 Key 的模型 API 调用，PRD R1 已明确云端为显式可选项）
- stream_chat(messages, on_delta)：SSE 流式增量回调，返回完整文本
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx

CONFIG_PATH = Path(__file__).resolve().parents[2] / "data" / "config.json"

DEFAULTS = {
    "base_url": "https://api.deepseek.com/v1",
    "model": "deepseek-chat",
    "api_key": "",
}


def load_config() -> dict:
    cfg = dict(DEFAULTS)
    try:
        if CONFIG_PATH.exists():
            cfg.update(json.loads(CONFIG_PATH.read_text(encoding="utf-8")))
    except Exception:
        pass
    return cfg


def save_config(patch: dict) -> dict:
    cfg = load_config()
    # None = 不改；空字符串 = 显式清空（如清 Key）
    cfg.update({k: v for k, v in patch.items() if v is not None})
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    return cfg


def has_key() -> bool:
    return bool(load_config().get("api_key"))


def build_rag_prompt(question: str, hits: list[dict], max_chars: int = 3000, scope_label: str = "全部数据", emotion_summary: str | None = None) -> list[dict]:
    """RAG：检索片段 + 情感统计组装为 system 上下文（PRD §4.5 Token 控制）。

    scope_label：本次分析范围（全部 / 某联系人 / 某群）。
    emotion_summary：可选的情感统计摘要（均值/趋势/分布），注入后 LLM 可感知
    情绪全貌，避免仅凭零散片段得出片面结论。
    """
    parts = []
    for i, h in enumerate(hits, 1):
        m = h.get("metadata") or {}
        score = m.get("emotion_score", "")
        score_tag = f" [情感:{score:+.2f}]" if isinstance(score, (int, float)) else ""
        parts.append(f"[{i}] ({m.get('year_month', '?')} {m.get('talker', '?')}){score_tag} {h.get('content', '')}")
    context = "\n".join(parts)[:max_chars]

    system_parts = [
        "你是「记忆镜像」，基于用户本地微信聊天记录回答关于其社交关系、回忆与情绪的",
        f"问题。本次分析范围：{scope_label}。",
    ]
    if emotion_summary:
        system_parts.append(f"\n【情感统计】\n{emotion_summary}\n")
    system_parts.extend([
        "以下是从聊天记录检索到的相关片段（含情感分数，+为正/-为负）：",
        context,
        "\n请基于检索片段和情感统计回答。引用时注明片段编号；片段不足时明确说明。",
    ])
    return [
        {"role": "system", "content": "\n".join(system_parts)},
        {"role": "user", "content": question},
    ]


def stream_chat(messages: list[dict], on_delta, timeout: float = 60.0) -> str:
    """OpenAI 兼容流式补全。on_delta(str) 接收每个增量；返回完整文本。"""
    cfg = load_config()
    if not cfg.get("api_key"):
        raise RuntimeError("未配置 API Key（POST /api/config 设置）")
    headers = {"Authorization": f"Bearer {cfg['api_key']}"}
    payload = {"model": cfg["model"], "messages": messages, "stream": True}
    full: list[str] = []
    with httpx.Client(verify=False, timeout=timeout) as client:
        with client.stream(
            "POST", f"{cfg['base_url']}/chat/completions", json=payload, headers=headers
        ) as r:
            r.raise_for_status()
            for line in r.iter_lines():
                if not line or not line.startswith("data: "):
                    continue
                data = line[6:]
                if data.strip() == "[DONE]":
                    break
                try:
                    delta = json.loads(data)["choices"][0]["delta"].get("content", "")
                except Exception:
                    continue
                if delta:
                    full.append(delta)
                    on_delta(delta)
    return "".join(full)
