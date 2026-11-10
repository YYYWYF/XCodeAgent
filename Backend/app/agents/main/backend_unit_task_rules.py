"""把 Bootstrap 和后端模块职责投影为任务规划规则。"""

from __future__ import annotations

import re
from typing import Any

from app.services.unit_generation_contracts import UnitGenerationContext
from app.services.builtin_skills import (
    SPRINGBOOT_TEMPLATE_BOUNDARY_SKILL_NAME,
    resolve_builtin_skills_root,
)
from app.services.unit_task_manifest import (
    BACKEND_MODULE_PREFIX, PHYSICAL_SOURCE_TYPES, backend_bootstrap_task_id, backend_stage_manifest,
)


def _text(value: Any) -> str:
    """只读取无需修剪的非空正式身份。"""

    return value if isinstance(value, str) and value and value == value.strip() else ""


def _bootstrap_rules(context: UnitGenerationContext) -> tuple[str, ...]:
    """为后端公共依赖与框架启用生成唯一 bootstrap Task 规则。"""

    source_types = sorted({
        _text(requirement.source_refs.get("data_source_type"))
        for requirement in context.generation_requirements
        if _text(requirement.source_refs.get("data_source_type"))
    })
    if (
        not source_types
        or set(source_types) - PHYSICAL_SOURCE_TYPES
        or any(
            _text(requirement.source_refs.get("kind")) != "backend.bootstrap"
            for requirement in context.generation_requirements
        )
    ):
        raise ValueError(
            "backend:bootstrap 必须只包含 database/external_api 的 "
            "backend.bootstrap 增量职责。"
        )
    capabilities = []
    if "database" in source_types:
        capabilities.append("MyBatis-Plus/MySQL")
    if "external_api" in source_types:
        capabilities.append("Spring Cloud OpenFeign")
    rules = [
        f"Emit exactly one root Task with id `{backend_bootstrap_task_id(context)}` and dependencies "
        "`[]`. It must cover every current backend.bootstrap requirement in one Task and "
        "declare one exact backend.bootstrap deliverable per requirement. Its capability "
        f"scope is {', '.join(capabilities) or 'the listed bootstrap requirements only'}.",
        f"Use unit_id `{context.unit_id}`, owner `backend`, task_type `backend.code` and status "
        "`pending`. target_files, allowed_paths and change_scope must list the same exact "
        "workspace-relative files; deliverable paths must stay within them. Give each deliverable "
        "a unique stable ID under the task ID. Use add for "
        "missing files and modify for existing files. Write titles and descriptions in Simplified Chinese.",
        "Plan only the minimum additions to backend/pom.xml and the required common configuration.",
        "This Unit must not generate endpoint Clients, transport DTOs, endpoint objects, "
        "repositories, services, Controllers, DDL, migrations, seed data, tests, builds, "
        "verification, or acceptance work.",
    ]
    if "database" in source_types:
        rules.append("Database: prepare only the required datasource and MyBatis bootstrap configuration.")
    if "external_api" in source_types:
        rules.append(
            "External API: add only the required OpenFeign dependency and missing @EnableFeignClients "
            "activation on the real Application.java or existing Feign configuration."
        )
    return tuple(rules)


def _backend_common_rules() -> tuple[str, ...]:
    """声明 Endpoint 级路径、命名、描述和权限边界。"""

    return (
        "Plan business files only under backend/src/main/java or backend/src/main/resources. "
        "Resolve the base package from Application.java or neighboring classes in the backend tree; "
        "resolve module/type names from the confirmed API Contract and Endpoint API Design. "
        "Never derive task identity, sources or deliverable targets from TechnicalPlan entity_ids.",
        "Path selection:\n"
        "- Existing implementation: modify its exact existing path and preserve existing members. "
        "Prefer reuse over a second implementation of the same responsibility.\n"
        "- New implementation: use the resolved module root + the layout directory + filename. "
        "Existing flat packages do not define the layout for new responsibilities.\n"
        "- Resolve one module root consistently from the backend tree and confirmed contracts; "
        "do not mechanically turn an API identifier such as product_api into a package name.\n"
        "- Directory migration is outside this plan. A directory listing proves existence, not method "
        "signatures or implementation compatibility.\n"
        "- Use add for missing files and modify for existing files or files created by earlier module tasks.",
        "File descriptions: use only sections needed by the file's actual responsibility, in Simplified Chinese. "
        "There is no mandatory five-section template. Put each included section on its own line. "
        "Simple DTO/domain data classes: list fields, types and contract-required nullability/validation; "
        "POs also need table/column/key mappings. Do not invent processing logic for data containers. "
        "Repository interfaces: method signatures and only caller-visible semantics needed to interpret them; "
        "leave query implementation details to RepositoryImpl. Feign interfaces also need exact HTTP bindings. "
        "Foundation skeletons: inheritance, injection and intentionally empty methods only. "
        "Implementation files: describe only their own logic and relevant boundaries — Service business "
        "decisions/orchestration, RepositoryImpl filters/pagination/write guards, external adapters request "
        "or response adaptation, Converter/Assembler field mappings and any required nontrivial conversion. "
        "Direct mappings need only a concise mapping statement, not generic boundary boilerplate. "
        "Controllers need route/binding/envelope/authorization/delegation details, not business algorithms. "
        "Use labels such as 字段、接口/方法、处理逻辑、边界约束、复用要求 only when useful; omit empty "
        "or redundant sections. Mention specific members to preserve only when modifying shared files. "
        "Separate sections with actual line breaks encoded as \\n inside the JSON string, not literal "
        "backslash-n text after JSON decoding. Do not insert raw unescaped newlines inside JSON strings. "
        "Never fill placeholders or repeat the file path, generic architecture rules, or another layer's "
        "full algorithm. Preserve all contract-required details in their responsible file descriptions. "
        "Labels are description text, not additional JSON fields.",
        "Do not change auth/common infrastructure, AuthConstants, authorization services or invent "
        "request-derived permission checks/data rules.",
        "No extra tasks, standalone mapping tasks, database-owner work, DDL, schema/table changes, migrations, seed data, "
        "tests, builds, lint, runtime verification or acceptance work. Only backend:bootstrap owns "
        "pom.xml and global datasource/MyBatis/OpenFeign activation.",
    )


def _module_layout_rules(sources: set[str]) -> tuple[str, ...]:
    """从现有规范的文件清单投影目录表，不内联示例工程树或修改 Skill。"""

    reference = (
        resolve_builtin_skills_root() / SPRINGBOOT_TEMPLATE_BOUNDARY_SKILL_NAME
        / "references" / "module-layout.md"
    )
    # 只读取随应用发布的固定规范，不访问业务工作区，也不从任务候选反推目录规则。
    layout = reference.read_text(encoding="utf-8")
    directories: dict[str, list[str]] = {}
    database_prefixes = ("infrastructure/po/", "infrastructure/mapper/", "infrastructure/repository/",
                         "domain/repository/", "resources/mapper/")
    # 只读取编号文件表；相同目录的请求/响应 DTO 合并为一行，保留原规范的目录位置。
    for line in layout.splitlines():
        if not re.match(r"^\|\s*\d+\s*\|", line):
            continue
        cells = [cell.strip() for cell in line.strip("|").split("|")]
        match = re.fullmatch(r"`([^`]+)`", cells[1])
        if match is None:
            raise ValueError("module-layout.md 文件清单缺少精确路径。")
        path = match[1]
        if "database" not in sources and path.startswith(database_prefixes):
            continue
        directory = path.rsplit("/", 1)[0]
        directories.setdefault(directory, []).append(cells[-1])
    if not directories:
        raise ValueError("module-layout.md 缺少可投影的模块目录清单。")
    if "external_api" in sources:
        directories.update({
            "infrastructure/client": ["Client"], "infrastructure/client/dto": ["Upstream transport DTO"],
            "infrastructure/client/config": ["Client-local configuration, if needed"],
            "infrastructure/client/adapter": ["Transport adapter, if needed"],
        })
    rows = ["| File role | Directory |", "| --- | --- |"]
    rows.extend(f"| {' / '.join(roles)} | `{directory}` |" for directory, roles in directories.items())
    return (
        "Required new Java files use these directories relative to the resolved module package. "
        "resources/mapper is relative to backend/src/main. Create only contract-required files, "
        "not every role in the table.\n" + "\n".join(rows),
    )


def _foundation_rules(sources: set[str]) -> tuple[str, ...]:
    """定义共享基础骨架的文件边界与复用要求。"""

    rules = [
        "Foundation prepares shared skeletons from confirmed sourceSnapshots, with no business/operation "
        "methods, transport DTOs, custom Mapper XML or routes."
    ]
    if "database" in sources:
        rules.append("Database foundation: PO, BaseMapper interface, empty domain Repository interface "
                     "and RepositoryImpl skeleton with Mapper injection only in the implementation.")
    if "external_api" in sources:
        rules.append("External foundation: compatible FeignClient skeleton and exact client-local connection configuration.")
    return tuple(rules)


def _repository_rules() -> tuple[str, ...]:
    """定义 database 分支的 Endpoint repository 职责边界。"""

    return (
        "Repository business work: plan matching domain Repository signatures and RepositoryImpl methods "
        "for confirmed databaseQuery/databaseWrites/fieldMappings, with necessary domain results and PO "
        "conversions. Keep PO, MyBatis Page and wrappers inside infrastructure; domain interfaces must not "
        "import infrastructure or application DTOs. Use BaseMapper and LambdaQueryWrapper/LambdaUpdateWrapper "
        "for ordinary single-table CRUD, filters, count, pagination, sorting, LIKE and IN. Custom Mapper/XML "
        "requires a confirmed join, aggregation, UNION, subquery, window/database-specific SQL or documented "
        "performance need BaseMapper cannot express clearly; state the reason. No placeholder XML.",
    )


def _upstream_rules() -> tuple[str, ...]:
    """定义 external_api 分支的 Endpoint upstream 职责边界。"""

    return (
        "Upstream business work: complete the prepared Client method and full typed transport DTOs from "
        "sourceSnapshots[].details.operation, preserving Path/Query/Header/body locations, names, types, "
        "required flags, optional/unmapped parameters and nested request/response structure. Reuse the same "
        "method/DTOs for the same operation. externalApiBindings supplies Service call-site values, never "
        "a reduced transport signature. Omit unmapped optional request targets; keep their declarations. "
        "Keep shared headers in existing configuration, transport adaptation in upstream, and response "
        "fieldMappings/source composition in Service. Retain the selected upstream call even with no "
        "parameter bindings or source-mapped response fields. Add no unrelated persistence work.",
    )


def _service_rules(sources: set[str]) -> tuple[str, ...]:
    """定义统一 service 对字段映射、组合和业务说明的职责边界。"""

    rules = [
        "Each business task implements one Endpoint's methods and required types, not an exclusive set "
        "of classes. Plan all module tasks together to choose consistent shared file paths before "
        "assigning per-task changes. Default to one module ApplicationService and one module Assembler, "
        "adding separately named methods for different Endpoints rather than generating an "
        "Endpoint-specific Service or Assembler for each operation. For example, detail and list tasks "
        "extend the same ProductApplicationService and ProductAssembler, not GetProductApplicationService "
        "and ListProductsApplicationService or ProductDetailAssembler and ProductListItemAssembler. "
        "Use actual module names; these examples are not mandatory file names. "
        "Reuse compatible existing classes without renaming or migrating them. Split shared classes only "
        "when confirmed responsibility boundaries require it, and state the reason in the file description.",
        "The first responsible business task creates a missing shared class with only its own methods; "
        "later tasks modify the same path, append their own methods and preserve prior methods and injected "
        "dependencies. Do not move business implementations into foundation or merge Endpoint tasks. "
        "Reuse domain entities and error enums across the module. Reuse converters by source/target model "
        "pair: database Endpoints share PO-to-domain converters; external API Endpoints share compatible "
        "transport-to-domain adapters, Clients and transport DTOs by upstream contract. Database and external "
        "API Endpoints may share the module Service, Assembler and compatible domain models, but keep their "
        "transport types, access layers and conversion boundaries distinct. Never create duplicate mapping "
        "methods for the same semantics or one generic converter mixing unrelated sources.",
        "Request/response DTOs follow endpointContract; reuse only when fields, types, validation and "
        "business semantics match, otherwise keep distinct DTOs and mapping methods in the shared Assembler. "
        "Keep internal DTOs separate from "
        "source models. Service alone owns business decisions, source composition and final response "
        "fieldMappings. Plan matching typed boundaries in the same responsible task.",
        "When a confirmed business failure needs BizException, plan the concrete module "
        "domain/exception/<Module>ErrorCode.java enum implementing IBizErrorCode from API Contract "
        "error_codes. Reuse/extend it and include its path/covers; never reference a missing type. "
        "Prefer business-task ownership and throwing in ApplicationService, preserving explicit existing conventions.",
    ]
    if "database" in sources:
        rules.append("Service uses domain Repository interfaces, never PO, Mapper or RepositoryImpl. "
                     "Keep PO-to-domain conversion in infrastructure and domain-to-API composition in application.")
    return tuple(rules)


def _field_planning_rules(sources: set[str]) -> tuple[str, ...]:
    """只注入本轮物理来源适用的规划检查项，执行阶段详细规则保持原样。"""

    values = []
    if "database" in sources:
        values.extend(("databaseQuery.items", "databaseWrites"))
    if "external_api" in sources:
        values.append("externalApiBindings")
    rules = [
        f"Read complete fieldMappings, {', '.join(values)} and optional implementationDescription. "
        "Assign target paths/columns, dependencies, processing, missing rules and pre-call/post-call order "
        "to the descriptions of files that implement them, rather than repeating them in every file. "
        "Service resolves values before access and maps source results after "
        "access; access layers receive typed values with explicit presence. sourceBinding/sourceSnapshots "
        "fix the physical sources; equal field names, origin categories or free-text guidance cannot "
        "invent bindings, sources or override contracts. Missing/blank implementationDescription adds no work.",
        "Preserve typed fixed values and declared endpointFields/builtinFields/sourceFields dependencies. "
        "value_mapping needs no sourceFields; direct source_mapping assigns directly, descriptive mappings "
        "apply their businessDescription. Preserve array order and item correspondence; no implicit flattening "
        "or first-item selection. Distinguish absence from null, false, zero and empty strings according to "
        "field constraints. Follow missingBehavior error/omit/default with typed defaultValue and any "
        "explicit missing-input branch. Never mask invalid inputs or source failures with defaults, omit "
        "required targets or guess missing algorithms, timezones, rounding or error semantics; report contract gaps.",
    ]
    if "database" in sources:
        rules.append("Repository preserves parameterized values and AND/OR grouping, including repeated "
                     "columns and omitted predicates. Reject update/delete when omit removes all effective predicates.")
    return tuple(rules)


def _controller_rules() -> tuple[str, ...]:
    """定义 Endpoint controller 的薄适配职责边界。"""

    return (
        "The shared controller task runs after the scoped business tasks. For each listed "
        "Endpoint, implement the exact internal endpointContract method/path, typed request "
        "binding, status/envelope contract, required authorization annotation, and delegation "
        "to its Service. It must never expose an upstream path, "
        "perform repository/client access, repeat fieldMappings, or hard-code sample values.",
        "Only Controller may add one @RequireAnyResource per endpoint with operationResourceKeys, "
        "using platform AuthConstants and ANY-OF semantics. Omit it when no operationResourceKeys are declared.",
    )


def resolve_backend_unit_task_rules(
    context: UnitGenerationContext,
) -> tuple[str, ...]:
    """按 Bootstrap 或后端模块清单选择本轮阶段及来源的规则。"""

    if context.unit_id == "backend:bootstrap":
        return _bootstrap_rules(context)
    if not context.unit_id.startswith(BACKEND_MODULE_PREFIX):
        raise ValueError(f"Backend Unit {context.unit_id} 没有模块任务规划规则。")
    manifest = backend_stage_manifest(context)
    if not manifest:
        raise ValueError(f"后端模块 Unit {context.unit_id} 缺少增量职责。")
    kinds = {spec["kind"] for item in manifest for spec in item["deliverables"]}
    stages = {item["stage"] for item in manifest}
    sources = {source for item in manifest for source in item["data_source_types"]}
    rules = [
        "### Common paths and file descriptions",
        *_backend_common_rules(),
        *_module_layout_rules(sources),
    ]
    if "foundation" in stages:
        foundation_sources = {source for item in manifest if item["stage"] == "foundation"
                              for source in item["data_source_types"]}
        rules.extend(("### foundation — shared skeletons", *_foundation_rules(foundation_sources)))
    if "business" in stages:
        business_sources = {source for item in manifest if item["stage"] == "business"
                            for source in item["data_source_types"]}
        rules.append("### business — one Endpoint per task")
        rules.extend(_service_rules(business_sources))
        if "backend.repository" in kinds:
            rules.extend(_repository_rules())
        if "backend.upstream" in kinds:
            rules.extend(_upstream_rules())
        rules.extend(_field_planning_rules(business_sources))
    if "controller" in stages:
        rules.extend(("### controller — shared web adapter", *_controller_rules()))
    return tuple(rules)
