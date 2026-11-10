import { ProfileOutlined, SafetyOutlined, SettingOutlined } from '@ant-design/icons'
import { cx } from '../../../../utils'
import './TaskFileDescription.less'

/** 只把原文中明确标出的栏目排成详情行，不补造栏目或改写业务约束。 */
export function TaskFileDescription({ description }: { description: string }): JSX.Element {
  const matches = [
    ...description.matchAll(
      /^(职责|类定义|字段(?:定义|映射)?|接口(?:\/方法)?|方法签名|处理逻辑|边界约束|复用要求|映射规则)[：:][ \t]*/gm
    )
  ]
  if (!matches.length) return <p className={cx('workflow-task-description-plain')}>{description}</p>
  const prefix = description.slice(0, matches[0].index)
  return (
    <div className={cx('workflow-task-description-sections')}>
      {prefix && <p className={cx('workflow-task-description-plain')}>{prefix}</p>}
      <dl>
        {matches.map((match, index) => {
          const label = match[1]
          const value = description.slice(
            match.index! + match[0].length,
            matches[index + 1]?.index ?? description.length
          )
          return (
            <div key={match.index}>
              <dt>
                <span aria-hidden="true">
                  {label === '边界约束' ? (
                    <SafetyOutlined />
                  ) : label === '处理逻辑' ? (
                    <SettingOutlined />
                  ) : (
                    <ProfileOutlined />
                  )}
                </span>
                {label}
              </dt>
              <dd>{value}</dd>
            </div>
          )
        })}
      </dl>
    </div>
  )
}
