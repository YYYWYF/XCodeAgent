"""权限资源确定性执行测试共用的当前应用配置。"""

import json
from pathlib import Path

from app.services.application_config.schema import normalize_application_configuration, validate_application_configuration


def write_authorization_application(workspace: Path) -> None:
    """写入合法 schema v6 合成配置，确保执行器按真实应用开关读取而非绕过校验。"""

    application = normalize_application_configuration({
        "schemaVersion": 6, "configRevision": 1, "datasource": {"type": "database"},
        "auth": {"enable": True, "yht": {
            "clientId": "test-client", "privateKey": "synthetic-private",
            "publicKey": "synthetic-public", "centerPublicKey": "synthetic-center",
        }},
        "authorization": {"enabled": True, "initialAdministratorSubjects": ["test-admin"]},
    })
    validate_application_configuration(application)
    target = workspace / ".devagentstudio/application.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(application), encoding="utf-8")
