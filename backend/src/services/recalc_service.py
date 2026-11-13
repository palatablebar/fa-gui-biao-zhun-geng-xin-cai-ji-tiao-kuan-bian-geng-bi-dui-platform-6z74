"""按新规则包重算同一对版本：生成可对照的新结论，原子切换生效口径。

关键保证：
1. 不覆盖：重算产生**新的 DiffRun / ChangeItem**，旧批次原样保留，仍可查看与导出。
2. 逐条对照：新结论完整落库后，按条款稳定键比对新旧两份结论，
   标出 added（新增）/ disappeared（消失）/ regraded（改级）/ unchanged（不变）。
3. 原子切换：对照明细全部写入同一事务，最后一步才翻转 is_active；
   事务提交前统计与变更清单仍读旧结论，不会混用两个口径。
4. 失败安全：任何一步抛错整体回滚，旧批次保持 succeeded + is_active，重算记录落 failed。
5. 幂等：(source_run, 目标规则包) 维度去重；在途重算直接 409，已完成直接返回同一记录，
   重复执行不会重复生成结论。
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.config.database import session_scope
from src.constants import error_codes as codes
from src.constants.enums import (
    RECALC_DIFF_LABELS,
    DiffRunStatus,
    RecalcDiffStatus,
    RecalcStatus,
    RunLineage,
    Severity,
)
from src.constants.log_templates import (
    ACTION_RECALC_FAIL,
    ACTION_RECALC_RUN,
    ACTION_RECALC_SWITCH,
)
from src.models.change_item import ChangeItem
from src.models.diff_run import DiffRun
from src.models.recalculation import Recalculation, RecalculationItem
from src.models.user import User
from src.services import audit_service, diff_service
from src.services.item_keys import side_keys
from src.services.rule_registry import RuleContext, get_rule_context
from src.utils.errors import AppError, not_found


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ------------------------------------------------------------------ 发起 ----


def request_recalculation(
    db: Session,
    *,
    run_id: uuid.UUID,
    target_rule_bundle_version: str,
    actor: User,
    trace_id: str = "",
) -> Recalculation:
    """幂等发起重算。返回的 Recalculation 可能是在途/已完成的既有记录。"""
    source_run = db.get(DiffRun, run_id)
    if source_run is None:
        raise not_found("比对批次", run_id)
    if source_run.status != DiffRunStatus.SUCCEEDED.value:
        raise AppError(codes.DIFF_NOT_READY, detail=f"status={source_run.status}")

    try:
        context = get_rule_context(target_rule_bundle_version)
    except KeyError as exc:
        raise AppError(codes.RULE_BUNDLE_UNKNOWN, detail=str(exc)) from exc
    if context.bundle_version == source_run.rule_bundle_version:
        raise AppError(
            codes.RULE_BUNDLE_SAME,
            detail=f"run={source_run.rule_bundle_version} target={context.bundle_version}",
        )

    # 行锁串行化同一批次上的并发重算（PG 行锁；SQLite 写事务天然串行）。
    db.execute(select(DiffRun).where(DiffRun.id == source_run.id).with_for_update())

    # 先处理本批次的幂等：同一 source + 目标规则包的在途/已完成记录直接返回，
    # 保证「重复执行重算」永远拿到同一条记录。
    existing = db.execute(
        select(Recalculation).where(
            Recalculation.source_run_id == source_run.id,
            Recalculation.target_rule_bundle_version == context.bundle_version,
        )
    ).scalars().all()
    in_flight = [row for row in existing if row.status in (RecalcStatus.QUEUED.value, RecalcStatus.RUNNING.value)]
    if in_flight:
        raise AppError(codes.RECALC_IN_PROGRESS, detail=f"recalculation_id={in_flight[0].id}")
    done = next((row for row in existing if row.status == RecalcStatus.SUCCEEDED.value), None)
    if done is not None:
        # 已成功重算过同一口径：幂等返回，不再生成第二份结论。
        return done

    # 该版本对已有目标规则包的**成功**结论时不能再建一份（数据库唯一约束也会兜底）：
    # 引导到既有批次，避免重复结论。失败批次不在此列——它要留给下面的失败重发逻辑复用。
    target_existing = db.execute(
        select(DiffRun).where(
            DiffRun.base_document_id == source_run.base_document_id,
            DiffRun.target_document_id == source_run.target_document_id,
            DiffRun.rule_bundle_version == context.bundle_version,
            DiffRun.status == DiffRunStatus.SUCCEEDED.value,
        )
    ).scalars().first()
    if target_existing is not None:
        raise AppError(
            codes.RULE_BUNDLE_EXISTS,
            detail=f"run_id={target_existing.id} rule={context.bundle_version}",
            extra={
                "existing_run_id": str(target_existing.id),
                "rule_bundle_version": context.bundle_version,
                "is_active": target_existing.is_active,
            },
        )

    # 只剩 failed 记录：复用该行重新执行，不新增结论行。
    recalc = next((row for row in existing if row.status == RecalcStatus.FAILED.value), None)
    if recalc is None:
        recalc = Recalculation(
            source_run_id=source_run.id,
            requested_by=actor.id if actor else None,
            source_rule_bundle_version=source_run.rule_bundle_version,
            target_rule_bundle_version=context.bundle_version,
            status=RecalcStatus.QUEUED.value,
            diff_summary={},
            error_message="",
        )
        db.add(recalc)
    else:
        recalc.status = RecalcStatus.QUEUED.value
        recalc.error_message = ""
        recalc.new_run_id = None
        recalc.diff_summary = {}
    db.flush()
    audit_service.record(
        db,
        action=ACTION_RECALC_RUN,
        actor=actor,
        entity_type="recalculation",
        entity_id=recalc.id,
        detail={
            "source_run_id": str(source_run.id),
            "from_rule": source_run.rule_bundle_version,
            "to_rule": context.bundle_version,
        },
        trace_id=trace_id,
    )
    db.commit()

    _dispatch_recalc(recalc, db)
    db.refresh(recalc)
    return recalc


def _dispatch_recalc(recalc: Recalculation, db: Session) -> None:
    try:
        from src.config.celery_app import TASK_RECALC, celery_app

        celery_app.send_task(TASK_RECALC, args=[str(recalc.id)])
        return
    except Exception as exc:  # noqa: BLE001 - broker 异常退化为同步执行
        recalc.diff_summary = {**recalc.diff_summary, "dispatch": "inline_fallback", "dispatch_error": str(exc)[:200]}
        db.commit()
    execute_recalculation_in_session(db, recalc)


def execute_recalculation(recalc_id: uuid.UUID | str) -> None:
    """worker 入口：独立 session 执行重算。"""
    with session_scope() as db:
        recalc = db.get(Recalculation, uuid.UUID(str(recalc_id)))
        if recalc is None:
            return
        if recalc.status == RecalcStatus.SUCCEEDED.value:
            return
        execute_recalculation_in_session(db, recalc)


# -------------------------------------------------------------- 逐条对照 ----


def _index_side_keys(serialized: list[dict]) -> tuple[dict[str, list[dict]], dict[str, list[dict]]]:
    old_index: dict[str, list[dict]] = {}
    new_index: dict[str, list[dict]] = {}
    for item in serialized:
        old_key, new_key = side_keys(item)
        if old_key:
            old_index.setdefault(old_key, []).append(item)
        if new_key:
            new_index.setdefault(new_key, []).append(item)
    return old_index, new_index


def compare_changes(old_items: list[dict], new_items: list[dict]) -> list[dict]:
    """按条款稳定键对照两份结论，输出逐条 diff（含改级/新增/消失/不变）。

    匹配顺序：
    1) 修改项按新/旧条款键配对（覆盖配对微调）；
    2) 单边项（新增只有 new: 键、删除只有 old: 键）按同前缀键互配，
       两个口径都新增/都删除的同一条款因此判「不变」而不是一增一消失；
    3) 新口径剩余 = 新增，旧口径剩余 = 消失。
    """
    old_by_key, _ = _index_side_keys(old_items)
    _, new_by_key = _index_side_keys(new_items)
    matched_old: set[str] = set()
    matched_new: set[str] = set()
    results: list[dict] = []

    def take_old(candidates: list[dict]) -> dict | None:
        for candidate in candidates:
            if candidate["id"] not in matched_old:
                return candidate
        return None

    # 1) 修改项优先按「新条款键」配对，再退而按「旧条款键」配对。
    for new in new_items:
        if new["change_type"] != "modified":
            continue
        old_key, new_key = side_keys(new)
        old = take_old(old_by_key.get(_opposite_key(new_key), [])) if new_key else None
        if old is None and old_key:
            old = take_old(old_by_key.get(old_key, []))
        if old is None or old["change_type"] != "modified":
            continue
        matched_old.add(old["id"])
        matched_new.add(new["id"])
        results.append(_pair_row(old, new))

    # 2) 单边项按同前缀键互配（new:↔new:、old:↔old:）。
    for new in list(new_items):
        if new["id"] in matched_new or new["change_type"] == "modified":
            continue
        _, new_key = side_keys(new)
        old_key, _ = side_keys(new)
        old = None
        if new_key:
            old = take_old(old_by_key.get(new_key, []))
        if old is None and old_key:
            old = take_old(old_by_key.get(old_key, []))
        if old is not None:
            matched_old.add(old["id"])
            matched_new.add(new["id"])
            results.append(_pair_row(old, new))

    # 3) 新口径独有的条款 = 新增。
    for new in new_items:
        if new["id"] in matched_new:
            continue
        matched_new.add(new["id"])
        results.append(_solo_row(new, is_new=True))

    # 4) 旧口径独有、未被关联的条款 = 消失。
    for old in old_items:
        if old["id"] in matched_old:
            continue
        matched_old.add(old["id"])
        results.append(_solo_row(old, is_new=False))

    order = {
        RecalcDiffStatus.REGRADED.value: 0,
        RecalcDiffStatus.ADDED.value: 1,
        RecalcDiffStatus.DISAPPEARED.value: 2,
        RecalcDiffStatus.UNCHANGED.value: 3,
    }
    results.sort(key=lambda row: (order[row["diff_status"]], row["item_key"]))
    return results


def _opposite_key(key: str) -> str:
    return ("old:" + key[4:]) if key.startswith("new:") else ("new:" + key[4:])


def _as_uuid(value: object) -> uuid.UUID | None:
    if value is None or value == "":
        return None
    return value if isinstance(value, uuid.UUID) else uuid.UUID(str(value))


def _pair_row(old: dict, new: dict) -> dict:
    regraded = (old["change_type"], old["severity"]) != (new["change_type"], new["severity"])
    status = RecalcDiffStatus.REGRADED.value if regraded else RecalcDiffStatus.UNCHANGED.value
    # 复合键保证配对行不会与单边行（old:/new: 前缀）撞 item_key。
    pair_key = "pair:" + (old.get("item_key") or "") + "||" + (new.get("item_key") or "")
    return {
        "diff_status": status,
        "item_key": pair_key,
        "old_change_item_id": _as_uuid(old["id"]),
        "new_change_item_id": _as_uuid(new["id"]),
        "old_change_type": old["change_type"],
        "new_change_type": new["change_type"],
        "old_severity": old["severity"],
        "new_severity": new["severity"],
        "clause_label_old": new.get("clause_label_old") or old.get("clause_label_old", ""),
        "clause_label_new": new.get("clause_label_new") or old.get("clause_label_new", ""),
        "detail": {
            "old_reason": old.get("reason", ""),
            "new_reason": new.get("reason", ""),
            "old_similarity": old.get("similarity", 0),
            "new_similarity": new.get("similarity", 0),
        },
    }


def _solo_row(item: dict, *, is_new: bool) -> dict:
    if is_new:
        return {
            "diff_status": RecalcDiffStatus.ADDED.value,
            "item_key": item.get("item_key", ""),
            "old_change_item_id": None,
            "new_change_item_id": _as_uuid(item["id"]),
            "old_change_type": "",
            "new_change_type": item["change_type"],
            "old_severity": "",
            "new_severity": item["severity"],
            "clause_label_old": item.get("clause_label_old", ""),
            "clause_label_new": item.get("clause_label_new", ""),
            "detail": {"new_reason": item.get("reason", "")},
        }
    return {
        "diff_status": RecalcDiffStatus.DISAPPEARED.value,
        "item_key": item.get("item_key", ""),
        "old_change_item_id": _as_uuid(item["id"]),
        "new_change_item_id": None,
        "old_change_type": item["change_type"],
        "new_change_type": "",
        "old_severity": item["severity"],
        "new_severity": "",
        "clause_label_old": item.get("clause_label_old", ""),
        "clause_label_new": item.get("clause_label_new", ""),
        "detail": {"old_reason": item.get("reason", "")},
    }


def summarize_diff(rows: list[dict]) -> dict:
    counts = {status.value: 0 for status in RecalcDiffStatus}
    severity_delta: dict[str, int] = {}
    for row in rows:
        counts[row["diff_status"]] += 1
        if row["diff_status"] == RecalcDiffStatus.REGRADED.value:
            severity_delta[f"{row['old_severity']}->{row['new_severity']}"] = (
                severity_delta.get(f"{row['old_severity']}->{row['new_severity']}", 0) + 1
            )
    return {"diff_counts": counts, "severity_transitions": severity_delta}


# -------------------------------------------------------------- 执行重算 ----


def execute_recalculation_in_session(db: Session, recalc: Recalculation) -> Recalculation:
    """跑新口径结论 + 对照 + 原子切换，全部在一个事务里。"""
    if recalc.status == RecalcStatus.SUCCEEDED.value:
        return recalc

    source_run = db.get(DiffRun, recalc.source_run_id)
    if source_run is None:
        raise AppError(codes.NOT_FOUND, "被重算的比对批次不存在")

    recalc.status = RecalcStatus.RUNNING.value
    recalc.started_at = _now()
    db.commit()

    try:
        context = get_rule_context(recalc.target_rule_bundle_version)

        # 幂等兜底：同版本对 + 同规则包的结论已存在（例如上次执行在切换后中断），直接复用。
        new_run = db.execute(
            select(DiffRun).where(
                DiffRun.base_document_id == source_run.base_document_id,
                DiffRun.target_document_id == source_run.target_document_id,
                DiffRun.rule_bundle_version == context.bundle_version,
                DiffRun.id != source_run.id,
            )
        ).scalars().first()

        if new_run is None:
            new_run = DiffRun(
                document_key=source_run.document_key,
                base_document_id=source_run.base_document_id,
                target_document_id=source_run.target_document_id,
                requested_by=recalc.requested_by,
                status=DiffRunStatus.QUEUED.value,
                algorithm_version=diff_service.ALGORITHM_VERSION,
                rule_bundle_version=context.bundle_version,
                rule_config=context.to_dict(),
                is_active=False,  # 切换前绝不能生效，避免清单混用两个口径。
                lineage=RunLineage.RECALC.value,
                recalc_of_id=source_run.id,
                root_run_id=source_run.root_run_id or source_run.id,
                stats={"base_label": source_run.stats.get("base_label", ""),
                       "target_label": source_run.stats.get("target_label", "")},
                severity_counts={},
            )
            db.add(new_run)
            # 先独立提交批次骨架：后续算法即使失败回滚，失败批次仍可追溯，且保持 is_active=False。
            db.commit()
            db.refresh(new_run)

        if new_run.status != DiffRunStatus.SUCCEEDED.value:
            # 失败会在 execute_* 内部把 new_run 置 failed 并抛错；这里捕获后统一落重算失败态。
            diff_service.execute_diff_run_in_session(db, new_run)
            db.refresh(new_run)
            if new_run.status != DiffRunStatus.SUCCEEDED.value:
                raise AppError(codes.RECALC_FAILED, detail="新口径批次未成功完成")

        # 清掉历史失败遗留的对照明细，保证重跑后明细与新结论严格一致。
        old_rows = db.execute(
            select(RecalculationItem).where(RecalculationItem.recalculation_id == recalc.id)
        ).scalars().all()
        for row in old_rows:
            db.delete(row)
        db.flush()

        old_serialized = diff_service.list_changes(db, source_run.id)
        new_serialized = diff_service.list_changes(db, new_run.id)
        diff_rows = compare_changes(old_serialized, new_serialized)
        for row in diff_rows:
            db.add(RecalculationItem(recalculation_id=recalc.id, **row))
        summary = summarize_diff(diff_rows)

        # —— 原子切换：同事务最后一步翻转生效口径 ——
        siblings = db.execute(
            select(DiffRun).where(
                DiffRun.base_document_id == source_run.base_document_id,
                DiffRun.target_document_id == source_run.target_document_id,
            )
        ).scalars().all()
        for sibling in siblings:
            sibling.is_active = sibling.id == new_run.id

        recalc.new_run_id = new_run.id
        recalc.status = RecalcStatus.SUCCEEDED.value
        recalc.finished_at = _now()
        recalc.diff_summary = {
            **summary,
            "old_run_id": str(source_run.id),
            "new_run_id": str(new_run.id),
            "old_rule": recalc.source_rule_bundle_version,
            "new_rule": context.bundle_version,
            "old_change_count": len(old_serialized),
            "new_change_count": len(new_serialized),
            "old_severity_counts": source_run.severity_counts,
            "new_severity_counts": new_run.severity_counts,
        }
        audit_service.record(
            db,
            action=ACTION_RECALC_SWITCH,
            entity_type="recalculation",
            entity_id=recalc.id,
            detail={
                "source_run_id": str(source_run.id),
                "new_run_id": str(new_run.id),
                **summary,
            },
        )
        db.commit()
        return recalc
    except Exception as exc:  # noqa: BLE001 - 失败整体回滚，旧结论保持生效
        db.rollback()
        failed = db.get(Recalculation, recalc.id)
        if failed is not None:
            failed.status = RecalcStatus.FAILED.value
            failed.error_message = str(exc)[:500]
            failed.finished_at = _now()
            audit_service.record(
                db,
                action=ACTION_RECALC_FAIL,
                entity_type="recalculation",
                entity_id=failed.id,
                detail={"error": str(exc)[:300]},
            )
            db.commit()
        raise


# ------------------------------------------------------------------ 查询 ----


def list_recalculations(db: Session, run_id: uuid.UUID | None = None) -> list[dict]:
    query = select(Recalculation).order_by(Recalculation.created_at.desc())
    if run_id is not None:
        query = query.where(Recalculation.source_run_id == run_id)
    rows = db.execute(query).scalars().all()
    return [serialize_recalculation(row) for row in rows]


def get_recalculation(db: Session, recalc_id: uuid.UUID) -> Recalculation:
    recalc = db.get(Recalculation, recalc_id)
    if recalc is None:
        raise not_found("重算记录", recalc_id)
    return recalc


def get_recalculation_items(db: Session, recalc_id: uuid.UUID) -> list[dict]:
    rows = db.execute(
        select(RecalculationItem)
        .where(RecalculationItem.recalculation_id == recalc_id)
        .order_by(RecalculationItem.id)
    ).scalars().all()
    return [serialize_recalc_item(row) for row in rows]


def serialize_recalculation(recalc: Recalculation) -> dict:
    return {
        "id": str(recalc.id),
        "source_run_id": str(recalc.source_run_id),
        "new_run_id": str(recalc.new_run_id) if recalc.new_run_id else None,
        "source_rule_bundle_version": recalc.source_rule_bundle_version,
        "target_rule_bundle_version": recalc.target_rule_bundle_version,
        "status": recalc.status,
        "diff_summary": recalc.diff_summary,
        "error_message": recalc.error_message,
        "created_at": recalc.created_at.isoformat() if recalc.created_at else None,
        "started_at": recalc.started_at.isoformat() if recalc.started_at else None,
        "finished_at": recalc.finished_at.isoformat() if recalc.finished_at else None,
    }


def serialize_recalc_item(row: RecalculationItem) -> dict:
    return {
        "id": str(row.id),
        "recalculation_id": str(row.recalculation_id),
        "diff_status": row.diff_status,
        "diff_status_label": RECALC_DIFF_LABELS.get(row.diff_status, row.diff_status),
        "item_key": row.item_key,
        "old_change_item_id": str(row.old_change_item_id) if row.old_change_item_id else None,
        "new_change_item_id": str(row.new_change_item_id) if row.new_change_item_id else None,
        "old_change_type": row.old_change_type,
        "new_change_type": row.new_change_type,
        "old_severity": row.old_severity,
        "new_severity": row.new_severity,
        "clause_label_old": row.clause_label_old,
        "clause_label_new": row.clause_label_new,
        "detail": row.detail or {},
    }
