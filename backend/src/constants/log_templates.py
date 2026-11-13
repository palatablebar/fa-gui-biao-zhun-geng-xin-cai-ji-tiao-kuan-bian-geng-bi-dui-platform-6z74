"""审计日志动作模板（供 audit_service 复用）。"""
from __future__ import annotations

ACTION_LOGIN = "auth.login"
ACTION_CRAWL = "source.crawl"
ACTION_DIFF_RUN = "diff.run"
ACTION_DIFF_FAIL = "diff.fail"
ACTION_RECALC_RUN = "recalc.run"
ACTION_RECALC_FAIL = "recalc.fail"
ACTION_RECALC_SWITCH = "recalc.switch"
ACTION_CHANGE_CONFIRM = "change.confirm"
ACTION_CHANGE_PUBLISH = "change.publish"
ACTION_SOURCE_TOGGLE = "source.toggle"
ACTION_SWEEP = "source.sweep"

AUDIT_ACTIONS: tuple[str, ...] = (
    ACTION_LOGIN,
    ACTION_CRAWL,
    ACTION_DIFF_RUN,
    ACTION_DIFF_FAIL,
    ACTION_RECALC_RUN,
    ACTION_RECALC_FAIL,
    ACTION_RECALC_SWITCH,
    ACTION_CHANGE_CONFIRM,
    ACTION_CHANGE_PUBLISH,
    ACTION_SOURCE_TOGGLE,
    ACTION_SWEEP,
)
