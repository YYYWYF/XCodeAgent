"""当前已核对官方默认应用层的直接依赖，归入 T21 审阅和真实安装证据。"""

from pathlib import Path
from typing import Any

# 当前 0.0.1 成品公开 Source Registry 的 platform preset closure；不由模型选包或升级。
SOURCE_DEPENDENCIES = (('lucide-react', '1.41.0'), ('zod', '4.5.4'))


def prepare_source_dependencies(package: dict[str, Any]) -> list[dict[str, str]]:
    """补足默认应用层直接生产声明；已有不同版本或其他字段声明必须单独审阅。"""
    dependencies = package.setdefault('dependencies', {})
    for name, version in SOURCE_DEPENDENCIES:
        if any(name in package.get(field, {}) for field in ('devDependencies', 'optionalDependencies', 'peerDependencies')):
            raise ValueError(f'{name} 必须单独审阅为直接生产依赖，不能自动移动声明。')
        if name in dependencies and dependencies[name] != version:
            raise ValueError(f'{name} 已有版本需单独审阅，不能自动升级或降级。')
        dependencies[name] = version
    return [{'name': name, 'version': version} for name, version in SOURCE_DEPENDENCIES]


def source_dependency_evidence(target: Path, plan: dict[str, Any], lock: dict[str, Any]) -> list[dict[str, str]]:
    """核对目标直接解析的实际包与锁，不接受仅存在于工具或传递依赖目录的包。"""
    from app.services.agent_ui_dependency_plan import read_object, sha256

    expected = [{'name': name, 'version': version} for name, version in SOURCE_DEPENDENCIES]
    if plan['sourceDependencies'] != expected:
        raise ValueError('应用层依赖计划与当前成品组合不同，请重新审阅。')
    package = read_object(target / 'package.json')
    result: list[dict[str, str]] = []
    for item in expected:
        name, version = item['name'], item['version']
        if package.get('dependencies', {}).get(name) != version:
            raise ValueError(f'{name} 缺少已批准的直接生产依赖声明。')
        metadata = (target / 'node_modules' / name / 'package.json').resolve(strict=True)
        if not metadata.is_relative_to(target / 'node_modules'):
            raise ValueError(f'{name} 解析到目标依赖目录之外。')
        actual = read_object(metadata)
        if actual.get('name') != name or actual.get('version') != version:
            raise ValueError(f'{name} 实际安装版本不匹配。')
        if plan['manager'] == 'pnpm':
            root = lock.get('importers', {}).get('.', {}).get('dependencies', {}).get(name, {})
            resolved = str(root.get('version', '')).split('(')[0]
            record = lock.get('packages', {}).get(f'{name}@{resolved}', {})
            integrity = record.get('resolution', {}).get('integrity', '')
            valid = root.get('specifier') == version and resolved == version
        else:
            root = lock.get('packages', {}).get('', {}).get('dependencies', {})
            record = lock.get('packages', {}).get('node_modules/' + name, {})
            integrity = record.get('integrity', '')
            valid = root.get(name) == version and record.get('version') == version and not record.get('link')
        if not valid or not isinstance(integrity, str) or not integrity.startswith('sha512-'):
            raise ValueError(f'{name} 锁身份或 integrity 不匹配。')
        result.append({**item, 'integrity': integrity, 'packageJsonSha256': sha256(metadata.read_bytes())})
    return result
