import { asRecord, recordItems, stringItems, textValue, type JsonRecord } from './TechnicalPlanDocPanelData'

export type EndpointAuthorizationView = {
  id: string
  name: string
  method: string
  path: string
  permissionsKnown: boolean
  operations: string[]
  resources: Array<{ key: string; name: string }>
}

/** 只展示技术规划生成的操作、接口与资源绑定，不再重复上游角色授权。 */
export function endpointAuthorizationView(plan: JsonRecord, productPlan: JsonRecord): EndpointAuthorizationView[] {
  const manifest = asRecord(plan.authorization_manifest)
  const permissionsKnown = manifest.schema_version === 'authorization-manifest.v3' &&
    Array.isArray(manifest.resources) && Array.isArray(asRecord(manifest.bindings).endpoints)
  const resources = new Map(recordItems(manifest.resources).map((item) => [textValue(item.resourceKey), item]))
  const bindings = new Map(recordItems(asRecord(manifest.bindings).endpoints).map((item) => [
    textValue(item.endpointId), stringItems(item.operationResourceKeys)
  ]))
  const productPages = new Map(recordItems(productPlan.pages).map((page) => [textValue(page.pageId), page]))
  const operations = new Map<string, Set<string>>()
  for (const page of recordItems(plan.pages)) {
    const productPage = productPages.get(textValue(page.pageId))
    const actions = new Map(recordItems(productPage?.actions).map((action) => [textValue(action.actionId), action]))
    for (const binding of recordItems(asRecord(page.references).action_implementations)) {
      const label = `${textValue(productPage?.name, textValue(page.pageId))} / ${textValue(actions.get(textValue(binding.actionId))?.name, textValue(binding.actionId))}`
      const endpoints = [textValue(binding.endpointId), ...recordItems(binding.stepBindings).map((step) => textValue(step.endpointId))]
      for (const endpoint of endpoints.filter(Boolean)) {
        const labels = operations.get(endpoint) || new Set<string>()
        labels.add(label)
        operations.set(endpoint, labels)
      }
    }
  }
  return recordItems(plan.api_contracts).flatMap((contract) => recordItems(contract.endpoints).map((endpoint) => {
    const id = textValue(endpoint.id)
    return {
      id, name: textValue(endpoint.name, id), method: textValue(endpoint.method), path: textValue(endpoint.path), permissionsKnown,
      operations: [...(operations.get(id) || [])],
      resources: (bindings.get(id) || []).map((key) => ({ key, name: textValue(resources.get(key)?.name, '引用未解析') }))
    }
  }))
}
