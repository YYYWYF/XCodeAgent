import type { IntegrationTestCheckRecord } from '../../../../service/agUiAgent'

/** 仅允许前端性能检查行展示完整 Lighthouse HTML 入口。 */
export function integrationTestCheckReportPath(
  check: IntegrationTestCheckRecord
): string | undefined {
  return check.id === 'frontend_performance' && check.reportPath ? check.reportPath : undefined
}
