"""将明确的会话操作核对到官方组合中的实际控件上下文。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app.services.agent_product_actions import NATIVE_RESPONSE_OPERATIONS

PluginPath = tuple[tuple[str, str], ...]
_NATIVE_PLACEMENTS: dict[str, tuple[str, PluginPath]] = {
    'stop_generation': ('submit-action', (('conversation-surface', 'composer'), ('composer', 'submitAction'))),
    'copy_response': ('copy-action', (('conversation-surface', 'assistantResponseFooter'), ('response-footer', 'actions'))),
}


def _mounted_plugins(node: Any, context: PluginPath = ()) -> set[tuple[str, PluginPath]]:
    """只遍历当前 AppUIModel 的布局与插件插槽，不把目录或未挂载声明当作能力。"""
    result: set[tuple[str, PluginPath]] = set()
    if not isinstance(node, dict):
        return result
    plugin_id = node.get('pluginId')
    if isinstance(plugin_id, str):
        if node.get('enabled') is False:
            return result
        result.add((plugin_id, context))
        slots = node.get('slots', {})
        if isinstance(slots, dict):
            for slot, children in slots.items():
                for child in children if isinstance(children, list) else []:
                    result.update(_mounted_plugins(child, (*context, (plugin_id, slot))))
    elif node.get('type') in {'row', 'column'}:
        children = node.get('children', [])
        for child in children if isinstance(children, list) else []:
            result.update(_mounted_plugins(child, context))
    elif node.get('type') == 'panel':
        result.update(_mounted_plugins(node.get('child'), context))
    elif node.get('type') == 'stack':
        children = node.get('children', [])
        active = node.get('activeIndex', 0)
        if isinstance(children, list) and isinstance(active, int) and 0 <= active < len(children):
            result.update(_mounted_plugins(children[active], context))
    elif node.get('type') == 'sidebar':
        for part in ('header', 'footer', 'content'):
            result.update(_mounted_plugins(node.get(part), context))
        items = node.get('items', [])
        for item in items if isinstance(items, list) else []:
            if isinstance(item, dict) and item.get('id') == node.get('defaultActive'):
                result.update(_mounted_plugins(item.get('child'), context))
    elif node.get('type') == 'slot':
        plugins = node.get('plugins', [])
        for plugin in plugins if isinstance(plugins, list) else []:
            result.update(_mounted_plugins(plugin, context))
    return result


def validate_native_action_composition(project: Path, pages: list[dict[str, Any]]) -> None:
    """生成、修订和确认共同验证原生停止/完整回复复制；不修改正式产品归属。"""
    required: list[tuple[str, str, str]] = []
    for page in pages:
        agents = {surface['agentId'] for surface in page.get('agent_surfaces', [])}
        for action in page.get('actions', []):
            behavior = action.get('behavior') or {}
            operation = behavior.get('operation')
            if (behavior.get('type') != 'agent' or not isinstance(operation, str)
                    or operation not in NATIVE_RESPONSE_OPERATIONS):
                continue
            if behavior.get('agentId') not in agents:
                raise ValueError(f"原生会话操作 {page['pageId']}/{action['actionId']} 缺少同页 Agent Surface。")
            required.append((page['pageId'], action['actionId'], operation))
    if not required:
        return
    source = project / 'src/agent-ui/app-ui.json'
    if source.is_symlink() or not source.resolve().is_relative_to(project.resolve()):
        raise ValueError('原生会话操作的组合文件不能链接到工程之外。')
    try:
        model = json.loads(source.read_text(encoding='utf-8'))
    except (OSError, ValueError) as exc:
        raise ValueError('原生会话操作缺少有效的 AppUIModel 组合，请重新生成并确认设计。') from exc
    mounted = _mounted_plugins(model.get('root') if isinstance(model, dict) else None)
    missing: list[str] = []
    for page_id, action_id, operation in required:
        plugin_id, placement = _NATIVE_PLACEMENTS[operation]
        if not any(identity == plugin_id and context[-len(placement):] == placement
                   for identity, context in mounted):
            missing.append(f'{page_id}/{action_id} ({operation})')
    if missing:
        raise ValueError('官方会话组合未挂载所需原生操作：' + '、'.join(missing)
                         + '；必须在输入区或助手回复中挂载对应官方控件，不能用页面本地成功提示替代。')
