import { InfoCircleOutlined } from '@ant-design/icons'
import { Tooltip } from 'antd'
import type { ReactElement } from 'react'
import type { BackgroundDispatchChoice } from '../../../../backgroundTasks'
import { cx } from '../../../../utils'
import './index.less'

export type BackgroundDispatchOption = {
  /** 选项取值：同步执行（sync）或某套后台任务系统（async/tide）。 */
  key: BackgroundDispatchChoice
  /** 选项标题，例如「同步执行」。 */
  label: string
  /** 选项说明；收进注释图标（Tooltip）里，不占版面。 */
  description: string
  /** 选项图标：与左侧菜单入口、任务抽屉共用同一套图标语言。 */
  icon?: ReactElement
}

type Props = {
  /** 执行方式选项；渲染为一行等宽选项，点击即提交。 */
  options: BackgroundDispatchOption[]
  disabled?: boolean
  /** 提交的答案键：页面轮与接口轮分别提交，避免两轮选择互相覆盖。 */
  answerKey: string
  /** 选择某个执行方式后提交。 */
  onSelect: (key: BackgroundDispatchChoice, answerKey: string) => void
}

/**
 * 执行方式选择卡：通用的精简交互卡，供各类工作流在派发任务前选择执行通道
 * （同步执行 / 异步队列 / 潮汐队列）。卡面只保留选项本身，解释性话术收进
 * 每个选项的注释图标（Tooltip）；点击选项即代表选定并提交，不再叠加确认步骤。
 * 该卡作为工作流节点内嵌在节点轨迹中渲染，不单独成块。
 */
export default function BackgroundDispatchCard({
  disabled = false,
  answerKey,
  onSelect,
  options
}: Props): ReactElement {
  return (
    <div
      aria-label="选择执行方式"
      className={cx('background-dispatch-card')}
      role="group"
    >
      {options.map((option) => (
        <button
          type="button"
          key={option.key}
          className={cx('background-dispatch-card-option')}
          disabled={disabled}
          onClick={() => onSelect(option.key, answerKey)}
        >
          {option.icon ? (
            <span aria-hidden="true" className={cx('background-dispatch-card-option-icon')}>
              {option.icon}
            </span>
          ) : null}
          <span className={cx('background-dispatch-card-option-label')}>{option.label}</span>
          <Tooltip title={option.description}>
            <span
              aria-label={`${option.label}说明`}
              className={cx('background-dispatch-card-option-hint')}
              role="note"
            >
              <InfoCircleOutlined />
            </span>
          </Tooltip>
        </button>
      ))}
    </div>
  )
}
