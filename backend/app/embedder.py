"""Embedding 服务（Week 4，R4）：pluggable 接口 + 当前实现

实现决策（2026-08-16）：本机网络环境无法获取 bge-small-zh ONNX 模型
（huggingface.co 不可达 / hf-mirror 404 / modelscope 500，均已 curl -k 尝试），
故先用 **numpy TF-IDF 兜底**（纯离线、仅依赖已装的 numpy，不引入 sklearn 大依赖）。

接口约定：
  fit(texts) / embed_texts(texts) -> np.ndarray shape (n, DIM)
向量维度固定 512，与 bge-small-zh-v1.5 输出维度一致——后续在可联网环境下载
ONNX 模型后，只需把 embed_texts 分发切到 BGE 实现（R4：中文检索质量更高）。
"""

from __future__ import annotations

import math
import re
import unicodedata
from collections import Counter

import jieba
import numpy as np

# 主动初始化 jieba（其字典加载为惰性过程，若首次 lcut 发生在后台线程，
# 主线程查询时可能读到未就绪状态——这里在导入期一次性预热，规避线程竞争）
jieba.initialize()

DIM = 512  # 与 bge-small-zh-v1.5 输出维度一致，便于后续无缝替换

# 停用词（功能词/口语词，分词后过滤——避免"怎么样/的/了"干扰检索）
_STOPWORDS = {
    "的", "了", "吗", "吧", "呢", "啊", "哦", "嗯", "哈", "哈哈", "哈哈哈",
    "是", "在", "和", "就", "都", "也", "很", "有", "要", "会", "能", "没", "不",
    "这", "那", "一个", "怎么", "怎么样", "什么", "真的", "还是", "一下", "已经",
    "可以", "我们", "你们", "他们", "自己", "没有",
}

_CJK_RUN = re.compile(r"[\u4e00-\u9fff]+")


def _tokenize(text: str) -> list[str]:
    """CJK 字符 n-gram 分词（单字 + 相邻双字），过滤停用词。

    仅保留 CJK：真实聊天内容含大量 ASCII 噪声（\\xHH 转义 xfd/xcc、wxid_xxx、
    URL/哈希/0k），任何 ASCII 通道都会污染 top-512 词表（已有两轮正则实证）。
    中文聊天检索的核心内容全在 CJK；英文词（ok/hello/api）对兜底 TF-IDF 收益低，
    多语言由后续 bge 阶段承担。字符 n-gram 是中文 IR 无词典的标准兜底做法。
    """
    text = unicodedata.normalize("NFKC", text or "").lower()
    tokens: list[str] = []
    for seg in _CJK_RUN.findall(text):
        for ch in seg:
            if ch not in _STOPWORDS:
                tokens.append(ch)
        for i in range(len(seg) - 1):
            bigram = seg[i : i + 2]
            if bigram not in _STOPWORDS:
                tokens.append(bigram)
    return tokens


class TfidfEmbedder:
    """numpy TF-IDF 兜底 embedding（词袋 + IDF + L2 归一化，稠密 DIM 维）。

    min_df=1：小语料也能建立词表（否则仅 2-3 条消息时词表可能为空）；
    大语料不受影响——词表按词频取 top-DIM 截断。
    """

    def __init__(self, dim: int = DIM, min_df: int = 1):
        self.dim = dim
        self.min_df = min_df
        self.vocab: dict[str, int] = {}
        self.idf: np.ndarray | None = None

    def fit(self, texts) -> "TfidfEmbedder":
        """拟合词表。texts 支持 list 或任意迭代器（生成器可避免大语料驻留内存）。

        词表策略（中文 IR 字符 n-gram 的标准做法）：双字优先（判别力强：
        晚安/今天/哈哈），单字补充覆盖（我/你）。防止 512 槽位被高频单字占满、
        查询双字词落空、向量退化为低区分度。
        """
        df: Counter[str] = Counter()
        n = 0
        for t in texts:
            df.update(set(_tokenize(t)))
            n += 1
        cand = [(w, c) for w, c in df.items() if c >= self.min_df]
        bigrams = sorted((w for w, _ in cand if len(w) == 2), key=lambda w: -df[w])
        singles = sorted((w for w, _ in cand if len(w) == 1), key=lambda w: -df[w])
        nb = int(self.dim * 0.75)  # 75% 槽位给双字
        self.vocab = {w: i for i, w in enumerate(bigrams[:nb])}
        # 单字从实际已用槽位继续编号（勿硬编码 nb+i：词表不满 512 时会造成
        # 编号空洞，idf 长度 < max index，embed 时越界——小语料必现）
        next_i = len(self.vocab)
        for w in singles[: self.dim - next_i]:
            self.vocab[w] = next_i
            next_i += 1
        self.idf = np.array(
            [math.log((1 + n) / (1 + df[w])) + 1.0 for w in self.vocab],
            dtype=np.float32,
        )
        return self

    def embed_texts(self, texts: list[str]) -> np.ndarray:
        if self.idf is None:
            raise RuntimeError("TfidfEmbedder 需先 fit()")
        out = np.zeros((len(texts), len(self.vocab)), dtype=np.float32)
        for i, t in enumerate(texts):
            for w, c in Counter(_tokenize(t)).items():
                j = self.vocab.get(w)
                if j is not None:
                    out[i, j] = c * self.idf[j]
        norms = np.linalg.norm(out, axis=1, keepdims=True)
        norms[norms == 0] = 1.0
        return out / norms


# ---------------- 全局单例与分发（后续切 BGE 只改这里） ----------------
_embedder: TfidfEmbedder | None = None
_fitted = False


def get_embedder() -> TfidfEmbedder:
    global _embedder
    if _embedder is None:
        _embedder = TfidfEmbedder(dim=DIM)
    return _embedder


def fit(texts: list[str]) -> None:
    global _fitted
    get_embedder().fit(texts)
    _fitted = True


def embed_texts(texts: list[str]) -> np.ndarray:
    """统一入口：返回 (n, DIM) 归一化向量。未 fit 时自动 fit（空语料则退化）。"""
    if not _fitted and texts:
        fit(texts)
    if not _fitted:
        return np.zeros((len(texts), DIM), dtype=np.float32)
    v = get_embedder().embed_texts(texts)
    if v.shape[1] < DIM:  # 词表不足 DIM 时补零到统一维度
        v = np.hstack([v, np.zeros((v.shape[0], DIM - v.shape[1]), dtype=np.float32)])
    elif v.shape[1] > DIM:
        v = v[:, :DIM]
    return v
