import { access } from 'node:fs/promises'
import { resolve } from 'node:path'
import { fileURLToPath, pathToFileURL } from 'node:url'
import { planReactAgentUIProject, createAgentUIInitializationHost } from '@za38.03/agent-ui-creator/project/host'
import { initializeAgentUIProject } from '@za38.03/agent-ui-creator/bootstrap'

/** 通过成品包公开事务初始化全新消费者；已有应用层必须由其当前 owner 修改。 */
export async function initializeConsumer(projectRoot) {
  const input = { projectRoot: resolve(projectRoot), sourceRoot: 'src/agent-ui', mode: 'platform' }
  try {
    await access(resolve(input.projectRoot, input.sourceRoot))
  } catch (error) {
    if (error.code !== 'ENOENT') throw error
    const plan = await planReactAgentUIProject(input)
    if (!plan.valid) throw new Error(JSON.stringify(plan.issues))
    if (plan.dependencyPlan?.installRequired) {
      throw new Error('Install the consumer package.json dependencies before initialization.')
    }
    // 文件写入、校验及失败回滚全部交给公开 SDK，不复制或修改包私有实现。
    return initializeAgentUIProject(input, createAgentUIInitializationHost())
  }
  throw new Error('Consumer application layer already exists; refusing to overwrite it.')
}

if (process.argv[1] && import.meta.url === pathToFileURL(resolve(process.argv[1])).href) {
  const root = process.argv[2] ?? fileURLToPath(new URL('../agent-ui-consumer', import.meta.url))
  console.log(JSON.stringify(await initializeConsumer(root), null, 2))
}
