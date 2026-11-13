"""RuleBundle：判级规则包（切分 / 对齐 / 判级三段口径的版本化登记）。

注册表（services/rule_registry.py）是代码侧事实来源，启动/seed 时幂等 upsert 到本表，
让规则版本可以通过接口列出；每份 DiffRun 另外快照 rule_config，
本表行是否继续存在不影响历史结论复现。
"""
from __future__ import annotations

from sqlalchemy import Boolean, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from src.config.settings import settings
from src.models.base import Base, TimestampMixin


class RuleBundle(Base, TimestampMixin):
    __tablename__ = "rule_bundles"
    __table_args__ = {"schema": settings.db_schema}

    version: Mapped[str] = mapped_column(String(30), primary_key=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False, default="")
    change_summary: Mapped[str] = mapped_column(Text, nullable=False, default="")
    rule_config: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    is_current: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, index=True)
