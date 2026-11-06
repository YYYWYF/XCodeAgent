const translations = {
  'zh-CN': {
    title: 'Agent UI 设计转入',
    open: '转入 Agent UI',
    inspect: '读取当前差异',
    prepare: '准备依赖',
    install: '确认安装依赖',
    initialize: '确认初始化',
    transfer: '确认转入',
    query: '查询原任务',
    stop: '停止',
    close: '关闭',
    review: '我已审阅差异、验证与恢复范围',
    intro: '将已确认设计的应用层转入当前工程。初始化后需重新审阅，完成转入后仍需接线和运行验收。',
    empty: '先读取当前差异；若依赖尚未准备，请先审阅依赖计划。',
    dependency: '依赖与样式计划',
    files: '逐文件差异',
    excluded: '保留与排除',
    validation: '验证与恢复',
    newTarget: '新目标需先初始化。默认应用层生成后，将重新计算设计差异供你确认。',
    conflict: '存在冲突或不支持的资源，当前计划不能执行。',
    ready: '差异已就绪，等待明确确认。',
    unknown: '连接结果未知，请查询原任务。写入不会自动重试。',
    error: '操作失败，请检查当前结果。',
    pending: '处理中…',
    installHint: '使用已审阅的本地成品包组合；保留原有包管理器、业务依赖和构建工具。',
    status: '任务状态',
    record: '来源与恢复记录',
    unchanged: '无变化',
    add: '新增',
    modify: '修改',
    blocked: '冲突',
    source: '设计来源',
    target: '目标',
    progress: '当前阶段',
    copy: '转入应用层',
    composition: '校验组件组合',
    initialization: '初始化目标',
    checks: '静态检查'
  },
  'en-US': {
    title: 'Transfer Agent UI design',
    open: 'Transfer Agent UI',
    inspect: 'Review current changes',
    prepare: 'Prepare dependencies',
    install: 'Confirm dependency installation',
    initialize: 'Confirm initialization',
    transfer: 'Confirm transfer',
    query: 'Query original task',
    stop: 'Stop',
    close: 'Close',
    review: 'I reviewed the changes, validation and recovery scope',
    intro:
      'Transfer the confirmed application layer into this project. Review again after initialization. Host wiring and runtime acceptance follow separately.',
    empty:
      'Review the current changes first. If dependencies are missing, review their installation plan.',
    dependency: 'Dependency and style plan',
    files: 'File changes',
    excluded: 'Preserved and excluded',
    validation: 'Validation and recovery',
    newTarget:
      'Initialize the new target first. Then review the differences between the default application layer and your confirmed design.',
    conflict: 'Conflicts or unsupported resources block execution.',
    ready: 'Changes are ready for explicit confirmation.',
    unknown:
      'Connection outcome is unknown. Query the original task; writes are never retried automatically.',
    error: 'Operation failed. Check the current result.',
    pending: 'Working…',
    installHint:
      'Use the reviewed local package artifacts while preserving the existing package manager, business dependencies and build tool.',
    status: 'Task status',
    record: 'Provenance and recovery record',
    unchanged: 'Unchanged',
    add: 'Add',
    modify: 'Modify',
    blocked: 'Conflict',
    source: 'Design source',
    target: 'Target',
    progress: 'Current stage',
    copy: 'Transferring application layer',
    composition: 'Validating composition',
    initialization: 'Initializing target',
    checks: 'Static checks'
  }
}

/** 宿主尚无语言上下文时跟随系统语言，新功能文案集中维护双语配对。 */
export function transferMessages(): (typeof translations)['zh-CN'] {
  return translations[
    typeof navigator !== 'undefined' && navigator.language.startsWith('zh') ? 'zh-CN' : 'en-US'
  ]
}
