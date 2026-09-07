import { useEffect, useMemo, useState } from 'react'
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

  useEffect(() => {
    setDraft(normalizeApiDesignDraft(payload))
  }, [payload])

  const errors = useMemo(
    () => validateApiDesignDraft(draft, payload.entityTemplates || []),
    [draft, payload.entityTemplates]
  )

  return { draft, errors, setDraft }
}
