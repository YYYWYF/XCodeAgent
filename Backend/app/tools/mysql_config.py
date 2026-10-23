"""向后端生成 Agent 提供无凭据的数据库运行配置合同。"""

import json

from langchain_core.tools import tool

from app.services.database_credentials import DatabaseCredentialError
from app.services.database_runtime_source import select_runtime_database


def create_get_mysql_config_tool(workspace_root: str | None, *, source_ids: tuple[str, ...] | None = None):
    """绑定平台提供的工作区与任务数据源身份，不让模型选择其他数据库。"""

    @tool("get_mysql_config")
    def workspace_get_mysql_config() -> str:
        """读取任务数据库模式和 Spring 运行时注入说明，不返回真实连接凭据。"""

        return get_mysql_config_for_workspace(workspace_root, source_ids=source_ids)

    return workspace_get_mysql_config


def get_mysql_config_for_workspace(
    workspace_root: str | None, *, source_ids: tuple[str, ...] | None = None,
) -> str:
    """区分无需数据库、尚未配置和运行时注入模式，已失效引用返回明确错误。"""

    try:
        if source_ids == ():
            result = {"status": "not_required", "message": "当前任务不涉及数据库，无需补齐 spring.datasource。"}
        else:
            if source_ids is not None and len(source_ids) != 1:
                raise DatabaseCredentialError("任务必须绑定唯一的数据库数据源。")
            source = select_runtime_database(workspace_root, source_id=source_ids[0] if source_ids else None)
            if source is None:
                result = {"status": "not_configured", "message": "尚未配置数据库，可在应用内新增；不得猜测连接或将数据库依赖任务标记为完成。"}
            else:
                result = {
                    "status": "ok", "mode": source.mode, "sourceId": source.source_id,
                    "configurationSource": "runtime_environment",
                    "springDatasource": {
                        "url": "${SPRING_DATASOURCE_URL}",
                        "username": "${SPRING_DATASOURCE_USERNAME}",
                        "password": "${SPRING_DATASOURCE_PASSWORD}",
                    },
                    "message": (
                        "模拟数据库连接由平台启动时注入，无需用户填写连接或获取真实密码；不得自动建表或运行迁移。"
                        if source.mode == "builtin" else
                        "平台启动时从应用内当前数据源解析并注入连接；仅使用环境变量占位符，不将真实连接或密码写入工程。"
                    ),
                }
    except DatabaseCredentialError as exc:
        result = {"status": "error", "error": str(exc)}
    return json.dumps({"tool": "get_mysql_config", **result}, ensure_ascii=False)
