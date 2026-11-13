"""跨规则包对照的稳定条款键。

同一条款在不同规则包产出的结论里 item_key 必须一致，才能逐条标出新增/消失/改级。
对齐可能因阈值不同而换配对，因此键不能依赖配对结果，改由条款自身内容决定：
- 修改项：旧条款键 + 新条款键组合；
- 新增项：只含新条款键；删除项：只含旧条款键。
对照阶段再按单侧键把「修改拆散成的新增/删除」重新关联起来。
"""
from __future__ import annotations

import hashlib


def clause_key(label: str, text: str) -> str:
    """单条条款的稳定键：条号展示 + 正文指纹（条号重排不影响，正文改一个字也能区分）。"""
    digest = hashlib.sha1((text or "").encode("utf-8")).hexdigest()[:12]
    return f"{label or '条'}:{digest}"


def build_item_key(
    change_type: str,
    old_label: str,
    old_text: str,
    new_label: str,
    new_text: str,
) -> str:
    if change_type == "added":
        return "new:" + clause_key(new_label, new_text)
    if change_type == "removed":
        return "old:" + clause_key(old_label, old_text)
    return "mod:" + clause_key(old_label, old_text) + "->" + clause_key(new_label, new_text)


def side_keys(item: dict) -> tuple[str | None, str | None]:
    """取一条序列化变更的旧/单侧键，用于跨结论关联。"""
    old_key = clause_key(item.get("clause_label_old", ""), item.get("old_text", ""))
    new_key = clause_key(item.get("clause_label_new", ""), item.get("new_text", ""))
    if item.get("change_type") == "added":
        return None, "new:" + new_key
    if item.get("change_type") == "removed":
        return "old:" + old_key, None
    return "old:" + old_key, "new:" + new_key
