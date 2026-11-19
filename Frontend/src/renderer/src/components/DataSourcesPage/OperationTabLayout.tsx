import { SwapOutlined } from '@ant-design/icons'
import { Button } from 'antd'
import type { ReactNode } from 'react'

type Props = {
  scrollLabel?: string
  children: ReactNode
  sync?: { label: string; disabled?: boolean; onClick: () => void }
  primaryAction?: ReactNode
  feedback?: ReactNode
}

/** 接口页签只滚动正文，同步、保存和提交反馈始终留在可视区。 */
export default function OperationTabLayout({ scrollLabel, children, sync, primaryAction, feedback }: Props) {
  return <section className="source-operation-tab-layout">
    {scrollLabel ? <div className="source-detail-content source-operation-scroll" role="region" aria-label={scrollLabel} tabIndex={0}>
      {children}
    </div> : children}
    {feedback && <div className="source-operation-tab-feedback">{feedback}</div>}
    <footer className="source-operation-tab-actions">
      {sync && <Button className="source-operation-sync" icon={<SwapOutlined />} disabled={sync.disabled} onClick={sync.onClick}>{sync.label}</Button>}
      {primaryAction}
    </footer>
  </section>
}
