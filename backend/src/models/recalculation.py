"""Recalculation：一次「按新规则包重算同一对版本」的编排记录。

生命周期（status）：
  queued → running → succeeded / failed
- 重算产物（新 DiffRun + 新 ChangeItem + 对照明细）全部在同一事务里落库，
  最后一步才原子切换 is_active；任何一步失败都回滚，旧结论继续生效。
- (source_run_id, target_rule_bundle_version) 唯一：重复发起重算不会重复生成结论，
  接口直接返回同一条记录。
"""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, JSON, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from src.config.settings import settings
from src.models.base import Base, TimestampMixin, new_uuid, utcnow


class Recalculation(Base, TimestampMixin):
    __tablename__ = "recalculations"
    __table_args__ = (
        UniqueConstraint(
            "source_run_id", "target_rule_bundle_version",
            name="uq_recalc_run_rule_bundle",
        ),
        {"schema": settings.db_schema},
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=new_uuid)
    source_run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(f"{settings.db_schema}.diff_runs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    new_run_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(f"{settings.db_schema}.diff_runs.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    requested_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(f"{settings.db_schema}.users.id", ondelete="SET NULL"),
        nullable=True,
    )
    source_rule_bundle_version: Mapped[str] = mapped_column(String(30), nullable=False)
    target_rule_bundle_version: Mapped[str] = mapped_column(String(30), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="queued", index=True)
    diff_summary: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    error_message: Mapped[str] = mapped_column(Text, nullable=False, default="")
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class RecalculationItem(Base):
    """新旧结论的逐条对照明细：新增 / 消失 / 改级 / 不变。"""

    __tablename__ = "recalculation_items"
    __table_args__ = (
        # item_key 不做数据库唯一约束：同一侧条款键可能出现在配对行与单边行，
        # 应用层 compare_changes 保证每次重算每个 item_key 只落一行。
        Index("ix_recalc_item_lookup", "recalculation_id", "item_key"),
        {"schema": settings.db_schema},
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=new_uuid)
    recalculation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(f"{settings.db_schema}.recalculations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # 对照状态：added=新口径新增；disappeared=旧口径有、新口径消失；
    # regraded=两边都有但类型/级别变化；unchanged=两边一致。
    diff_status: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    item_key: Mapped[str] = mapped_column(String(120), nullable=False)
    old_change_item_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    new_change_item_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    old_change_type: Mapped[str] = mapped_column(String(20), nullable=False, default="")
    new_change_type: Mapped[str] = mapped_column(String(20), nullable=False, default="")
    old_severity: Mapped[str] = mapped_column(String(20), nullable=False, default="")
    new_severity: Mapped[str] = mapped_column(String(20), nullable=False, default="")
    clause_label_old: Mapped[str] = mapped_column(String(40), nullable=False, default="")
    clause_label_new: Mapped[str] = mapped_column(String(40), nullable=False, default="")
    detail: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
