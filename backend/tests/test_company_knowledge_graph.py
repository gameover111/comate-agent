"""B1 文档级关系图谱：纯逻辑测试（不依赖数据库）。"""

import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app.plugins.company_knowledge.graph_service import (
    GraphServiceError,
    _system_supersede_edge,
    get_confirmed_relations,
    relation_to_dict,
    validate_relation_input,
)


class RelationValidationTests(unittest.TestCase):
    def test_accepts_valid_input(self):
        validate_relation_input(
            source_id="a",
            target_source_id="b",
            relation_type="cite",
            direction="directed",
            origin="llm",
        )

    def test_rejects_same_source_and_target(self):
        with self.assertRaises(GraphServiceError):
            validate_relation_input(
                source_id="a", target_source_id="a", relation_type="cite"
            )

    def test_rejects_unknown_relation_type(self):
        with self.assertRaises(GraphServiceError):
            validate_relation_input(
                source_id="a", target_source_id="b", relation_type="friendship"
            )

    def test_rejects_unknown_direction_and_origin(self):
        with self.assertRaises(GraphServiceError):
            validate_relation_input(
                source_id="a", target_source_id="b", relation_type="cite", direction="sideways"
            )
        with self.assertRaises(GraphServiceError):
            validate_relation_input(
                source_id="a", target_source_id="b", relation_type="cite", origin="guessed"
            )

    def test_rejects_missing_ids(self):
        with self.assertRaises(GraphServiceError):
            validate_relation_input(source_id="", target_source_id="b", relation_type="cite")


class RelationConversionTests(unittest.TestCase):
    def test_relation_to_dict(self):
        relation = SimpleNamespace(
            id="rel-1",
            source_id="src-1",
            target_source_id="src-2",
            relation_type="supersede",
            direction="directed",
            evidence="新版替代旧版",
            origin="system",
            status="confirmed",
            created_by="admin-1",
            confirmed_by="admin-2",
            created_at=datetime(2026, 8, 10, tzinfo=timezone.utc),
            confirmed_at=None,
        )
        data = relation_to_dict(relation)
        self.assertEqual(data["relation_type"], "supersede")
        self.assertEqual(data["relation_label"], "替代")
        self.assertEqual(data["status"], "confirmed")
        self.assertIsNone(data["confirmed_at"])

    def test_system_supersede_edge(self):
        source = SimpleNamespace(id="new-id", replaced_source_id="old-id")
        edge = _system_supersede_edge(source)
        self.assertIsNotNone(edge)
        self.assertEqual(edge["source_id"], "old-id")
        self.assertEqual(edge["target_source_id"], "new-id")
        self.assertEqual(edge["relation_type"], "supersede")
        self.assertEqual(edge["origin"], "system")
        self.assertEqual(edge["status"], "confirmed")

    def test_system_supersede_edge_without_replaced(self):
        source = SimpleNamespace(id="new-id", replaced_source_id=None)
        self.assertIsNone(_system_supersede_edge(source))


class ConfirmedRelationsTests(unittest.IsolatedAsyncioTestCase):
    async def test_empty_source_ids_returns_empty(self):
        db = AsyncMock()
        result = await get_confirmed_relations(db, [])
        self.assertEqual(result, [])
        db.execute.assert_not_called()

    async def test_invalid_source_ids_filtered_out(self):
        db = AsyncMock()
        db.execute.return_value = SimpleNamespace(
            scalars=lambda: SimpleNamespace(all=lambda: [])
        )
        result = await get_confirmed_relations(db, ["not-a-uuid", "also-bad"])
        self.assertEqual(result, [])

    async def test_returns_relations_for_valid_ids(self):
        db = AsyncMock()
        db.execute.return_value = SimpleNamespace(
            scalars=lambda: SimpleNamespace(
                all=lambda: [
                    SimpleNamespace(
                        id="rel-1",
                        source_id="11111111-1111-1111-1111-111111111111",
                        target_source_id="22222222-2222-2222-2222-222222222222",
                        relation_type="related",
                        direction="undirected",
                        evidence="",
                        origin="manual",
                        status="confirmed",
                        created_by="admin-1",
                        confirmed_by="admin-1",
                        created_at=None,
                        confirmed_at=None,
                    )
                ]
            )
        )
        result = await get_confirmed_relations(
            db, ["11111111-1111-1111-1111-111111111111"]
        )
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["relation_type"], "related")


class GraphExtractionTests(unittest.IsolatedAsyncioTestCase):
    def test_parse_relations_filters_invalid_items(self):
        from app.plugins.company_knowledge.graph_extractor import _parse_relations

        raw = """
        ```json
        [
          {"relation_type": "cite", "target_title": "《员工考勤制度》", "evidence": "按考勤制度执行"},
          {"relation_type": "friendship", "target_title": "某文档"},
          {"relation_type": "related", "target_title": ""},
          "not-a-dict"
        ]
        ```
        """
        items = _parse_relations(raw)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["relation_type"], "cite")

    def test_match_title_exact_and_containment(self):
        from app.plugins.company_knowledge.graph_extractor import _match_title

        known = [
            {"id": "a", "title": "员工考勤与休假管理制度"},
            {"id": "b", "title": "差旅报销管理办法"},
        ]
        self.assertEqual(_match_title("员工考勤与休假管理制度", known)["id"], "a")
        self.assertEqual(_match_title("《员工考勤与休假管理制度》", known)["id"], "a")
        self.assertEqual(_match_title("员工考勤与休假管理制度（V2）", known), None)

    def test_match_title_ignores_too_short(self):
        from app.plugins.company_knowledge.graph_extractor import _match_title

        known = [{"id": "a", "title": "考勤"}]
        self.assertIsNone(_match_title("考勤", known))

    async def test_extract_relations_from_text_matches_and_unmatched(self):
        from app.plugins.company_knowledge.graph_extractor import extract_relations_from_text

        known = [
            {"id": "src-a", "title": "员工考勤与休假管理制度"},
            {"id": "src-b", "title": "差旅报销管理办法"},
        ]

        async def fake_chat(prompt, system=""):
            return (
                '[{"relation_type": "cite", "target_title": "员工考勤与休假管理制度", "evidence": "按考勤制度执行"}, '
                '{"relation_type": "related", "target_title": "不存在的制度", "evidence": "关联"}]'
            )

        with patch(
            "app.plugins.company_knowledge.graph_extractor.gateway.chat",
            side_effect=fake_chat,
        ):
            matched, unmatched = await extract_relations_from_text(
                "正文内容", source_title="假期管理办法", known_titles=known
            )

        self.assertEqual(len(matched), 1)
        self.assertEqual(matched[0]["target_source_id"], "src-a")
        self.assertEqual(matched[0]["relation_type"], "cite")
        self.assertEqual(len(unmatched), 1)
        self.assertEqual(unmatched[0]["target_title"], "不存在的制度")


if __name__ == "__main__":
    unittest.main()
