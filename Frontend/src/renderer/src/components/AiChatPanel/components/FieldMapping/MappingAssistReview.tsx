import { createContext, useContext, type ReactElement } from 'react'
import type { MappingAssistArea, MappingAssistance } from '../../../../typings/endpointDesignAssist'

export const MappingReviewContext = createContext<MappingAssistance['reviews']>([])

/** 将需核对的依据放在对应查询组或字段旁，直接映射保持简洁。 */
export default function MappingAssistReview({ area, target }: { area: MappingAssistArea; target: string }): ReactElement | null {
  const review = useContext(MappingReviewContext).find((item) => item.area === area && item.key === target && item.needsReview)
  if (!review) return null
  return <details className="mapping-assist-local-review">
    <summary>查看{area === 'query' ? '筛选规则' : '取值'}依据</summary>
    <p>{review.reason}</p>
  </details>
}
