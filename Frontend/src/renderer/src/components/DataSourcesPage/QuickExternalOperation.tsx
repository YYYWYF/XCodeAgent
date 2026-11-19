import { Alert, Button, Input, Select } from 'antd'
import { useEffect, useState, type FormEvent } from 'react'
import type { DataSourceOperation } from '../../typings'
import { cx } from '../../utils'
import { synchronizePathParameters } from './dataSourceOperations'

type Props = {
  saving: boolean
  onClose: () => void
  onDirtyChange: (dirty: boolean) => void
  onSave: (operation: DataSourceOperation) => Promise<void>
}

const requestMethods: DataSourceOperation['method'][] = ['GET', 'POST', 'PUT', 'DELETE']

/** 从用户填写的名称、请求方式和路径创建接口。 */
export default function QuickExternalOperation({ saving, onClose, onDirtyChange, onSave }: Props) {
  const [name, setName] = useState('')
  const [method, setMethod] = useState<DataSourceOperation['method']>()
  const [path, setPath] = useState('')
  const [error, setError] = useState('')

  /** 三项输入任一已填时，离开新建页会保护草稿。 */
  useEffect(() => {
    onDirtyChange(Boolean(name || method || path))
  }, [name, method, path, onDirtyChange])

  /** 校验必填项，并为路径占位符生成必要定义。 */
  const submit = async (event: FormEvent<HTMLFormElement>): Promise<void> => {
    event.preventDefault()
    if (saving) return
    try {
      setError('')
      const route = path.trim()
      if (!name.trim()) throw new Error('请输入接口名称。')
      if (!method) throw new Error('请选择请求方式。')
      if (!route || route.startsWith('//') || /[?#\\]/.test(route)) throw new Error('请输入相对接口路径，例如 /posts/{id}。')
      const pathParameters = synchronizePathParameters(route, []).map(({ rowId: _rowId, ...parameter }) => parameter)
      await onSave({
        id: 'operation-' + crypto.randomUUID(), name: name.trim(), path: route,
        method, description: '', pathParameters, queryParameters: [], headers: [],
        requestStructure: null, responseStructure: null
      })
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : '新增接口失败。')
    }
  }
  return <form className="source-operation-editor" onSubmit={submit}>
    {error && <Alert showIcon type="error" message={error} />}
    <div className={cx('data-source-editor-form')}>
      <label><span>接口名称</span><Input autoFocus disabled={saving} required maxLength={128}
        value={name} placeholder="例如：查询文章" onChange={(event) => setName(event.target.value)} /></label>
      <label><span>请求方式</span>
        <Select aria-label="请求方式" aria-required="true" placeholder="请选择请求方式" popupClassName="source-operation-method-menu"
          disabled={saving} value={method} onChange={setMethod} options={requestMethods.map((value) => ({ value }))} />
      </label>
      <label><span>路径</span><Input disabled={saving} required maxLength={2048}
        value={path} placeholder="/posts/{id}" onChange={(event) => setPath(event.target.value)} /></label>
    </div>
    <div className={cx('data-source-modal-footer')}>
      <Button disabled={saving} onClick={onClose}>取消</Button>
      <Button htmlType="submit" loading={saving} type="primary">保存</Button>
    </div>
  </form>
}
