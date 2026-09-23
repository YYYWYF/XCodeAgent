"""DevAgent Studio 的统一品牌和持久化命名常量。"""

from pathlib import Path

PRODUCT_DISPLAY_NAME = "DevAgent Studio"
PRODUCT_ID = "devagentstudio"
ENV_PREFIX = "DEVAGENTSTUDIO"
WORKSPACE_ARTIFACT_DIR_NAME = ".devagentstudio"
WORKSPACE_ARTIFACT_DIR = Path(WORKSPACE_ARTIFACT_DIR_NAME)
DEFAULT_USER_DATA_DIRECTORY_NAME = ".devagentstudio_dev"
USER_DATA_DIRECTORY_NAMES = frozenset(
    {
        DEFAULT_USER_DATA_DIRECTORY_NAME,
        ".devagentstudio_st",
        ".devagentstudio_uat",
        ".devagentstudio",
    }
)
