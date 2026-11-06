import { readFile } from 'node:fs/promises'
import path from 'node:path'

/** 只通过成品包公开 exports 解析工具；目标应用不加载工具代码。 */
const host = await import('@za38.03/agent-ui-creator/project/host')
const dev = await import('@za38.03/agent-ui-creator/project/dev')
const bootstrap = await import('@za38.03/agent-ui-creator/bootstrap')
let text = ''
for await (const chunk of process.stdin) {
  text += chunk
  if (text.length > 16000) throw new Error('input too large')
}
const input = JSON.parse(text)

try {
  const config = { projectRoot: input.projectRoot, sourceRoot: input.sourceRoot, mode: 'platform' }
  if (!path.isAbsolute(config.projectRoot)) throw new Error('invalid project')
  if (input.action === 'initialize') {
    // XCodeAgent 已通过 T21 校验并安装依赖；公开 Bootstrap Host 仍先检查实际包与源码边界。
    // React 依赖规划器面向尚未准备依赖的宿主，会拒绝独立包的 pnpm 构建策略文件。
    const result = await bootstrap.initializeAgentUIProject(config, host.createAgentUIInitializationHost())
    process.stdout.write(JSON.stringify({ status: 'passed', createdPaths: result.createdPaths }))
  } else if (input.action === 'verify') {
    const record = JSON.parse(await readFile(path.join(config.projectRoot, '.agent-ui/project.json'), 'utf8'))
    if (record.sourceRoot !== input.sourceRoot) throw new Error('source root mismatch')
    await dev.synchronizeAgentUIPluginRegistry(config.projectRoot)
    const result = await dev.verifyUIProject(config.projectRoot)
    process.stdout.write(JSON.stringify({ status: result.status, codes: result.errors.map(issue => issue.code) }))
  } else throw new Error('unsupported action')
} catch (error) {
  // 原始异常可能含绝对路径或工具输出，只返回稳定拒绝信息。
  const code = typeof error?.code === 'string' && /^[A-Z_]+$/.test(error.code) ? error.code : 'public_tool_rejected'
  process.stderr.write(error instanceof Error ? error.message : 'public tool rejected')
  process.stdout.write(JSON.stringify({ status: 'failed', codes: [code] }))
  process.exitCode = 1
}
