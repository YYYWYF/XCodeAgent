import type { ReactElement } from 'react'
import { cx } from '../../../../utils'
import ApplicationOutline from '../ApplicationOutline'
import type { ApplicationOutlineProps } from '../ApplicationOutline'
import './DevelopmentArtifactsPanel.less'

type Props = ApplicationOutlineProps & {
  detailLabel?: string
}

/** 并排展示常驻菜单和随选中产物更新的详情占位，菜单选择不切换工作区标签。 */
export default function DevelopmentArtifactsPanel({
  detailLabel,
  ...outlineProps
}: Props): ReactElement {
  return (
    <div className={cx('development-artifacts-panel')}>
      <aside aria-label="开发产物菜单" className={cx('development-artifacts-menu')}>
        <ApplicationOutline {...outlineProps} />
      </aside>
      <section
        aria-label={detailLabel ? `${detailLabel}详情` : '开发产物详情'}
        className={cx('development-artifacts-detail')}
      >
        <div aria-atomic="true" className={cx('development-artifacts-placeholder')} role="status">
          <h3>{detailLabel || '请选择开发产物'}</h3>
          <p>{detailLabel ? '详情内容待设计' : '点击左侧菜单查看详情'}</p>
        </div>
      </section>
    </div>
  )
}
