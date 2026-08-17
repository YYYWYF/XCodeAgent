import { LoadingOutlined } from '@ant-design/icons'
import { Typography } from 'antd'
import { cx } from '../../../../utils'
import './index.less'

const { Text } = Typography

type Props = {
  title?: string
  hint?: string
  /** bare：纯圆动画，无卡片/标题/进度条（用于预览区等已有容器，只要中间好看的圆动画）。 */
  bare?: boolean
}

/** 富加载态（gradient 圆 + 光晕轨道）。
 * 默认带标题+三色 shimmer 进度条（文档区）；bare 只保留中间圆动画（预览区）。 */
export default function RichLoading({ title, hint, bare }: Props): JSX.Element {
  if (bare) {
    return (
      <div className={cx('rich-loading-orbit-only')} aria-live="polite">
        <div className={cx('detail-page-progress-visual')} aria-hidden="true">
          <span className={cx('detail-page-progress-orbit')}>
            <span />
          </span>
          <LoadingOutlined className={cx('detail-page-progress-loading')} />
        </div>
        {title ? (
          <Text className={cx('rich-loading-orbit-title')} type="secondary">{title}</Text>
        ) : null}
      </div>
    )
  }
  return (
    <div className={cx('detail-page-progress', 'rich-loading')}>
      <div className={cx('detail-page-progress-visual')} aria-hidden="true">
        <span className={cx('detail-page-progress-orbit')}>
          <span />
        </span>
        <LoadingOutlined className={cx('detail-page-progress-loading')} />
      </div>
      {title ? <Text className={cx('rich-loading-title')} strong>{title}</Text> : null}
      {hint ? (
        <Text className={cx('rich-loading-hint')} type="secondary">{hint}</Text>
      ) : null}
      <div className={cx('detail-page-progress-track')} aria-hidden="true">
        <span className={cx('detail-page-progress-bar')}>
          <span />
        </span>
      </div>
    </div>
  )
}
