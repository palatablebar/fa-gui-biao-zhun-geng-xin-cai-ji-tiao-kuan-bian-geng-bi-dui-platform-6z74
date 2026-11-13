"""rule bundles and recalculation

新增规则包 / 重算 / 重算明细表，并给 diff_runs、change_items 补规则绑定与血缘字段。
建表口径仍以 SQLAlchemy 模型为唯一事实来源：新表直接 create_all，
已存在的表用幂等 ALTER 补列（demo 库由 bootstrap_db.create_all 一次性建好，不会走到这里）。

Revision ID: 0002
Revises: 0001
Create Date: 2026-11-13
"""
from __future__ import annotations

from alembic import op
from sqlalchemy import text

from src.config.settings import settings
from src.models import Base

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None

_SCHEMA = settings.db_schema

_DIFF_RUN_COLUMNS: dict[str, str] = {
    "rule_bundle_version": "VARCHAR(30) NOT NULL DEFAULT '1.0.0'",
    "rule_config": "JSON NOT NULL DEFAULT '{}'::json",
    "is_active": "BOOLEAN NOT NULL DEFAULT TRUE",
    "lineage": "VARCHAR(20) NOT NULL DEFAULT 'initial'",
    "recalc_of_id": "UUID NULL",
    "root_run_id": "UUID NULL",
}

_CHANGE_ITEM_COLUMNS: dict[str, str] = {
    "rule_bundle_version": "VARCHAR(30) NOT NULL DEFAULT '1.0.0'",
    "item_key": "VARCHAR(120) NOT NULL DEFAULT ''",
}


def _column_exists(bind, table: str, column: str) -> bool:
    row = bind.execute(
        text(
            "SELECT 1 FROM information_schema.columns "
            "WHERE table_schema = :schema AND table_name = :table AND column_name = :column"
        ),
        {"schema": _SCHEMA, "table": table, "column": column},
    ).first()
    return row is not None


def _table_exists(bind, table: str) -> bool:
    row = bind.execute(
        text(
            "SELECT 1 FROM information_schema.tables WHERE table_schema = :schema AND table_name = :table"
        ),
        {"schema": _SCHEMA, "table": table},
    ).first()
    return row is not None


def upgrade() -> None:
    bind = op.get_bind()
    bind.exec_driver_sql(f'CREATE SCHEMA IF NOT EXISTS "{_SCHEMA}"')

    # 新表（rule_bundles / recalculations / recalculation_items）按模型一次性建立。
    Base.metadata.create_all(bind=bind, tables=[
        Base.metadata.tables[f"{_SCHEMA}.rule_bundles"],
        Base.metadata.tables[f"{_SCHEMA}.recalculations"],
        Base.metadata.tables[f"{_SCHEMA}.recalculation_items"],
    ])

    for column, ddl in _DIFF_RUN_COLUMNS.items():
        if not _column_exists(bind, "diff_runs", column):
            bind.exec_driver_sql(f'ALTER TABLE "{_SCHEMA}"."diff_runs" ADD COLUMN {column} {ddl}')
    for column, ddl in _CHANGE_ITEM_COLUMNS.items():
        if not _column_exists(bind, "change_items", column):
            bind.exec_driver_sql(f'ALTER TABLE "{_SCHEMA}"."change_items" ADD COLUMN {column} {ddl}')

    if not _table_exists(bind, "uq_diff_pair_rule_bundle"):
        bind.exec_driver_sql(
            f'ALTER TABLE "{_SCHEMA}"."diff_runs" '
            'ADD CONSTRAINT uq_diff_pair_rule_bundle '
            "UNIQUE (base_document_id, target_document_id, rule_bundle_version)"
        )

    # 历史数据补齐：升级前的批次视为各自版本对的生效结论与重算根。
    bind.exec_driver_sql(
        f'UPDATE "{_SCHEMA}"."diff_runs" SET root_run_id = id WHERE root_run_id IS NULL'
    )


def downgrade() -> None:
    bind = op.get_bind()
    bind.exec_driver_sql(f'DROP TABLE IF EXISTS "{_SCHEMA}"."recalculation_items"')
    bind.exec_driver_sql(f'DROP TABLE IF EXISTS "{_SCHEMA}"."recalculations"')
    bind.exec_driver_sql(f'DROP TABLE IF EXISTS "{_SCHEMA}"."rule_bundles"')
    bind.exec_driver_sql(
        f'ALTER TABLE "{_SCHEMA}"."diff_runs" DROP CONSTRAINT IF EXISTS uq_diff_pair_rule_bundle'
    )
    for column in _DIFF_RUN_COLUMNS:
        bind.exec_driver_sql(
            f'ALTER TABLE "{_SCHEMA}"."diff_runs" DROP COLUMN IF EXISTS {column}'
        )
    for column in _CHANGE_ITEM_COLUMNS:
        bind.exec_driver_sql(
            f'ALTER TABLE "{_SCHEMA}"."change_items" DROP COLUMN IF EXISTS {column}'
        )
