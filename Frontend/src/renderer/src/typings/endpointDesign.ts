import type { WorkflowApiDesignPayload } from './workflow'

/** Endpoint API 设计正式产物的状态。 */
export type EndpointDesignStatus = 'pending' | 'confirmed' | 'stale'

/** 独立 Endpoint 设计查询返回的结构。 */
export type EndpointDesignDetail = {
  apiContractId: string
  endpointId: string
  status: EndpointDesignStatus
  designed: boolean
  reason: string
  design?: Record<string, unknown>
  markdown?: string
}

/** 独立编辑器准备阶段返回的草稿与乐观并发版本。 */
export type EndpointDesignPreparation = {
  apiContractId: string
  endpointId: string
  payload: WorkflowApiDesignPayload
  artifactRevision?: string | null
}

/** 独立保存动作返回的最新设计结果。 */
export type EndpointDesignSaveResult = {
  status: 'saved'
  apiContractId: string
  endpointId: string
  artifactRevision: string
  design?: Record<string, unknown>
  detail?: EndpointDesignDetail
  artifacts?: Record<string, string>
}

/** Endpoint 设计 AG-UI 查询的最终载荷。 */
export type EndpointDesignsPayload = {
  schemaVersion: 1
  runId: string
  threadId: string
  status: 'completed' | 'failed'
  action?: 'get' | 'prepare' | 'save'
  detail?: EndpointDesignDetail
  preparation?: EndpointDesignPreparation
  saved?: EndpointDesignSaveResult
  error?: { type?: string; message?: string }
}
