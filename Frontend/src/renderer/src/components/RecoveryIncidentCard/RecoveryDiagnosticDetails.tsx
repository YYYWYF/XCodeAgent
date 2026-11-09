import { Fragment, type ReactElement } from 'react'
import type { RecoveryFailureDiagnostic } from '../../service/recoveryActionPlan'
import { cx } from '../../utils'

const diagnosticFields = [
  ['错误代码', 'code'], ['HTTP Status', 'httpStatus'], ['Provider Error Code', 'providerErrorCode'],
  ['来源', 'origin'], ['Provider', 'provider'], ['Model', 'model'], ['失败阶段', 'stage'],
  ['失败步骤 / Operation', 'operation'], ['Dependency', 'dependency'],
  ['Source Run ID', 'sourceRunId'], ['错误信息', 'message']
] as const

/** 仅从公开诊断白名单读取有效字段，展示和复制保持同一份内容。 */
function diagnosticRows(diagnostic?: RecoveryFailureDiagnostic | null): Array<[string, string]> {
  if (!diagnostic) return []
  return diagnosticFields.flatMap(([label, key]) => {
    const value = diagnostic[key]
    return value === undefined || value === null || value === '' ? [] : [[label, String(value)] as [string, string]]
  })
}

/** 为原生剪贴板生成与展开区一致的公开诊断文本。 */
export function recoveryDiagnosticText(diagnostic?: RecoveryFailureDiagnostic | null): string {
  return diagnosticRows(diagnostic).map(([label, value]) => `${label}：${value}`).join('\n')
}

/** 在共用诊断面板内展示当前失败的结构化字段，不建立额外恢复状态。 */
export default function RecoveryDiagnosticDetails({ diagnostic }: { diagnostic: RecoveryFailureDiagnostic }): ReactElement {
  return <dl className={cx('agent-error-card-diagnostic-fields')}>
    {diagnosticRows(diagnostic).map(([label, value]) => <Fragment key={label}>
      <dt>{label}</dt><dd>{value}</dd>
    </Fragment>)}
  </dl>
}
