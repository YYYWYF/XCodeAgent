"""从正式任务编译前端执行输入和精确文件授权。"""

from pathlib import Path, PurePosixPath
from stat import S_ISDIR, S_ISREG
from typing import Any

from app.agents.frontend.project_plan_context import (
    FrontendTaskProjectionError,
    project_frontend_task_project_plan,
)
from app.services.builtin_skills import FRONTEND_ENGINEERING_SKILL_NAME
from app.services.build_task_planner import tasks_from_build_task_plan
from app.services.template_state import (
    TEMPLATE_STATE_RELATIVE_PATH, assert_template_context_matches,
    load_template_state, validate_template_context,
)
from app.services.authorization_frontend_projection import RESOURCES_RELATIVE_PATH

API = "frontend.api_module"
PAGE = "frontend.page"
STATIC = "frontend.static_data_module"
SERVICE = "/frontend/src/apis/service.ts"
BUSINESS_DIRS = {"apis", "pages", "components", "constants", "typings", "hooks", "utils"}
PROTECTED = {
    "apis/auth.ts", "apis/authorization.ts", "apis/login.ts", "apis/service.ts", "apis/welcome.ts",
    "constants/index.ts", "constants/routes.ts", "constants/routes.tsx", "constants/resources.ts",
    "constants/layout.ts", "constants/yst.ts", "constants/login.ts", "constants/menus.ts",
    "typings/index.ts", "typings/workbench.ts", "typings/yst.ts", "typings/config.ts",
    "typings/routes.ts", "typings/login.ts", "typings/authorization.ts",
    "utils/route.tsx", "utils/workbench.tsx", "utils/pageidentity.ts", "utils/pageroutetree.ts",
    "utils/pageroutes.tsx",
    "hooks/useguard.ts", "hooks/usepermission.ts", "hooks/usepagemenus.ts",
}
PROTECTED_DIRS = {
    "components/appstateview", "components/authorization", "components/errorboundary",
    "pages/login", "pages/logout", "pages/authorizationmanagement", "pages/system", "pages/defaultpage",
}
STEPS = {
    PAGE: (
        "完整理解业务、布局、动作、状态、权限、导航及规范；按需核对本任务已声明的 API、设计稿、模板组件、类型和常量依赖，确定实施方案。不要求读完全部依赖。",
        "先确定容器、表格或表单、字段和状态展示，保留合规结构并集中修改。API、权限和导航在 Step 3 接入。",
        "接入 API、useRequest、Permission、事件、导航和状态，复用 Step 1 核对结果及 Step 2 布局；仅当文件版本变化、核对未完成或出现明确契约冲突时，对已授权精确依赖补充读取；读取失败不得猜测接口或扩大搜索范围。",
        "只读核对交付物及 API 接入后结束，提供结构化结果。",
    ),
}


def task_kind(task: dict) -> str:
    """从正式交付物确定职责，避免把 repair 类型误认成另一类任务。"""
    kinds = {item.get("kind") for item in task.get("deliverables", [])}
    return next(iter(kinds)) if len(kinds) == 1 else ""


def virtual_file(value: Any) -> str:
    """只接受规范文件路径，拒绝替代分隔符、点段、通配符和宿主路径格式。"""
    if not isinstance(value, str) or set(value) & set('\\:*?[]{}\x00'):
        raise ValueError("文件必须使用规范虚拟路径")
    path = value.removeprefix("/")
    if any(part in {"", ".", ".."} or part != part.strip() or part.endswith(".")
           for part in path.split("/")):
        raise ValueError("文件必须使用规范虚拟路径")
    return "/" + path


def _business_path(path: str, kind: str) -> bool:
    """校验业务文件所在目录及模板受管文件保护边界。"""
    if not path.startswith("/frontend/src/"):
        return False
    parts = PurePosixPath(path.removeprefix("/frontend/src/")).parts
    location = "/".join(parts).casefold()
    return (
        len(parts) >= 2 and parts[0] in BUSINESS_DIRS
        and not any(part.startswith(".") or part.casefold() == "node_modules" for part in parts)
        and location not in PROTECTED and "/".join(parts[:2]).casefold() not in PROTECTED_DIRS
        and PurePosixPath(path).suffix in {".ts", ".tsx", ".css", ".less", ".scss"}
        and ((parts[0] == "apis") == (kind in {API, STATIC}))
    )


def _target_operations(task: dict, kind: str) -> dict[str, str]:
    """交叉验证精确目标、任务操作与原有 allowed_paths。"""
    from app.services.engineering_acceptance_verifier import _path_matches_any

    targets = {virtual_file(path) for path in task.get("target_files", [])}
    operations = {}
    for change in task.get("change_scope", []):
        path, operation = virtual_file(change["path"]), change["operation"]
        if operation not in {"add", "modify", "delete"} or path in operations:
            raise ValueError("任务修改操作无效")
        operations[path] = operation
    patterns = [p.lstrip("/") for p in task.get("allowed_paths", [])]
    if (not targets or operations.keys() - targets
        or len({path.casefold() for path in targets}) != len(targets)
        or any(not _business_path(path, kind) or not _path_matches_any(path[1:], patterns)
               for path in targets)
        or (kind == API and len(targets) != 1)):
        raise ValueError("任务的精确目标文件未获授权")
    if kind == PAGE:
        page_prefix = "/frontend/src/pages/"
        entries = {virtual_file(path) for item in task["deliverables"]
                   for path in item.get("paths", [])}
        directories = sorted({path.removesuffix("index.tsx") for path in entries & targets
                              if path.startswith(page_prefix) and path.endswith("/index.tsx")},
                             key=len)
        if (not directories or directories[0] == page_prefix
            or any(path.startswith(page_prefix) and not path.startswith(directories[0])
                   for path in targets)):
            raise FrontendTaskProjectionError(task.get("id"), "task_packet", "page_target_paths")
    undeclared = "read" if task.get("kind") == "repair" and operations else "write"
    return {path: operations.get(path, undeclared) for path in sorted(targets)}


def _design_path(ui_designs: dict | None, page_id: str) -> str:
    """只信任同一页面唯一的已确认设计稿标识。"""
    matches = [
        item for item in (ui_designs or {}).get("pages", [])
        if item.get("pageId") == page_id and item.get("status") == "confirmed"
    ]
    if len(matches) > 1:
        raise ValueError("页面有多个已确认设计稿")
    if not matches:
        return ""
    key = matches[0].get("page_key")
    if not isinstance(key, str) or "/" in key:
        raise ValueError("设计稿路径无效")
    return virtual_file(f"/.devagentstudio/ui-design/pages/{key}/index.tsx")


def frontend_dependencies(
    task: dict, build_task_plan: dict, required: set[tuple[str, str]],
) -> list[dict]:
    """用正式 Contract/Endpoint 组合筛选已完成的直接依赖。"""
    if not required:
        return []
    registry = {item["id"]: item for item in tasks_from_build_task_plan(build_task_plan)}
    parent = registry.get(task.get("repairs", {}).get("task_id"), {})
    dependencies = {*task.get("dependencies", []), *parent.get("dependencies", [])}
    modules = {}
    for candidate in registry.values():
        if (candidate["id"] not in dependencies
            or candidate.get("status") not in {"completed", "already_satisfied"}
            or candidate.get("owner") != "frontend"):
            continue
        kind = task_kind(candidate)
        if kind not in {API, STATIC}:
            continue
        refs = candidate.get("source_refs", {})
        declared_contracts = refs.get("api_contract_ids", [])
        pairs = {
            (endpoint["api_contract_id"], endpoint["endpoint_id"])
            for check in candidate.get("business_acceptance_checks", [])
            if check.get("kind") in {"frontend.api_contract", "frontend.static_data_contract"}
            for endpoint in check.get("expected", {}).get("endpoints", [])
            if endpoint.get("api_contract_id") and endpoint.get("endpoint_id")
        }
        for item in candidate.get("deliverables", []):
            endpoint = item.get("target_id")
            identities = {pair for pair in pairs if pair[1] == endpoint}
            if not identities and len(declared_contracts) == 1:
                identities = {(declared_contracts[0], endpoint)}
            matches = identities & required
            if len(identities) != 1 or not matches:
                continue
            for value in item.get("paths", []):
                path = virtual_file(value)
                if _business_path(path, kind):
                    module = modules.setdefault(path, {
                        "path": path, "kind": kind, "endpoints": [],
                    })
                    contract_id, endpoint_id = next(iter(matches))
                    binding = {"api_contract_id": contract_id, "endpoint_id": endpoint_id}
                    if binding not in module["endpoints"]:
                        module["endpoints"].append(binding)
    return list(modules.values())


def _standard_rules(kind: str) -> dict:
    """为两种职责分派固定规范、可验证规则和按需示例。"""
    page = kind == PAGE
    return {
        "standard_id": FRONTEND_ENGINEERING_SKILL_NAME,
        "standard_version": "1.0",
        "required_rule_ids": ["FE-API-BOUNDARY", "FE-TYPES", "FE-CODE-LAYERING",
                              "FE-DATA-PRECISION", "FE-NAMING", *(
            ["FE-API-USE-REQUEST", "FE-API-UI-DECOUPLING", "FE-CONSTANTS",
             "FE-PRO-COMPONENTS", "FE-HOOKS", "FE-ERROR-PRESENTATION",
             "FE-REACT-STATE", "FE-RENDER-SECURITY", "FE-LIST-KEY", "FE-JSX",
             "FE-FORM", "FE-SELECTION"] if page else []
        )],
        "example_refs": ["api-request-patterns", *(
            ["component-patterns", "page-structure-examples"] if page else []
        )],
        "acceptance_rule_ids": ["FE-NO-DIRECT-HTTP", "FE-NO-UI-REQUEST-PROP",
                                "FE-QUERY-USE-REQUEST"] if page else [],
    }


def _template_read_dependencies(workspace: str | None, context: dict, task_id: str) -> list[str]:
    """从当前模板受管记录解析权限源码，只在缺少记录时核对有限约定位置。"""
    try:
        if not workspace:
            raise ValueError("缺少工作区")
        root = Path(workspace).resolve(strict=True)

        def checked_file(value: str) -> Path:
            """逐层拒绝链接和非普通文件，保证虚拟授权与物理路径一致。"""
            path = virtual_file(value)
            current = root
            parts = path[1:].split("/")
            for index, part in enumerate(parts):
                current = current / part
                info = current.lstat()
                final = index == len(parts) - 1
                junction = getattr(current, "is_junction", None)
                if (junction and junction()) or not (
                    S_ISREG(info.st_mode) and info.st_nlink == 1 if final else S_ISDIR(info.st_mode)
                ):
                    raise ValueError(f"不安全依赖：{path}")
            if current.resolve(strict=True) != current:
                raise ValueError(f"不安全映射：{path}")
            return current

        checked_file(TEMPLATE_STATE_RELATIVE_PATH.as_posix())
        state = load_template_state(root)
        assert_template_context_matches(state, context)
        # 只从 authorization 自身安装清单挑选 Permission 源文件，不使用页面 import。
        candidates = set()
        for artifact in state["installedArtifacts"].values():
            path = virtual_file(artifact["target"])
            source = PurePosixPath(path)
            if (artifact["capabilityId"] == "authorization"
                and path.startswith("/frontend/src/components/")
                and source.suffix in {".ts", ".tsx"}
                and (source.stem == "Permission"
                     or source.stem == "index" and source.parent.name == "Permission")):
                candidates.add(path)
        if not candidates:
            # 缺少精确记录时仅核对 authorization 受管目录的四个固定候选；链接也须报错。
            for relative in ("Permission.tsx", "Permission.ts", "Permission/index.tsx", "Permission/index.ts"):
                path = "/frontend/src/components/authorization/" + relative
                physical = root / path[1:]
                if physical.exists() or physical.is_symlink():
                    candidates.add(path)
        if len(candidates) != 1:
            raise ValueError("Permission 候选缺失或不唯一")
        paths = sorted({*candidates, virtual_file(RESOURCES_RELATIVE_PATH.as_posix())})
        for path in paths:
            checked_file(path)
        return paths
    except (ValueError, TypeError, OSError) as exc:
        raise FrontendTaskProjectionError(
            task_id, "task_packet", f"template_read_dependency_unresolved: {exc}",
        ) from exc


def frontend_task_packet(
    project_plan: dict, build_task_plan: dict, task: dict, *,
    ui_designs: dict | None = None, page_template: dict | None = None,
    template_page_id: str | None = None, workspace: str | None = None,
) -> dict:
    """构造单任务实施契约，任何范围歧义在调用模型前失败。"""
    kind = task_kind(task)
    try:
        if kind not in {API, PAGE}:
            raise ValueError("任务不属于受控前端职责")
        plan_view = project_frontend_task_project_plan(project_plan, [task])
        operations = _target_operations(task, kind)
        template = build_task_plan.get("template_context")
        context = validate_template_context(template) if template else {}
        refs = task.get("source_refs", {})
        page_id = task["unit_id"].removeprefix("page:") if kind == PAGE else ""
        design = _design_path(ui_designs, page_id) if page_id else ""
        implementation = {
            **plan_view,
            "template_context": {
                "effective_capabilities": sorted(context.get("effective_capabilities", {})),
            },
            "frontend_standards": _standard_rules(kind),
        }
        if task.get("kind") == "repair":
            implementation["repair_instructions"] = str(task.get("description") or "")[:2000]
        if kind == PAGE:
            entries = {virtual_file(path) for deliverable in task["deliverables"]
                       for path in deliverable.get("paths", [])}
            implementation["page_entry"] = min(
                (path for path in entries & operations.keys()
                 if path.startswith("/frontend/src/pages/") and path.endswith("/index.tsx")),
                key=lambda path: (len(path), path),
            )
            implementation["authorization_actions"] = [
                {key: action[key] for key in (
                    "action_id", "actionId", "resource_key", "resourceKey", "mode",
                    "resourceConstant",
                ) if key in action}
                for action in refs.get("authorization", {}).get("actions", [])
            ]
            if ("authorization" in context.get("effective_capabilities", {})
                and implementation["authorization_actions"]):
                implementation["template_read_dependencies"] = _template_read_dependencies(
                    workspace, context, task.get("id"),
                )
            required = {(contract["id"], endpoint["id"])
                        for contract in plan_view["api_contracts"] for endpoint in contract["endpoints"]}
            implementation["api_dependencies"] = frontend_dependencies(task, build_task_plan, required)
            bound = [(item["api_contract_id"], item["endpoint_id"])
                     for module in implementation["api_dependencies"] for item in module["endpoints"]]
            if required != set(bound) or len(bound) != len(required):
                raise FrontendTaskProjectionError(task.get("id"), "task_packet", "completed_api_dependency")
            support = [path for value in task.get("allowed_paths", [])
                       if value.lstrip("/").startswith(("frontend/src/constants/", "frontend/src/typings/"))
                       and value.endswith((".ts", ".tsx")) and not set(value) & set("*?[]{}")
                       and (path := virtual_file(value))
                       and _business_path(path, PAGE) and path not in operations]
            if support:
                implementation["type_and_constant_files"] = list(dict.fromkeys(support))
            if design:
                implementation["confirmed_design"] = design
            if page_template and template_page_id == page_id:
                from app.services.page_templates import load_template_source

                template_id = page_template["id"]
                implementation["page_template"] = {
                    "id": template_id, "source": load_template_source(template_id),
                }
        step_ids = [f"step_{index}" for index in range(1, 5)] if kind == PAGE else None
        return {
            "id": task["id"], "unit_id": task["unit_id"], "kind": kind, "owner": task["owner"],
            "stage": kind.rsplit(".", 1)[-1], "target_files": list(operations),
            "allowed_paths": list(operations),
            "change_scope": [{"path": path, "operation": op} for path, op in operations.items()],
            **({"execution_steps": step_ids,
                "step_contracts": dict(zip(step_ids, STEPS[kind], strict=True))}
               if kind == PAGE else {}),
            "implementation_contract": implementation,
            "acceptance_rules": list(dict.fromkeys(
                [check["kind"] for key in ("business_acceptance_checks", "acceptance_checks")
                 for check in task.get(key, []) if check.get("kind")]
            )),
        }
    except FrontendTaskProjectionError:
        raise
    except (ValueError, TypeError, KeyError, AttributeError, OSError) as exc:
        raise FrontendTaskProjectionError(task.get("id"), "task_packet", type(exc).__name__) from exc
