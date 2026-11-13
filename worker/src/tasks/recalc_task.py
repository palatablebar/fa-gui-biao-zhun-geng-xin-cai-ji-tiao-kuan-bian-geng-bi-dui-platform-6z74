"""重算任务：按新规则包重算同一对版本（与 run_diff 一样在 backend services 里执行）。"""
from __future__ import annotations

from worker_src.config.celery_app import celery_app


@celery_app.task(name="lawdelta.run_recalc", bind=True, max_retries=0)
def run_recalc(self, recalculation_id: str) -> dict:
    from src.services.recalc_service import execute_recalculation

    execute_recalculation(recalculation_id)
    return {"recalculation_id": recalculation_id, "status": "executed"}
