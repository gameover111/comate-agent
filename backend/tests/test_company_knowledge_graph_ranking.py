"""图谱候选进入 RRF 的受限扩展回归测试。"""

import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app.plugins.company_knowledge import retriever


SOURCE_A = "11111111-1111-1111-1111-111111111111"
SOURCE_B = "22222222-2222-2222-2222-222222222222"


def _result(rows):
    return SimpleNamespace(mappings=lambda: SimpleNamespace(all=lambda: rows))


def _candidate(chunk_id: str, source_id: str, title: str, content: str, similarity: float | None = None) -> dict:
    result = {
        "chunk_id": chunk_id,
        "chunk_set_id": f"set-{source_id[-1]}",
        "source_id": source_id,
        "title": title,
        "version": "V1.0",
        "effective_at": datetime(2026, 8, 1, tzinfo=timezone.utc),
        "section_path": "第一章",
        "content": content,
        "metadata": {},
    }
    if similarity is not None:
        result["similarity"] = similarity
    return result


class GraphRankingTests(unittest.IsolatedAsyncioTestCase):
    async def test_confirmed_one_hop_neighbor_enters_weighted_rrf_with_graph_origin(self):
        direct = _candidate("chunk-a", SOURCE_A, "差旅管理制度", "差旅报销按费用制度执行。", 0.91)
        neighbor = _candidate("chunk-b", SOURCE_B, "费用报销制度", "住宿报销需要发票和行程单。", 0.48)

        async def fake_execute(statement, params=None):
            sql = str(statement)
            if "ROW_NUMBER() OVER" in sql:
                self.assertEqual(params["per_source_limit"], 2)
                self.assertEqual(params["top_k"], 6)
                self.assertEqual(params["graph_source_ids"], [SOURCE_B])
                return _result([neighbor])
            if "ORDER BY chunk.embedding" in sql:
                return _result([direct])
            return _result([direct, neighbor])

        db = SimpleNamespace(execute=fake_execute)
        confirmed_relation = {
            "source_id": SOURCE_A,
            "target_source_id": SOURCE_B,
            "relation_type": "cite",
            "relation_label": "引用",
            "status": "confirmed",
        }

        with (
            patch.object(retriever, "get_embedding", AsyncMock(return_value=[0.1, 0.2])),
            patch.object(retriever, "_keyword_rank", return_value=["chunk-a"]),
            patch.object(retriever, "is_graph_ranking_enabled", return_value=True),
            patch.object(retriever, "get_confirmed_relations", AsyncMock(return_value=[confirmed_relation])),
        ):
            chunks = await retriever.retrieve_company_knowledge(
                "差旅报销需要什么票据？", "policy", db, top_k=2
            )

        self.assertEqual([item.chunk_id for item in chunks], ["chunk-a", "chunk-b"])
        self.assertEqual(chunks[0].retrieval_origin, "direct")
        self.assertEqual(chunks[1].retrieval_origin, "graph")
        self.assertEqual(chunks[1].graph_relation_type, "cite")
        self.assertEqual(chunks[1].to_citation()["graph_relation_label"], "引用")

    async def test_draft_relation_is_rejected_even_if_upstream_returns_it(self):
        relation = {
            "source_id": SOURCE_A,
            "target_source_id": SOURCE_B,
            "relation_type": "cite",
            "relation_label": "引用",
            "status": "draft",
        }
        db = AsyncMock()
        with patch.object(retriever, "get_confirmed_relations", AsyncMock(return_value=[relation])):
            neighbors = await retriever._get_graph_neighbor_relations(db, [SOURCE_A])

        self.assertEqual(neighbors, {})
        db.execute.assert_not_called()

    async def test_graph_candidate_uses_one_bounded_slot_without_displacing_direct_main_chain(self):
        direct_rows = [
            _candidate(
                f"chunk-{index}",
                SOURCE_A,
                f"直接命中文档-{index}",
                f"请假制度直接证据 {index}",
                0.8 - index * 0.01,
            )
            for index in range(1, 7)
        ]
        neighbor = _candidate(
            "chunk-graph",
            SOURCE_B,
            "关联补充制度",
            "关联资料中的补充要求。",
            0.42,
        )

        async def fake_execute(statement, params=None):
            sql = str(statement)
            if "ROW_NUMBER() OVER" in sql:
                return _result([neighbor])
            if "ORDER BY chunk.embedding" in sql:
                return _result(direct_rows)
            return _result([*direct_rows, neighbor])

        db = SimpleNamespace(execute=fake_execute)
        confirmed_relation = {
            "source_id": SOURCE_A,
            "target_source_id": SOURCE_B,
            "relation_type": "cite",
            "relation_label": "引用",
            "status": "confirmed",
        }
        direct_ids = [row["chunk_id"] for row in direct_rows]
        with (
            patch.object(retriever, "get_embedding", AsyncMock(return_value=[0.1, 0.2])),
            patch.object(retriever, "_keyword_rank", return_value=direct_ids),
            patch.object(retriever, "is_graph_ranking_enabled", return_value=True),
            patch.object(retriever, "get_confirmed_relations", AsyncMock(return_value=[confirmed_relation])),
        ):
            chunks = await retriever.retrieve_company_knowledge("请假制度", "policy", db, top_k=6)

        self.assertEqual([item.chunk_id for item in chunks], [*direct_ids[:5], "chunk-graph"])
        self.assertTrue(all(item.retrieval_origin == "direct" for item in chunks[:5]))
        self.assertEqual(chunks[-1].retrieval_origin, "graph")
        self.assertEqual(chunks[-1].graph_relation_label, "引用")

    async def test_graph_switch_off_preserves_the_direct_retrieval_path(self):
        direct = _candidate("chunk-a", SOURCE_A, "差旅管理制度", "差旅报销按费用制度执行。", 0.91)

        async def fake_execute(statement, params=None):
            if "ORDER BY chunk.embedding" in str(statement):
                return _result([direct])
            return _result([direct])

        db = SimpleNamespace(execute=fake_execute)
        with (
            patch.object(retriever, "get_embedding", AsyncMock(return_value=[0.1, 0.2])),
            patch.object(retriever, "_keyword_rank", return_value=["chunk-a"]),
            patch.object(retriever, "is_graph_ranking_enabled", return_value=False),
            patch.object(retriever, "get_confirmed_relations", AsyncMock()) as graph,
        ):
            chunks = await retriever.retrieve_company_knowledge("差旅报销", "policy", db, top_k=1)

        self.assertEqual([item.chunk_id for item in chunks], ["chunk-a"])
        self.assertEqual(chunks[0].retrieval_origin, "direct")
        graph.assert_not_awaited()


class GraphRrfTests(unittest.TestCase):
    def test_graph_weight_cannot_override_a_top_direct_hit(self):
        from app.plugins.company_knowledge.keyword_retriever import rrf_merge_weighted

        merged = rrf_merge_weighted(
            [(["direct"], 1.0), (["direct"], 1.0), (["graph"], 0.65)],
            allowed_ids={"direct", "graph"},
            limit=2,
        )

        self.assertEqual(merged, ["direct", "graph"])


if __name__ == "__main__":
    unittest.main()
