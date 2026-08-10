"""文档级关系图谱服务（B1/B4）：关系 CRUD、图谱数据组装与检索扩展查询。

- 关系状态：draft(待确认) / confirmed(已确认，参与检索) / rejected(已拒绝)。
- 来源：llm(大模型抽取) / manual(人工维护) / system(系统规则，如版本替代)。
- 图谱节点口径：已发布且生效、access_scope 允许的文档；system 替代边由
  `company_knowledge_sources.replaced_source_id` 实时并入，不落关系表。
"""

import uuid
from datetime import datetime, timezone

from sqlalchemy import or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.company_knowledge import (
    CompanyKnowledgeRelation,
    CompanyKnowledgeSource,
)
from app.plugins.company_knowledge.graph_extractor import extract_relations_from_text

RELATION_TYPES = ("cite", "supersede", "parent", "related")
RELATION_TYPE_LABELS = {"cite": "引用", "supersede": "替代", "parent": "上下位", "related": "关联"}
RELATION_STATUSES = ("draft", "confirmed", "rejected")
RELATION_ORIGINS = ("llm", "manual", "system")
DIRECTIONS = ("directed", "undirected")


class GraphServiceError(RuntimeError):
    pass


def relation_to_dict(relation: CompanyKnowledgeRelation) -> dict:
    return {
        "id": str(relation.id),
        "source_id": str(relation.source_id),
        "target_source_id": str(relation.target_source_id),
        "relation_type": relation.relation_type,
        "relation_label": RELATION_TYPE_LABELS.get(relation.relation_type, relation.relation_type),
        "direction": relation.direction,
        "evidence": relation.evidence,
        "origin": relation.origin,
        "status": relation.status,
        "created_by": str(relation.created_by),
        "confirmed_by": str(relation.confirmed_by) if relation.confirmed_by else None,
        "created_at": relation.created_at.isoformat() if relation.created_at else None,
        "confirmed_at": relation.confirmed_at.isoformat() if relation.confirmed_at else None,
    }


def _system_supersede_edge(source: CompanyKnowledgeSource) -> dict | None:
    """把 replaced_source_id 表达为 system 来源的 supersede 边（旧版 → 新版）。"""
    if not source.replaced_source_id:
        return None
    return {
        "id": f"system-supersede-{source.id}",
        "source_id": str(source.replaced_source_id),
        "target_source_id": str(source.id),
        "relation_type": "supersede",
        "relation_label": "替代",
        "direction": "directed",
        "evidence": "系统规则：版本替代（replaced_source_id）",
        "origin": "system",
        "status": "confirmed",
        "created_by": "",
        "confirmed_by": "",
        "created_at": None,
        "confirmed_at": None,
    }


def validate_relation_input(
    *,
    source_id: str,
    target_source_id: str,
    relation_type: str,
    direction: str = "undirected",
    origin: str = "manual",
) -> None:
    if not source_id or not target_source_id:
        raise GraphServiceError("源文档与目标文档均必填")
    if str(source_id) == str(target_source_id):
        raise GraphServiceError("源文档与目标文档不能相同")
    if relation_type not in RELATION_TYPES:
        raise GraphServiceError(f"不支持的关系类型：{relation_type}")
    if direction not in DIRECTIONS:
        raise GraphServiceError(f"不支持的方向类型：{direction}")
    if origin not in RELATION_ORIGINS:
        raise GraphServiceError(f"不支持的关系来源：{origin}")


async def create_relation(
    db: AsyncSession,
    *,
    source_id: str,
    target_source_id: str,
    relation_type: str,
    direction: str = "undirected",
    evidence: str = "",
    origin: str = "manual",
    admin_id,
) -> CompanyKnowledgeRelation:
    validate_relation_input(
        source_id=source_id,
        target_source_id=target_source_id,
        relation_type=relation_type,
        direction=direction,
        origin=origin,
    )
    source_uuid = uuid.UUID(str(source_id))
    target_uuid = uuid.UUID(str(target_source_id))
    rows = await db.execute(
        select(CompanyKnowledgeSource.id).where(
            CompanyKnowledgeSource.id.in_([source_uuid, target_uuid])
        )
    )
    found = {row[0] for row in rows.all()}
    missing = {source_uuid, target_uuid} - found
    if missing:
        raise GraphServiceError("源文档或目标文档不存在")

    relation = CompanyKnowledgeRelation(
        source_id=source_uuid,
        target_source_id=target_uuid,
        relation_type=relation_type,
        direction=direction,
        evidence=(evidence or "").strip(),
        origin=origin,
        status="draft",
        created_by=admin_id,
    )
    db.add(relation)
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise GraphServiceError("该关系已存在（相同两端与类型）") from exc
    await db.refresh(relation)
    return relation


async def update_relation_status(
    db: AsyncSession,
    *,
    relation_id: str,
    status: str,
    admin_id,
) -> CompanyKnowledgeRelation:
    if status not in RELATION_STATUSES:
        raise GraphServiceError(f"不支持的关系状态：{status}")
    relation = await db.get(CompanyKnowledgeRelation, uuid.UUID(str(relation_id)))
    if not relation:
        raise GraphServiceError("关系不存在")
    relation.status = status
    relation.confirmed_by = admin_id
    relation.confirmed_at = datetime.now(timezone.utc)
    await db.commit()
    await db.refresh(relation)
    return relation


async def delete_relation(db: AsyncSession, *, relation_id: str) -> None:
    relation = await db.get(CompanyKnowledgeRelation, uuid.UUID(str(relation_id)))
    if not relation:
        raise GraphServiceError("关系不存在")
    await db.delete(relation)
    await db.commit()


async def list_relations(
    db: AsyncSession,
    *,
    source_id: str | None = None,
    status: str | None = None,
    limit: int = 500,
) -> list[CompanyKnowledgeRelation]:
    query = select(CompanyKnowledgeRelation).order_by(CompanyKnowledgeRelation.created_at.desc())
    if source_id:
        source_uuid = uuid.UUID(str(source_id))
        query = query.where(
            or_(
                CompanyKnowledgeRelation.source_id == source_uuid,
                CompanyKnowledgeRelation.target_source_id == source_uuid,
            )
        )
    if status:
        query = query.where(CompanyKnowledgeRelation.status == status)
    query = query.limit(limit)
    rows = await db.execute(query)
    return list(rows.scalars().all())


async def build_graph(
    db: AsyncSession,
    *,
    include_system_edges: bool = True,
) -> dict:
    """组装图谱数据：nodes = 已发布生效文档；edges = 关系（含 system 替代边）。

    待确认(draft)关系也会返回（前端高亮提示确认），检索仅使用 confirmed。
    """
    now = datetime.now(timezone.utc)
    rows = await db.execute(
        select(CompanyKnowledgeSource)
        .where(
            CompanyKnowledgeSource.status == "published",
            CompanyKnowledgeSource.access_scope == "all_users",
            CompanyKnowledgeSource.effective_at <= now,
            or_(
                CompanyKnowledgeSource.expires_at.is_(None),
                CompanyKnowledgeSource.expires_at > now,
            ),
        )
        .order_by(CompanyKnowledgeSource.title.asc())
    )
    sources = rows.scalars().all()
    node_ids = {source.id for source in sources}
    node_by_id = {source.id: source for source in sources}

    nodes = [
        {
            "id": str(source.id),
            "title": source.title,
            "knowledge_type": source.knowledge_type,
            "category": source.category,
            "version": source.version,
            "effective_at": source.effective_at.date().isoformat() if source.effective_at else None,
        }
        for source in sources
    ]

    edges = []
    relation_rows = await db.execute(
        select(CompanyKnowledgeRelation).order_by(CompanyKnowledgeRelation.created_at.desc())
    )
    for relation in relation_rows.scalars().all():
        if relation.source_id in node_ids and relation.target_source_id in node_ids:
            edges.append(relation_to_dict(relation))

    if include_system_edges:
        for source in sources:
            edge = _system_supersede_edge(source)
            if edge and uuid.UUID(edge["source_id"]) in node_ids:
                edges.append(edge)

    return {"nodes": nodes, "edges": edges}


async def get_confirmed_relations(
    db: AsyncSession,
    source_ids: list[str],
) -> list[dict]:
    """B4：取一组源文档的 1 跳已确认关系（含方向相反的边）。"""
    if not source_ids:
        return []
    uuids = []
    for raw in source_ids:
        try:
            uuids.append(uuid.UUID(str(raw)))
        except (ValueError, TypeError):
            continue
    if not uuids:
        return []
    rows = await db.execute(
        select(CompanyKnowledgeRelation).where(
            CompanyKnowledgeRelation.status == "confirmed",
            or_(
                CompanyKnowledgeRelation.source_id.in_(uuids),
                CompanyKnowledgeRelation.target_source_id.in_(uuids),
            ),
        )
    )
    return [relation_to_dict(relation) for relation in rows.scalars().all()]


async def extract_source_relations(
    db: AsyncSession,
    *,
    source_id: str,
    admin_id,
) -> dict:
    """B2：对已发布资料做 LLM 关系抽取，结果进草稿态。

    返回 {"created": n, "skipped": n, "unmatched": [条目]}。
    """
    source = await db.get(CompanyKnowledgeSource, uuid.UUID(str(source_id)))
    if not source:
        raise GraphServiceError("资料不存在")
    if source.status != "published":
        raise GraphServiceError("只有已发布资料可以抽取关系")

    now = datetime.now(timezone.utc)
    rows = await db.execute(
        select(CompanyKnowledgeSource).where(
            CompanyKnowledgeSource.status == "published",
            CompanyKnowledgeSource.access_scope == "all_users",
            CompanyKnowledgeSource.effective_at <= now,
            or_(
                CompanyKnowledgeSource.expires_at.is_(None),
                CompanyKnowledgeSource.expires_at > now,
            ),
        )
    )
    known_titles = [
        {"id": item.id, "title": item.title}
        for item in rows.scalars().all()
        if item.id != source.id
    ]

    text = source.preprocessed_content or source.markdown_content or source.raw_content
    try:
        matched, unmatched = await extract_relations_from_text(
            text,
            source_title=source.title,
            known_titles=known_titles,
        )
    except Exception as exc:
        raise GraphServiceError(f"关系抽取失败：{exc}") from exc

    created = 0
    skipped = 0
    for item in matched:
        try:
            await create_relation(
                db,
                source_id=str(source.id),
                target_source_id=item["target_source_id"],
                relation_type=item["relation_type"],
                evidence=item["evidence"],
                origin="llm",
                admin_id=admin_id,
            )
            created += 1
        except GraphServiceError:
            skipped += 1
    return {"created": created, "skipped": skipped, "unmatched": unmatched}
