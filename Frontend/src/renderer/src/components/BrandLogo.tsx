import type { CSSProperties, ReactElement } from 'react'
import { cx } from '../utils'
import './BrandLogo.less'

type Props = {
  /** mark 尺寸 px（文字字号按比例缩放）。欢迎页 34，工作台顶栏 22 左右。 */
  size?: number
  showText?: boolean
  className?: string
}

/** XCodeAgent 品牌 Logo：两条倾斜交叉光条 + 文字。欢迎页 / 工作台顶栏共用，避免各处自造。 */
export default function BrandLogo({ size = 34, showText = true, className }: Props): ReactElement {
  return (
    <span
      aria-label="XCodeAgent"
      className={cx('brand-logo', className)}
      style={{ '--brand-logo-size': `${size}px` } as CSSProperties}
    >
      <span className={cx('brand-logo-mark')} aria-hidden="true">
        <i />
        <i />
      </span>
      {showText ? <span className={cx('brand-logo-text')}>XCodeAgent</span> : null}
    </span>
  )
}
