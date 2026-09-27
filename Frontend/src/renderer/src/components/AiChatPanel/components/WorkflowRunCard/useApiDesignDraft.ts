import { useEffect, useMemo, useRef, useState } from 'react'
import type {
  WorkflowApiDesignDraft,
  WorkflowApiDesignPayload
} from '../../../../typings'
import {
  normalizeApiDesignDraft,
  validateApiDesignDraft
} from './apiDesignSerialization'

const EMPTY_VALIDATION_ERRORS: ReturnType<typeof validateApiDesignDraft> = {}

/** 管理当前 Endpoint 的 API 设计草稿，并在提交尝试后显示校验错误。 */
export function useApiDesignDraft(payload: WorkflowApiDesignPayload): {
  draft: WorkflowApiDesignDraft
  errors: ReturnType<typeof validateApiDesignDraft>
  validationErrors: ReturnType<typeof validateApiDesignDraft>
  showValidationErrors: () => void
  setDraft: (draft: WorkflowApiDesignDraft) => void
} {
  const [draft, setDraft] = useState<WorkflowApiDesignDraft>(() =>
    normalizeApiDesignDraft(payload)
  )
  const [validationVisible, setValidationVisible] = useState(false)
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
    setValidationVisible(false)
  }, [payload, payloadSignature])

  const validationErrors = useMemo(
    () => validateApiDesignDraft(draft),
    [draft]
  )
  const errors = validationVisible ? validationErrors : EMPTY_VALIDATION_ERRORS

  return {
    draft,
    errors,
    validationErrors,
    showValidationErrors: () => setValidationVisible(true),
    setDraft
  }
}
