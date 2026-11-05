import React from 'react'
import { Checkbox, Descriptions, Input, InputNumber, Select, Space, Typography } from 'antd'

type Schema = { properties?: Record<string, { type?: string; title?: string; description?: string; enum?: unknown[] }>; required?: string[] }

/** 按正式输入字段展示业务表单，嵌套对象或数组接受业务 JSON 数据，完整校验由 Runtime 执行。 */
export function AgentTaskFields({ schema, value, disabled, onChange }: {
  schema: Schema; value: Record<string, unknown>; disabled: boolean; onChange: (value: Record<string, unknown>) => void
}): React.ReactElement {
  return <Space direction="vertical" style={{ width: '100%' }}>
    {Object.entries(schema.properties || {}).map(([name, field]) => {
      /** 单字段编辑只更新当前业务值，不修改正式 Schema。 */
      const change = (next: unknown): void => onChange({ ...value, [name]: next })
      return <div key={name}>
        <Typography.Text>{field.title || name}{schema.required?.includes(name) ? ' *' : ''}</Typography.Text>
        {field.enum ? <Select disabled={disabled} value={value[name] as string | undefined} style={{ width: '100%' }}
          options={field.enum.map((item) => ({ label: String(item), value: item as string }))} onChange={change} />
          : field.type === 'boolean' ? <Checkbox disabled={disabled} checked={value[name] === true} onChange={(event) => change(event.target.checked)}>是</Checkbox>
          : field.type === 'number' || field.type === 'integer' ? <InputNumber disabled={disabled} value={value[name] as number | undefined}
            precision={field.type === 'integer' ? 0 : undefined} onChange={change} />
          : <Input.TextArea disabled={disabled} value={String(value[name] ?? '')} autoSize={{ minRows: 2, maxRows: 8 }}
            placeholder={field.description || (field.type === 'object' || field.type === 'array' ? '填写业务 JSON 数据' : undefined)}
            onChange={(event) => change(event.target.value)} />}
      </div>
    })}
  </Space>
}

/** 提交前只解析对象/数组输入，保留未填写项供服务端按正式必填规则校验。 */
export function taskInputValues(schema: Schema, values: Record<string, unknown>): Record<string, unknown> {
  const result: Record<string, unknown> = {}
  for (const [name, value] of Object.entries(values)) {
    if (value === '' || value === undefined || value === null) continue
    const field = schema.properties?.[name]
    if ((field?.type === 'object' || field?.type === 'array') && typeof value === 'string') {
      try { result[name] = JSON.parse(value) }
      catch { throw new Error(`${field.title || name} 需要合法的 JSON 数据。`) }
    } else result[name] = value
  }
  return result
}

/** 显示已经由服务端校验的检测字段，文本说明与结构化结果分别保留。 */
export function AgentTaskResult({ result }: { result: unknown }): React.ReactElement {
  const values = result && typeof result === 'object' && !Array.isArray(result)
    ? Object.entries(result) : [['检测结果', result]]
  return <Descriptions title="检测结果" column={1} size="small">
    {values.map(([key, value]) => <Descriptions.Item key={String(key)} label={String(key)}>
      {typeof value === 'object' ? <pre style={{ whiteSpace: 'pre-wrap', overflowWrap: 'anywhere', margin: 0 }}>{JSON.stringify(value, null, 2)}</pre>
        : String(value ?? '无')}
    </Descriptions.Item>)}
  </Descriptions>
}
