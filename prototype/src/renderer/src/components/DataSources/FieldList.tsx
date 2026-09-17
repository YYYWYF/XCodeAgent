import type { ReactElement } from 'react'
import { cx } from '../../utils'

type FieldLike = { name: string; comment: string; required?: boolean }

/** 参数/字段清单：名称等宽展示 + 说明，必填带弱标记；抽屉行详情与详情弹框共用。 */
export default function FieldList({
  emptyText,
  fields
}: {
  emptyText: string
  fields: FieldLike[]
}): ReactElement {
  return (
    <div className={cx('ds-fields')}>
      {fields.length === 0 ? <p className={cx('ds-field-empty')}>{emptyText}</p> : null}
      {fields.map((field) => (
        <div className={cx('ds-field')} key={field.name}>
          <code>{field.name}</code>
          <span>{field.comment}</span>
          {field.required ? <em className={cx('ds-field-required')}>必填</em> : null}
        </div>
      ))}
    </div>
  )
}

/** 参数分组小标题：与 FieldList 搭配构成 Postman 式只读分组。 */
export function ParamHead({ children }: { children: string }): ReactElement {
  return <div className={cx('ds-param-head')}>{children}</div>
}
