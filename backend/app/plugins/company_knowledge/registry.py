"""公司知识资料类型的单一注册表。"""

from dataclasses import dataclass


@dataclass(frozen=True)
class KnowledgeTypeDefinition:
    key: str
    label: str
    description: str
    icon: str
    import_enabled: bool
    query_enabled: bool
    user_visible: bool
    required_metadata: tuple[str, ...] = ()
    # 已有的图谱关联推荐开关：命中后展示已确认的一跳关联资料。
    graph_expansion_enabled: bool = False
    # A3 强化：描述 + 正文 embedding，默认关闭，后续仅通过受控 reindex 生效。
    contextual_embedding_enabled: bool = False
    # B4 强化：图谱候选进入 RRF 排序，独立于关联推荐，默认关闭。
    graph_ranking_enabled: bool = False

    def to_dict(self) -> dict:
        return {
            "key": self.key,
            "label": self.label,
            "description": self.description,
            "icon": self.icon,
            "import_enabled": self.import_enabled,
            "query_enabled": self.query_enabled,
            "user_visible": self.user_visible,
            "required_metadata": list(self.required_metadata),
            "graph_expansion_enabled": self.graph_expansion_enabled,
            "contextual_embedding_enabled": self.contextual_embedding_enabled,
            "graph_ranking_enabled": self.graph_ranking_enabled,
        }


KNOWLEDGE_TYPES = (
    KnowledgeTypeDefinition(
        key="policy",
        label="公司制度",
        description="查询已发布、已生效的公司制度。",
        icon="BookOpen",
        import_enabled=True,
        query_enabled=True,
        user_visible=True,
        required_metadata=("version", "effective_at"),
        graph_expansion_enabled=True,
    ),
    KnowledgeTypeDefinition(
        key="faq",
        label="常见问答",
        description="沉淀公司常见问题与标准答复。",
        icon="CircleHelp",
        import_enabled=False,
        query_enabled=False,
        user_visible=False,
    ),
    KnowledgeTypeDefinition(
        key="history",
        label="公司历史",
        description="记录公司发展历程和关键节点。",
        icon="History",
        import_enabled=False,
        query_enabled=False,
        user_visible=False,
    ),
    KnowledgeTypeDefinition(
        key="news",
        label="近期动态",
        description="发布近期公告和公司动态。",
        icon="Newspaper",
        import_enabled=False,
        query_enabled=False,
        user_visible=False,
        required_metadata=("published_at",),
    ),
    KnowledgeTypeDefinition(
        key="department_knowledge",
        label="部门知识",
        description="沉淀部门范围内的流程和资料。",
        icon="Building2",
        import_enabled=False,
        query_enabled=False,
        user_visible=False,
        required_metadata=("department",),
    ),
)

_TYPE_BY_KEY = {item.key: item for item in KNOWLEDGE_TYPES}


def list_knowledge_types() -> list[dict]:
    return [item.to_dict() for item in KNOWLEDGE_TYPES]


def get_knowledge_type(key: str) -> KnowledgeTypeDefinition | None:
    return _TYPE_BY_KEY.get(key)


def is_import_enabled(key: str) -> bool:
    item = get_knowledge_type(key)
    return bool(item and item.import_enabled)


def is_query_enabled(key: str) -> bool:
    item = get_knowledge_type(key)
    return bool(item and item.query_enabled)


def is_contextual_embedding_enabled(key: str) -> bool:
    item = get_knowledge_type(key)
    return bool(item and item.contextual_embedding_enabled)


def is_graph_ranking_enabled(key: str) -> bool:
    item = get_knowledge_type(key)
    return bool(item and item.graph_ranking_enabled)
