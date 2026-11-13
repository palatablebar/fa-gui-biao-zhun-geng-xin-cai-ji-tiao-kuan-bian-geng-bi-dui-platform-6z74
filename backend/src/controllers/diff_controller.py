"""比对批次：创建 / 查询 / 变更列表 / 规则包查询 / 重算。"""
from __future__ import annotations

import uuid

from fastapi import Request
from sqlalchemy.orm import Session

from src.models.user import User
from src.schemas.domain import DiffCreateRequest, RecalcRequest
from src.services import diff_service, recalc_service, rule_service
from src.services.rule_registry import CURRENT_RULE_BUNDLE_VERSION


def create_diff(db: Session, payload: DiffCreateRequest, user: User, request: Request) -> dict:
    run = diff_service.create_diff_run(
        db,
        base_version_id=payload.base_version_id,
        target_version_id=payload.target_version_id,
        actor=user,
        trace_id=getattr(request.state, "trace_id", ""),
        rule_bundle_version=payload.rule_bundle_version or CURRENT_RULE_BUNDLE_VERSION,
    )
    return {"run": diff_service.serialize_run(run, db)}


def list_runs(
    db: Session,
    document_key: str | None = None,
    limit: int = 30,
    active_only: bool = False,
) -> dict:
    return {"items": diff_service.list_runs(db, document_key=document_key, limit=limit, active_only=active_only)}


def get_run(db: Session, run_id: uuid.UUID) -> dict:
    run = diff_service.get_run(db, run_id)
    return {"run": diff_service.serialize_run(run, db)}


def list_changes(db: Session, run_id: uuid.UUID, severity: str | None = None) -> dict:
    run = diff_service.get_run(db, run_id)
    changes = diff_service.list_changes(db, run.id, severity=severity)
    return {
        "run": diff_service.serialize_run(run, db),
        "items": changes,
        "total": len(changes),
    }


def list_rule_bundles(db: Session) -> dict:
    items = rule_service.list_rule_bundles(db)
    return {"items": items, "current": CURRENT_RULE_BUNDLE_VERSION, "total": len(items)}


def create_recalc(db: Session, run_id: uuid.UUID, payload: RecalcRequest, user: User, request: Request) -> dict:
    recalc = recalc_service.request_recalculation(
        db,
        run_id=run_id,
        target_rule_bundle_version=payload.target_rule_bundle_version or CURRENT_RULE_BUNDLE_VERSION,
        actor=user,
        trace_id=getattr(request.state, "trace_id", ""),
    )
    return {"recalculation": recalc_service.serialize_recalculation(recalc)}


def list_recalcs(db: Session, run_id: uuid.UUID | None = None) -> dict:
    items = recalc_service.list_recalculations(db, run_id)
    return {"items": items, "total": len(items)}


def get_recalc(db: Session, recalc_id: uuid.UUID) -> dict:
    recalc = recalc_service.get_recalculation(db, recalc_id)
    return {
        "recalculation": recalc_service.serialize_recalculation(recalc),
        "items": recalc_service.get_recalculation_items(db, recalc.id),
    }
