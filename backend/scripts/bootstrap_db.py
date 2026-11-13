"""建表引导：幂等 create_all（容器 entrypoint 第一步）。

demo 场景下用 SQLAlchemy metadata 建表，避免首次启动卡在迁移版本对齐；
正式环境可改用 `alembic upgrade head`（见 backend/migrations/）。
建表后把代码侧规则包注册表同步到 rule_bundles 表（幂等 upsert）。
"""
from __future__ import annotations

from sqlalchemy import text

from src.config.database import engine
from src.config.database import SessionLocal
from src.models import Base
from src.services.rule_service import sync_rule_bundles


def main() -> None:
    from src.config.settings import settings

    with engine.begin() as conn:
        conn.execute(text("SELECT 1"))
        conn.execute(text(f'CREATE SCHEMA IF NOT EXISTS "{settings.db_schema}"'))
    Base.metadata.create_all(bind=engine)

    with SessionLocal() as db:
        bundle_count = sync_rule_bundles(db)
        db.commit()

    print(
        f"[bootstrap] schema={settings.db_schema} tables={len(Base.metadata.tables)} "
        f"rule_bundles={bundle_count}",
        flush=True,
    )


if __name__ == "__main__":
    main()
