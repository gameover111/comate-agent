"""公司知识的权限过滤与混合检索（BM25 关键词 + pgvector 向量 + RRF 融合）。"""

from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.plugins.company_knowledge.graph_service import get_confirmed_relations
from app.plugins.company_knowledge.keyword_retriever import BM25Index, rrf_merge, rrf_merge_weighted
from app.plugins.company_knowledge.registry import is_graph_ranking_enabled, is_query_enabled
from app.services.embedding_service import get_embedding


DEFAULT_TOP_K = 6
MIN_SIMILARITY = 0.35
# User questions may use evidence just below the stricter validation threshold.
MIN_USER_QUERY_SIMILARITY = 0.34
# 混合检索的候选池宽度：向量与 BM25 各自召回的数量，随后 RRF 融合取最终 top_k。
CANDIDATE_TOP_K = 20
# 图谱候选只补足直接命中遗漏的跨资料内容，不能替代基础检索。
GRAPH_SEED_TOP_K = 6
GRAPH_CANDIDATES_PER_SOURCE = 2
GRAPH_CANDIDATE_TOP_K = 6
# 图谱增强只占用受限的补位槽，确保直接检索仍是回答证据的主体。
GRAPH_RESULT_SLOT_LIMIT = 1
GRAPH_MIN_SIMILARITY = 0.25
GRAPH_RELATION_WEIGHTS = {
    "cite": 0.65,
    "parent": 0.55,
    "related": 0.45,
    "supersede": 0.35,
}


class RetrievalError(RuntimeError):
    pass


@dataclass(frozen=True)
class RetrievedChunk:
    chunk_id: str
    source_id: str
    title: str
    version: str
    effective_at: str | None
    section_path: str
    content: str
    similarity: float
    chunk_set_id: str | None = None
    contextual_description: str | None = None
    retrieval_origin: str = "direct"
    graph_relation_type: str | None = None
    graph_relation_label: str | None = None
    graph_seed_source_id: str | None = None

    def to_citation(self) -> dict:
        citation = {
            "source_id": self.source_id,
            "chunk_id": self.chunk_id,
            "chunk_set_id": self.chunk_set_id,
            "title": self.title,
            "version": self.version,
            "effective_at": self.effective_at,
            "section_path": self.section_path,
            "excerpt": self.content[:240],
            "similarity": round(self.similarity, 4),
            "retrieval_origin": self.retrieval_origin,
        }
        if self.retrieval_origin == "graph":
            citation.update(
                {
                    "graph_relation_type": self.graph_relation_type,
                    "graph_relation_label": self.graph_relation_label,
                    "graph_seed_source_id": self.graph_seed_source_id,
                }
            )
        return citation

    def to_preview(self) -> dict:
        return {
            "chunk_id": self.chunk_id,
            "chunk_set_id": self.chunk_set_id,
            "section_path": self.section_path,
            "content": self.content,
            "similarity": round(self.similarity, 4),
            "meets_minimum_similarity": self.similarity >= MIN_SIMILARITY,
            "retrieval_origin": self.retrieval_origin,
        }


async def retrieve_company_knowledge(
    question: str,
    knowledge_type: str,
    db: AsyncSession,
    *,
    top_k: int = DEFAULT_TOP_K,
) -> list[RetrievedChunk]:
    if not is_query_enabled(knowledge_type):
        raise RetrievalError("该资料类型暂未启用查询")

    now = datetime.now(timezone.utc)
    candidates = await _load_published_candidates(db, knowledge_type, now)
    query_vector = await _get_query_vector(question)
    vector_ranked, vector_similarity = await _vector_rank(
        db,
        question,
        top_k=CANDIDATE_TOP_K,
        where_clause=PUBLISHED_WHERE,
        params={"knowledge_type": knowledge_type, "now": now},
        query_vector=query_vector,
    )
    keyword_ranked = _keyword_rank(question, candidates)
    base_fused_ids = rrf_merge(vector_ranked, keyword_ranked, limit=top_k)
    direct_ids = _filter_direct_result_ids(base_fused_ids, vector_similarity)
    if not is_graph_ranking_enabled(knowledge_type):
        return _build_retrieved_chunks(candidates, direct_ids, vector_similarity)

    graph_rankings, graph_metadata, graph_similarity = await _build_graph_rankings(
        db,
        seed_source_ids=[
            candidates[item]["source_id"]
            for item in direct_ids[:GRAPH_SEED_TOP_K]
            if item in candidates
        ],
        direct_candidate_ids=set(vector_ranked) | set(keyword_ranked),
        query_vector=query_vector,
        knowledge_type=knowledge_type,
        now=now,
    )
    graph_ids = {chunk_id for ranked_ids, _ in graph_rankings for chunk_id in ranked_ids}
    fused_ids = rrf_merge_weighted(
        [(vector_ranked, 1.0), (keyword_ranked, 1.0), *graph_rankings],
        # 先取完整的受限竞争集，再按结果槽位保留直接检索主链路。
        limit=len(set(direct_ids) | graph_ids),
        allowed_ids=set(direct_ids) | graph_ids,
    )
    result_ids = _select_graph_augmented_result_ids(
        fused_ids,
        direct_ids=set(direct_ids),
        graph_ids=graph_ids,
        top_k=top_k,
    )
    similarity = {**vector_similarity, **graph_similarity}
    return _build_retrieved_chunks(candidates, result_ids, similarity, graph_metadata=graph_metadata)


async def preview_company_knowledge_chunk_set(
    question: str,
    db: AsyncSession,
    *,
    source_id: str,
    chunk_set_id: str,
    top_k: int = DEFAULT_TOP_K,
) -> list[RetrievedChunk]:
    """管理员发布前验证指定分片集（混合检索），不要求资料已发布。"""
    candidates = await _load_chunk_set_candidates(db, source_id, chunk_set_id)
    vector_ranked, vector_similarity = await _vector_rank(
        db,
        question,
        top_k=CANDIDATE_TOP_K,
        where_clause=CHUNK_SET_WHERE,
        params={"source_id": source_id, "chunk_set_id": chunk_set_id},
    )
    keyword_ranked = _keyword_rank(question, candidates)
    fused_ids = rrf_merge(vector_ranked, keyword_ranked, limit=top_k)

    chunks = []
    for chunk_id in fused_ids:
        candidate = candidates.get(chunk_id)
        if not candidate:
            continue
        similarity = vector_similarity.get(chunk_id, 0.0)
        chunks.append(_to_retrieved_chunk(candidate, similarity))
    return chunks


# 用户端：已发布且生效、属于 active chunk_set 的分片（向量与 BM25 共用的过滤条件）
PUBLISHED_WHERE = (
    "source.knowledge_type = :knowledge_type"
    " AND source.status = 'published'"
    " AND source.access_scope = 'all_users'"
    " AND source.effective_at <= :now"
    " AND (source.expires_at IS NULL OR source.expires_at > :now)"
    " AND source.active_chunk_set_id = chunk_set.id"
    " AND chunk_set.status IN ('indexed', 'validated', 'published')"
    " AND chunk.status = 'indexed'"
    " AND chunk.embedding IS NOT NULL"
)

# 管理端：指定资料的分片版本（验证预览用）
CHUNK_SET_WHERE = (
    "source.id = CAST(:source_id AS uuid)"
    " AND chunk_set.id = CAST(:chunk_set_id AS uuid)"
    " AND chunk_set.status IN ('indexed', 'validated')"
    " AND chunk.status = 'indexed'"
    " AND chunk.embedding IS NOT NULL"
)


async def _load_published_candidates(
    db: AsyncSession,
    knowledge_type: str,
    now: datetime,
) -> dict[str, dict]:
    """加载已发布且生效、属于 active chunk_set 的全部分片，供 BM25 索引构建。"""
    result = await db.execute(
        text(
            f"""
            SELECT
                chunk.id AS chunk_id,
                chunk_set.id AS chunk_set_id,
                source.id AS source_id,
                source.title,
                source.version,
                source.effective_at,
                chunk.section_path,
                chunk.content,
                chunk.metadata
            FROM company_knowledge_chunks AS chunk
            JOIN company_knowledge_sources AS source ON source.id = chunk.source_id
            JOIN company_knowledge_chunk_sets AS chunk_set ON chunk_set.id = chunk.chunk_set_id
            WHERE {PUBLISHED_WHERE}
            """
        ),
        {"knowledge_type": knowledge_type, "now": now},
    )
    candidates: dict[str, dict] = {}
    for row in result.mappings().all():
        candidates[str(row["chunk_id"])] = _candidate_from_row(row)
    return candidates


async def _load_chunk_set_candidates(
    db: AsyncSession,
    source_id: str,
    chunk_set_id: str,
) -> dict[str, dict]:
    result = await db.execute(
        text(
            f"""
            SELECT
                chunk.id AS chunk_id,
                chunk_set.id AS chunk_set_id,
                source.id AS source_id,
                source.title,
                source.version,
                source.effective_at,
                chunk.section_path,
                chunk.content,
                chunk.metadata
            FROM company_knowledge_chunks AS chunk
            JOIN company_knowledge_sources AS source ON source.id = chunk.source_id
            JOIN company_knowledge_chunk_sets AS chunk_set ON chunk_set.id = chunk.chunk_set_id
            WHERE {CHUNK_SET_WHERE}
            """
        ),
        {"source_id": source_id, "chunk_set_id": chunk_set_id},
    )
    candidates: dict[str, dict] = {}
    for row in result.mappings().all():
        candidates[str(row["chunk_id"])] = _candidate_from_row(row)
    return candidates


def _candidate_from_row(row) -> dict:
    effective_at = row["effective_at"]
    metadata = row["metadata"] or {}
    return {
        "chunk_id": str(row["chunk_id"]),
        "chunk_set_id": str(row["chunk_set_id"]),
        "source_id": str(row["source_id"]),
        "title": row["title"],
        "version": row["version"],
        "effective_at": effective_at.date().isoformat() if effective_at else None,
        "section_path": row["section_path"] or "",
        "content": row["content"],
        "contextual_description": metadata.get("contextual_description"),
    }


def _keyword_rank(question: str, candidates: dict[str, dict]) -> list[str]:
    if not candidates:
        return []
    index = BM25Index([(chunk_id, candidate["content"]) for chunk_id, candidate in candidates.items()])
    return [hit.chunk_id for hit in index.search(question, top_k=CANDIDATE_TOP_K)]


async def _vector_rank(
    db: AsyncSession,
    question: str,
    *,
    top_k: int,
    where_clause: str,
    params: dict,
    query_vector: list[float] | None = None,
) -> tuple[list[str], dict[str, float]]:
    """向量召回 Top-K，返回 (按相似度降序的 chunk_id 列表, chunk_id -> similarity)。

    where_clause 必须与对应候选加载的过滤条件完全一致，保证向量与 BM25 候选池相同。
    """
    vector = query_vector or await _get_query_vector(question)
    vector_literal = _vector_literal(vector)
    query_params = dict(params)
    query_params.update({"query_vector": vector_literal, "top_k": top_k})
    result = await db.execute(
        text(
            f"""
            SELECT
                chunk.id AS chunk_id,
                1 - (chunk.embedding <=> CAST(:query_vector AS vector)) AS similarity
            FROM company_knowledge_chunks AS chunk
            JOIN company_knowledge_sources AS source ON source.id = chunk.source_id
            JOIN company_knowledge_chunk_sets AS chunk_set ON chunk_set.id = chunk.chunk_set_id
            WHERE {where_clause}
            ORDER BY chunk.embedding <=> CAST(:query_vector AS vector)
            LIMIT :top_k
            """
        ),
        query_params,
    )
    ranked: list[str] = []
    similarity_map: dict[str, float] = {}
    for row in result.mappings().all():
        chunk_id = str(row["chunk_id"])
        similarity = float(row["similarity"] or 0)
        ranked.append(chunk_id)
        similarity_map[chunk_id] = similarity
    return ranked, similarity_map


async def _get_query_vector(question: str) -> list[float]:
    vector = await get_embedding(question)
    if not vector:
        raise RetrievalError("暂时无法生成查询向量")
    return vector


def _vector_literal(vector: list[float]) -> str:
    return "[" + ",".join(str(value) for value in vector) + "]"


def _filter_direct_result_ids(
    chunk_ids: list[str],
    vector_similarity: dict[str, float],
) -> list[str]:
    """保持旧链路的低相似度过滤规则。"""
    return [
        chunk_id
        for chunk_id in chunk_ids
        if not (
            vector_similarity.get(chunk_id, 0.0) < MIN_USER_QUERY_SIMILARITY
            and chunk_id in vector_similarity
        )
    ]


def _select_graph_augmented_result_ids(
    fused_ids: list[str],
    *,
    direct_ids: set[str],
    graph_ids: set[str],
    top_k: int,
) -> list[str]:
    """在统一 RRF 顺序内为图谱增强保留极小补位，直接检索始终优先。

    图谱候选只有一个按关系类型衰减后的排序通道，直接候选至少有 BM25 或
    向量通道；标准 RRF 在候选较多时会让低权图谱通道永远落在 Top-K 之外。
    因此仅在图谱开关开启且候选通过相似度门槛后，最多让其占用一个结果位。
    其余结果位全部来自原 BM25 + 向量主链路，且开关关闭时不会调用本函数。
    """
    direct_ranked = [chunk_id for chunk_id in fused_ids if chunk_id in direct_ids]
    graph_ranked = [chunk_id for chunk_id in fused_ids if chunk_id in graph_ids]
    if top_k <= 0:
        return []
    if not graph_ranked or top_k == 1:
        return direct_ranked[:top_k]

    graph_slots = min(GRAPH_RESULT_SLOT_LIMIT, len(graph_ranked), top_k - 1)
    direct_slots = top_k - graph_slots
    # 直接资料先呈现，图谱资料作为末位的补充证据，便于回答模型和前端解释来源。
    return direct_ranked[:direct_slots] + graph_ranked[:graph_slots]


async def _build_graph_rankings(
    db: AsyncSession,
    *,
    seed_source_ids: list[str],
    direct_candidate_ids: set[str],
    query_vector: list[float],
    knowledge_type: str,
    now: datetime,
) -> tuple[list[tuple[list[str], float]], dict[str, dict], dict[str, float]]:
    """构建一跳、已确认、受限的图谱候选排序通道。"""
    neighbor_relations = await _get_graph_neighbor_relations(db, seed_source_ids)
    if not neighbor_relations:
        return [], {}, {}
    ranked_rows = await _graph_vector_rank(
        db,
        query_vector=query_vector,
        source_ids=sorted(neighbor_relations),
        knowledge_type=knowledge_type,
        now=now,
    )
    by_weight: dict[float, list[str]] = {}
    metadata: dict[str, dict] = {}
    similarity: dict[str, float] = {}
    for row in ranked_rows:
        chunk_id = str(row["chunk_id"])
        if chunk_id in direct_candidate_ids or float(row["similarity"] or 0.0) < GRAPH_MIN_SIMILARITY:
            continue
        relation = neighbor_relations.get(str(row["source_id"]))
        if not relation:
            continue
        by_weight.setdefault(relation["weight"], []).append(chunk_id)
        metadata[chunk_id] = relation
        similarity[chunk_id] = float(row["similarity"] or 0.0)
    rankings = [(chunk_ids, weight) for weight, chunk_ids in sorted(by_weight.items(), reverse=True)]
    return rankings, metadata, similarity


async def _get_graph_neighbor_relations(
    db: AsyncSession,
    seed_source_ids: list[str],
) -> dict[str, dict]:
    seed_ids = set(seed_source_ids)
    if not seed_ids:
        return {}
    relations = await get_confirmed_relations(db, sorted(seed_ids))
    neighbors: dict[str, dict] = {}
    for relation in relations:
        # 即使上游查询逻辑未来变更，也不允许未确认关系进入排序。
        if relation.get("status") != "confirmed":
            continue
        source_id = relation["source_id"]
        target_id = relation["target_source_id"]
        if source_id in seed_ids and target_id not in seed_ids:
            neighbor_id, seed_id = target_id, source_id
        elif target_id in seed_ids and source_id not in seed_ids:
            neighbor_id, seed_id = source_id, target_id
        else:
            continue
        weight = GRAPH_RELATION_WEIGHTS.get(relation["relation_type"])
        if weight is None:
            continue
        item = {
            "weight": weight,
            "relation_type": relation["relation_type"],
            "relation_label": relation.get("relation_label") or relation["relation_type"],
            "graph_seed_source_id": seed_id,
        }
        current = neighbors.get(neighbor_id)
        if not current or (item["weight"], item["relation_type"], item["graph_seed_source_id"]) > (
            current["weight"], current["relation_type"], current["graph_seed_source_id"]
        ):
            neighbors[neighbor_id] = item
    return neighbors


async def _graph_vector_rank(
    db: AsyncSession,
    *,
    query_vector: list[float],
    source_ids: list[str],
    knowledge_type: str,
    now: datetime,
) -> list[dict]:
    """按查询相似度为每个关联资料取少量活跃分片。"""
    if not source_ids:
        return []
    result = await db.execute(
        text(
            f"""
            SELECT chunk_id, source_id, similarity
            FROM (
                SELECT
                    chunk.id AS chunk_id,
                    source.id AS source_id,
                    1 - (chunk.embedding <=> CAST(:query_vector AS vector)) AS similarity,
                    ROW_NUMBER() OVER (
                        PARTITION BY source.id
                        ORDER BY chunk.embedding <=> CAST(:query_vector AS vector)
                    ) AS source_rank
                FROM company_knowledge_chunks AS chunk
                JOIN company_knowledge_sources AS source ON source.id = chunk.source_id
                JOIN company_knowledge_chunk_sets AS chunk_set ON chunk_set.id = chunk.chunk_set_id
                WHERE {PUBLISHED_WHERE}
                  AND source.id = ANY(CAST(:graph_source_ids AS uuid[]))
            ) AS graph_candidates
            WHERE source_rank <= :per_source_limit
            ORDER BY similarity DESC, chunk_id ASC
            LIMIT :top_k
            """
        ),
        {
            "knowledge_type": knowledge_type,
            "now": now,
            "query_vector": _vector_literal(query_vector),
            "graph_source_ids": source_ids,
            "per_source_limit": GRAPH_CANDIDATES_PER_SOURCE,
            "top_k": GRAPH_CANDIDATE_TOP_K,
        },
    )
    return [dict(row) for row in result.mappings().all()]


def _build_retrieved_chunks(
    candidates: dict[str, dict],
    chunk_ids: list[str],
    similarity: dict[str, float],
    *,
    graph_metadata: dict[str, dict] | None = None,
) -> list[RetrievedChunk]:
    return [
        _to_retrieved_chunk(
            candidates[chunk_id],
            similarity.get(chunk_id, 0.0),
            graph_metadata=(graph_metadata or {}).get(chunk_id),
        )
        for chunk_id in chunk_ids
        if chunk_id in candidates
    ]


def _to_retrieved_chunk(
    candidate: dict,
    similarity: float,
    *,
    graph_metadata: dict | None = None,
) -> RetrievedChunk:
    return RetrievedChunk(
        chunk_id=candidate["chunk_id"],
        chunk_set_id=candidate["chunk_set_id"],
        source_id=candidate["source_id"],
        title=candidate["title"],
        version=candidate["version"],
        effective_at=candidate["effective_at"],
        section_path=candidate["section_path"],
        content=candidate["content"],
        similarity=similarity,
        contextual_description=candidate.get("contextual_description"),
        retrieval_origin="graph" if graph_metadata else "direct",
        graph_relation_type=graph_metadata.get("relation_type") if graph_metadata else None,
        graph_relation_label=graph_metadata.get("relation_label") if graph_metadata else None,
        graph_seed_source_id=graph_metadata.get("graph_seed_source_id") if graph_metadata else None,
    )
