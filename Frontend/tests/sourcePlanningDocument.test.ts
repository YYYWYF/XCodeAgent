import assert from 'node:assert/strict'
import { test } from 'node:test'
import type {
  ReadWorkspaceFileRequest,
  ReadWorkspaceFileResult
} from '../src/renderer/src/service/workspaceTools'
import { readSourcePlanningDocument } from '../src/renderer/src/components/AiChatPanel/components/SourcePanel/sourcePlanningDocument'

const requirementPath = '.devagentstudio/specs/requirement-spec.md'
const productPlanPath = '.devagentstudio/plans/product-plan.md'

/** 构造完整文件回执，支持验证截断和非法内容的处理。 */
function fileResult(content: string, truncated = false): ReadWorkspaceFileResult {
  return {
    tool: 'file.read',
    path: requirementPath,
    start_line: 1,
    end_line: 1,
    total_lines: 1,
    truncated,
    content
  }
}

test('需求可视化读取同一工作区和修订的正式 JSON，并保留产品页面细节', async () => {
  const requests: ReadWorkspaceFileRequest[] = []
  const spec = { app_name: '年龄录入', pages: [{ pageId: 'current_time' }] }
  const productPlan = { pages: [{ pageId: 'current_time', goal: '显示当前时间' }] }
  const document = await readSourcePlanningDocument(
    {
      workspaceRoot: '/workspace',
      documentKey: 'requirement-spec',
      documentPath: requirementPath,
      productPlanPath,
      revision: 'dev'
    },
    async (request) => {
      requests.push(request)
      return fileResult(
        JSON.stringify(request.path.includes('requirement-spec') ? spec : productPlan)
      )
    }
  )
  assert.deepEqual(document, { documentKey: 'requirement-spec', spec, productPlan })
  assert.deepEqual(
    requests.map((request) => request.path),
    [requirementPath.replace('.md', '.json'), productPlanPath.replace('.md', '.json')]
  )
  for (const request of requests) {
    assert.equal(request.workspace_root, '/workspace')
    assert.equal(request.revision, 'dev')
    assert.equal(request.max_lines, 5000)
    assert.equal(request.max_chars, 200000)
  }
})

test('草稿需求保持草稿路径，产品规划缺失时仍提供需求概览', async () => {
  const requests: ReadWorkspaceFileRequest[] = []
  const draftPath = '.devagentstudio/drafts/specs/requirement-spec.md'
  const document = await readSourcePlanningDocument(
    { workspaceRoot: '/workspace', documentKey: 'requirement-spec', documentPath: draftPath },
    async (request) => {
      requests.push(request)
      return fileResult('{"app_name":"草稿应用"}')
    }
  )
  assert.equal(requests.length, 1)
  assert.equal(requests[0].path, draftPath.replace('.md', '.json'))
  assert.deepEqual(document, {
    documentKey: 'requirement-spec',
    spec: { app_name: '草稿应用' },
    productPlan: {}
  })
})

test('截断、非法 JSON 和非对象内容明确失败，避免展示误导的空文档', async () => {
  for (const result of [
    fileResult('{"pages":[]}', true),
    fileResult('{'),
    fileResult('[]'),
    fileResult('null')
  ]) {
    await assert.rejects(
      readSourcePlanningDocument(
        {
          workspaceRoot: '/workspace',
          documentKey: 'requirement-spec',
          documentPath: requirementPath
        },
        async () => result
      )
    )
  }
})

test('产品规划读取失败向上传递，避免静默丢失页面目标和验收标准', async () => {
  await assert.rejects(
    readSourcePlanningDocument(
      {
        workspaceRoot: '/workspace',
        documentKey: 'requirement-spec',
        documentPath: requirementPath,
        productPlanPath
      },
      async (request) => {
        if (request.path.includes('product-plan')) throw new Error('产品规划读取失败')
        return fileResult('{"app_name":"年龄录入"}')
      }
    ),
    /产品规划读取失败/
  )
})

test('产品规划单独读取所选草稿，不依赖需求或技术规划文件', async () => {
  const requests: ReadWorkspaceFileRequest[] = []
  const productPlan = { app: { name: '年龄录入' }, pages: [{ pageId: 'current_time' }] }
  const draftPath = '.devagentstudio/drafts/plans/product-plan.md'
  const document = await readSourcePlanningDocument(
    {
      workspaceRoot: '/workspace',
      documentKey: 'product-plan',
      documentPath: draftPath,
      productPlanPath
    },
    async (request) => {
      requests.push(request)
      return fileResult(JSON.stringify(productPlan))
    }
  )
  assert.deepEqual(document, { documentKey: 'product-plan', productPlan })
  assert.equal(requests.length, 1)
  assert.equal(requests[0].path, draftPath.replace('.md', '.json'))
})

test('技术规划与页面绑定的产品规划使用相同修订，并保留完整契约', async () => {
  const requests: ReadWorkspaceFileRequest[] = []
  const plan = { api_contracts: [{ id: 'clock', endpoints: [{ id: 'get_time' }] }] }
  const productPlan = { pages: [{ pageId: 'current_time', name: '当前时间页' }] }
  const document = await readSourcePlanningDocument(
    {
      workspaceRoot: '/workspace',
      documentKey: 'technical-plan',
      documentPath: '.devagentstudio/plans/technical-plan.md',
      productPlanPath,
      revision: 'dev'
    },
    async (request) => {
      requests.push(request)
      return fileResult(
        JSON.stringify(request.path.includes('technical-plan') ? plan : productPlan)
      )
    }
  )
  assert.deepEqual(document, { documentKey: 'technical-plan', plan, productPlan })
  assert.deepEqual(
    requests.map((request) => request.path),
    ['.devagentstudio/plans/technical-plan.json', '.devagentstudio/plans/product-plan.json']
  )
  assert.ok(requests.every((request) => request.revision === 'dev'))
})

test('技术规划缺少产品规划依赖时明确报错，避免不完整的页面绑定', async () => {
  await assert.rejects(
    readSourcePlanningDocument(
      {
        workspaceRoot: '/workspace',
        documentKey: 'technical-plan',
        documentPath: '.devagentstudio/plans/technical-plan.md'
      },
      async () => fileResult('{}')
    ),
    /未找到产品规划/
  )
})
