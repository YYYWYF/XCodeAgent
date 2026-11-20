const { createHash } = require('node:crypto')

/** 仅改写当前受审阅 Host 的开发编译输入；每次启动重建会话，不复用模拟消息。 */
module.exports = function managedPreviewEndpoint(code) {
  const { sha256, agentId, mode, endpoint, sessionId, observe } = this.getOptions()
  const identity = `agentId=${JSON.stringify(agentId)}`
  const endpoints = [...code.matchAll(/endpoint=("[^"\r\n]*")/g)]
  if (typeof agentId !== 'string' || !agentId.trim() ||
      createHash('sha256').update(code).digest('hex') !== sha256 ||
      endpoints.length !== 1 || !['""', JSON.stringify(`/agents/${agentId}/run`)].includes(endpoints[0][1]) || code.split(identity).length !== 2) {
    throw new Error('Agent UI Host 已变化，请重新启动并审阅开发预览。')
  }
  if (!['real-text', 'official-mock', 'replay'].includes(mode) || !/^[a-f0-9]{32}$/.test(sessionId) ||
      (mode === 'real-text' && (endpoint !== `/agents/${agentId}/run` || !/^[a-z][a-z0-9_]*$/.test(agentId)))) {
    throw new Error('Agent UI 开发连接合同无效，请重新启动预览。')
  }
  const connected = code.replace(endpoints[0][0], `endpoint=${JSON.stringify(mode === 'real-text' ? endpoint : '/__agent_ui_mock')}`)
    .replace('<SharedAgentApplication ', `<SharedAgentApplication key=${JSON.stringify(mode + ':' + sessionId)} `)
  // Mock 不生成业务证据；真实模式保留原只读观察，缺可信 runId 时仍显示 unknown。
  const result = mode === 'real-text' ? connected : connected.replace(identity, 'agentId={undefined}')
  if (!observe) return result
  return require('./agent_ui_preview_observer.cjs').call({ getOptions: () => ({
    sha256: createHash('sha256').update(result).digest('hex'), sessionId, mode, agentId
  }) }, result)
}
