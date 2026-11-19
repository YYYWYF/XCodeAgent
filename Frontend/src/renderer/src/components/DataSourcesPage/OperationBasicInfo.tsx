import { BookOutlined, CopyOutlined, EditOutlined } from '@ant-design/icons'
import { Alert, Button, Input, Select, Tag, Tooltip, Typography, message } from 'antd'
import type { FormEvent } from 'react'
import { cx } from '../../utils'
import type { OperationAddress, OperationBasic } from './operationEditorDraft'

type Props = {
  saved: OperationBasic
  draft?: OperationBasic
  saving: boolean
  error: string
  pendingAddress?: OperationAddress
  onEdit: () => void
  onChange: (draft: OperationBasic) => void
  onCancel: () => void
  onSave: () => void
}
const methods: OperationBasic['method'][] = ['GET', 'POST', 'PUT', 'DELETE']

/** 只展示接口基本信息，在同一卡片内完成编辑和保存。 */
export default function OperationBasicInfo({
  saved, draft, saving, error, pendingAddress, onEdit, onChange, onCancel, onSave
}: Props) {
  /** 复制已保存的接口路径。 */
  async function copyPath(): Promise<void> {
    try {
      await navigator.clipboard.writeText(saved.path)
      message.success('请求路径已复制')
    } catch { message.error('复制失败，请手动复制接口路径') }
  }

  /** 统一通过表单提交基本信息，验证和保存范围由工作区负责。 */
  function submit(event: FormEvent<HTMLFormElement>): void {
    event.preventDefault()
    onSave()
  }

  return <section className={cx('data-source-operation-detail-card', 'tone-basic', 'source-operation-basic-card')}>
    <div className={cx('data-source-operation-detail-card-header')}>
      <span className={cx('data-source-operation-detail-card-heading')}>
        <span className={cx('data-source-operation-detail-card-icon', 'tone-basic')}><BookOutlined /></span>
        <strong>基本信息</strong>
      </span>
      {!draft && <Button className="source-operation-basic-edit" size="small" icon={<EditOutlined />} disabled={saving} onClick={onEdit}>编辑</Button>}
    </div>
    <div className={cx('data-source-operation-detail-card-body')}>
      {draft ? <form className="source-operation-basic-form" onSubmit={submit}>
        {pendingAddress && <Alert showIcon type="info" message="保存本次调试的请求方式和路径后，将继续同步接口配置。"
          description={<code>{pendingAddress.method} {pendingAddress.path}</code>} />}
        {error && <Alert showIcon type="error" message={error} />}
        <div className={cx('data-source-form-grid', 'data-source-operation-basic')}>
          <label><span>接口名称</span><Input autoFocus aria-label="接口名称" value={draft.name} maxLength={256} disabled={saving}
            onChange={(event) => onChange({ ...draft, name: event.target.value })} /></label>
          <label><span>请求方式</span><Select aria-label="请求方式" value={draft.method} disabled={saving}
            popupClassName="source-operation-method-menu" options={methods.map((value) => ({ value }))}
            onChange={(method) => onChange({ ...draft, method })} /></label>
          <label className={cx('data-source-form-grid-wide')}><span>接口路径</span>
            <Input aria-label="接口路径" value={draft.path} maxLength={2048} disabled={saving} placeholder="/posts/{id}"
              onChange={(event) => onChange({ ...draft, path: event.target.value })} /></label>
          <label className={cx('data-source-form-grid-wide')}><span>接口描述</span>
            <Input.TextArea aria-label="接口描述" value={draft.description} maxLength={500} autoSize={{ minRows: 2, maxRows: 4 }} disabled={saving}
              onChange={(event) => onChange({ ...draft, description: event.target.value })} /></label>
        </div>
        <div className="source-operation-basic-actions">
          <Button disabled={saving} onClick={onCancel}>取消编辑</Button>
          <Button htmlType="submit" type="primary" loading={saving}>保存基本信息</Button>
        </div>
      </form> : <div className={cx('data-source-operation-detail-basic-info')}>
        <div><span>接口名称</span><strong>{saved.name}</strong></div>
        <div><span>请求方式</span><Tag className={cx('data-source-operation-detail-method')}>{saved.method}</Tag></div>
        <div><span>接口路径</span><span className={cx('data-source-operation-detail-path')}><code>{saved.path}</code>
          <Tooltip title="复制接口路径"><Button type="text" size="small" aria-label="复制接口路径" icon={<CopyOutlined />} onClick={() => void copyPath()} /></Tooltip>
        </span></div>
        <div><span>接口描述</span><Typography.Text type={saved.description ? undefined : 'secondary'}>{saved.description || '—'}</Typography.Text></div>
      </div>}
    </div>
  </section>
}
