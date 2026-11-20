const { createHash } = require('node:crypto')
const { readFileSync } = require('node:fs')
const path = require('node:path')

/** 在当前 Provider 的开发编译输入内订阅公开 Host，不改变 endpoint 或磁盘源码。 */
module.exports = function observationLoader(code) {
  const { sha256, sessionId, mode, agentId } = this.getOptions()
  const application = code.match(/import\s*\{\s*SharedAgentApplication\s*\}\s*from\s*['"]([^'"\r\n]+)\/SharedApplication['"]/)
  if (createHash('sha256').update(code).digest('hex') !== sha256 ||
      !/^[a-f0-9]{32}$/.test(sessionId) || !['official-mock', 'replay', 'real-text', 'formal-text', 'formal-business'].includes(mode) ||
      code.split('{children}').length !== 2 || code.includes('AgentUiPreviewObserver') || !application ||
      !/^[a-z][a-z0-9_]*$/.test(agentId)) {
    throw new Error('Agent UI 观察输入已变化，请重启当前受管预览。')
  }
  const directory = application[1]
  const observer = readFileSync(path.join(__dirname, 'agent_ui_preview_observer.tsx'), 'utf8')
    .replace("'./__agent_ui_observation__/useRuntimeResult'", JSON.stringify(directory + '/useRuntimeResult'))
    .replace("import { useSharedContextObservation as useObservationContext } from './__agent_ui_observation__/runtimeContext'",
      mode === 'formal-business'
        ? `import { useSharedContextObservation as useObservationContext } from ${JSON.stringify(directory + '/runtimeContext')}`
        : '/** 非业务来源不声明页面记录 Context。 */\nfunction useObservationContext(): ObservationContext | null { return null }')
  // 正式来源同样按 launch epoch 重建会话，HMR 先到也不能保留上一服务的待办。
  const isolated = ['formal-text', 'formal-business'].includes(mode)
    ? code.replace('<SharedAgentApplication ', `<SharedAgentApplication key=${JSON.stringify(mode + ':' + sessionId)} `) : code
  return isolated.replace('{children}', `{children}<AgentUiPreviewObserver epoch=${JSON.stringify(sessionId)} source=${JSON.stringify(mode)} key=${JSON.stringify(mode + ':' + sessionId)} agentId=${JSON.stringify(['official-mock', 'replay'].includes(mode) ? '' : agentId)} />`) +
    '\n' + observer
}
