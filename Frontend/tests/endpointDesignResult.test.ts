import assert from 'node:assert/strict'
import { test } from 'node:test'
import type { WorkflowRunPayload } from '../src/renderer/src/typings'
import { readApiDesignResult } from '../src/renderer/src/components/AiChatPanel/components/WorkflowRunCard/apiDesignResult'
import { endpointDesignSummary, groupEndpointDesignRows, projectEndpointDesignRows } from '../src/renderer/src/components/AiChatPanel/components/EndpointDesignResult/endpointDesignResultModel'

/** 验证正式 Endpoint 设计的请求/返回投影与设计表字段结构一致。 */
test('Endpoint 设计结果投影保留请求和返回映射', () => {
  const detail = {
    apiContractId: 'orders-api',
    endpointId: 'orders.list',
    status: 'confirmed' as const,
    designed: true,
    reason: '',
    design: {
      sourceSnapshots: [{ sourceType: 'database', sourceId: 'db', name: '业务数据库', details: {} }],
      fieldMappings: [
        { endpointField: { side: 'request', location: 'query', path: 'page', type: 'integer' }, mappingType: 'source_mapping', processingType: 'direct', sourceFields: [{ sourceType: 'database', sourceId: 'db', schema: 'app', table: 'orders', column: 'page' }] },
        { endpointField: { side: 'response', location: 'response_body', path: 'items[].id', type: 'integer' }, mappingType: 'source_mapping', processingType: 'direct', sourceFields: [{ sourceType: 'external_api', sourceId: 'upstream', directoryId: 'catalog', operationId: 'list', section: 'response_body', path: 'data.id' }] },
        { endpointField: { side: 'request', location: 'query', path: 'sort', type: 'string' }, mappingType: 'business_description', businessDescription: '控制排序字段' }
      ]
    }
  }
  assert.equal(projectEndpointDesignRows(detail, 'request').length, 2)
  assert.equal(projectEndpointDesignRows(detail, 'response')[0].mapping, '直接映射')
  assert.equal(projectEndpointDesignRows(detail, 'request')[0].endpoint, 'page')
  assert.equal(projectEndpointDesignRows(detail, 'request')[0].locationLabel, 'Query')
  assert.equal(projectEndpointDesignRows(detail, 'request')[0].dataSourceType, '数据库')
  assert.equal(projectEndpointDesignRows(detail, 'request')[0].dataSource, '业务数据库')
  assert.equal(projectEndpointDesignRows(detail, 'request')[0].mappingField, 'app.orders.page')
  assert.equal(projectEndpointDesignRows(detail, 'response')[0].dataSourceType, '外部 API')
  assert.equal(projectEndpointDesignRows(detail, 'response')[0].dataSource, 'upstream')
  assert.equal(projectEndpointDesignRows(detail, 'response')[0].mappingField, 'catalog / list / response_body / data.id')
  assert.equal(groupEndpointDesignRows(projectEndpointDesignRows(detail, 'request'))[0].label, 'Query 参数')
  assert.equal(projectEndpointDesignRows(detail, 'request')[0].dataSource.includes('sourceId'), false)
  assert.deepEqual(endpointDesignSummary(detail), { requestCount: 2, responseCount: 1, totalCount: 3 })
})

/** 验证会话 JSON 恢复后的确认快照仍可读取并投影完整字段映射。 */
test('恢复历史 API 设计快照后保留实现描述和业务说明', () => {
  const workflow: WorkflowRunPayload = {
    runId: 'history-run',
    threadId: 'history-thread',
    summary: {
      apiDesignResult: JSON.parse(
        JSON.stringify({
          status: 'confirmed',
          targetType: 'endpoint',
          targetId: 'orders.list',
          targetLabel: 'GET /orders',
          confirmedForDevelopment: true,
          designs: [gateDesign('orders.list', '1'.repeat(32), {
              implementationDescription: '从订单服务读取分页列表',
              fieldMappings: [
                {
                  endpointField: { side: 'request', location: 'query', path: 'tenantId', type: 'string' },
                  mappingType: 'business_description',
                  businessDescription: '按租户隔离数据'
                }
              ]
            })]
        })
      )
    },
    events: []
  }
  const restored = readApiDesignResult(workflow)
  const restoredDesign = restored?.designs[0]
  assert.equal(restoredDesign?.design.implementationDescription, '从订单服务读取分页列表')
  assert.equal(projectEndpointDesignRows({
    apiContractId: restoredDesign?.apiContractId || '',
    endpointId: restoredDesign?.endpointId || '',
    status: 'confirmed',
    designed: true,
    reason: '',
    design: restoredDesign?.design
  }, 'request')[0].description, '按租户隔离数据')
})

/** 页面门禁结果必须在一次序列化恢复后保留全部 Endpoint 映射。 */
test('页面 API 门禁快照保留全部关联 Endpoint', () => {
  const workflow: WorkflowRunPayload = {
    runId: 'page-run',
    threadId: 'page-thread',
    summary: {
      apiDesignResult: JSON.parse(JSON.stringify({
        status: 'confirmed',
        targetType: 'page',
        targetId: 'orders',
        targetLabel: '订单页',
        confirmedForDevelopment: true,
        designs: [
          gateDesign('orders.list', '1'.repeat(32)),
          gateDesign('orders.create', '2'.repeat(32))
        ]
      }))
    },
    events: []
  }
  const restored = readApiDesignResult(workflow)
  assert.equal(restored?.targetType, 'page')
  assert.deepEqual(restored?.designs.map((item) => item.endpointId), ['orders.list', 'orders.create'])
})

/** 失败或草稿结果不能让门禁误显示为已确认映射。 */
test('门禁忽略失败和草稿 API 设计结果', () => {
  const workflow: WorkflowRunPayload = {
    runId: 'gate-run',
    threadId: 'gate-thread',
    summary: {
      clarification: {
        mode: 'api_design_required',
        status: 'requires_user_input'
      },
      apiDesignResult: {
        status: 'failed',
        targetType: 'endpoint',
        targetId: 'orders.list',
        targetLabel: 'GET /orders',
        designs: []
      }
    },
    events: []
  }
  assert.equal(readApiDesignResult(workflow), undefined)
})

/** 构造符合当前聚合门禁协议的单项正式映射。 */
function gateDesign(
  endpointId: string,
  artifactRevision: string,
  overrides: Record<string, unknown> = {}
): Record<string, unknown> {
  const design = {
    status: 'confirmed',
    apiContractId: 'orders-api',
    endpointId,
    artifactRevision,
    fieldMappings: [],
    ...overrides
  }
  return {
    apiContractId: 'orders-api',
    endpointId,
    artifactRevision,
    design
  }
}
