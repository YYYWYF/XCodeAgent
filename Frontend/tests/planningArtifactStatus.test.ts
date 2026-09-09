import assert from 'node:assert/strict'
import fs from 'node:fs/promises'
import os from 'node:os'
import path from 'node:path'
import { createHash } from 'node:crypto'
import { test } from 'node:test'
import {
  endpointDesignDocumentExists,
  endpointDesignDocumentPath,
  endpointDesignJsonPath,
  endpointDesignDocumentStatus,
  PRODUCT_PLAN_SCHEMA_VERSION
} from '../src/main/planningArtifactStatus'

/** 验证 Electron 工作台只读取后端当前的 ProductPlan v5 契约。 */
test('ProductPlan 工作台校验使用 v5', () => {
  assert.equal(PRODUCT_PLAN_SCHEMA_VERSION, 'product-plan.v5')
})

/** 创建隔离工作区并在用例结束后清理。 */
async function withTemporaryWorkspace(
  run: (workspaceRoot: string) => Promise<void>
): Promise<void> {
  const workspaceRoot = await fs.mkdtemp(path.join(os.tmpdir(), 'xcodeagent-endpoint-status-'))
  try {
    await run(workspaceRoot)
  } finally {
    await fs.rm(workspaceRoot, { force: true, recursive: true })
  }
}

/** 验证 TechnicalPlan 中声明 endpoint 但没有独立文档时仍保持待设计。 */
test('没有 endpoint Markdown 时接口保持待设计', async () => {
  await withTemporaryWorkspace(async (workspaceRoot) => {
    assert.equal(
      await endpointDesignDocumentExists(workspaceRoot, 'employee-api', 'employee.list'),
      false
    )
  })
})

/** 验证空文件不属于已产出的有效 endpoint 设计文档。 */
test('空 endpoint Markdown 不会标记为已设计', async () => {
  await withTemporaryWorkspace(async (workspaceRoot) => {
    const documentPath = endpointDesignDocumentPath(
      workspaceRoot,
      'employee-api',
      'employee.list'
    )
    await fs.mkdir(path.dirname(documentPath), { recursive: true })
    await fs.writeFile(documentPath, '   \n', 'utf8')

    assert.equal(
      await endpointDesignDocumentExists(workspaceRoot, 'employee-api', 'employee.list'),
      false
    )
  })
})

/** 验证只有当前版双文件与 TechnicalPlan 指纹一致时才标记为已设计。 */
test('当前版 endpoint 双文件和指纹有效时接口标记为已设计', async () => {
  await withTemporaryWorkspace(async (workspaceRoot) => {
    await writeCurrentEndpointDesign(workspaceRoot)

    assert.equal(
      await endpointDesignDocumentExists(workspaceRoot, 'employee-api', 'employee.list'),
      true
    )
  })
})

/** 验证 JSON 与 Markdown 修订号不一致时不会误判为已设计。 */
test('endpoint JSON 与 Markdown 修订号不一致时需重新设计', async () => {
  await withTemporaryWorkspace(async (workspaceRoot) => {
    await writeCurrentEndpointDesign(workspaceRoot)
    const markdownPath = endpointDesignDocumentPath(workspaceRoot, 'employee-api', 'employee.list')
    await fs.writeFile(markdownPath, '# Employee list endpoint\n<!-- xcodeagent-artifact-revision: ffffffffffffffffffffffffffffffff -->\n', 'utf8')
    const status = await endpointDesignDocumentStatus(workspaceRoot, 'employee-api', 'employee.list')
    assert.equal(status.status, 'stale')
    assert.equal(status.designed, false)
  })
})

/** 验证 TechnicalPlan 修改后 Endpoint 进入需重新设计状态。 */
test('TechnicalPlan 指纹改变后接口需重新设计', async () => {
  await withTemporaryWorkspace(async (workspaceRoot) => {
    await writeCurrentEndpointDesign(workspaceRoot)
    const technicalPlanPath = path.join(
      workspaceRoot,
      '.xcodeagent',
      'plans',
      'technical-plan.json'
    )
    await fs.writeFile(technicalPlanPath, '{"artifact_type":"technical-plan","revision":2}\n')
    const status = await endpointDesignDocumentStatus(
      workspaceRoot,
      'employee-api',
      'employee.list'
    )
    assert.equal(status.status, 'stale')
    assert.equal(status.designed, false)
  })
})

/** 验证内部 JSON 单独存在时会被识别为残缺 stale。 */
test('只有 endpoint JSON 时接口需重新设计', async () => {
  await withTemporaryWorkspace(async (workspaceRoot) => {
    const markdownPath = endpointDesignDocumentPath(
      workspaceRoot,
      'employee-api',
      'employee.list'
    )
    await fs.mkdir(path.dirname(markdownPath), { recursive: true })
    await fs.writeFile(markdownPath.replace(/\.md$/, '.json'), '{"status":"confirmed"}\n', 'utf8')

    const status = await endpointDesignDocumentStatus(workspaceRoot, 'employee-api', 'employee.list')
    assert.equal(status.status, 'stale')
    assert.equal(status.designed, false)
    assert.equal(await endpointDesignDocumentExists(workspaceRoot, 'employee-api', 'employee.list'), false)
  })
})

/** 验证确认产物不能保留草稿态的未配置字段。 */
test('正式 Endpoint 产物含未配置字段时需重新设计', async () => {
  await withTemporaryWorkspace(async (workspaceRoot) => {
    await writeCurrentEndpointDesign(workspaceRoot, {
      fieldMappings: [{
        endpointField: {
          side: 'response', location: 'response_body', path: 'id', type: 'string', required: false
        },
        mappingType: 'unconfigured'
      }]
    })
    const status = await endpointDesignDocumentStatus(workspaceRoot, 'employee-api', 'employee.list')
    assert.equal(status.status, 'stale')
    assert.equal(status.designed, false)
  })
})

/** 验证确认产物不能缺少 Endpoint 字段映射集合。 */
test('正式 Endpoint 产物缺少字段映射时需重新设计', async () => {
  await withTemporaryWorkspace(async (workspaceRoot) => {
    await writeCurrentEndpointDesign(workspaceRoot)
    const status = await endpointDesignDocumentStatus(workspaceRoot, 'employee-api', 'employee.list')
    assert.equal(status.status, 'stale')
    assert.equal(status.designed, false)
  })
})

/** Endpoint 实现描述属于可选指导，缺失或为空不影响当前版设计状态。 */
test('缺少 Endpoint 实现描述仍保持已确认', async () => {
  await withTemporaryWorkspace(async (workspaceRoot) => {
    await writeCurrentEndpointDesign(workspaceRoot)
    const status = await endpointDesignDocumentStatus(workspaceRoot, 'employee-api', 'employee.list')
    assert.equal(status.status, 'confirmed')
    assert.equal(status.designed, true)
  })
})

/** Endpoint 实现描述存在时必须满足当前文本长度约束。 */
test('过长 Endpoint 实现描述需重新设计', async () => {
  await withTemporaryWorkspace(async (workspaceRoot) => {
    await writeCurrentEndpointDesign(workspaceRoot, { implementationDescription: 'x'.repeat(4001) })
    const status = await endpointDesignDocumentStatus(workspaceRoot, 'employee-api', 'employee.list')
    assert.equal(status.status, 'stale')
    assert.equal(status.designed, false)
  })
})

/** 写入满足当前版 Endpoint API 设计契约的完整测试产物。 */
async function writeCurrentEndpointDesign(
  workspaceRoot: string,
  options: {
    technicalPlan?: Record<string, unknown>
    fieldMappings?: unknown[]
    implementationDescription?: unknown
  } = {}
): Promise<void> {
  const markdownPath = endpointDesignDocumentPath(
    workspaceRoot,
    'employee-api',
    'employee.list'
  )
  const jsonPath = endpointDesignJsonPath(workspaceRoot, 'employee-api', 'employee.list')
  const technicalPlanPath = path.join(
    workspaceRoot,
    '.xcodeagent',
    'plans',
    'technical-plan.json'
  )
  await fs.mkdir(path.dirname(markdownPath), { recursive: true })
  const technicalPlan = Buffer.from(JSON.stringify(options.technicalPlan || {
    artifact_type: 'technical-plan', revision: 1
  }) + '\n')
  await fs.writeFile(technicalPlanPath, technicalPlan)
  const artifactRevision = '0123456789abcdef0123456789abcdef'
  await fs.writeFile(markdownPath, `# Employee list endpoint\n<!-- xcodeagent-artifact-revision: ${artifactRevision} -->\n`, 'utf8')
  await fs.writeFile(
    jsonPath,
    JSON.stringify({
      schemaVersion: 'endpoint-field-mapping.v3',
      artifactType: 'endpoint-field-mapping',
      status: 'confirmed',
      confirmationStatus: 'confirmed',
      artifactRevision,
      apiContractId: 'employee-api',
      endpointId: 'employee.list',
      confirmedAt: new Date().toISOString(),
      fieldMappings: options.fieldMappings || [{
        endpointField: {
          side: 'response', location: 'response_body', path: 'id', type: 'string', required: false
        },
        mappingType: 'business_description',
        businessDescription: '返回员工标识'
      }],
      ...(options.implementationDescription !== undefined
        ? { implementationDescription: options.implementationDescription }
        : {}),
      basedOn: [
        {
          artifactKey: 'technical-plan',
          sha256: createHash('sha256').update(technicalPlan).digest('hex')
        }
      ]
    }),
    'utf8'
  )
}
