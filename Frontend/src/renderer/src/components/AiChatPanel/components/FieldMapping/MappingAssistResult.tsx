import { Button } from 'antd'
import { ExclamationCircleFilled } from '@ant-design/icons'
import type { ReactElement } from 'react'
import type { MappingAssistance } from '../../../../typings/endpointDesignAssist'
import type { MappingAssistState } from './useMappingAssist'
import { mappingAssistIssues } from './mappingAssistReviewModel'
import { MAPPING_REQUIREMENTS_LIMIT } from './mappingAssistModel'
import MappingAssistIssues from './MappingAssistIssues'
import './MappingAssistResult.less'

/** 独立呈现待明确问题，采纳时才通过原 AG-UI 动作回填草稿。 */
export default function MappingAssistResult({ assist, result, disabled }: {
  assist: MappingAssistState; result: MappingAssistance; disabled: boolean
}): ReactElement {
  const issues = mappingAssistIssues(result)
  const running = assist.phase !== 'idle'
  const showRegenerate = issues.length > 0 || assist.requirementsChanged
  const canRegenerate = !disabled && !running && assist.requirementsChanged && assist.requirementsLength <= MAPPING_REQUIREMENTS_LIMIT
  const pending = result.mapping?.fieldMappings.filter((item) => item.endpointField.side === 'response' && item.mappingType === 'unconfigured') || []
  const pendingRequests = result.mapping?.externalApiBindings?.filter((item) => !item.right) || []

  return <div className="mapping-assist-result">
    {!result.mapping && <p className="mapping-assist-secondary">{result.operationReason}</p>}
    <MappingAssistIssues issues={issues} assist={assist} disabled={disabled} />
    <footer className="mapping-assist-replace">
      {Boolean(pending.length || pendingRequests.length) && <div className="mapping-review-notice" role="status">
        <ExclamationCircleFilled aria-hidden="true" /><span>采纳后，下列字段会置为未配置。
          {pending.length > 0 && <> 返回字段：{pending.map((item) => item.endpointField.path).join('、')}。</>}
          {pendingRequests.length > 0 && <> 外部入参：{pendingRequests.map((item) => `${item.externalField.section}.${item.externalField.path}`).join('、')}。</>}
        </span>
      </div>}
      <div className="mapping-assist-toolbar">
        <p className="mapping-assist-secondary">{result.mapping ? '当前页面正在预览 AI 结果。采纳后回填草稿，正式生效仍需保存并确认。' : '补充数据库操作说明后可重新生成。'}</p>
        <div className="mapping-review-footer-actions">
          <Button disabled={running} onClick={assist.close}>{result.mapping ? '不采纳' : '收起'}</Button>
          {showRegenerate && <Button disabled={!canRegenerate} onClick={() => void assist.generate()}>补充并重新生成</Button>}
          {result.mapping && <Button type="primary" aria-label="采纳 AI 映射" loading={assist.phase === 'applying'} disabled={disabled || running || !assist.canApply}
            onClick={() => void assist.apply()}>采纳</Button>}
        </div>
      </div>
    </footer>
  </div>
}
