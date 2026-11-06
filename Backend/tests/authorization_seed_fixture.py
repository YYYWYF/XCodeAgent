"""为权限 Seed 路由和 SaaS 协议回归提供虚构正式输入。"""

import json
from pathlib import Path

from app.services.authorization_manifest import _with_fingerprint
from app.services.authorization_table_projection import TABLES
from app.workspace.endpoint_design_documents import technical_plan_path

KEY = "12ab34cd56ef"
SUBJECT = "fixture-admin-甲"
SYSTEM = "system_authorization_management"


def seed_plan() -> dict:
    """创建与当前 Manifest 编译器结构一致的已确认规划。"""

    manifest = _with_fingerprint({
        "schema_version": "authorization-manifest.v3",
        "systemAuthorization": {"adminRoleSeedKey": "system_admin", "managementResourceKey": SYSTEM},
        "resources": [
            {"resourceKey": SYSTEM, "name": "权限管理", "description": "管理授权。", "origin": "system",
             "type": "system", "targetResourceRef": "system:authorization_management", "sourceRuleIds": []},
            {"resourceKey": "home", "name": "首页", "description": "查看首页。", "origin": "business",
             "type": "page", "targetResourceRef": "page:home", "sourceRuleIds": ["page_rule"]},
        ],
        "bindings": {"pages": [{"pageId": "home", "resourceKey": "home"}], "actions": [], "endpoints": []},
        "defaultRoleAuthorization": {"roles": [{"roleSeedKey": "staff", "name": "员工", "description": ""}],
                                     "roleResourceGrants": [{"roleSeedKey": "staff", "resourceKeys": ["home"]}]},
    })
    return {"artifact_type": "technical-plan", "confirmation_status": "confirmed", "authorization_manifest": manifest}


def save_plan(root: Path, plan: dict) -> None:
    """保存正式规划到生产读取路径。"""

    path = technical_plan_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(plan, ensure_ascii=False), encoding="utf-8")


def write_schema(root: Path) -> Path:
    """写入支持测试结构投影的四条独立 CREATE。"""

    path = root / "backend/sql/authorization/schema.sql"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(f"CREATE TABLE `{name}` (`id` BIGINT AUTO_INCREMENT, PRIMARY KEY (`id`));"
                              for name in TABLES), encoding="utf-8")
    return path
