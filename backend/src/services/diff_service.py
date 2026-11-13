"""比对批次编排：建批次 → 切分 → 对齐 → 判级 → 落 ChangeItem。

计算全部在后端/worker（算法见 clause_splitter / clause_aligner / change_classifier），
前端只展示接口回传的数字。

规则绑定：每个批次固定绑定一个规则包（RuleContext），落库时快照 rule_config；
重算同一对版本会用新规则包生成**另一份** DiffRun，不覆盖旧结论。
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.config.database import session_scope
from src.constants import error_codes as codes
from src.constants.enums import (
    CHANGE_TYPE_LABELS,
    SEVERITY_LABELS,
    DiffRunStatus,
    RunLineage,
    Severity,
)
from src.constants.limits import MAX_CLAUSES_PER_DIFF
from src.constants.log_templates import ACTION_DIFF_FAIL, ACTION_DIFF_RUN
from src.models.change_item import ChangeItem
from src.models.diff_run import DiffRun
from src.models.document import Document
from src.models.user import User
from src.services import audit_service
from src.services.change_classifier import classify_change
from src.services.clause_aligner import align_clauses
from src.services.clause_splitter import split_clauses
from src.services.item_keys import build_item_key
from src.services.rule_registry import (
    CURRENT_RULE_BUNDLE_VERSION,
    LEGACY_RULE_BUNDLE_VERSION,
    RuleContext,
    context_from_config,
    get_rule_context,
)
from src.utils.errors import AppError, not_found

ALGORITHM_VERSION = "1.0.0"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _resolve_context(rule_bundle_version: str) -> RuleContext:
    try:
        return get_rule_context(rule_bundle_version)
    except KeyError as exc:
        raise AppError(codes.RULE_BUNDLE_UNKNOWN, detail=str(exc)) from exc


def create_diff_run(
    db: Session,
    *,
    base_version_id: uuid.UUID,
    target_version_id: uuid.UUID,
    actor: User,
    trace_id: str = "",
    rule_bundle_version: str = CURRENT_RULE_BUNDLE_VERSION,
) -> DiffRun:
    base = db.get(Document, base_version_id)
    target = db.get(Document, target_version_id)
    if base is None:
        raise not_found("文档版本", base_version_id)
    if target is None:
        raise not_found("文档版本", target_version_id)
    if base.document_key != target.document_key:
        raise AppError(codes.VALIDATION_ERROR, "左右版本不属于同一部法规", f"{base.document_key} vs {target.document_key}")
    if base.id == target.id or base.version_no == target.version_no:
        raise AppError(codes.SAME_VERSION, detail=f"{base.version_label} == {target.version_label}")

    context = _resolve_context(rule_bundle_version)

    # 幂等：同一对版本 + 同一规则包只保留一份结论。
    existing = db.execute(
        select(DiffRun).where(
            DiffRun.base_document_id == base.id,
            DiffRun.target_document_id == target.id,
            DiffRun.rule_bundle_version == context.bundle_version,
        ).order_by(DiffRun.created_at.desc())
    ).scalars().first()
    if existing is not None:
        if existing.status == DiffRunStatus.SUCCEEDED.value:
            # 已成功：直接返回既有批次，绝不重复生成结论。
            return existing
        # 上次失败：复用同一行重新执行，避免唯一约束冲突，也不留下重复批次。
        existing.status = DiffRunStatus.QUEUED.value
        existing.error_message = ""
        existing.started_at = None
        existing.finished_at = None
        db.commit()
        _dispatch(existing, db)
        db.refresh(existing)
        return existing

    run = DiffRun(
        document_key=base.document_key,
        base_document_id=base.id,
        target_document_id=target.id,
        requested_by=actor.id,
        status=DiffRunStatus.QUEUED.value,
        algorithm_version=ALGORITHM_VERSION,
        rule_bundle_version=context.bundle_version,
        rule_config=context.to_dict(),
        is_active=True,
        lineage=RunLineage.INITIAL.value,
        root_run_id=None,
        stats={
            "base_label": base.version_label,
            "target_label": target.version_label,
            "base_clause_count": base.clause_count,
            "target_clause_count": target.clause_count,
        },
        severity_counts={},
    )
    db.add(run)
    db.flush()
    # 首次比对默认成为该版本对的生效结论；若已有其它口径结论，则让位给既有结论，
    # 由后续「重算切换」显式激活，避免两套口径在清单里混用。
    sibling = db.execute(
        select(DiffRun).where(
            DiffRun.base_document_id == base.id,
            DiffRun.target_document_id == target.id,
            DiffRun.id != run.id,
            DiffRun.is_active.is_(True),
        )
    ).scalars().first()
    if sibling is not None:
        run.is_active = False
        # 归到同一条重算链的根（root_run_id 已在链上逐代传递）。
        run.root_run_id = sibling.root_run_id or sibling.id
    else:
        run.root_run_id = run.id
    audit_service.record(
        db,
        action=ACTION_DIFF_RUN,
        actor=actor,
        entity_type="diff_run",
        entity_id=run.id,
        detail={
            "document_key": run.document_key,
            "base": base.version_label,
            "target": target.version_label,
            "rule_bundle_version": context.bundle_version,
            "lineage": run.lineage,
        },
        trace_id=trace_id,
    )
    db.commit()

    _dispatch(run, db)
    db.refresh(run)
    return run


def _dispatch(run: DiffRun, db: Session) -> None:
    """投递给 Celery worker；broker 不可用时退化为同步执行（不让主流程卡死）。"""
    try:
        from src.config.celery_app import TASK_RUN_DIFF, celery_app

        celery_app.send_task(TASK_RUN_DIFF, args=[str(run.id)])
        return
    except Exception as exc:  # noqa: BLE001 - 任何 broker 异常都退化为同步
        run.stats = {**run.stats, "dispatch": "inline_fallback", "dispatch_error": str(exc)[:200]}
        db.commit()
    execute_diff_run_in_session(db, run)


def execute_diff_run(run_id: uuid.UUID | str) -> None:
    """worker 入口：独立 session 执行一次比对。"""
    with session_scope() as db:
        run = db.get(DiffRun, uuid.UUID(str(run_id)))
        if run is None:
            return
        if run.status == DiffRunStatus.SUCCEEDED.value:
            return
        execute_diff_run_in_session(db, run)


def _build_change_items(run: DiffRun, context: RuleContext, aligned, split_base, split_target) -> list[ChangeItem]:
    severity_counts = {level.value: 0 for level in Severity}
    change_items: list[ChangeItem] = []
    for pair in aligned.pairs:
        if pair.change_type == "unchanged":
            continue
        old_text = pair.old.text if pair.old else ""
        new_text = pair.new.text if pair.new else ""
        classified = classify_change(pair.change_type, old_text, new_text, context)
        severity_counts[classified.severity] = severity_counts.get(classified.severity, 0) + 1
        old_label = pair.old.label if pair.old else ""
        new_label = pair.new.label if pair.new else ""
        change_items.append(
            ChangeItem(
                diff_run_id=run.id,
                rule_bundle_version=context.bundle_version,
                item_key=build_item_key(pair.change_type, old_label, old_text, new_label, new_text),
                change_type=pair.change_type,
                severity=classified.severity,
                clause_no_old=pair.old.number if pair.old else None,
                clause_no_new=pair.new.number if pair.new else None,
                clause_label_old=old_label,
                clause_label_new=new_label,
                old_text=old_text,
                new_text=new_text,
                similarity=pair.similarity,
                edit_ratio=classified.edit_ratio,
                keywords=classified.keywords,
                industries=classified.industries,
                reason=classified.reason,
            )
        )
    severity_counts[Severity.UNPARSED.value] = split_base.unparsed_count + split_target.unparsed_count
    return change_items, severity_counts


def execute_diff_run_in_session(db: Session, run: DiffRun) -> DiffRun:
    """执行比对并落库。成功/失败都只改本批次，不触碰其他结论的 is_active。"""
    if run.status == DiffRunStatus.SUCCEEDED.value:
        return run
    run.status = DiffRunStatus.RUNNING.value
    run.started_at = _now()
    db.commit()

    try:
        base = db.get(Document, run.base_document_id)
        target = db.get(Document, run.target_document_id)
        if base is None or target is None:
            raise AppError(codes.NOT_FOUND, "版本已被删除")

        # 优先用批次快照的规则（历史复现）；老数据没有快照时回落到注册表里的同版本。
        if run.rule_config:
            context = context_from_config(run.rule_config)
        else:
            context = _resolve_context(run.rule_bundle_version or LEGACY_RULE_BUNDLE_VERSION)
            run.rule_config = context.to_dict()
            run.rule_bundle_version = context.bundle_version

        split_base = split_clauses(base.raw_text, context)
        split_target = split_clauses(target.raw_text, context)
        if not split_base.clauses or not split_target.clauses:
            raise AppError(codes.DOCUMENT_EMPTY, detail="条款切分为空")
        if len(split_base.clauses) > MAX_CLAUSES_PER_DIFF or len(split_target.clauses) > MAX_CLAUSES_PER_DIFF:
            raise AppError(codes.DIFF_TOO_LARGE)

        aligned = align_clauses(
            split_base.clauses,
            split_target.clauses,
            match_threshold=context.aligner.match_threshold,
            change_threshold=context.aligner.change_threshold,
            number_bonus=context.aligner.number_bonus,
            tfidf_weight=context.aligner.tfidf_weight,
            simhash_weight=context.aligner.simhash_weight,
        )

        change_items, severity_counts = _build_change_items(run, context, aligned, split_base, split_target)
        db.add_all(change_items)

        run.stats = {
            **aligned.stats,
            "base_label": base.version_label,
            "target_label": target.version_label,
            "base_clause_count": len(split_base.clauses),
            "target_clause_count": len(split_target.clauses),
            "base_hash": base.content_hash,
            "target_hash": target.content_hash,
            "unparsed_old": split_base.unparsed_count,
            "unparsed_new": split_target.unparsed_count,
            "severity": severity_counts,
            "algorithm_version": ALGORITHM_VERSION,
            "rule_bundle_version": context.bundle_version,
            "rule_change_summary": context.change_summary,
        }
        run.severity_counts = severity_counts
        run.change_count = len(change_items)
        run.status = DiffRunStatus.SUCCEEDED.value
        run.finished_at = _now()
        db.commit()
        return run
    except Exception as exc:  # noqa: BLE001 - 批次失败要落库可查
        db.rollback()
        failed = db.get(DiffRun, run.id)
        if failed is not None:
            failed.status = DiffRunStatus.FAILED.value
            failed.error_message = str(exc)[:500]
            failed.finished_at = _now()
            audit_service.record(
                db,
                action=ACTION_DIFF_FAIL,
                entity_type="diff_run",
                entity_id=failed.id,
                detail={"error": str(exc)[:300], "rule_bundle_version": failed.rule_bundle_version},
            )
            db.commit()
        raise


def get_run(db: Session, run_id: uuid.UUID) -> DiffRun:
    run = db.get(DiffRun, run_id)
    if run is None:
        raise not_found("比对批次", run_id)
    return run


def list_runs(
    db: Session,
    document_key: str | None = None,
    limit: int = 30,
    *,
    active_only: bool = False,
) -> list[dict]:
    query = select(DiffRun).order_by(DiffRun.created_at.desc()).limit(limit)
    if document_key:
        query = query.where(DiffRun.document_key == document_key)
    if active_only:
        query = query.where(DiffRun.is_active.is_(True))
    runs = db.execute(query).scalars().all()
    return [serialize_run(run, db) for run in runs]


def get_active_run(db: Session, base_document_id: uuid.UUID, target_document_id: uuid.UUID) -> DiffRun | None:
    return db.execute(
        select(DiffRun).where(
            DiffRun.base_document_id == base_document_id,
            DiffRun.target_document_id == target_document_id,
            DiffRun.is_active.is_(True),
        )
    ).scalars().first()


def serialize_run(run: DiffRun, db: Session | None = None) -> dict:
    base = db.get(Document, run.base_document_id) if db else None
    target = db.get(Document, run.target_document_id) if db else None
    payload = {
        "id": str(run.id),
        "document_key": run.document_key,
        "title": target.title if target else "",
        "base_version_id": str(run.base_document_id),
        "target_version_id": str(run.target_document_id),
        "base_label": base.version_label if base else run.stats.get("base_label", ""),
        "target_label": target.version_label if target else run.stats.get("target_label", ""),
        "status": run.status,
        "algorithm_version": run.algorithm_version,
        "rule_bundle_version": run.rule_bundle_version,
        "rule_change_summary": run.rule_config.get("change_summary", "") if run.rule_config else "",
        "is_active": run.is_active,
        "lineage": run.lineage,
        "recalc_of_id": str(run.recalc_of_id) if run.recalc_of_id else None,
        "root_run_id": str(run.root_run_id) if run.root_run_id else None,
        "stats": run.stats,
        "severity_counts": run.severity_counts,
        "change_count": run.change_count,
        "error_message": run.error_message,
        "created_at": run.created_at.isoformat() if run.created_at else None,
        "started_at": run.started_at.isoformat() if run.started_at else None,
        "finished_at": run.finished_at.isoformat() if run.finished_at else None,
        "published_at": run.published_at.isoformat() if run.published_at else None,
        "publish_note": run.publish_note,
    }
    return payload


def serialize_change(change: ChangeItem) -> dict:
    return {
        "id": str(change.id),
        "diff_run_id": str(change.diff_run_id),
        "rule_bundle_version": change.rule_bundle_version,
        "item_key": change.item_key,
        "change_type": change.change_type,
        "change_type_label": CHANGE_TYPE_LABELS.get(change.change_type, change.change_type),
        "severity": change.severity,
        "severity_label": SEVERITY_LABELS.get(change.severity, change.severity),
        "clause_no_old": change.clause_no_old,
        "clause_no_new": change.clause_no_new,
        "clause_label_old": change.clause_label_old,
        "clause_label_new": change.clause_label_new,
        "old_text": change.old_text,
        "new_text": change.new_text,
        "similarity": round(float(change.similarity or 0), 4),
        "edit_ratio": round(float(change.edit_ratio or 0), 4),
        "keywords": change.keywords or [],
        "industries": change.industries or [],
        "reason": change.reason,
        "confirmed": change.confirmed,
        "confirmed_at": change.confirmed_at.isoformat() if change.confirmed_at else None,
        "impact_industries": change.impact_industries or [],
        "impact_note": change.impact_note,
        "published": change.published,
        "published_at": change.published_at.isoformat() if change.published_at else None,
        "publish_note": change.publish_note,
    }


def list_changes(db: Session, run_id: uuid.UUID, *, severity: str | None = None, only_change_type: bool = True) -> list[dict]:
    query = select(ChangeItem).where(ChangeItem.diff_run_id == run_id)
    if severity:
        query = query.where(ChangeItem.severity == severity)
    query = query.order_by(ChangeItem.clause_no_new.nulls_last(), ChangeItem.clause_no_old.nulls_last())
    changes = db.execute(query).scalars().all()
    if only_change_type:
        changes = [change for change in changes if change.change_type != "unchanged"]
    return [serialize_change(change) for change in changes]
