"""显式运行的真实模板 SQL 路径验收；使用合成配置，仅预检，不连接数据库。"""

from __future__ import annotations

import asyncio
import hashlib
import json
import tempfile
from pathlib import Path

from app.config import Settings
from app.services.application_config.schema import normalize_application_configuration
from app.services.authorization_bootstrap import _command_for_workspace
from app.services.template_reconcile.config_surfaces import validate_ensure_package, validate_working_surfaces
from app.services.template_reconcile.desired import requested_config_from_application_config
from app.services.template_reconcile.executor_v3 import UpdatePackageExecutorV3, WorkingCopyStoreV3
from app.services.template_reconcile.transaction_v4 import apply_working_copy_v4
from app.services.template_reconcile.update_package_v4 import validate_update_package_v4
from app.services.workspace_bootstrap.materializer import WorkspaceMaterializer
from app.services.workspace_bootstrap.models import ArchiveLimits
from app.services.workspace_bootstrap.template_engine_client import TemplateEngineClient
from app.services.workspace_bootstrap.template_package import validate_template_package

SCHEMA = "backend/sql/authorization/schema.sql"
SCRIPTS = ("backend/scripts/bootstrap-authorization.sh", "backend/scripts/bootstrap-authorization.ps1")
STATE = ".devagentstudio/template-state.json"


def _configuration(login: bool, authorization: bool) -> dict:
    """构造不含真实凭据的当前版本 Generate 配置。"""

    config = normalize_application_configuration({
        "schemaVersion": 6, "configRevision": 1,
        "appName": "Authorization SQL Test", "appIcon": "AppstoreOutlined",
        "theme": {"primaryColor": "#9254de"}, "datasource": {"type": "database"},
        "auth": {"enable": login, "yht": dict.fromkeys(
            ("clientId", "privateKey", "publicKey", "centerPublicKey"), "synthetic-test-value")},
        "authorization": {"enabled": authorization,
            "initialAdministratorSubjects": ["synthetic-admin"] if authorization else [],
            "api": {"baseUrl": "https://example.invalid/api", "viewId": "synthetic-view"}},
    })
    # 权限 API 参数不属于 SQL 交付验收，使用可选对象缺省；当前服务会拒绝该 Generate 字段。
    config["authorization"].pop("api", None)
    return config


def _inspect_workspace(root: Path) -> dict:
    """只输出文件身份和跨语言合同检查结果，不输出配置或数据库参数内容。"""

    command, error = _command_for_workspace(root)
    paths = (SCHEMA, *SCRIPTS, "backend/migrations/001-schema.sql", "backend/docs/auth/sql/ddl.sql")
    java_files = list((root / "backend").rglob("AuthorizationBootstrapCommand.java"))
    java = "\n".join(path.read_text(encoding="utf-8") for path in java_files)
    return {
        "preflightPassed": bool(command) and error is None, "preflightError": error,
        "files": [{"path": str(root / name), "exists": (root / name).is_file(),
                   "symlink": (root / name).is_symlink()} for name in paths],
        "crossLanguage": {
            "javaPresent": bool(java_files),
            "javaUsesCurrentDdl": 'backend.resolve("sql/authorization/schema.sql")' in java,
            "javaUsesCurrentWorkspace": ".devagentstudio/plans/technical-plan.json" in java
                and ".devagentstudio/application.json" in java and ".xcodeagent" not in java,
            "javaUsesManifestV3": "authorization-manifest.v3" in java
                and "authorization-manifest.v2" not in java,
            "javaMentionsBee": "bee" in java.lower(),
            "javaMentionsTdsql": "tdsql" in java.lower(),
            # 这是静态线索检查；数据库连接适配的执行验收仍需独立环境。
            "scriptsUseCurrentDdl": all((root / name).is_file() and
                "sql/authorization/schema.sql" in (root / name).read_text(encoding="utf-8")
                for name in SCRIPTS),
        },
    }


async def main() -> None:
    """验证真实 Generate、Base→Authorization、Login→Authorization 的落盘与预检。"""

    settings = Settings.from_env()
    limits = ArchiveLimits(settings.template_package_max_bytes, settings.template_package_max_files,
                           settings.template_package_max_extracted_bytes)
    client = TemplateEngineClient(base_url=settings.template_engine_base_url,
        connect_timeout=5, read_timeout=60, max_package_bytes=settings.template_package_max_bytes)
    evidence = Path(tempfile.mkdtemp(prefix="authorization-sql-e2e-")).resolve()
    report: dict = {"evidenceDirectory": str(evidence), "databaseExecuted": False, "scenarios": []}
    for scenario in ("generate-authorization", "base-to-authorization", "login-to-authorization"):
        root = evidence / scenario
        root.mkdir()
        generated = await client.generate(requested_config_from_application_config(
            _configuration(scenario != "base-to-authorization", scenario == "generate-authorization")),
            temporary_dir=evidence)
        validated = validate_template_package(generated.temporary_path, limits)
        WorkspaceMaterializer().materialize(workspace=root, archive_path=generated.temporary_path,
                                             template_state=validated.template_state)
        item = {"scenario": scenario, "workspace": str(root),
                "templateRevision": validated.template_state.templateRevision,
                "generateZipSha256": generated.sha256}
        if scenario != "generate-authorization":
            current = validated.template_state
            target = requested_config_from_application_config(_configuration(True, True))["capabilities"]
            update = await client.update_v4(current.model_dump(mode="json", exclude={"managedConfiguration"}),
                                             {"capabilities": target}, temporary_dir=evidence)
            assert update is not None, "首次安装 Authorization 不应返回 204"
            package = validate_update_package_v4(update.temporary_path, limits)
            next_state = package.package.nextTemplateState
            assert package.package.sourceRevision == current.templateRevision
            assert package.package.expectedStateSequence == current.stateSequence
            assert next_state.stateSequence == current.stateSequence + 1
            assert {key: value.model_dump(mode="json") for key, value in next_state.effective.items()} == target
            newly_installed = set(next_state.effective) - set(current.effective)
            validate_ensure_package(package.package, package.payloads, newly_installed)
            store = WorkingCopyStoreV3(root)
            UpdatePackageExecutorV3().execute(package.package, store, package.payloads)
            # 此后验门逐一比较原有 YAML/dotenv 值，拒绝任何用户配置覆盖。
            validate_working_surfaces(store, newly_installed)
            store.entry(STATE).working = (json.dumps(next_state.model_dump(mode="json",
                exclude={"managedConfiguration"}), ensure_ascii=False, indent=2) + "\n").encode()
            apply_working_copy_v4(root, store)
            assert json.loads((root / STATE).read_text()) == next_state.model_dump(mode="json", exclude={"managedConfiguration"})
            item.update({"v4Committed": True, "existingConfigurationPreserved": True,
                "v4ZipSha256": hashlib.sha256(update.temporary_path.read_bytes()).hexdigest(),
                "schemaAddFile": any(operation.type == "ADD_FILE" and operation.target == SCHEMA
                                     for operation in package.package.operations)})
        item.update(_inspect_workspace(root))
        report["scenarios"].append(item)
    report_path = evidence / "report.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    # 明确让模板端缺件导致验收失败，不能用消费端单测掩盖上游合同缺失。
    if any(not item["preflightPassed"] or item.get("schemaAddFile") is False
           or not all(item["crossLanguage"][key] for key in (
               "javaUsesCurrentDdl", "javaUsesCurrentWorkspace", "javaUsesManifestV3", "scriptsUseCurrentDdl"))
           for item in report["scenarios"]):
        raise SystemExit(1)


if __name__ == "__main__":
    asyncio.run(main())
