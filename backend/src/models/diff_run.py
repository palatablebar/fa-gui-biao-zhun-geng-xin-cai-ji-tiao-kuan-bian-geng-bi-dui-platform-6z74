"""DiffRun：条款比对批次（实体 3/4）。"""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, JSON, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from src.config.settings import settings
from src.models.base import Base, TimestampMixin, new_uuid


class DiffRun(Base, TimestampMixin):
    __tablename__ = "diff_runs"
    __table_args__ = (
        # 同一对版本 + 同一规则包只允许一份结论（重算幂等的数据库兜底）。
        UniqueConstraint(
            "base_document_id", "target_document_id", "rule_bundle_version",
            name="uq_diff_pair_rule_bundle",
        ),
        {"schema": settings.db_schema},
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=new_uuid)
    document_key: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    base_document_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(f"{settings.db_schema}.documents.id", ondelete="CASCADE"),
        nullable=False,
    )
    target_document_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(f"{settings.db_schema}.documents.id", ondelete="CASCADE"),
        nullable=False,
    )
    requested_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(f"{settings.db_schema}.users.id", ondelete="SET NULL"),
        nullable=True,
    )
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="queued", index=True)
    algorithm_version: Mapped[str] = mapped_column(String(30), nullable=False, default="1.0.0")

    # —— 规则绑定：每份结论固定绑定当次切分/对齐/判级规则 ——
    rule_bundle_version: Mapped[str] = mapped_column(String(30), nullable=False, default="1.0.0", index=True)
    rule_config: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)

    # —— 结论血缘与生效口径 ——
    # 同一对版本（document_key + base/target）的多份结论里只有一份 is_active=True：
    # 统计与变更清单默认只读生效结论；重算全部落库后在同一事务内切换。
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, index=True)
    # lineage: initial=首次比对；recalc=按新规则包重算产生的对照结论。
    lineage: Mapped[str] = mapped_column(String(20), nullable=False, default="initial", index=True)
    # 重算链：recalc_of_id 指向被重算的旧批次；同链批次通过 root_run_id 归组。
    recalc_of_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(f"{settings.db_schema}.diff_runs.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    root_run_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True, index=True)

    stats: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    severity_counts: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    error_message: Mapped[str] = mapped_column(Text, nullable=False, default="")
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    published_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    publish_note: Mapped[str] = mapped_column(Text, nullable=False, default="")
    change_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
