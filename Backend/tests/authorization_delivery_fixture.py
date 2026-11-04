"""构造来自可信安装边界的七文件与四表 DDL，不连接数据库。"""

import json
from pathlib import Path

from app.services.authorization_projection_sources import PO_FILES, XML_FILES, record_materialized_authorization_sources
from app.services.authorization_table_projection import TABLES, compile_authorization_tables
from app.services.authorization_workspace_projector import RUNTIME, ensure_projection
from app.services.authorization_manifest import SYSTEM_RESOURCE_KEY
from app.services.template_reconcile.state_v3 import load_template_state_v3


def install_authorization(root: Path, application: dict, plan: dict) -> dict:
    """安装隔离模板并投影，保存明确 Schema 成功而 Seed 未执行的本地证据。"""

    application["authorization"]["enabled"] = True
    application["authorization"]["initialAdministratorSubjects"] = ["admin"]
    plan["authorization_manifest"] = {
        "fingerprint": "sha256:resources", "systemAuthorization": {"managementResourceKey": SYSTEM_RESOURCE_KEY}}
    artifacts = {}
    for relative in (*PO_FILES, *XML_FILES):
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        text = (f'@TableName("{PO_FILES[relative]}")\nclass Example {{}}' if relative in PO_FILES else
                '<mapper><select id="all">SELECT r.id FROM role r JOIN resource s ON r.id=s.id '
                'JOIN role_resource rr ON r.id=rr.role_id JOIN role_member rm ON r.id=rm.role_id '
                "WHERE r.name='FROM role'</select>"
                '<insert id="add">INSERT INTO role (id) VALUES (1)</insert>'
                '<update id="edit">UPDATE role_resource SET role_id=1</update>'
                '<delete id="delete">DELETE FROM role_member WHERE id=1</delete></mapper>')
        path.write_text(text, encoding="utf-8")
        artifacts["backend:authorization:" + relative.removeprefix("backend/")] = {
            "target": relative, "capabilityId": "authorization", "installedRevision": "2026.11.04.1"}
    state = root / ".devagentstudio/template-state.json"
    state.write_text(json.dumps({"schemaVersion": 3, "templateRevision": "2026.11.04.1", "requested": {},
                                "effective": {"authorization": {"enabled": True, "config": {}}},
                                "installedArtifacts": artifacts, "managedContributions": {}}), encoding="utf-8")
    record_materialized_authorization_sources(root, load_template_state_v3(root))
    ddl = root / "backend/sql/authorization/schema.sql"
    ddl.parent.mkdir(parents=True, exist_ok=True)
    ddl.write_text("\n".join(
        f"-- {name}\nCREATE TABLE `{name}` (`id` BIGINT NOT NULL AUTO_INCREMENT, "
        "`role_id` BIGINT DEFAULT 0 COMMENT 'role; FROM role', PRIMARY KEY (`id`), "
        "UNIQUE KEY `uk_role` (`role_id`)) ENGINE=InnoDB;" for name in TABLES) + "\n", encoding="utf-8")
    key = application["managedDatabase"]["datasourceKey"]
    ensure_projection(root, key)
    compiled = compile_authorization_tables(root, key)
    record = {"schemaVersion": 1, "datasourceMode": "builtin", "datasourceKey": key,
              "datasourceId": application["managedDatabase"]["datasourceId"],
              "ddlFingerprint": compiled["ddlFingerprint"], "status": "schema_ready", "schemaReady": True,
              "seedReady": False, "verifiedAt": "2026-11-04T00:00:00Z"}
    (root / RUNTIME / "state.json").write_text(json.dumps(record), encoding="utf-8")
    return compiled
