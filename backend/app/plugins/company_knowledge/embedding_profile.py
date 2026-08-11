"""公司知识向量档案的稳定契约。

本模块不负责调用 Embedding 模型；它只定义不同档案的输入模板和元数据读写方式。
这样可以先在默认关闭的状态下完成数据兼容，后续开启 Contextual Retrieval
重建时也不会把旧的正文向量与新向量混为同一种数据。
"""

from dataclasses import dataclass
from typing import Mapping


EMBEDDING_PROFILE_METADATA_KEY = "embedding_profile"
EMBEDDING_PROFILE_CONTENT_ONLY = "content_only"
EMBEDDING_PROFILE_CONTEXTUAL_V1 = "contextual_v1"


@dataclass(frozen=True)
class EmbeddingInput:
    """一次向量化应使用的文本及其可观测档案。"""

    text: str
    profile: str
    uses_contextual_description: bool


def build_embedding_input(
    content: str,
    contextual_description: str | None,
    *,
    contextual_embedding_enabled: bool,
) -> EmbeddingInput:
    """按开关与描述可用性返回稳定的向量输入。

    即使开关开启，历史分片可能尚未生成描述，或单块描述生成失败；这两种情况
    都必须安全回退到正文向量，而非阻塞后续的受控 reindex。
    """
    description = (contextual_description or "").strip()
    if contextual_embedding_enabled and description:
        return EmbeddingInput(
            text=f"上下文描述：{description}\n\n分片正文：{content}",
            profile=EMBEDDING_PROFILE_CONTEXTUAL_V1,
            uses_contextual_description=True,
        )
    return EmbeddingInput(
        text=content,
        profile=EMBEDDING_PROFILE_CONTENT_ONLY,
        uses_contextual_description=False,
    )


def get_embedding_profile(metadata: Mapping | None) -> str:
    """读取档案；历史分片缺少标记时按正文向量兼容。"""
    value = (metadata or {}).get(EMBEDDING_PROFILE_METADATA_KEY)
    if value in {EMBEDDING_PROFILE_CONTENT_ONLY, EMBEDDING_PROFILE_CONTEXTUAL_V1}:
        return value
    return EMBEDDING_PROFILE_CONTENT_ONLY


def with_embedding_profile(metadata: Mapping | None, profile: str) -> dict:
    """返回保留既有元数据的档案标记副本。"""
    if profile not in {EMBEDDING_PROFILE_CONTENT_ONLY, EMBEDDING_PROFILE_CONTEXTUAL_V1}:
        raise ValueError(f"未知 embedding profile：{profile}")
    result = dict(metadata or {})
    result[EMBEDDING_PROFILE_METADATA_KEY] = profile
    return result
