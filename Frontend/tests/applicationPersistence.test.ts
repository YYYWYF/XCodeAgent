import assert from 'node:assert/strict'
import { test } from 'node:test'
import { spawnSync } from 'node:child_process'
import path from 'node:path'
import { buildApplicationSchema } from '../src/renderer/src/components/Welcome/utils'
import { initialApplicationDraft } from '../src/renderer/src/components/Welcome/constants'
import { assertCurrentApplicationSchema } from '../src/main/managedWorkspace'
import { configChangesDigest } from '../src/renderer/src/service/applicationConfigOperation'
import { saveApplication } from '../src/renderer/src/components/Welcome/applicationService'
import { saveWorkspaceApplicationConfig } from '../src/renderer/src/service/applicationStorage'
import type { ApplicationConfig, ApplicationDraft } from '../src/renderer/src/typings'

/** 构造当前版本新建应用，并使用无真实含义的测试凭据。 */
function schema(login: boolean, authorization: boolean) {
  const draft = structuredClone(initialApplicationDraft) as ApplicationDraft
  draft.auth.enable = login
  Object.assign(draft.auth.yht, { clientId: 'test-client', privateKey: 'test-private', publicKey: 'test-public', centerPublicKey: 'test-center' })
  draft.authorization.enabled = authorization
  draft.authorization.initialAdministratorSubjects = authorization ? ['test-admin'] : []
  return buildApplicationSchema(draft)
}

for (const [login, authorization] of [[false, false], [true, false], [true, true]]) {
  test(`新建配置同时满足 Electron 和 Python 当前合同：login=${login},authorization=${authorization}`, () => {
    const current = schema(login, authorization)
    assert.doesNotThrow(() => assertCurrentApplicationSchema(current as unknown as Record<string, unknown>))
    const backend = path.resolve(process.cwd(), '../Backend')
    const result = spawnSync(path.join(backend, '.venv/bin/python'), ['-c', 'import json,sys; from app.services.application_config.schema import validate_application_configuration; validate_application_configuration(json.load(sys.stdin))'], {
      cwd: backend, input: JSON.stringify(current), encoding: 'utf8'
    })
    assert.equal(result.status, 0, result.stderr)
  })
}

test('普通保存以磁盘受管字段为准，不回退修订、权限和管理员', async () => {
  const originalWindow = globalThis.window
  const stale = schema(false, false)
  const disk = { ...schema(true, true), configRevision: 5 }
  let written: unknown
  Object.assign(globalThis, { window: { devAgentStudio: { workspace: {
    readApplication: async () => ({ application: disk }),
    writeApplication: async (payload: { application: unknown }) => { written = payload.application; return { application: payload.application } }
  } } } })
  try {
    const saved = await saveWorkspaceApplicationConfig('/tmp/new-config', stale)
    assert.equal(saved.configRevision, 5)
    assert.deepEqual(saved.auth, disk.auth)
    assert.deepEqual(saved.authorization, disk.authorization)
    assert.ok(written)
  } finally {
    Object.assign(globalThis, { window: originalWindow })
  }
})

test('已有工作区读取失败必须中止 saveApplication，不能以旧内存写盘', async () => {
  const originalWindow = globalThis.window
  let writes = 0
  Object.assign(globalThis, { window: { devAgentStudio: { workspace: {
    readApplication: async () => { throw new Error('invalid disk') },
    writeApplication: async () => { writes++; throw new Error('unexpected write') }
  } } } })
  try {
    await assert.rejects(saveApplication({ ...schema(false, false), workspaceRoot: '/tmp/existing-config' } as ApplicationConfig), /读取失败，已中止保存/)
    assert.equal(writes, 0)
  } finally {
    Object.assign(globalThis, { window: originalWindow })
  }
})


test('前端提案摘要与 Python 对中文、数组和转义字符的摘要一致', async () => {
  const backend = path.resolve(process.cwd(), '../Backend')
  const changes = { appName: '应用\n名称', 'authorization.initialAdministratorSubjects': ['成员甲', 'member-2'], 'auth.enable': true }
  const result = spawnSync(path.join(backend, '.venv/bin/python'), ['-c', 'import json,sys; from app.services.config_operation_state import changes_digest; print(changes_digest(json.load(sys.stdin)))'], { cwd: backend, input: JSON.stringify(changes), encoding: 'utf8' })
  assert.equal(result.status, 0)
  assert.equal(await configChangesDigest(changes), result.stdout.trim())
})
