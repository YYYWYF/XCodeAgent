import assert from 'node:assert/strict'
import { test } from 'node:test'
import { endpointDesignSummary, groupEndpointDesignRows, projectEndpointDesignRows } from '../src/renderer/src/components/AiChatPanel/components/EndpointDesignResult/endpointDesignResultModel'

/** 验证正式 Endpoint 设计的请求/返回结果投影与设计表字段结构一致。 */
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
        { endpointField: { side: 'request', location: 'query', path: 'page', type: 'integer' }, mappingType: 'source_mapping', processingType: 'direct', sourceFields: [{ sourceType: 'database', sourceId: 'db', schema: 'app', table: 'orders', column: 'page', usage: 'filter', filterOperator: 'gte' }] },
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
  assert.equal(projectEndpointDesignRows(detail, 'request')[0].filterOperator, 'gte')
  assert.equal(projectEndpointDesignRows(detail, 'response')[0].dataSourceType, '外部 API')
  assert.equal(projectEndpointDesignRows(detail, 'response')[0].dataSource, 'upstream')
  assert.equal(projectEndpointDesignRows(detail, 'response')[0].mappingField, 'catalog / list / response_body / data.id')
  assert.equal(groupEndpointDesignRows(projectEndpointDesignRows(detail, 'request'))[0].label, 'Query 参数')
  assert.equal(projectEndpointDesignRows(detail, 'request')[0].dataSource.includes('sourceId'), false)
  assert.deepEqual(endpointDesignSummary(detail), { requestCount: 2, responseCount: 1, totalCount: 3 })
})
