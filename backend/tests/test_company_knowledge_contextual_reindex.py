"""Contextual Retrieval 向量化与受控重建回归测试。"""

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from app.plugins.company_knowledge import service


def _rows(items):
    return SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: items))


class ContextualEmbeddingIndexTests(unittest.IsolatedAsyncioTestCase):
    def _source(self):
        return SimpleNamespace(id="source-1", status="published", knowledge_type="policy")

    def _chunk_set(self):
        return SimpleNamespace(
            id="set-1",
            mode="auto",
            status="confirmed",
            rule_snapshot={},
            indexed_chunks=0,
            indexed_at=None,
        )

    def _chunk(self, *, description=None):
        metadata = {} if description is None else {"contextual_description": description}
        return SimpleNamespace(
            id="chunk-1",
            content="员工应提前三个工作日申请年假。",
            metadata_=metadata,
            embedding=None,
            status="draft",
        )

    async def test_index_uses_contextual_template_and_records_each_profile(self):
        source = self._source()
        chunk_set = self._chunk_set()
        contextual_chunk = self._chunk(description="《员工休假制度》中的年假申请条款。")
        fallback_chunk = SimpleNamespace(
            id="chunk-2",
            content="事假应说明请假原因。",
            metadata_={},
            embedding=None,
            status="draft",
        )
        db = SimpleNamespace(
            execute=AsyncMock(return_value=_rows([contextual_chunk, fallback_chunk])),
            add=Mock(),
            commit=AsyncMock(),
            refresh=AsyncMock(),
            rollback=AsyncMock(),
            get=AsyncMock(),
        )

        with (
            patch.object(service, "_get_source", AsyncMock(return_value=source)),
            patch.object(service, "_get_chunk_set", AsyncMock(return_value=chunk_set)),
            patch.object(service, "is_contextual_embedding_enabled", return_value=True),
            patch.object(service, "get_embeddings_batch", AsyncMock(return_value=[[0.1], [0.2]])) as embed,
        ):
            result = await service.index_chunk_set(
                db, source_id="source-1", chunk_set_id="set-1", admin_id="admin-1"
            )

        self.assertIs(result, chunk_set)
        embed.assert_awaited_once_with(
            [
                "上下文描述：《员工休假制度》中的年假申请条款。\n\n分片正文：员工应提前三个工作日申请年假。",
                "事假应说明请假原因。",
            ]
        )
        self.assertEqual(contextual_chunk.metadata_["embedding_profile"], "contextual_v1")
        self.assertEqual(fallback_chunk.metadata_["embedding_profile"], "content_only")
        self.assertEqual(
            chunk_set.rule_snapshot["embedding"]["profiles"],
            {"contextual_v1": 1, "content_only": 1},
        )
        job = db.add.call_args.args[0]
        self.assertEqual(job.job_type, "index")
        self.assertEqual(job.request_snapshot["embedding_profiles"], {"contextual_v1": 1, "content_only": 1})

    async def test_index_keeps_content_only_when_contextual_feature_is_disabled(self):
        source = self._source()
        chunk_set = self._chunk_set()
        chunk = self._chunk(description="本段属于年假申请流程。")
        db = SimpleNamespace(
            execute=AsyncMock(return_value=_rows([chunk])),
            add=Mock(),
            commit=AsyncMock(),
            refresh=AsyncMock(),
            rollback=AsyncMock(),
            get=AsyncMock(),
        )

        with (
            patch.object(service, "_get_source", AsyncMock(return_value=source)),
            patch.object(service, "_get_chunk_set", AsyncMock(return_value=chunk_set)),
            patch.object(service, "is_contextual_embedding_enabled", return_value=False),
            patch.object(service, "get_embeddings_batch", AsyncMock(return_value=[[0.1]])) as embed,
        ):
            await service.index_chunk_set(
                db, source_id="source-1", chunk_set_id="set-1", admin_id="admin-1"
            )

        embed.assert_awaited_once_with(["员工应提前三个工作日申请年假。"])
        self.assertEqual(chunk.metadata_["embedding_profile"], "content_only")


class ContextualReindexTests(unittest.IsolatedAsyncioTestCase):
    def _source(self):
        return SimpleNamespace(
            id="source-1",
            status="published",
            active_chunk_set_id="old-set",
            knowledge_type="policy",
            markdown_version=3,
        )

    def _active_chunk(self):
        return SimpleNamespace(
            chunk_index=0,
            section_path="休假 / 年假",
            content="员工应提前三个工作日申请年假。",
            content_hash="content-hash",
            token_count=16,
            metadata_={"contextual_description": "《员工休假制度》的年假申请规则。"},
        )

    async def test_reindex_clones_active_chunks_and_switches_only_after_success(self):
        source = self._source()
        old_set = SimpleNamespace(id="old-set", status="published")
        new_set = SimpleNamespace(id="new-set", status="confirmed", rule_snapshot={})
        db = SimpleNamespace(
            get=AsyncMock(return_value=old_set),
            execute=AsyncMock(return_value=_rows([self._active_chunk()])),
            add=Mock(),
            add_all=Mock(),
            flush=AsyncMock(),
            commit=AsyncMock(),
            refresh=AsyncMock(),
        )

        with (
            patch.object(service, "_get_source", AsyncMock(return_value=source)),
            patch.object(service, "is_contextual_embedding_enabled", return_value=True),
            patch.object(service, "CompanyKnowledgeChunkSet", return_value=new_set),
            patch.object(service, "index_chunk_set", AsyncMock(return_value=new_set)) as index,
        ):
            returned_source, returned_set = await service.reindex_company_source(
                db, "source-1", "admin-1"
            )

        self.assertIs(returned_source, source)
        self.assertIs(returned_set, new_set)
        index.assert_awaited_once_with(
            db, source_id="source-1", chunk_set_id="new-set", admin_id="admin-1"
        )
        cloned_chunk = db.add_all.call_args.args[0][0]
        self.assertEqual(cloned_chunk.content, "员工应提前三个工作日申请年假。")
        self.assertEqual(cloned_chunk.metadata_["contextual_description"], "《员工休假制度》的年假申请规则。")
        self.assertEqual(old_set.status, "superseded")
        self.assertEqual(new_set.status, "published")
        self.assertEqual(source.active_chunk_set_id, "new-set")

    async def test_reindex_failure_preserves_old_active_set(self):
        source = self._source()
        old_set = SimpleNamespace(id="old-set", status="published")
        new_set = SimpleNamespace(id="new-set", status="confirmed", rule_snapshot={})
        db = SimpleNamespace(
            get=AsyncMock(return_value=old_set),
            execute=AsyncMock(return_value=_rows([self._active_chunk()])),
            add=Mock(),
            add_all=Mock(),
            flush=AsyncMock(),
            commit=AsyncMock(),
            refresh=AsyncMock(),
        )

        with (
            patch.object(service, "_get_source", AsyncMock(return_value=source)),
            patch.object(service, "is_contextual_embedding_enabled", return_value=True),
            patch.object(service, "CompanyKnowledgeChunkSet", return_value=new_set),
            patch.object(
                service,
                "index_chunk_set",
                AsyncMock(side_effect=service.CompanyKnowledgeServiceError("向量化失败")),
            ),
        ):
            with self.assertRaisesRegex(service.CompanyKnowledgeServiceError, "向量化失败"):
                await service.reindex_company_source(db, "source-1", "admin-1")

        self.assertEqual(old_set.status, "published")
        self.assertEqual(new_set.status, "confirmed")
        self.assertEqual(source.active_chunk_set_id, "old-set")


if __name__ == "__main__":
    unittest.main()
