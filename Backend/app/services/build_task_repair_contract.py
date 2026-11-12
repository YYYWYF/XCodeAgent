"""单任务修复的精确文件边界、源码证据和确定性任务编译。"""

from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path, PurePosixPath
import re

from app.domain.build_task_repair import TaskRepairProposal
from app.services.build_task_planner import replace_build_task_plan_tasks, _template_boundary_errors
from app.services.business_acceptance import compile_business_acceptance
from app.services.engineering_acceptance import compile_engineering_acceptance
from app.workspace.task_documents import _TASK_RUNTIME_FIELDS


def digest(value) -> str:
    """对展开后的语义内容计算稳定摘要，绑定草稿及源码证据。"""
    return sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def repair_file(workspace: str, task: dict, name: str) -> Path:
    """仅允许真实工作区内的当前 owner 源码，拒绝通配、别名和模板公共文件。"""
    owner = task.get("owner")
    parts = name.split("/")
    if (owner not in {"frontend", "backend"} or name != name.strip()
            or any(part in {"", ".", ".."} or part.startswith(".") for part in parts)
            or any(char in name for char in "\\:*?[]{}")
            or any(re.fullmatch(r"(?:CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\..*)?", part, re.I) for part in parts)
            or not name.startswith(f"{owner}/src/")
            or PurePosixPath(name).suffix not in {".java", ".xml", ".yml", ".yaml", ".properties", ".ts", ".tsx", ".js", ".jsx", ".less", ".css", ".json"}):
        raise ValueError(f"修复文件必须是当前任务工程内的精确源码路径：{name}")
    if owner == "backend" and (not name.startswith(("backend/src/main/java/", "backend/src/main/resources/"))
            or name.startswith(("backend/src/main/java/com/cmbchina/backend/common/", "backend/src/main/java/com/cmbchina/backend/auth/", "backend/src/main/resources/mapper/auth/"))
            or name.endswith("/Application.java")):
        raise ValueError(f"不能通过单任务修复修改模板公共基础设施：{name}")
    if errors := _template_boundary_errors(task, paths=[name]):
        raise ValueError("；".join(errors))
    root = Path(workspace).resolve()
    target = root / name
    if target.resolve() != target or not target.resolve().is_relative_to(root) or target.is_dir():
        raise ValueError(f"修复路径不能穿过链接或指向目录：{name}")
    return target


def source_files(workspace: str, task: dict, paths: list[str]) -> tuple[dict, dict]:
    """读取有界完整源码并记录存在性和摘要，超限时明确失败而不截断证据。"""
    texts, signatures = {}, {}
    total = 0
    if len(set(paths)) > 80:
        raise ValueError("相关源码超过 80 个文件，不能安全进行单任务修复。")
    for name in sorted(set(paths)):
        path = repair_file(workspace, task, name)
        if path.is_file() and total + path.stat().st_size > 1024 * 1024:
            raise ValueError("相关源码超过 1 MiB，请缩小修复范围。")
        raw = path.read_bytes() if path.is_file() else None
        signatures[name] = sha256(raw).hexdigest() if raw is not None else None
        total += len(raw or b"")
        if total > 1024 * 1024:
            raise ValueError("相关源码超过 1 MiB，请缩小修复范围。")
        texts[name] = raw.decode("utf-8-sig") if raw is not None else None
    return texts, signatures


def task_view(task: dict) -> dict:
    """只公开审阅所需的任务文字和文件，不返回正式设计全文或内部输入。"""
    return {"title": task["title"], "description": task.get("description", ""),
            "files": deepcopy(task.get("change_scope", [])),
            "checks": [item["description"] for item in task.get("business_acceptance_checks", []) if item.get("description")]}


def related_inventory(workspace: str, task: dict, paths: list[str]) -> list[str]:
    """只枚举任务所在模块的源码名，帮助定向发现未进入原计划的 PO 等文件。"""
    roots = set()
    for name in paths:
        target = repair_file(workspace, task, name)
        root = target.parent
        for marker in ("/application/", "/domain/", "/infrastructure/", "/adapter/"):
            if marker in name:
                root = Path(workspace).resolve() / name.split(marker, 1)[0]
                break
        roots.add(root)
    names = set(paths)
    base = Path(workspace).resolve()
    for root in sorted(roots):
        if not root.is_dir():
            continue
        # 显式有界目录遍历且不穿过链接，避免被模块内的依赖目录或 junction 放大读取。
        pending = [root]
        visited = 0
        while pending and len(names) < 200 and visited < 100:
            directory = pending.pop()
            visited += 1
            for child in directory.iterdir():
                if child.resolve() != child or child.name.startswith("."):
                    continue
                if child.is_dir():
                    if child.name not in {"node_modules", "target", "dist", "build"}:
                        pending.append(child)
                elif child.suffix in {".java", ".xml", ".ts", ".tsx", ".json", ".less", ".css"}:
                    names.add(child.relative_to(base).as_posix())
                if len(names) >= 200:
                    break
    return sorted(names)


def compile_repaired_plan(state: dict, task_id: str, proposal: TaskRepairProposal, context: dict) -> dict:
    """仅替换目标任务的文件合同，保留职责和其他任务并重算完整 DAG。"""
    if proposal.decision != "propose":
        raise ValueError(proposal.reason)
    plan = state["build_task_plan"]
    original = plan["task_registry"][task_id]
    if any(item.get("operation") not in {"add", "modify"} for item in original.get("change_scope", [])):
        raise ValueError("当前单任务修复只支持新增和修改文件，包含删除或重命名的任务需要重规划。")
    workspace = state["workspace"]
    files = proposal.files
    delta_names = [item.path for item in files]
    if len(set(name.casefold() for name in delta_names)) != len(delta_names):
        raise ValueError("修复文件清单存在重复路径。")
    # 原文件清单与职责由平台保留，模型只追加解决报错所必需的增量。
    scope = {item["path"]: deepcopy(item) for item in original.get("change_scope", [])}
    names = list(original.get("target_files", []))
    deliverables = deepcopy(original["deliverables"])
    coverage = {item["id"]: list(item.get("paths", [])) for item in deliverables}
    for item in files:
        target = repair_file(workspace, original, item.path)
        if item.operation != ("modify" if target.is_file() else "add"):
            raise ValueError(f"文件操作与当前磁盘不一致：{item.path}")
        if len(set(item.deliverableIds)) != len(item.deliverableIds) or set(item.deliverableIds) - coverage.keys():
            raise ValueError(f"文件引用了未知或重复的交付职责：{item.path}")
        if item.path not in names:
            if item.path.casefold() in {name.casefold() for name in names}:
                raise ValueError("修复文件路径大小写与原计划不一致。")
            names.append(item.path)
        previous = scope.get(item.path)
        updated = {**(previous or {}), **item.model_dump(exclude={"deliverableIds"})}
        if previous and previous.get("description"):
            updated["description"] = previous["description"] + "\n修复补充：" + item.description
        scope[item.path] = updated
        for identity in item.deliverableIds:
            if item.path not in coverage[identity]:
                coverage[identity].append(item.path)
    for item in deliverables:
        if not coverage[item["id"]] or not set(item.get("paths", [])) <= set(coverage[item["id"]]):
            raise ValueError("修复不能移除原交付物的文件或职责。")
        item["paths"] = coverage[item["id"]]
    # 创建文件的阶段不构成永久独占；允许业务任务补充同模块已完成骨架中的 PO。
    for other in plan["task_registry"].values():
        if other["id"] == task_id:
            continue
        if set(other.get("target_files", [])) & set(names):
            if other.get("unit_id") != original.get("unit_id"):
                raise ValueError(f"文件属于其他 Unit 的任务 {other['id']}，需要任务重规划。")
    replacement = {key: deepcopy(value) for key, value in original.items()
                   if key not in _TASK_RUNTIME_FIELDS | {"completed_by_repair", "repair_closed_at"}}
    replacement.update(description=original.get("description", "") + "\n\n修复补充：" + proposal.repairInstructions, target_files=names,
        allowed_paths=list(names), change_scope=list(scope.values()),
        deliverables=deliverables, status="pending",
        retry_count=max(int(original.get("retry_count") or 0), int(state.get("task_repair_retry_count") or 0)) + 1)
    replacement["lock_scope"] = sorted(names)
    replacement = compile_engineering_acceptance([replacement], context)[0]
    replacement = compile_business_acceptance([replacement], context)[0]
    # 不允许重编译丢失原验收种类和正式预期；路径可变，业务预期必须原样保持。
    if business_expectations(original) != business_expectations(replacement):
        raise ValueError("修复后的业务验收预期发生变化，请检查正式输入或进入任务重规划。")
    registry = {key: replacement if key == task_id else deepcopy(value) for key, value in plan["task_registry"].items()}
    checked = replace_build_task_plan_tasks(deepcopy(plan), list(registry.values()),
        {**context, "_validate_task_scope": False, "_compile_auth_capability_dependencies": True},
        preserve_task_contract_ids=set(registry))
    # 文件重叠可能改变其他任务的并发投影；保留统一重算结果，防止运行时再次
    # 注解后与新快照产生合同漂移。其他业务字段和完成证据必须逐字段保持。
    scheduling_fields = {"executionMode", "assignedAgent", "directWriteReason", "can_run_in_parallel", "parallel_with", "parallel_reason"}
    for identity, previous in registry.items():
        if identity != task_id:
            actual = checked["task_registry"][identity]
            if ({key: value for key, value in actual.items() if key not in scheduling_fields}
                    != {key: value for key, value in previous.items() if key not in scheduling_fields}):
                raise ValueError(f"修复影响了其他任务 {identity} 的业务合同，不能提交。")
    errors = checked["task_graph"]["validation"]["errors"]
    if errors or checked["execution"]["blocked_batches"]:
        raise ValueError("修复后 DAG 校验未通过：" + "；".join(errors or ["存在阻断批次"]))
    checked["status"] = "ready"
    return checked


def business_expectations(task: dict) -> list[str]:
    """比较原正式业务预期和来源，不将可更新的验收文件路径算作业务变更。"""
    return sorted(digest({"kind": item["kind"], "expected": item.get("expected"), "sources": item.get("sources")})
                  for item in task.get("business_acceptance_checks", []))


