import { Alert, Button, Form, Input, Modal, Tag } from 'antd'
import { CodeOutlined, InfoCircleOutlined } from '@ant-design/icons'
import { useMemo, useState, type ReactElement } from 'react'
import type { WorkflowApiDesignDraft } from '../../../../typings'
import { parsePreviewInput, previewInputKey, staticPreviewInputs } from './staticPreviewInputs'
import { previewStaticResponse } from './staticData'

/** 试填预览所需参数；数据仅保存在弹窗内，不写入接口配置。 */
export default function StaticPreviewParameters({ draft, onCancel }: {
  draft: WorkflowApiDesignDraft
  onCancel: () => void
}): ReactElement {
  const [values, setValues] = useState<Record<string, string>>({})
  const fields = useMemo(() => staticPreviewInputs(draft), [draft])
  // 每次输入都重新校验并计算；无效时移除旧结果，避免将过期结果当作当前预览。
  const preview = useMemo(() => {
    const next: Record<string, string> = {}
    const inputs: Record<string, unknown> = {}
    for (const field of fields) {
      const key = previewInputKey(field)
      try { inputs[key] = parsePreviewInput(field, values[key] || '') }
      catch (error) { next[key] = error instanceof Error ? error.message : '参数值无效。' }
    }
    if (Object.keys(next).length) return { errors: next, result: undefined, error: '' }
    try { return { errors: next, result: JSON.stringify(previewStaticResponse(draft, inputs), null, 2), error: '' } }
    catch (error) { return { errors: next, result: undefined, error: error instanceof Error ? error.message : '无法预览。' } }
  }, [draft, fields, values])
  return <Modal className="static-preview-modal" title={<span className="static-preview-title"><CodeOutlined />返回结果预览</span>} open width={720} onCancel={onCancel}
    footer={<div className="static-preview-footer"><span><InfoCircleOutlined /> 试填参数仅用于本次预览</span><Button onClick={onCancel}>关闭</Button></div>}>
    <div className="static-preview-layout">
    {fields.length > 0 && <section className="static-preview-inputs"><div className="static-preview-heading"><h4>接口参数</h4><span>{fields.length} 项 · 修改后实时更新</span></div><Form layout="horizontal" colon={false}>
      {fields.map((field) => {
        const key = previewInputKey(field)
        const error = Object.prototype.hasOwnProperty.call(values, key) ? preview.errors[key] : undefined
        const location = { path: 'Path', query: 'Query', header: 'Header', request_body: 'Body', response_body: 'Response' }[field.location]
        return <Form.Item key={key} label={<span className="static-preview-field"><code>{field.path}</code><Tag>{location}</Tag><span className="static-preview-type">{field.type}</span></span>} validateStatus={error ? 'error' : undefined} help={error}>
          <Input aria-label={`${field.location} ${field.path}`} value={values[key] || ''}
            placeholder={field.type === 'string' ? '请输入文本；空字符串请填写 ""' : `请输入 ${field.type} 类型的 JSON 值`}
            onChange={(event) => setValues({ ...values, [key]: event.target.value })} />
        </Form.Item>
      })}
    </Form></section>}
    <section className="static-preview-result" aria-live="polite"><div className="static-preview-heading"><h4>返回结果</h4><span>{preview.result !== undefined ? '已更新' : preview.error ? '无法预览' : '待填写'}</span></div>
      {preview.error ? <Alert type="info" showIcon message={preview.error} /> : preview.result !== undefined
        ? <div className="static-preview-editor"><div className="static-preview-editor-heading">JSON</div><pre className="static-response-preview">{preview.result.split('\n').map((line, index) => <span className="static-preview-line" key={index}><span className="static-preview-line-number" aria-hidden="true">{index + 1}</span><code>{line}</code></span>)}</pre></div>
        : <div className="static-preview-empty">请填写有效的接口参数，返回结果将自动展示。</div>}
    </section>
    </div>
  </Modal>
}
