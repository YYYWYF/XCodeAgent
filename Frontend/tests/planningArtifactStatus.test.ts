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
import { readWorkbenchPlanningArtifacts } from '../src/main/workbenchPlanningArtifacts'

/** 创建完整当前正式规划，再替换单项状态以复现修订期间的工作台读取。 */
async function writeWorkbenchPlans(workspaceRoot: string): Promise<void> {
  const artifacts = {
    'specs/requirement-spec.md': '# 需求',
    'specs/requirement-spec.json': JSON.stringify({ confirmation_status: 'confirmed' }),
    'plans/product-plan.md': '# 产品规划',
    'plans/product-plan.json': JSON.stringify({
      schema_version: PRODUCT_PLAN_SCHEMA_VERSION, confirmation_status: 'confirmed',
      pages: [{ pageId: 'help', name: '帮助页', path: '/page/help' }]
    }),
    'plans/technical-plan.json': JSON.stringify({
      artifact_type: 'technical-plan', confirmation_status: 'confirmed',
      pages: [{ pageId: 'help' }], entities: [{ id: 'article' }],
      api_contracts: [{ id: 'articles', endpoints: [{ id: 'list', path: '/articles' }] }]
    })
  }
  for (const [relativePath, content] of Object.entries(artifacts)) {
    const target = path.join(workspaceRoot, '.devagentstudio', relativePath)
    await fs.mkdir(path.dirname(target), { recursive: true })
    await fs.writeFile(target, content)
  }
}

/** 验证目录读取当前技术草稿，确认前保留正式版本，只有 plans 的应用继续走原读取。 */
test('技术草稿优先用于目录浏览，确认后读取正式计划', async () => {
  await withTemporaryWorkspace(async (workspaceRoot) => {
    await writeWorkbenchPlans(workspaceRoot)
    const formalPath = path.join(workspaceRoot, '.devagentstudio/plans/technical-plan.json')
    const draftPath = path.join(workspaceRoot, '.devagentstudio/drafts/plans/technical-plan.json')
    const original = await fs.readFile(formalPath, 'utf8')
    const draft = {
      ...JSON.parse(original), confirmation_status: 'pending_user_confirmation',
      entities: [{ id: 'revised_article' }]
    }
    await fs.mkdir(path.dirname(draftPath), { recursive: true })
    await fs.writeFile(draftPath, JSON.stringify(draft))
    const pending = await readWorkbenchPlanningArtifacts(workspaceRoot)
    assert.deepEqual(pending.technicalPlan?.entities, draft.entities)
    assert.deepEqual(pending.invalid, ['drafts/plans/technical-plan.json'])
    assert.deepEqual(pending.catalogIssues, [])
    assert.equal(await fs.readFile(formalPath, 'utf8'), original)
    // 首次生成只有草稿时也可正常浏览，不能报告正式计划缺失。
    await fs.rename(formalPath, `${formalPath}.test-backup`)
    const initial = await readWorkbenchPlanningArtifacts(workspaceRoot)
    assert.deepEqual(initial.technicalPlan?.entities, draft.entities)
    assert.deepEqual(initial.missing, [])
    await fs.writeFile(draftPath, JSON.stringify({ ...draft, confirmation_status: 'confirmed' }))
    await fs.rename(draftPath, formalPath)
    const confirmed = await readWorkbenchPlanningArtifacts(workspaceRoot)
    assert.deepEqual(confirmed.technicalPlan?.entities, draft.entities)
    assert.deepEqual(confirmed.invalid, [])
    assert.deepEqual(confirmed.catalogIssues, [])
  })
})

/** 验证技术规划待确认时页面、接口、实体均可浏览，但正式准入仍被阻止。 */
test('待确认技术规划保留完整接口和实体供目录浏览', async () => {
  await withTemporaryWorkspace(async (workspaceRoot) => {
    await writeWorkbenchPlans(workspaceRoot)
    const technicalPath = path.join(workspaceRoot, '.devagentstudio/plans/technical-plan.json')
    const technical = JSON.parse(await fs.readFile(technicalPath, 'utf8'))
    technical.confirmation_status = 'pending_user_confirmation'
    await fs.writeFile(technicalPath, JSON.stringify(technical))
    const pending = await readWorkbenchPlanningArtifacts(workspaceRoot)
    assert.deepEqual(pending.technicalPlan?.api_contracts, technical.api_contracts)
    assert.deepEqual(pending.technicalPlan?.entities, technical.entities)
    assert.equal((pending.productPlan?.pages as unknown[]).length, 1)
    assert.deepEqual(pending.invalid, ['plans/technical-plan.json'])
    assert.deepEqual(pending.catalogIssues, [])
    technical.confirmation_status = 'confirmed'
    await fs.writeFile(technicalPath, JSON.stringify(technical))
    const confirmed = await readWorkbenchPlanningArtifacts(workspaceRoot)
    assert.deepEqual(confirmed.technicalPlan?.api_contracts, technical.api_contracts)
    assert.deepEqual(confirmed.invalid, [])
  })
})

/** 验证技术规划缺失、损坏或状态未知时，产品目录保持可读，整体准入仍报告该错误。 */
test('技术规划异常不会遮蔽已确认的产品页面，也不会通过正式产物准入', async () => {
  await withTemporaryWorkspace(async (workspaceRoot) => {
    await writeWorkbenchPlans(workspaceRoot)
    const technicalPath = path.join(workspaceRoot, '.devagentstudio/plans/technical-plan.json')
    for (const content of [
      JSON.stringify({ artifact_type: 'technical-plan', confirmation_status: 'unknown' }),
      '{invalid-json',
      JSON.stringify({ artifact_type: 'wrong-contract', confirmation_status: 'confirmed' })
    ]) {
      await fs.writeFile(technicalPath, content)
      const result = await readWorkbenchPlanningArtifacts(workspaceRoot)
      assert.deepEqual(result.productPlan?.pages, [{ pageId: 'help', name: '帮助页', path: '/page/help' }])
      assert.equal(result.technicalPlan, undefined)
      assert.deepEqual(result.invalid, ['plans/technical-plan.json'])
      assert.deepEqual(result.catalogIssues, [{ source: 'technical', reason: 'invalid' }])
    }
    await fs.rename(technicalPath, `${technicalPath}.test-backup`)
    const missing = await readWorkbenchPlanningArtifacts(workspaceRoot)
    assert.equal((missing.productPlan?.pages as unknown[]).length, 1)
    assert.deepEqual(missing.missing, ['plans/technical-plan.json'])
    assert.deepEqual(missing.catalogIssues, [{ source: 'technical', reason: 'missing' }])
  })
})

/** 验证缺失数组与合法空数组严格区分，只有后者代表真实没有目录内容。 */
test('损坏的目录数组不能被当成真实空目录', async () => {
  await withTemporaryWorkspace(async (workspaceRoot) => {
    await writeWorkbenchPlans(workspaceRoot)
    const technicalPath = path.join(workspaceRoot, '.devagentstudio/plans/technical-plan.json')
    const technical = JSON.parse(await fs.readFile(technicalPath, 'utf8'))
    await fs.writeFile(technicalPath, JSON.stringify({ ...technical, api_contracts: null }))
    const invalid = await readWorkbenchPlanningArtifacts(workspaceRoot)
    assert.equal(invalid.technicalPlan, undefined)
    assert.deepEqual(invalid.catalogIssues, [{ source: 'technical', reason: 'invalid' }])
    await fs.writeFile(technicalPath, JSON.stringify({ ...technical, pages: [], api_contracts: [], entities: [] }))
    const empty = await readWorkbenchPlanningArtifacts(workspaceRoot)
    assert.deepEqual(empty.technicalPlan?.api_contracts, [])
    assert.deepEqual(empty.technicalPlan?.entities, [])
    assert.deepEqual(empty.catalogIssues, [])
  })
})

/** 验证待确认产品页仍可浏览但不通过正式准入，技术契约也独立读取。 */
test('产品规划与技术规划独立校验，确认后重新读取最新目录', async () => {
  await withTemporaryWorkspace(async (workspaceRoot) => {
    await writeWorkbenchPlans(workspaceRoot)
    const productPath = path.join(workspaceRoot, '.devagentstudio/plans/product-plan.json')
    const product = JSON.parse(await fs.readFile(productPath, 'utf8'))
    await fs.writeFile(productPath, JSON.stringify({ ...product, confirmation_status: 'pending_user_confirmation' }))
    const pending = await readWorkbenchPlanningArtifacts(workspaceRoot)
    assert.deepEqual(pending.productPlan?.pages, product.pages)
    assert.equal((pending.technicalPlan?.api_contracts as unknown[]).length, 1)
    assert.deepEqual(pending.invalid, ['plans/product-plan.json'])
    product.pages.push({ pageId: 'notice', name: '公告页', path: '/page/notice' })
    await fs.writeFile(productPath, JSON.stringify(product))
    const confirmed = await readWorkbenchPlanningArtifacts(workspaceRoot)
    assert.equal((confirmed.productPlan?.pages as unknown[]).length, 2)
    assert.deepEqual(confirmed.missing, [])
    assert.deepEqual(confirmed.invalid, [])
  })
})

/** 验证 Electron 工作台只读取后端当前的 ProductPlan v5 契约。 */
test('ProductPlan 工作台校验使用 v5', () => {
  assert.equal(PRODUCT_PLAN_SCHEMA_VERSION, 'product-plan.v5')
})

/** 创建隔离工作区并在用例结束后清理。 */
async function withTemporaryWorkspace(
  run: (workspaceRoot: string) => Promise<void>
): Promise<void> {
  const workspaceRoot = await fs.mkdtemp(path.join(os.tmpdir(), 'devagentstudio-endpoint-status-'))
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

/** 验证旧正式产物明确要求重新配置。 */
test('v4 endpoint 产物需重新配置', async () => {
  await withTemporaryWorkspace(async (workspaceRoot) => {
    await writeCurrentEndpointDesign(workspaceRoot, { schemaVersion: 'endpoint-field-mapping.v4' })
    const status = await endpointDesignDocumentStatus(workspaceRoot, 'employee-api', 'employee.list')
    assert.equal(status.status, 'stale')
    assert.match(status.reason, /重新配置/)
  })
})

/** 验证 JSON 与 Markdown 修订号不一致时不会误判为已设计。 */
test('endpoint JSON 与 Markdown 修订号不一致时需重新设计', async () => {
  await withTemporaryWorkspace(async (workspaceRoot) => {
    await writeCurrentEndpointDesign(workspaceRoot)
    const markdownPath = endpointDesignDocumentPath(workspaceRoot, 'employee-api', 'employee.list')
    await fs.writeFile(markdownPath, '# Employee list endpoint\n<!-- devagentstudio-artifact-revision: ffffffffffffffffffffffffffffffff -->\n', 'utf8')
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
      '.devagentstudio',
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
    await writeCurrentEndpointDesign(workspaceRoot, { omitFieldMappings: true })
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
    schemaVersion?: string
    omitFieldMappings?: boolean
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
    '.devagentstudio',
    'plans',
    'technical-plan.json'
  )
  await fs.mkdir(path.dirname(markdownPath), { recursive: true })
  const technicalPlan = Buffer.from(JSON.stringify(options.technicalPlan || {
    artifact_type: 'technical-plan', revision: 1
  }) + '\n')
  await fs.writeFile(technicalPlanPath, technicalPlan)
  const artifactRevision = '0123456789abcdef0123456789abcdef'
  await fs.writeFile(markdownPath, `# Employee list endpoint\n<!-- devagentstudio-artifact-revision: ${artifactRevision} -->\n`, 'utf8')
  await fs.writeFile(
    jsonPath,
    JSON.stringify({
      schemaVersion: options.schemaVersion || 'endpoint-field-mapping.v7',
      artifactType: 'endpoint-field-mapping',
      status: 'confirmed',
      confirmationStatus: 'confirmed',
      artifactRevision,
      apiContractId: 'employee-api',
      endpointId: 'employee.list',
      confirmedAt: new Date().toISOString(),
      ...(!options.omitFieldMappings ? { fieldMappings: options.fieldMappings || [{
        endpointField: {
          side: 'response', location: 'response_body', path: 'id', type: 'string', required: false
        },
        mappingType: 'business_description',
        businessDescription: '返回员工标识'
      }] } : {}),
      databaseWrites: [],
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
