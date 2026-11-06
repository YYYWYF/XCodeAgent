import { createHash } from 'node:crypto'
import { readFile, realpath } from 'node:fs/promises'
import { resolve, relative, isAbsolute } from 'node:path'

/** 只在受检工具安装内解析公开 Mock export，绝不把 Creator 安装或打包进目标页面。 */
export async function loadDesignMock(options, importMockApi) {
  const { installationRoot, creatorVersion, packageJsonSha256, scenarioId, binding } = options
  if (!binding || typeof binding.pageId !== 'string' || !binding.pageId.trim() || binding.pageId.length > 256 ||
      /[\u0000-\u001f]/.test(binding.pageId) ||
      !['revision', 'sourceDigest', 'buildDigest'].every((key) => /^[a-f0-9]{64}$/.test(binding[key]))) {
    throw new Error('设计 Mock 缺少当前页面或构建身份。')
  }
  const modules = await realpath(resolve(installationRoot, 'node_modules'))
  const metadataPath = await realpath(resolve(modules, '@za38.03/agent-ui-creator/package.json'))
  const metadata = await readFile(metadataPath)
  const identity = JSON.parse(metadata)
  const part = relative(modules, metadataPath)
  if (part.startsWith('..') || isAbsolute(part)) throw new Error('Mock 不能链接到工具安装之外。')
  if (identity.name !== '@za38.03/agent-ui-creator' || identity.version !== creatorVersion ||
      createHash('sha256').update(metadata).digest('hex') !== packageJsonSha256) {
    throw new Error('Mock 工具身份发生变化，请重新检查环境。')
  }
  // 先核对 metadata，再执行由工具 cwd 正常解析的动态 import。
  const api = await importMockApi()
  const catalog = api.listOfficialMockScenarios().map(({ id, capabilities = [] }) => ({ id, capabilities }))
  if (!catalog.some(({ id }) => id === scenarioId)) throw new Error('当前安装不支持所选官方场景。')

  /** 录制只在开发侧导入；官方解析之后追加页面、构建和原始会话身份校验。 */
  function parseRecording(text, provenance) {
    if (typeof text !== 'string' || Buffer.byteLength(text) > Math.min(api.MAX_MOCK_RECORDING_BYTES, 2 * 1024 * 1024)) {
      throw new Error('录制超过开发侧导入上限。')
    }
    if (!provenance || !Object.keys(binding).every((key) => provenance[key] === binding[key]) ||
        !/^[\w-]{1,128}$/.test(provenance.id ?? '') || !provenance.threadId || !provenance.runId) {
      throw new Error('录制与当前设计身份不匹配。')
    }
    const recording = api.parseMockRecording(text, { id: provenance.id })
    const events = recording.events.map(({ event }) => event)
    if (events[0]?.type !== 'RUN_STARTED' || !['RUN_FINISHED', 'RUN_ERROR'].includes(events.at(-1)?.type) ||
        events.filter(({ type }) => type === 'RUN_STARTED').length !== 1 ||
        events.filter(({ type }) => ['RUN_FINISHED', 'RUN_ERROR'].includes(type)).length !== 1 ||
        events.some((event) => (event.threadId !== undefined && event.threadId !== provenance.threadId) ||
          (event.runId !== undefined && event.runId !== provenance.runId))) {
      throw new Error('录制必须是同一原始 thread/run 的完整生命周期。')
    }
    return recording
  }

  return {
    handler: api.createMockAgentHttpHandler({ defaultScenarioId: scenarioId }),
    evidence: { source: 'official-mock', creatorVersion, packageJsonSha256, scenarioId, binding, catalog,
      recordings: 'validated-offline-replay', businessRuntime: 'not-connected', businessApproval: 'not-verified' },
    parseRecording,
    runRecording: api.runMockRecording,
  }
}
