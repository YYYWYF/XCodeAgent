import type { ReactElement } from 'react'
import type { RightPanelLayout } from '../../types'
import { cx } from '../../../../utils'
import './RightPanelLayoutControl.less'

export type { RightPanelLayout } from '../../types'

type Props = {
  value: RightPanelLayout
  onChange: (value: RightPanelLayout) => void
  docked?: boolean
  floating?: boolean
}

const LAYOUT_OPTIONS: Array<{ label: string; value: RightPanelLayout }> = [
  { value: 'hidden', label: '隐藏右侧面板' },
  { value: 'split', label: '分栏显示右侧面板' },
  { value: 'full', label: '右侧面板全宽覆盖' }
]

/** 右侧布局三档控制器：隐藏、分栏和全宽覆盖均可直接选择。 */
export default function RightPanelLayoutControl({
  value,
  onChange,
  docked = false,
  floating = false
}: Props): ReactElement {
  return (
    <div
      aria-label="右侧面板布局"
      className={cx('right-panel-layout-control', docked && 'docked', floating && 'floating')}
      role="group"
    >
      {LAYOUT_OPTIONS.map((option) => (
        <button
          key={option.value}
          aria-label={option.label}
          aria-pressed={value === option.value}
          className={cx('right-panel-layout-option', `mode-${option.value}`, value === option.value && 'active')}
          onClick={(event) => {
            onChange(option.value)
            // 鼠标选档后不保留焦点，控制器会自然收回为边线提示。
            event.currentTarget.blur()
          }}
          onMouseDown={(event) => event.stopPropagation()}
          title={option.label}
          type="button"
        >
          <span aria-hidden="true" className={cx('right-panel-layout-icon')} />
        </button>
      ))}
    </div>
  )
}
