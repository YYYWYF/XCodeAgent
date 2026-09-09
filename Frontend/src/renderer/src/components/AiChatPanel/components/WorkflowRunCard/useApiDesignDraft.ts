import { useEffect, useMemo, useRef, useState } from 'react'
import type {
  WorkflowApiDesignDraft,
  WorkflowApiDesignPayload
} from '../../../../typings'
import {
  normalizeApiDesignDraft,
  validateApiDesignDraft
} from './apiDesignSerialization'

/** 管理仅属于当前 Endpoint 交互的 API 设计草稿与即时校验。 */
export function useApiDesignDraft(payload: WorkflowApiDesignPayload): {
  draft: WorkflowApiDesignDraft
  errors: ReturnType<typeof validateApiDesignDraft>
  setDraft: (draft: WorkflowApiDesignDraft) => void
} {
  const [draft, setDraft] = useState<WorkflowApiDesignDraft>(() =>
    normalizeApiDesignDraft(payload)
  )
  const payloadSignature = JSON.stringify({
    apiContractId: payload.draft.apiContractId,
    endpointId: payload.draft.endpointId,
    draft: payload.draft
  })
  const lastPayloadSignature = useRef(payloadSignature)

  useEffect(() => {
    // 普通流式状态会不断创建新的 payload 对象；只有目标或服务端草稿真正变化时才重置。
    if (lastPayloadSignature.current === payloadSignature) return
    lastPayloadSignature.current = payloadSignature
    setDraft(normalizeApiDesignDraft(payload))
  }, [payload, payloadSignature])

  const errors = useMemo(
    () => validateApiDesignDraft(draft),
    [draft]
  )

  return { draft, errors, setDraft }
}
