"""Contextual Retrieval 向量档案的纯逻辑契约测试。"""

import unittest

from app.plugins.company_knowledge.embedding_profile import (
    EMBEDDING_PROFILE_CONTENT_ONLY,
    EMBEDDING_PROFILE_CONTEXTUAL_V1,
    build_embedding_input,
    get_embedding_profile,
    with_embedding_profile,
)


class EmbeddingProfileTests(unittest.TestCase):
    def test_disabled_flag_always_keeps_content_only_embedding(self):
        payload = build_embedding_input(
            "员工应提前三天申请年假。",
            "《员工休假制度》的年假申请规则。",
            contextual_embedding_enabled=False,
        )

        self.assertEqual(payload.text, "员工应提前三天申请年假。")
        self.assertEqual(payload.profile, EMBEDDING_PROFILE_CONTENT_ONLY)
        self.assertFalse(payload.uses_contextual_description)

    def test_enabled_flag_uses_stable_description_and_content_template(self):
        payload = build_embedding_input(
            "员工应提前三天申请年假。",
            "  《员工休假制度》的年假申请规则。  ",
            contextual_embedding_enabled=True,
        )

        self.assertEqual(
            payload.text,
            "上下文描述：《员工休假制度》的年假申请规则。\n\n分片正文：员工应提前三天申请年假。",
        )
        self.assertEqual(payload.profile, EMBEDDING_PROFILE_CONTEXTUAL_V1)
        self.assertTrue(payload.uses_contextual_description)

    def test_enabled_flag_without_description_safely_falls_back_to_content_only(self):
        for description in (None, "", "   "):
            with self.subTest(description=description):
                payload = build_embedding_input(
                    "员工应提前三天申请年假。",
                    description,
                    contextual_embedding_enabled=True,
                )
                self.assertEqual(payload.text, "员工应提前三天申请年假。")
                self.assertEqual(payload.profile, EMBEDDING_PROFILE_CONTENT_ONLY)

    def test_legacy_metadata_defaults_to_content_only_and_profile_preserves_metadata(self):
        metadata = {"contextual_description": "年假规则", "markdown_version": 2}

        self.assertEqual(get_embedding_profile(metadata), EMBEDDING_PROFILE_CONTENT_ONLY)
        updated = with_embedding_profile(metadata, EMBEDDING_PROFILE_CONTEXTUAL_V1)
        self.assertEqual(updated["contextual_description"], "年假规则")
        self.assertEqual(updated["embedding_profile"], EMBEDDING_PROFILE_CONTEXTUAL_V1)
        self.assertEqual(get_embedding_profile(updated), EMBEDDING_PROFILE_CONTEXTUAL_V1)


if __name__ == "__main__":
    unittest.main()
