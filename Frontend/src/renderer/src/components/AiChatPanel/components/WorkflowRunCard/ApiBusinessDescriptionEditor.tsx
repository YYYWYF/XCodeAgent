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

/** 编辑单个 Request/Response 字段的业务处理，不执行表达式或字段级规则。 */
export default function ApiBusinessDescriptionEditor({ draft, endpoint, disabled, onCancel, onSave }: Props): ReactElement {
  const current = findFieldMapping(draft, endpoint)
  const [description, setDescription] = useState(
    current?.mappingType === 'business_description' ? current.businessDescription : ''
  )
  const [error, setError] = useState('')

  /** 校验并一次性写入当前 Endpoint 字段的业务处理映射。 */
  const save = (): void => {
    const value = description.trim()
    if (!value) {
      setError('请输入业务处理内容。')
      return
    }
    onSave(replaceFieldMapping(draft, createBusinessDescriptionMapping(endpoint, value)))
  }

  return <Form layout="vertical" className="api-design-business-description-editor">
    <Form.Item label="字段">
      <Text code>{endpoint.path}</Text>
      <Text type="secondary">（{endpoint.side === 'request' ? '请求体/请求参数' : '返回体'}）</Text>
    </Form.Item>
    <Form.Item label="业务处理" required validateStatus={error ? 'error' : undefined} help={error || '填写完整业务处理方式，不要遗漏影响结果的条件。'}>
      <Input.TextArea
        autoSize={{ minRows: 4, maxRows: 8 }}
        disabled={disabled}
        maxLength={2000}
        onChange={(event) => { setDescription(event.target.value); setError('') }}
        placeholder="请填写该字段的业务处理方式。"
        showCount
        value={description}
      />
    </Form.Item>
    <Space>
      <Button disabled={disabled} onClick={onCancel}>取消</Button>
      <Button disabled={disabled} onClick={save} type="primary">保存处理</Button>
    </Space>
  </Form>
}
