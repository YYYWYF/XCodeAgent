"""T28 当前磁盘与本轮预览身份；只读摘要不充当转入验证或执行授权。"""

from collections.abc import Mapping
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
from typing import Any

from app.services.agent_ui_dependency_plan import safe_path

PACKAGES = tuple('@za38.03/agent-ui-' + name for name in ('core', 'renderer', 'runtime', 'plugins'))


def _bytes(path: Path, limit: int = 2 * 1024 * 1024) -> bytes:
    """限制每个观察输入，拒绝将日志或任意大文件装入快照。"""
    with path.open('rb') as stream:
        value = stream.read(limit + 1)
    if len(value) > limit:
        raise ValueError('观察输入过大')
    return value


def capture_identity(workspace: Path) -> dict[str, Any]:
    """仅返回当前共享源码、实际包 metadata 和锁摘要，不返回内容或绝对路径。"""
    unavailable = {'status': 'unavailable'}
    root = workspace.resolve()
    try:
        target = safe_path(root, 'frontend')
        directory = safe_path(root, '.devagentstudio/runtime/agent-ui-transfer')
        pointer = json.loads(_bytes(safe_path(directory, 'current.json')))
        plan_id = pointer['planId']
        if not isinstance(plan_id, str) or not re.fullmatch('[a-f0-9]{64}', plan_id):
            return unavailable
        record = json.loads(_bytes(safe_path(directory, plan_id + '/record.json')))
        source_root = record['plan']['target']['sourceRoot']
        if (not isinstance(source_root, str) or not source_root.startswith('src/')
                or '\\' in source_root or '..' in PurePosixPath(source_root).parts):
            return unavailable
        owned = record['owned']
        if not isinstance(owned, dict) or not 0 < len(owned) <= 512:
            return unavailable
        sources: list[tuple[str, str]] = []
        total = 0
        for name in sorted(owned):
            if (not isinstance(name, str) or '\\' in name or PurePosixPath(name).is_absolute()
                    or '..' in PurePosixPath(name).parts):
                return unavailable
            if name.startswith(source_root + '/') or name == 'src/extensions/AgentUiHostProvider.tsx':
                value = _bytes(safe_path(target, name))
                total += len(value)
                if total > 8 * 1024 * 1024:
                    return unavailable
                sources.append((name, hashlib.sha256(value).hexdigest()))
        if not sources:
            return unavailable
        packages: list[dict[str, str]] = []
        modules = safe_path(target, 'node_modules').resolve()
        for name in PACKAGES:
            metadata_path = (modules / name / 'package.json').resolve(strict=True)
            if not metadata_path.is_relative_to(modules):
                return unavailable
            value = _bytes(metadata_path, 128 * 1024)
            metadata = json.loads(value)
            version = metadata.get('version')
            if (metadata.get('name') != name or not isinstance(version, str)
                    or not re.fullmatch(r'\d+\.\d+\.\d+(?:[-+][a-zA-Z0-9.-]+)?', version)):
                return unavailable
            packages.append({'name': name, 'version': version, 'metadataSha256': hashlib.sha256(value).hexdigest()})
        # 两种当前支持的 manager 均允许；同时存在多个锁则拒绝选择。
        locks = [safe_path(target, name) for name in ('pnpm-lock.yaml', 'package-lock.json')
                 if safe_path(target, name).is_file()]
        if len(locks) != 1:
            return unavailable
        return {'status': 'available', 'transferPlanId': plan_id,
            'sourceSha256': hashlib.sha256(json.dumps(sources, separators=(',', ':')).encode()).hexdigest(),
            'lockSha256': hashlib.sha256(_bytes(locks[0], 8 * 1024 * 1024)).hexdigest(), 'packages': packages}
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        return unavailable


def observation_snapshot(workspace: Path, frontend: Mapping[str, Any], runtime: Mapping[str, Any],
                         agent_id: str, healthy: bool) -> dict[str, Any]:
    """区分启动捕获与当前磁盘事实；不恢复缺失的旧启动身份，不推定回放来源。"""
    marker = frontend.get('agentUiPreview')
    launch = frontend.get('agentUiObservation')
    mode = marker.get('mode') if isinstance(marker, Mapping) else None
    source = 'unknown'
    if (frontend.get('status') == 'running' and isinstance(marker, Mapping)
            and marker.get('agentId') == agent_id and mode in {'official-mock', 'real-text', 'formal-text', 'formal-business'}
            and (mode == 'official-mock' or healthy)):
        source = mode
    session = launch.get('sessionId') if isinstance(launch, Mapping) else None
    session = session if isinstance(session, str) and re.fullmatch('[a-f0-9]{32}', session) else None
    current = capture_identity(workspace)
    started = launch.get('identity') if isinstance(launch, Mapping) else None
    launch_id = runtime.get('launchId')
    launch_id = launch_id if isinstance(launch_id, str) and re.fullmatch('[a-zA-Z0-9_-]{1,128}', launch_id) else None
    return {'source': source, 'sessionId': session, 'launchId': launch_id,
        'identityMatches': bool(session and current['status'] == 'available' and started == current),
        'identity': current, 'replay': 'not_implemented'}
