import { Button, Spin } from 'antd'
import type { ReactElement } from 'react'
import type { PlanningCatalogStatus } from '../../../../typings'
import { cx } from '../../../../utils'
import './CatalogReadNotice.less'

/** 明确区分正在读盘、文件缺失/损坏和真实空目录，并提供原路径重新读取。 */
export default function CatalogReadNotice({ status, onRetry }: {
  status?: PlanningCatalogStatus
  onRetry?: () => void
}): ReactElement | null {
  if (!status || (!status.loading && !status.error && !status.issues.length)) return null
  const failed = Boolean(status.error || status.issues.length)
  const reasons = { missing: '文件未读到', invalid: '文件格式无效', unreadable: '文件无法读取' }
  return (
    <div className={cx('catalog-read-notice')} role={failed && !status.loading ? 'alert' : 'status'}>
      <strong>{status.loading ? <><Spin size="small" /> 正在读取规划目录…</> : '部分规划目录未读取成功'}</strong>
      {status.error ? <div>{status.error}</div> : null}
      {status.issues.map((issue) => (
        <div key={issue.source}>
          {issue.source === 'product' ? '页面' : '接口与实体'}：{reasons[issue.reason]}
        </div>
      ))}
      {failed && (status.productAvailable || status.technicalAvailable) ? <div>已有内容暂时保留，请重新读取。</div> : null}
      {!status.loading && onRetry ? <Button size="small" onClick={onRetry}>重新读取</Button> : null}
    </div>
  )
}
