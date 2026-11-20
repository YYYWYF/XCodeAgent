"""仅按正式 Agent 声明派生 T29 用例，不增加业务能力或执行测试。"""

from typing import Any


def acceptance_cases(agent: dict[str, Any]) -> list[dict[str, Any]]:
    """四层场景各自要求可核验事实，未使用能力明确不适用。"""
    capability = agent['interactionContract']
    settings = agent['agentSettings']
    interactions = capability['interactionKinds']
    specifications = [
        ('code', 'code', '代码检查', True, [('lint', '当前源码 lint 通过'),
            ('types', '当前源码类型检查通过'), ('build', '当前目标构建通过'), ('tests', '相关测试通过')]),
        ('visual', 'visual', '页面与浅深主题', True, [('electron', '在 Electron 中核对当前正式目标'),
            ('light', '浅色布局、状态及焦点可读'), ('dark', '深色布局、状态及焦点可读'),
            ('session', '正式页面会话入口、消息与草稿正常，共享展示切换不重复请求')]),
        ('visual', 'formal_interactions', '正式业务对话卡片', bool(interactions or capability.get('resultSchema')),
            [('transferred', '当前设计已正式修订、确认并转入目标'),
             ('cards', '确认/澄清/结果使用正式会话卡片，隐藏原始 JSON'),
             ('locked', '已处理与失效动作锁定，异常时才提供恢复入口')]),
        ('connection', 'conversation', '真实多轮对话', capability['mode'] == 'conversation',
            [('provider', '使用实际供应商模型，非确定性替代'),
             ('turns', '至少两轮对话结果正确'), ('identity', '请求、回执和当前线程身份一致'),
             ('once', '前端没有重复请求')]
             + ([('stream', '声明的文本输出按增量显示')] if capability['textOutput'] else [])),
        ('connection', 'task_run', '真实任务调用', capability['mode'] == 'task',
            [('provider', '使用实际供应商模型，非确定性替代'), ('identity', '请求、回执和当前任务身份一致'),
             ('once', '前端没有重复请求')]),
        ('connection', 'error', '真实连接失败', True, [('error', '受控错误有明确失败状态'),
            ('continue', '失败后会话可继续，不污染历史')]),
        ('connection', 'stop', '停止', True, [('stopped', '输出前/运行中可停止，不继续显示原运行迟到输出'),
            ('no_replay', '停止不宣称回滚，不自动重发或重复执行')]),
        ('connection', 'unknown', '断流与未知结果', True, [('unknown', '缺失或冲突终态保持未知'),
            ('inspect', '显式检查只读，不追加消息或执行工具'), ('no_replay', '检查无待办不等于成功，不重放写入')]),
        ('business', 'context', '当前业务上下文', bool(settings['context']),
            [('target', '实际页面/记录/业务输入进入正确目标'),
             ('changed', '切换后使用新目标，无跨记录误写；存在待办时仍校验原目标')]),
        ('business', 'business_input', '结构化业务输入', bool(capability['inputSchema'].get('properties')),
            [('input', '实际字段值进入原任务，不使用默认或编造数据'),
             ('validation', '无效字段被后端拒绝，修正后可继续')]),
        ('business', 'tools', '真实工具与数据源', settings['tools']['enabled'],
            [('tool', '实际调用已声明工具及受控数据源'), ('postcondition', '从数据源独立核对结果或写入次数')]),
        ('business', 'business_result', '业务结果', capability.get('resultSchema') is not None,
            [('receipt', '正式 Schema 和身份校验后的结构化业务回执'),
             ('postcondition', '通过实际数据源或业务状态独立核对结果；非模型文字或终态')]),
        ('business', 'approve', '批准/确认', any(kind in interactions for kind in ('confirmation', 'approval_required')),
            [('pending', '原线程原目标的唯一当前待办'), ('approved', '显式同意后恢复原待办'),
             ('once', '双击/过期响应不重复执行'), ('postcondition', '独立核对实际业务结果及写入次数')]),
        ('business', 'reject', '拒绝/取消', any(kind in interactions for kind in ('confirmation', 'approval_required')),
            [('rejected', '显式拒绝后原动作锁定'), ('no_write', '实际数据源核对拒绝未产生写入')]),
        ('business', 'clarify', '澄清', 'clarification' in interactions,
            [('answer', '回答恢复原问题且目标不变'), ('no_approval', '澄清不替代写入批准'),
             ('result', '后续实际结果已独立核对')]),
    ]
    return [{'layer': layer, 'scenario': scenario, 'label': label, 'applicable': bool(applicable),
             'checks': [{'id': key, 'label': text} for key, text in checks]}
            for layer, scenario, label, applicable, checks in specifications]
