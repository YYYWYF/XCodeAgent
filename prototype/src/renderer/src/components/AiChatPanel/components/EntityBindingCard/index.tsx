import { Button } from 'antd'
import type { ReactElement } from 'react'
import { cx } from '../../../../utils'
import './index.less'

/** 绑定计划行：与剧本 EntityBindingPlanRow 同构，经 Workflow 载荷传输后为纯对象。 */
export type EntityBindingPlanItem = {
  name: string
  operationType: 'builtin' | 'custom'
  kind: string
  target: string
  fields: number
}

type Props = {
  /** 实体名，用于按钮文案与提示。 */
  objectName: string
  /** 卡面说明文案，来自工作流澄清消息。 */
  message: string
  /** 逐操作的绑定计划：数据实现去向与字段映射规模。 */
  operations: EntityBindingPlanItem[]
  disabled?: boolean
  /** 提交中状态：等待续跑快照替换期间保持禁用。 */
  submitting?: boolean
  /** 点击确认绑定后提交。 */
  onConfirm: () => void
}

/**
 * 实体绑定确认卡：实体开发工作流「绑定操作的数据实现」节点的动作卡。
 * 绑定与字段映射的确认全部在对话区内完成——逐操作列出数据实现去向与字段规模，
 * 单个确认按钮一次性写回全部绑定；右侧面板只按确认结果静态呈现，不承载编辑。
 * 该卡作为工作流节点内嵌在节点轨迹中渲染，不单独成块。
 */
export default function EntityBindingCard({
  objectName,
  message,
  operations,
  disabled = false,
  submitting = false,
  onConfirm
}: Props): ReactElement {
  return (
    <div aria-label="确认数据实现绑定" className={cx('entity-binding-card')} role="group">
      {message && <p className={cx('entity-binding-card-summary')}>{message}</p>}
      <div className={cx('entity-binding-card-list')}>
        {operations.map((operation) => (
          <div className={cx('entity-binding-card-row')} key={operation.name}>
            <span className={cx('entity-binding-card-name')}>
              {operation.name}
              <em>{operation.operationType === 'builtin' ? '内置' : '自定义'}</em>
            </span>
            <span className={cx('entity-binding-card-target')}>
              {operation.kind} · {operation.target}
            </span>
            <span className={cx('entity-binding-card-fields')}>{operation.fields} 个字段</span>
          </div>
        ))}
      </div>
      <div className={cx('entity-binding-card-actions')}>
        <Button
          aria-label={`确认实体${objectName}的数据实现绑定`}
          disabled={disabled || submitting}
          loading={submitting}
          onClick={onConfirm}
          type="primary"
        >
          确认绑定
        </Button>
      </div>
    </div>
  )
}
