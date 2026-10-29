"""保留 1028 Endpoint 阶段清单，把实现语言投射为 Python。"""

import json

from app.agents.field_value_prompt_rules import FIELD_VALUE_PLANNING_RULES
from app.services.unit_generation_contracts import UnitGenerationContext
from app.services.unit_task_manifest import backend_stage_manifest


def resolve_python_unit_task_rules(context: UnitGenerationContext) -> tuple[str, ...]:
    """按已确认映射规划 Python 任务，不增加 Entity/SQL 第二套开发流程。"""

    common = (
        "The implementation is Python 3.12 + FastAPI inside agent-runtime. The logical "
        "owner=backend and backend Unit IDs describe business responsibility, not Java. "
        "Never emit Java, Maven, Spring, MyBatis, DDL, migrations, seed data, or SQLite "
        "business storage. Reuse the Runtime template and its MySQL transaction adapter. "
        "The confirmed entity table_name is the exact physical table name; never derive it again.",
        "All business source changes must be exact .py files under agent-runtime/src/app/. "
        "Only backend:bootstrap may own agent-runtime/pyproject.toml. Never own uv.lock, "
        "environment files, platform artifacts, authentication, model factories or checkpoints. "
        "Never target app/settings.py, app/main.py, app/server/**, app/security/**, app/interaction/**, "
        "app/api/router.py, app/api/security.py, app/application/context.py, app/domain/base.py, "
        "app/infrastructure/database.py or app/infrastructure/database_settings.py, even in bootstrap. "
        "Use the actual template layout and reuse its business router, request context and error "
        "envelope. Write Simplified Chinese numbered execution steps. Tests, installation, "
        "builds, service startup and acceptance belong to the outer Workflow.",
        *FIELD_VALUE_PLANNING_RULES,
    )
    if context.unit_id == "backend:bootstrap":
        return (*common,
                "Emit exactly one Task backend:bootstrap::bootstrap, dependencies=[], covering "
                "all supplied backend.bootstrap requirements, one deliverable per source type. "
                "Reuse the existing MySQL driver/transaction and HTTP client configuration; "
                "only add genuinely missing business dependency declarations or business adapters. "
                "The template already owns MySQL setup, settings, FastAPI app and automatic endpoint "
                "router discovery; do not plan to recreate or edit these. Inspect the actual template "
                "before choosing files, never invent app/infra/mysql.py as a second database adapter. "
                "If existing code already satisfies bootstrap requirements, target the existing "
                "agent-runtime/pyproject.toml for inspection and let execution return already_satisfied "
                "with concrete evidence; do not invent a placeholder file or perform a redundant edit. "
                "Credentials are injected at startup; never fetch or persist them or create business tables.")
    manifest = backend_stage_manifest(context)
    if not manifest:
        raise ValueError(f"Python Endpoint Unit {context.unit_id} 缺少已确认的阶段职责。")
    return (*common,
            "Emit exactly one Task per stage manifest record. Keep its exact task_id, "
            "dependencies, deliverable_kind, endpoint_id target and requirement_id. "
            "objects owns Pydantic DTOs and typed values; repository owns parameterized "
            "MySQL access and persistence records; upstream owns the declared external HTTP "
            "operation; service alone owns field mappings, composition and business rules; "
            "controller owns a thin FastAPI route and the exact confirmed response envelope. "
            "REST and Agent Tools must delegate to the same service. Preserve trusted Principal "
            "ownership; never accept an owner identity from client request fields. Return a "
            "contract mismatch for unspecified behavior rather than inventing a model call.\n"
            + json.dumps(manifest, ensure_ascii=False, indent=2))
