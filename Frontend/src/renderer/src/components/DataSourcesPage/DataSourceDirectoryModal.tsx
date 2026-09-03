import { Alert, Button, Input, Modal } from 'antd'
import type { ReactElement } from 'react'
import { useEffect, useState } from 'react'
import type { DataSourceDirectory } from '../../typings'
import { cx } from '../../utils'

type Props = {
  editing?: DataSourceDirectory
  onClose: () => void
  onSave: (directory: DataSourceDirectory) => Promise<void>
  open: boolean
  saving: boolean
  theme: 'light' | 'dark'
}

/** 管理外部 API 普通目录名称的居中弹窗。 */
export default function DataSourceDirectoryModal({ editing, onClose, onSave, open, saving, theme }: Props): ReactElement {
  const [name, setName] = useState('')
  const [error, setError] = useState('')

  useEffect(() => {
    setName(editing?.name || '')
    setError('')
  }, [editing, open])

  /** 保存当前目录名称，并把校验错误保留在弹窗内。 */
  const handleSave = async (): Promise<void> => {
    if (!name.trim()) {
      setError('请输入目录名称。')
      return
    }
    try {
      setError('')
      await onSave({ id: editing?.id || '', name: name.trim(), operations: editing?.operations || [] })
    } catch (caughtError) {
      setError(caughtError instanceof Error ? caughtError.message : '目录保存失败。')
    }
  }

  return (
    <Modal
      centered
      className={cx('data-source-editor-modal')}
      destroyOnClose
      footer={<div className={cx('data-source-modal-footer')}><Button disabled={saving} onClick={onClose}>取消</Button><Button loading={saving} onClick={() => void handleSave()} type="primary">保存</Button></div>}
      keyboard={!saving}
      maskClosable={!saving}
      onCancel={onClose}
      title={editing ? '编辑目录' : '新增目录'}
      visible={open}
      width={520}
      wrapClassName={cx('data-source-editor-modal-wrap', `theme-${theme}`)}
    >
      {error ? <Alert className={cx('data-source-editor-error')} message={error} showIcon type="error" /> : null}
      <div className={cx('data-source-editor-form')}>
        <label><span>目录名称</span><Input autoFocus onChange={(event) => setName(event.target.value)} placeholder="例如：商品接口" value={name} /></label>
      </div>
    </Modal>
  )
}
