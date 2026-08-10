import assert from 'node:assert/strict'
import fs from 'node:fs/promises'
import os from 'node:os'
import path from 'node:path'
import { test } from 'node:test'
import { lstatIfPresent, removeDirectoryIfPresent } from '../src/main/filesystem'
import {
  sessionRuntimeKey,
  sessionRuntimeKeyBelongsToWorkspace
} from '../src/renderer/src/components/AiChatPanel/hooks/sessionRuntime'

/** 验证已不存在的路径可以重复执行删除而不产生错误。 */
test('删除不存在的目录按幂等成功处理', async () => {
  const temporaryRoot = await fs.mkdtemp(path.join(os.tmpdir(), 'xcodeagent-delete-'))
  const missingDirectory = path.join(temporaryRoot, 'missing-project')

  try {
    assert.equal(await lstatIfPresent(missingDirectory), undefined)
    await removeDirectoryIfPresent(missingDirectory)
    assert.equal(await lstatIfPresent(missingDirectory), undefined)
  } finally {
    await fs.rm(temporaryRoot, { force: true, recursive: true })
  }
})

/** 验证实际存在的目录仍然由删除工具移除。 */
test('删除存在的目录仍然执行递归删除', async () => {
  const temporaryRoot = await fs.mkdtemp(path.join(os.tmpdir(), 'xcodeagent-delete-'))
  const projectDirectory = path.join(temporaryRoot, 'project')

  try {
    await fs.mkdir(path.join(projectDirectory, 'nested'), { recursive: true })
    await fs.writeFile(path.join(projectDirectory, 'nested', 'file.txt'), 'content', 'utf8')
    await removeDirectoryIfPresent(projectDirectory)
    assert.equal(await lstatIfPresent(projectDirectory), undefined)
  } finally {
    await fs.rm(temporaryRoot, { force: true, recursive: true })
  }
})

/** 验证项目删除时只匹配同一绝对工作区的会话运行态，不误删同名目录记录。 */
test('会话运行态按完整工作区路径隔离清理', () => {
  const targetWorkspace = '/Users/example/projects/travels'
  const targetKey = sessionRuntimeKey(targetWorkspace, 'frontend', 'session-1')
  const sameNameElsewhereKey = sessionRuntimeKey(
    '/Users/example/archive/travels',
    'frontend',
    'session-2'
  )

  assert.equal(sessionRuntimeKeyBelongsToWorkspace(targetKey, targetWorkspace), true)
  assert.equal(sessionRuntimeKeyBelongsToWorkspace(sameNameElsewhereKey, targetWorkspace), false)
  assert.equal(sessionRuntimeKeyBelongsToWorkspace('malformed-key', targetWorkspace), false)
})
