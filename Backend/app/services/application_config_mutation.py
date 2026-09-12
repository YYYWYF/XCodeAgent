"""已迁移的应用配置入口；新代码应使用 application_config 服务包。"""

from app.services.application_config.schema import (
    ApplicationConfigError as ApplicationConfigMutationError,
    CURRENT_APPLICATION_SCHEMA_VERSION,
)
from app.services.application_config.service import (
    apply_application_config_changes,
    preview_application_config_changes,
    read_application_config,
)

__all__ = [
    "ApplicationConfigMutationError",
    "CURRENT_APPLICATION_SCHEMA_VERSION",
    "apply_application_config_changes",
    "preview_application_config_changes",
    "read_application_config",
]
