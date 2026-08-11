"""基于 jieba 分词的应用层 BM25 关键词召回。

对应迭代文档"BM25 + 向量检索 + RRF 融合"方案中的关键词召回部分。
对已发布且生效的分片构建内存倒排索引，按 BM25 打分返回 Top-K。
"""

from dataclasses import dataclass
from functools import lru_cache

import jieba

# BM25 超参数（经典默认值）
K1 = 1.5
B = 0.75


@lru_cache(maxsize=1)
def _jieba_lazy_load() -> None:
    """预加载 jieba 词典，避免首次查询时冷启动耗时。"""
    jieba.initialize()


def _tokenize(text: str) -> list[str]:
    _jieba_lazy_load()
    return [token for token in jieba.cut(text or "") if token.strip()]


@dataclass(frozen=True)
class KeywordHit:
    chunk_id: str
    score: float


class BM25Index:
    """内存 BM25 倒排索引，按分片内容构建。"""

    def __init__(self, docs: list[tuple[str, str]]):
        # docs: [(chunk_id, content)]
        self.doc_count = len(docs)
        self.doc_lengths: list[int] = []
        self.doc_ids: list[str] = []
        self.avgdl = 0.0
        self.inverted: dict[str, list[tuple[int, int]]] = {}  # term -> [(doc_idx, freq)]

        for doc_id, content in docs:
            tokens = _tokenize(content)
            self.doc_ids.append(doc_id)
            self.doc_lengths.append(len(tokens))
            term_freq: dict[str, int] = {}
            for token in tokens:
                term_freq[token] = term_freq.get(token, 0) + 1
            for term, freq in term_freq.items():
                self.inverted.setdefault(term, []).append((len(self.doc_ids) - 1, freq))
        if self.doc_count:
            self.avgdl = sum(self.doc_lengths) / self.doc_count
        # 预计算各 term 的 doc frequency
        self.df = {term: len(postings) for term, postings in self.inverted.items()}

    def _idf(self, term: str) -> float:
        import math

        df = self.df.get(term, 0)
        return math.log(1 + (self.doc_count - df + 0.5) / (df + 0.5))

    def search(self, query: str, top_k: int = 20) -> list[KeywordHit]:
        """对查询分词后按 BM25 打分，返回 Top-K 命中。"""
        if not self.doc_count:
            return []
        query_terms = _tokenize(query)
        scores: dict[int, float] = {}
        for term in set(query_terms):
            idf = self._idf(term)
            for doc_idx, freq in self.inverted.get(term, []):
                dl = self.doc_lengths[doc_idx]
                denominator = freq + K1 * (1 - B + B * dl / self.avgdl) if self.avgdl else freq + K1
                scores[doc_idx] = scores.get(doc_idx, 0.0) + idf * (freq * (K1 + 1)) / denominator
        ranked = sorted(scores.items(), key=lambda item: (-item[1], item[0]))
        return [KeywordHit(chunk_id=self.doc_ids[doc_idx], score=score) for doc_idx, score in ranked[:top_k]]


def rrf_merge(
    vector_ranked: list[str],
    keyword_ranked: list[str],
    *,
    k: int = 60,
    limit: int = 6,
) -> list[str]:
    """Reciprocal Rank Fusion：对两路 Top-K 分片 ID 列表做排名融合。

    score(doc) = Σ 1 / (k + rank_i)
    """
    return rrf_merge_weighted(
        [(vector_ranked, 1.0), (keyword_ranked, 1.0)],
        k=k,
        limit=limit,
    )


def rrf_merge_weighted(
    rankings: list[tuple[list[str], float]],
    *,
    k: int = 60,
    limit: int = 6,
    allowed_ids: set[str] | None = None,
) -> list[str]:
    """对多路排序做带权 RRF 融合。

    ``weight`` 只用于降低补充信号（如图谱扩展）的影响；主检索的向量与
    BM25 仍保持 1.0。``allowed_ids`` 允许调用方将最终竞争范围收敛在
    已验证的基础命中及受限扩展候选中，避免扩大候选池改变原有召回语义。
    """
    fused: dict[str, float] = {}
    for ranked_ids, weight in rankings:
        if weight <= 0:
            continue
        for rank, chunk_id in enumerate(ranked_ids, start=1):
            fused[chunk_id] = fused.get(chunk_id, 0.0) + float(weight) / (k + rank)
    if allowed_ids is not None:
        fused = {chunk_id: score for chunk_id, score in fused.items() if chunk_id in allowed_ids}
    ordered = sorted(fused.items(), key=lambda item: (-item[1], item[0]))
    return [chunk_id for chunk_id, _ in ordered[:limit]]
