"""配置操作身份和脱敏诊断，不保存配置值。"""
from __future__ import annotations

import hashlib
import json
import re
from typing import Any

_MISSING = object()
_STAGE_LABELS = {
    'desired_save': '应用配置保存', 'template_state_read': '读取工程模板状态',
    'template_capability_reconcile': '模板能力代码安装', 'application_parameter_update': '工程参数写入',
}
_SAFE_CODES = frozenset({
    'CONFIG_SAVE_FAILED', 'CONFIG_SNAPSHOT_INVALID', 'CONFIG_VALUE_INVALID', 'TEMPLATE_ENGINE_UNAVAILABLE',
    'TEMPLATE_PACKAGE_CONTRACT_INVALID', 'CONFIG_STRUCTURE_MISSING', 'MANAGED_SURFACE_CONFLICT',
    'CONFIG_TARGET_CONFLICT', 'CONFIG_APPLY_FAILED', 'CONFIG_RECOVERY_REQUIRED',
    'TEMPLATE_STATE_INVALID', 'TEMPLATE_UPDATE_RECOVERY_REQUIRED', 'TEMPLATE_RECONCILE_DISABLED',
})


class ApplicationConfigOperationError(ValueError):
    """统一配置操作的安全错误码和阶段说明。"""

    def __init__(self, code: str, message: str, *, failure_stage: str | None = None, failure_code: str | None = None, failure_target: str | None = None) -> None:
        """构造不携带提交值的可投影错误。"""
        super().__init__(f'{code}：{message}')
        self.code = code
        self.failure_stage = failure_stage
        self.failure_code = failure_code
        self.failure_target = failure_target


def value_at(config: dict[str, Any], path: str) -> Any:
    """读取提案完整字段路径，缺失叶子以哨兵参与判等。"""
    value: Any = config
    for part in path.split('.'):
        if not isinstance(value, dict) or part not in value:
            return _MISSING
        value = value[part]
    return value


def changes_digest(changes: dict[str, Any]) -> str:
    """以 UTF-8、排序键和紧凑 JSON 生成跨端一致的 SHA-256。"""
    encoded = json.dumps(changes, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode('utf-8')
    return hashlib.sha256(encoded).hexdigest()


def safe_failure_code(error: Exception) -> str:
    """优先识别已有固定错误码；绝不输出上游响应正文。"""
    prefix = str(error).split('：', 1)[0]
    if prefix in _SAFE_CODES:
        return prefix
    code = getattr(error, 'code', None)
    return code if isinstance(code, str) and code in _SAFE_CODES else 'CONFIG_APPLY_FAILED'


def safe_failure_target(error: Exception) -> str | None:
    """只接受受管文件或字段白名单内的路径，禁止异常正文中的值进入记录。"""
    target = getattr(error, 'target', None)
    if not isinstance(target, str):
        return None
    if target in {'frontend/.env', 'backend/src/main/resources/application.yml', 'backend/src/main/resources/application-dev.yml', '.devagentstudio/application.json', '.devagentstudio/template-state.json'}:
        return target
    if re.fullmatch(r'(?:yht\.(?:authentication|api)|bee\.auth(?:\.login)?)\.[a-z-]+', target):
        return target
    return None


def failure_summary(stage: str, code: str) -> str:
    """按真实阶段输出诊断，不根据用户提案推测未完成的工程范围。"""
    hint = '模板引擎不可用，请检查模板服务连接后重试' if code == 'TEMPLATE_ENGINE_UNAVAILABLE' else '请根据错误码检查该环节后使用原操作重试'
    return f'{_STAGE_LABELS.get(stage, "工程配置同步")}失败：{hint}'
