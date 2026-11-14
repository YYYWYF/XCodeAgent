const { createHash } = require('node:crypto')
const { readFileSync } = require('node:fs')
const path = require('node:path')

/** 在当前 Provider 的开发编译输入内订阅公开 Host，不改变 endpoint 或磁盘源码。 */
module.exports = function observationLoader(code) {
  const { sha256, sessionId, mode } = this.getOptions()
  if (createHash('sha256').update(code).digest('hex') !== sha256 ||
      !/^[a-f0-9]{32}$/.test(sessionId) || !['official-mock', 'real-text', 'formal-text', 'formal-business'].includes(mode) ||
      code.split('{children}').length !== 2 || code.includes('AgentUiPreviewObserver')) {
    throw new Error('Agent UI 观察输入已变化，请重启当前受管预览。')
  }
  return code.replace('{children}', `{children}<AgentUiPreviewObserver epoch=${JSON.stringify(sessionId)} source=${JSON.stringify(mode)} />`) +
    '\n' + readFileSync(path.join(__dirname, 'agent_ui_preview_observer.tsx'), 'utf8')
}
