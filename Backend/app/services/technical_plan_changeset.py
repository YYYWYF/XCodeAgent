"""技术规划 ChangeSet 的原子事务、事实源保护及每个子需求的后置核验。"""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from app.domain.application_planning_recovery import application_planning_sha256
from app.domain.technical_plan_changeset import TechnicalPlanChangeSet
from app.services.technical_plan_logical_patch import (
    apply_logical_operation, canonical_json, item_identity, path_segments, read_logical_value,
)

EDITABLE_SECTIONS = ("architecture", "entities", "api_contracts", "pages")


def revision_document(plan: dict[str, Any]) -> dict[str, Any]:
    """提取当前可执行业务文档，删除展示墓碑与标记，不复制平台派生字段。"""

    def clean(value: Any) -> Any:
        """递归剥离展示状态，墓碑仅用于审阅与下游清理，不是可修改节点。"""

        if isinstance(value, dict):
            return {key: clean(child) for key, child in value.items() if key != "_change_status"}
        if isinstance(value, list):
            return [clean(child) for child in value if not (
                isinstance(child, dict) and child.get("_change_status") == "deleted"
            )]
        return deepcopy(value)

    return {key: clean(plan[key]) for key in EDITABLE_SECTIONS}


def _escaped(segment: str) -> str:
    """编码逻辑路径段，名称内的斜杠和波浪号不会改变寻址层级。"""

    return segment.replace("~", "~0").replace("/", "~1")


def _effects(before: Any, after: Any, path: str = "") -> dict[str, tuple[bool, Any]]:
    """递归计算实际写入足迹，只用于验证操作效果，不参与候选合并。"""

    if canonical_json(before) == canonical_json(after):
        return {}
    old, new = None, None
    if isinstance(before, dict) and isinstance(after, dict):
        old, new = before, after
    elif isinstance(before, list) and isinstance(after, list):
        old_ids, new_ids = [item_identity(v) for v in before], [item_identity(v) for v in after]
        if all(old_ids + new_ids) and len(set(old_ids)) == len(old_ids) and len(set(new_ids)) == len(new_ids):
            # 同身份列表的纯顺序变化仍是真实效果，不能被按 ID 比较吞掉。
            if set(old_ids) == set(new_ids) and old_ids != new_ids:
                return {path: (True, deepcopy(after))}
            old, new = dict(zip(old_ids, before)), dict(zip(new_ids, after))
    if old is None:
        return {path: (True, deepcopy(after))}
    effects = {}
    for key in old.keys() | new.keys():
        child_path = path + "/" + _escaped(key)
        if key not in new:
            effects[child_path] = (False, None)
        elif key not in old:
            effects[child_path] = (True, deepcopy(new[key]))
        else:
            effects.update(_effects(old[key], new[key], child_path))
    return effects


def _overlaps(left: str, right: str) -> bool:
    """判断两项逻辑路径是否相同或互为祖先，识别子需求间的覆盖冲突。"""

    a, b = path_segments(left), path_segments(right)
    return a[:len(b)] == b or b[:len(a)] == a


def _validate_scope(operation: Any, editable_sections: tuple[str, ...], forbidden_value_keys: set[str]) -> None:
    """保护事实源而非按页签推断修改白名单，读取和写入均不可越过技术业务边界。"""

    for path in [operation.path, *([operation.from_path] if operation.op == "move" else [])]:
        segments = path_segments(path)
        if segments[0] not in editable_sections or any(segment in {"_change_status", "_app_change_status"} for segment in segments):
            raise ValueError(f"目标不属于可编辑技术事实，请进入对应正式修订流程：{path}。")
    # 展示标记和派生授权字段不能通过父对象替换夹带进来。
    def check(value: Any) -> None:
        """递归检查输入业务内容，拒绝模型对平台状态和权限事实的直接写入。"""

        if isinstance(value, dict):
            forbidden = forbidden_value_keys & value.keys()
            if forbidden:
                raise ValueError("操作值不得夹带平台派生或上游权限字段：" + "、".join(sorted(forbidden)))
            for child in value.values():
                check(child)
        elif isinstance(value, list):
            for child in value:
                check(child)
    if operation.op in {"add", "replace"}:
        check(operation.value)


def verify_revision_effects(candidate: dict[str, Any], effects: dict[str, tuple[bool, Any]]) -> None:
    """校验最终物化结果仍落实每项操作，不允许平台修复静默撤销已执行修改。"""

    for path, expected in effects.items():
        exists, value = read_logical_value(candidate, path)
        if exists != expected[0] or (exists and canonical_json(value) != canonical_json(expected[1])):
            raise ValueError(f"修订效果被后续处理覆盖，不能报告修改成功：{path}。")


def apply_document_changeset(
    document: dict[str, Any], changeset: TechnicalPlanChangeSet | dict[str, Any], *, request: str,
    editable_sections: tuple[str, ...], forbidden_value_keys: set[str],
) -> tuple[dict[str, Any], dict[str, tuple[bool, Any]]]:
    """在同一副本执行所有子需求，格式、基线、冲突、无变化任一失败均不提交。"""

    changeset = TechnicalPlanChangeSet.model_validate(changeset)
    if changeset.base_sha256 != application_planning_sha256(document):
        raise ValueError("ChangeSet baseSha256 与当前草稿不一致，请基于最新草稿重新解析。")
    # 每项原文需要覆盖整次请求；只忽略分隔符，不允许模型漏掉后半条调整。
    covered = [False] * len(request)
    for intent in changeset.intents:
        start = request.find(intent.source_text)
        if start < 0:
            raise ValueError(f"子需求 {intent.id} 的 sourceText 必须来自用户原始请求，不能自行改写。")
        for index in range(start, start + len(intent.source_text)):
            covered[index] = True
    separators = set(" ，,。；;、\n\r\t")
    if any(not covered[index] and char not in separators for index, char in enumerate(request)):
        raise ValueError("ChangeSet 子需求未覆盖用户完整原文，请保留每一项调整后重新提交。")
    working = deepcopy(document)
    claimed: list[tuple[str, str]] = []
    final_effects = {}
    for intent in changeset.intents:
        if intent.source_text not in request:
            raise ValueError(f"子需求 {intent.id} 的 sourceText 必须来自用户原始请求，不能自行改写。")
        before = deepcopy(working)
        for operation in intent.operations:
            _validate_scope(operation, editable_sections, forbidden_value_keys)
            if operation.op != "test":
                for path in [operation.path, *([operation.from_path] if operation.op == "move" else [])]:
                    if any(owner != intent.id and _overlaps(path, previous) for owner, previous in claimed):
                        raise ValueError(f"子需求之间存在覆盖冲突：{intent.id}，{path}。请明确最终要求。")
                    claimed.append((intent.id, path))
            try:
                apply_logical_operation(working, operation)
            except ValueError as exc:
                raise ValueError(f"子需求 {intent.id}（{intent.source_text}）未落实：{exc}") from exc
        effects = _effects(before, working)
        if not effects:
            raise ValueError(f"子需求 {intent.id}（{intent.source_text}）未产生实际变化，不能当作修改成功。")
        final_effects.update(effects)
    verify_revision_effects(working, final_effects)
    if canonical_json(document) == canonical_json(working):
        raise ValueError("本次要求未落实：执行前后内容完全一致。")
    return working, final_effects


def apply_technical_plan_changeset(
    document: dict[str, Any], changeset: TechnicalPlanChangeSet | dict[str, Any], *, request: str,
) -> tuple[dict[str, Any], dict[str, tuple[bool, Any]]]:
    """技术规划只声明事实源边界，实际执行复用跨产物的同一原子执行器。"""

    return apply_document_changeset(
        document, changeset, request=request, editable_sections=EDITABLE_SECTIONS,
        forbidden_value_keys={"_change_status", "_app_change_status", "authorization_manifest", "resourceKey", "roles", "permissionBindings"},
    )
