import { Button, Form, Input, Space, Typography } from 'antd'
import { useState } from 'react'
import type { ReactElement } from 'react'
import type { WorkflowApiDesignDraft, WorkflowApiField } from '../../../../typings'
import { createBusinessDescriptionMapping, findFieldMapping, replaceFieldMapping } from './apiDesignSerialization'

const { Text } = Typography

type Props = {
  draft: WorkflowApiDesignDraft
  endpoint: WorkflowApiField
  disabled?: boolean
  onCancel: () => void
  onSave: (draft: WorkflowApiDesignDraft) => void
}

/** 编辑单个 Request/Response 字段的一句话业务说明，不执行表达式或字段级规则。 */
export default function ApiBusinessDescriptionEditor({ draft, endpoint, disabled, onCancel, onSave }: Props): ReactElement {
  const current = findFieldMapping(draft, endpoint)
  const [description, setDescription] = useState(
    current?.mappingType === 'business_description' ? current.businessDescription : ''
  )
  const [error, setError] = useState('')

  /** 校验并一次性写入当前 Endpoint 字段的业务说明映射。 */
  const save = (): void => {
    const value = description.trim()
    if (!value) {
      setError('请输入一句业务说明。')
      return
    }
    onSave(replaceFieldMapping(draft, createBusinessDescriptionMapping(endpoint, value)))
  }

  return <Form layout="vertical" className="api-design-business-description-editor">
    <Form.Item label="字段">
      <Text code>{endpoint.path}</Text>
      <Text type="secondary">（{endpoint.side === 'request' ? '请求体/请求参数' : '返回体'}）</Text>
    </Form.Item>
    <Form.Item label="业务说明" required validateStatus={error ? 'error' : undefined} help={error || '用一句话说明该字段在本接口中的业务用途。'}>
      <Input.TextArea
        autoSize={{ minRows: 4, maxRows: 8 }}
        disabled={disabled}
        maxLength={2000}
        onChange={(event) => { setDescription(event.target.value); setError('') }}
        placeholder="例如：用于控制分页查询的页码，不映射为实体或数据源字段。"
        showCount
        value={description}
      />
    </Form.Item>
    <Space>
      <Button disabled={disabled} onClick={onCancel}>取消</Button>
      <Button disabled={disabled} onClick={save} type="primary">保存说明</Button>
    </Space>
  </Form>
}
