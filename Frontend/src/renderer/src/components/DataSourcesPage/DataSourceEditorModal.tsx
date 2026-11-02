import { confirmWorkspaceAction } from '../workspaceDialogs'
import { ApiOutlined, DatabaseOutlined, DeleteOutlined, LoadingOutlined } from '@ant-design/icons'
import { Alert, Button, Input, InputNumber, Modal, Radio, message } from 'antd'
import type { ReactElement } from 'react'
import { useEffect, useRef, useState } from 'react'
import type { DatabaseDataSource, DatabaseDataSourceInput, DataSourceValidation, ExternalApiDataSource, ExternalApiDataSourceInput } from '../../typings'
import { encryptPlantModePassword } from '../../service/databaseCredentialCrypto'
import { cx } from '../../utils'
import { HeaderEditor } from './ExternalApiFormParts'
import DatabaseConnectionForm from './DatabaseConnectionForm'
import type { DatabaseConnectionDraft, DatabaseConnectionErrors } from './DatabaseConnectionForm'
import './DataSourcesPage.less'
import './DatabaseConnectionForm.less'

type Props = {
  createType?: 'database' | 'external_api'
  editing?: DatabaseDataSource | ExternalApiDataSource
  onClose: () => void
  onDelete?: () => void
  onSave: (source: DatabaseDataSourceInput | ExternalApiDataSourceInput) => Promise<void>
  onValidate: (source: DatabaseDataSourceInput | ExternalApiDataSourceInput) => Promise<DataSourceValidation>
  open: boolean
  saving: boolean
  theme: 'light' | 'dark'
}

type DatabaseDraft = DatabaseConnectionDraft
type ApiDraft = { name: string; baseUrl: string; baseUrlConfigKey: string; timeoutMs: number; headers: ExternalApiDataSource['headers'] }

/** 创建数据库编辑表单的默认值。 */
const emptyDatabaseDraft = (): DatabaseDraft => ({ name: '', mode: 'direct', domain: '', port: '3306', schema: '', userName: '', dbid: '', password: '' })

/** 创建外部 API 域名编辑表单的默认值。 */
const emptyApiDraft = (): ApiDraft => ({ name: '', baseUrl: '', baseUrlConfigKey: '', timeoutMs: 10000, headers: [] })

/** 将数据库公开资源转换为编辑草稿，不把密码密文带回输入框。 */
function databaseDraftFromSource(source?: DatabaseDataSource): DatabaseDraft {
  if (!source) return emptyDatabaseDraft()
  return { name: source.name, mode: source.mode, domain: source.domain || '', port: source.port ? String(source.port) : '', schema: source.schema || '', userName: source.userName || '', dbid: source.dbid || '', password: '' }
}

/** 将外部 API 域名公开资源转换为编辑草稿。 */
function apiDraftFromSource(source?: ExternalApiDataSource): ApiDraft {
  if (!source) return emptyApiDraft()
  return { name: source.name, baseUrl: source.baseUrl, baseUrlConfigKey: source.baseUrlConfigKey || '', timeoutMs: source.timeoutMs, headers: source.headers.map((header) => ({ ...header })) }
}

/** 渲染数据库和外部 API 域名共享配置的居中弹窗。 */
export default function DataSourceEditorModal({ createType, editing, onClose, onDelete, onSave, onValidate, open, saving, theme }: Props): ReactElement {
  const [kind, setKind] = useState<'database' | 'external_api'>(editing?.type || createType || 'database')
  const [database, setDatabase] = useState<DatabaseDraft>(emptyDatabaseDraft)
  const [api, setApi] = useState<ApiDraft>(emptyApiDraft)
  const [error, setError] = useState('')
  const [fieldErrors, setFieldErrors] = useState<DatabaseConnectionErrors>({})
  const [validation, setValidation] = useState<DataSourceValidation>()
  const [activeAction, setActiveAction] = useState<'validate' | 'save'>()
  const requestVersion = useRef(0)
  const busy = saving || Boolean(activeAction)

  /** 有输入变化时保护关闭，避免误丢连接配置或新密码。 */
  const cancel = (): void => {
    if (busy) return
    const dirty = kind === 'database'
      ? JSON.stringify(database) !== JSON.stringify(databaseDraftFromSource(editing?.type === 'database' ? editing : undefined))
      : JSON.stringify(api) !== JSON.stringify(apiDraftFromSource(editing?.type === 'external_api' ? editing : undefined))
    if (!dirty) { onClose(); return }
    confirmWorkspaceAction({ title: '放弃未保存修改？', okText: '放弃修改', cancelText: '继续编辑', onOk: onClose })
  }

  useEffect(() => {
    requestVersion.current += 1
    setKind(editing?.type || createType || 'database')
    setDatabase(databaseDraftFromSource(editing?.type === 'database' ? editing : undefined))
    setApi(apiDraftFromSource(editing?.type === 'external_api' ? editing : undefined))
    setError('')
    setFieldErrors({})
    setValidation(undefined)
    setActiveAction(undefined)
    // 弹窗关闭、切换来源或卸载后丢弃旧检测回执，不污染下一份配置。
    return () => { requestVersion.current += 1 }
  }, [createType, editing, open])

  /** 修改任意连接字段后撤销旧检测结果，模式切换仅保留本地草稿。 */
  const updateDatabase = (patch: Partial<DatabaseDraft>): void => {
    setDatabase((current) => ({ ...current, ...patch }))
    setError(''); setValidation(undefined); setFieldErrors({})
  }

  /** 按连接模式构造输入，DBID 仅提交标识，直连的新密码使用平台加密。 */
  const buildDatabaseSource = async (): Promise<DatabaseDataSourceInput> => {
    const source: DatabaseDataSourceInput = { type: 'database', id: editing?.type === 'database' ? editing.id : undefined, name: database.name.trim(), mode: database.mode }
    if (database.mode === 'builtin') return source
    Object.assign(source, { domain: database.domain.trim() || undefined, port: database.port ? Number(database.port) : undefined, schema: database.schema.trim() || undefined, userName: database.userName.trim() || undefined })
    // 模式切换保留表单草稿，但提交时不携带另一种模式的密码或 DBID。
    if (database.mode === 'dbid') source.dbid = database.dbid.trim() || undefined
    else if (database.password.trim()) source.passwordCiphertext = await encryptPlantModePassword(database.password.trim())
    return source
  }

  /** 将域名共享配置转换为后端输入，并保留已编辑域名下的目录。 */
  const buildApiSource = (): ExternalApiDataSourceInput => ({ type: 'external_api', id: editing?.type === 'external_api' ? editing.id : undefined, name: api.name.trim(), baseUrl: api.baseUrl.trim(), baseUrlConfigKey: api.baseUrlConfigKey.trim() || undefined, timeoutMs: api.timeoutMs, headers: api.headers, directories: editing?.type === 'external_api' ? editing.directories : [] })

  /** 校验当前表单必填字段并构造数据源输入。 */
  const buildSource = async (): Promise<DatabaseDataSourceInput | ExternalApiDataSourceInput> => {
    if (kind === 'database') {
      const errors: DatabaseConnectionErrors = {}
      if (!database.name.trim()) errors.name = '请输入连接名称。'
      if (database.mode !== 'builtin') {
        if (!database.domain.trim()) errors.domain = '请输入数据库地址。'
        if (!Number.isInteger(Number(database.port)) || Number(database.port) < 1 || Number(database.port) > 65535) errors.port = '端口范围为 1–65535。'
        if (!database.schema.trim()) errors.schema = '请输入 Schema。'
        if (!database.userName.trim()) errors.userName = '请输入用户名。'
        if (database.mode === 'dbid' && !database.dbid.trim()) errors.dbid = '请输入 DBID。'
        if (database.mode === 'direct' && !(editing?.type === 'database' && editing.hasPassword) && !database.password.trim()) errors.password = '请输入数据库密码。'
      }
      setFieldErrors(errors)
      if (Object.keys(errors).length) throw new Error('请完善连接信息后再操作。')
      return buildDatabaseSource()
    }
    if (!api.name.trim()) throw new Error('请输入域名配置名称。')
    if (!api.baseUrl.trim()) throw new Error('请输入 Base URL 或域名。')
    return buildApiSource()
  }

  /** 使用现有 AG-UI 校验回执展示真实检测状态，不把静态校验当作连接成功。 */
  const handleValidate = async (): Promise<void> => {
    if (busy) return
    const version = requestVersion.current
    setActiveAction('validate'); setError(''); setValidation(undefined)
    try {
      const source = await buildSource()
      if (version !== requestVersion.current) return
      const result = await onValidate(source)
      if (version !== requestVersion.current) return
      if (!result.valid) throw new Error('数据源配置校验未通过。')
      if (kind === 'database') setValidation(result)
      else message.success('配置静态校验通过')
    } catch (caughtError) {
      if (version === requestVersion.current) setError(caughtError instanceof Error ? caughtError.message : '数据源校验失败。')
    } finally {
      if (version === requestVersion.current) setActiveAction(undefined)
    }
  }

  /** 保存域名或数据库配置并保留可恢复的表单错误。 */
  const handleSave = async (): Promise<void> => {
    if (busy) return
    const version = requestVersion.current
    setActiveAction('save')
    try {
      setError('')
      setValidation(undefined)
      const source = await buildSource()
      const databaseChanged = editing?.type === 'database' && source.type === 'database' && (
        editing.mode !== source.mode ||
        (editing.domain || '') !== (source.domain || '') ||
        (editing.port || '') !== (source.port || '') ||
        (editing.schema || '') !== (source.schema || '') ||
        (editing.dbid || '') !== (source.dbid || '')
      )
      // 将确认后的数据源提交交给父级，统一保留可恢复的保存错误。
      const persist = async (): Promise<void> => {
        if (version !== requestVersion.current) return
        setActiveAction('save')
        try { await onSave(source) }
        catch (caughtError) { if (version === requestVersion.current) setError(caughtError instanceof Error ? caughtError.message : '数据源保存失败。') }
        finally { if (version === requestVersion.current) setActiveAction(undefined) }
      }
      if (databaseChanged && editing?.type === 'database' && editing.managedTables.length) {
        confirmWorkspaceAction({
          title: '数据库定位已变化',
          content: `当前连接已添加 ${editing.managedTables.length} 张表。保存后将清空已添加表清单，需要重新添加。`,
          okText: '保存并清空清单',
          cancelText: '继续编辑',
          onOk: persist,
        })
        return
      }
      await persist()
    } catch (caughtError) { if (version === requestVersion.current) setError(caughtError instanceof Error ? caughtError.message : '数据源保存失败。') }
    finally { if (version === requestVersion.current) setActiveAction(undefined) }
  }

  return (
    <Modal
      bodyStyle={{ maxHeight: kind === 'database' ? 'calc(100vh - 240px)' : 'calc(100vh - 220px)', overflowY: 'auto', ...(kind === 'external_api' ? { padding: '0 24px 24px' } : {}) }} centered className={cx('data-source-editor-modal', kind === 'database' && 'database-connection-modal')} destroyOnClose
      footer={<div className={cx('data-source-modal-footer')}>
        {onDelete && editing ? <Button className={cx('data-source-modal-delete')} danger disabled={busy} icon={kind === 'database' ? <DeleteOutlined /> : undefined} onClick={onDelete} type={kind === 'database' ? 'text' : 'default'}>{editing.type === 'external_api' ? '删除域' : '删除连接'}</Button> : null}
        <div className={cx(kind === 'database' && 'database-connection-actions')}>
          <Button disabled={busy} onClick={cancel}>取消</Button>
          <Button disabled={busy && activeAction !== 'validate'} icon={kind === 'database' && database.mode !== 'builtin' ? <ApiOutlined /> : undefined} loading={activeAction === 'validate'} onClick={() => void handleValidate()}>{activeAction === 'validate' ? '检测中…' : kind === 'database' && database.mode !== 'builtin' ? '检测连接' : '校验配置'}</Button>
          <Button disabled={busy && activeAction !== 'save'} loading={activeAction === 'save'} onClick={() => void handleSave()} type="primary">保存</Button>
        </div>
      </div>}
      keyboard={!busy} maskClosable={!busy} onCancel={cancel} wrapClassName={cx('data-source-editor-modal-wrap', `theme-${theme}`)}
      title={kind === 'external_api' ? (editing ? '域设置' : '新增接口域') : <div className={cx('database-connection-title')}><DatabaseOutlined /><div><strong>{editing ? '连接设置' : '新增数据库连接'}</strong><small>配置业务数据库的连接信息</small></div></div>}
      visible={open} width={kind === 'external_api' ? 560 : 650}
    >
      {error && kind !== 'database' ? <Alert className={cx('data-source-editor-error')} message={error} showIcon type="error" /> : null}
      {!editing && !createType ? <Radio.Group buttonStyle="solid" disabled={busy} onChange={(event) => { setKind(event.target.value); setError(''); setValidation(undefined) }} options={[{ label: '数据库', value: 'database' }, { label: '外部 API 域名', value: 'external_api' }]} value={kind} /> : null}
      {kind === 'database' ? (
        <>
          <DatabaseConnectionForm value={database} errors={fieldErrors} disabled={busy} hasPassword={editing?.type === 'database' && editing.hasPassword} onChange={updateDatabase} />
          <div aria-live="polite">
            {activeAction === 'validate' ? <Alert showIcon icon={<LoadingOutlined />} message="正在检测连接…" type="info" />
              : error ? <Alert showIcon message={error} type="error" />
                : validation ? <Alert showIcon message={validation.connection === 'ok' ? '连接成功，数据库可正常访问。' : '配置静态校验通过，尚未检测数据库连接。'} type={validation.connection === 'ok' ? 'success' : 'info'} /> : null}
          </div>
        </>
      ) : (
        <div className={cx('data-source-editor-form')}>
          <label><span><em className={cx('data-source-required')}>*</em>域名称</span><Input onChange={(event) => setApi({ ...api, name: event.target.value })} placeholder="例如：商品中心" value={api.name} /></label>
          <label><span><em className={cx('data-source-required')}>*</em>域名地址</span><Input onChange={(event) => setApi({ ...api, baseUrl: event.target.value })} placeholder="https://api.example.com" value={api.baseUrl} /></label>
          <div className={cx('data-source-form-grid')}>
            <label><span>配置键（可选）</span><Input onChange={(event) => setApi({ ...api, baseUrlConfigKey: event.target.value })} placeholder="services.product.base-url" value={api.baseUrlConfigKey} /></label>
            <label><span>超时（毫秒）</span><InputNumber className={cx('data-source-full-control')} min={100} max={120000} onChange={(value) => setApi({ ...api, timeoutMs: Number(value || 10000) })} value={api.timeoutMs} /></label>
          </div>
          <HeaderEditor headers={api.headers} onChange={(headers) => setApi({ ...api, headers })} title="共享 Header" />
        </div>
      )}
    </Modal>
  )
}
