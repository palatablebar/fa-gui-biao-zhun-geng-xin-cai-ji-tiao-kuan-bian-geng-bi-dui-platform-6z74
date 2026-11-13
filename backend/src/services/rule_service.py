"""规则包服务：代码侧注册表与 rule_bundles 表同步、查询与默认口径解析。"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.models.rule_bundle import RuleBundle
from src.services.rule_registry import (
    CURRENT_RULE_BUNDLE_VERSION,
    RuleContext,
    get_rule_context,
    registered_versions,
)

# 规则包展示名（注册表只存技术版本与配置，名字集中在这里维护）。
BUNDLE_NAMES: dict[str, str] = {
    "1.0.0": "历史口径 v1（配对 0.55 / 变更 0.86 / 一般阈值 0.08）",
    "2.0.0": "新口径 v2（配对 0.75 / 变更 0.80 / 一般阈值 0.05 + 强制措辞升级）",
}


def sync_rule_bundles(db: Session) -> int:
    """把注册表幂等 upsert 到 rule_bundles 表；返回写入条数。

    已存在的版本只更新配置快照（注册表是事实来源），is_current 以注册表当前版本为准。
    """
    count = 0
    for version in registered_versions():
        context = get_rule_context(version)
        bundle = db.get(RuleBundle, version)
        if bundle is None:
            bundle = RuleBundle(version=version)
            db.add(bundle)
        bundle.name = BUNDLE_NAMES.get(version, f"规则包 {version}")
        bundle.change_summary = context.change_summary
        bundle.rule_config = context.to_dict()
        bundle.is_current = version == CURRENT_RULE_BUNDLE_VERSION
        count += 1
    db.flush()
    return count


def list_rule_bundles(db: Session) -> list[dict]:
    rows = db.execute(select(RuleBundle).order_by(RuleBundle.version)).scalars().all()
    if not rows:  # 表还没同步（如测试库）时直接读注册表，保证接口可用。
        sync_rule_bundles(db)
        rows = db.execute(select(RuleBundle).order_by(RuleBundle.version)).scalars().all()
    return [serialize_bundle(row) for row in rows]


def get_bundle_context(db: Session, version: str) -> RuleContext:
    """校验版本并返回上下文；未知版本抛 KeyError 由调用方转 AppError。"""
    return get_rule_context(version)


def serialize_bundle(bundle: RuleBundle) -> dict:
    return {
        "version": bundle.version,
        "name": bundle.name,
        "change_summary": bundle.change_summary,
        "is_current": bundle.is_current,
        "rule_config": bundle.rule_config,
    }
