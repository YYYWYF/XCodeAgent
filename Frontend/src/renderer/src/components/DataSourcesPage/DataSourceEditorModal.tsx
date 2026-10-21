import { confirmWorkspaceAction } from '../workspaceDialogs'
import { Alert, Button, Divider, Input, InputNumber, Modal, Radio, Typography } from 'antd'
import type { ReactElement } from 'react'
import { useEffect, useState } from 'react'
import type { DatabaseDataSource, DatabaseDataSourceInput, DatabaseSourceMode, ExternalApiDataSource, ExternalApiDataSourceInput } from '../../typings'
import { encryptPlantModePassword } from '../../service/databaseCredentialCrypto'
import { cx } from '../../utils'
import { HeaderEditor } from './ExternalApiFormParts'
import './DataSourcesPage.less'

const { Text } = Typography

type Props = {
  createType?: 'database' | 'external_api'
  editing?: DatabaseDataSource | ExternalApiDataSource
  onClose: () => void
  onDelete?: () => void
  onSave: (source: DatabaseDataSourceInput | ExternalApiDataSourceInput) => Promise<void>
  onValidate: (source: DatabaseDataSourceInput | ExternalApiDataSourceInput) => Promise<void>
  open: boolean
  saving: boolean
  theme: 'light' | 'dark'
}

type DatabaseDraft = { name: string; mode: DatabaseSourceMode; domain: string; port: string; schema: string; userName: string; dbid: string; password: string }
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

  /** 有输入变化时保护关闭，避免误丢连接配置或新密码。 */
  const cancel = (): void => {
    if (saving) return
    const dirty = kind === 'database'
      ? JSON.stringify(database) !== JSON.stringify(databaseDraftFromSource(editing?.type === 'database' ? editing : undefined))
      : JSON.stringify(api) !== JSON.stringify(apiDraftFromSource(editing?.type === 'external_api' ? editing : undefined))
    if (!dirty) { onClose(); return }
    confirmWorkspaceAction({ title: '放弃未保存修改？', okText: '放弃修改', cancelText: '继续编辑', onOk: onClose })
  }

  useEffect(() => {
    setKind(editing?.type || createType || 'database')
    setDatabase(databaseDraftFromSource(editing?.type === 'database' ? editing : undefined))
    setApi(apiDraftFromSource(editing?.type === 'external_api' ? editing : undefined))
    setError('')
  }, [createType, editing, open])

  /** 将数据库表单转换为后端输入，并对新密码使用平台加密。 */
  const buildDatabaseSource = async (): Promise<DatabaseDataSourceInput> => {
    const source: DatabaseDataSourceInput = { type: 'database', id: editing?.type === 'database' ? editing.id : undefined, name: database.name.trim(), mode: database.mode, domain: database.domain.trim() || undefined, port: database.port ? Number(database.port) : undefined, schema: database.schema.trim() || undefined, userName: database.userName.trim() || undefined, dbid: database.dbid.trim() || undefined }
    if (database.password.trim()) source.passwordCiphertext = await encryptPlantModePassword(database.password.trim())
    return source
  }

  /** 将域名共享配置转换为后端输入，并保留已编辑域名下的目录。 */
  const buildApiSource = (): ExternalApiDataSourceInput => ({ type: 'external_api', id: editing?.type === 'external_api' ? editing.id : undefined, name: api.name.trim(), baseUrl: api.baseUrl.trim(), baseUrlConfigKey: api.baseUrlConfigKey.trim() || undefined, timeoutMs: api.timeoutMs, headers: api.headers, directories: editing?.type === 'external_api' ? editing.directories : [] })

  /** 校验当前表单必填字段并构造数据源输入。 */
  const buildSource = async (): Promise<DatabaseDataSourceInput | ExternalApiDataSourceInput> => {
    if (kind === 'database') {
      if (!database.name.trim()) throw new Error('请输入数据源名称。')
      if (database.mode === 'direct' && !editing && !database.password.trim()) throw new Error('直连数据库必须填写密码。')
      if (database.mode === 'direct' && editing?.type === 'database' && !editing.hasPassword && !database.password.trim()) throw new Error('直连数据库必须填写密码。')
      return buildDatabaseSource()
    }
    if (!api.name.trim()) throw new Error('请输入域名配置名称。')
    if (!api.baseUrl.trim()) throw new Error('请输入 Base URL 或域名。')
    return buildApiSource()
  }

  /** 调用父级校验动作并在弹窗内显示错误。 */
  const handleValidate = async (): Promise<void> => {
    try { setError(''); await onValidate(await buildSource()) } catch (caughtError) { setError(caughtError instanceof Error ? caughtError.message : '数据源校验失败。') }
  }

  /** 保存域名或数据库配置并保留可恢复的表单错误。 */
  const handleSave = async (): Promise<void> => {
    try {
      setError('')
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
        try { await onSave(source) } catch (caughtError) { setError(caughtError instanceof Error ? caughtError.message : '数据源保存失败。') }
      }
      if (databaseChanged && editing?.type === 'database' && editing.managedTables.length) {
        confirmWorkspaceAction({
          title: '数据库定位已变化',
          content: `当前连接已添加 ${editing.managedTables.length} 张表。保存后将清空已添加表清单，需要重新添加。`,
          okText: '保存并清空清单',
          cancelText: '继续编辑',
          onOk: () => { void persist() },
        })
        return
      }
      await persist()
    } catch (caughtError) { setError(caughtError instanceof Error ? caughtError.message : '数据源保存失败。') }
  }

  return (
    <Modal
      bodyStyle={{ maxHeight: 'calc(100vh - 220px)', overflowY: 'auto', padding: '0 24px 24px' }} centered className={cx('data-source-editor-modal')} destroyOnClose
      footer={<div className={cx('data-source-modal-footer')}>{onDelete && editing ? <Button className={cx('data-source-modal-delete')} danger disabled={saving} onClick={onDelete}>{editing.type === 'external_api' ? '删除域' : '删除连接'}</Button> : null}<Button disabled={saving} onClick={cancel}>取消</Button><Button loading={saving} onClick={() => void handleValidate()}>{kind === 'database' && database.mode === 'direct' ? '检测连接' : '校验配置'}</Button><Button loading={saving} onClick={() => void handleSave()} type="primary">保存</Button></div>}
      keyboard={!saving} maskClosable={!saving} onCancel={cancel} wrapClassName={cx('data-source-editor-modal-wrap', `theme-${theme}`)} title={kind === 'external_api' ? (editing ? '域设置' : '新增接口域') : (editing ? '连接设置' : '新增数据库连接')} visible={open} width={kind === 'external_api' ? 560 : 880}
    >
      {error ? <Alert className={cx('data-source-editor-error')} message={error} showIcon type="error" /> : null}
      {!editing && !createType ? <Radio.Group buttonStyle="solid" onChange={(event) => setKind(event.target.value)} options={[{ label: '数据库', value: 'database' }, { label: '外部 API 域名', value: 'external_api' }]} value={kind} /> : null}
      {kind === 'database' ? (
        <div className={cx('data-source-editor-form')}>
          <label><span><em className={cx('data-source-required')}>*</em>名称</span><Input onChange={(event) => setDatabase({ ...database, name: event.target.value })} placeholder="例如：业务数据库" value={database.name} /></label>
          <Divider orientation="left">连接模式</Divider>
          <Radio.Group disabled={saving} onChange={(event) => setDatabase({ ...database, mode: event.target.value })} options={[{ label: '平台内置', value: 'builtin' }, { label: 'DBID', value: 'dbid' }, { label: '数据库直连', value: 'direct' }]} value={database.mode} />
          {database.mode !== 'builtin' ? <div className={cx('data-source-form-grid')}>
            <label><span>{database.mode === 'direct' ? <em className={cx('data-source-required')}>*</em> : null}数据库地址</span><Input onChange={(event) => setDatabase({ ...database, domain: event.target.value })} value={database.domain} /></label>
            <label><span>{database.mode === 'direct' ? <em className={cx('data-source-required')}>*</em> : null}端口</span><InputNumber className={cx('data-source-full-control')} min={1} max={65535} onChange={(value) => setDatabase({ ...database, port: value ? String(value) : '' })} value={database.port ? Number(database.port) : undefined} /></label>
            <label><span>{database.mode === 'direct' ? <em className={cx('data-source-required')}>*</em> : null}Schema</span><Input onChange={(event) => setDatabase({ ...database, schema: event.target.value })} value={database.schema} /></label>
            <label><span>{database.mode === 'direct' ? <em className={cx('data-source-required')}>*</em> : null}用户名</span><Input onChange={(event) => setDatabase({ ...database, userName: event.target.value })} value={database.userName} /></label>
            {database.mode === 'dbid' ? <label><span><em className={cx('data-source-required')}>*</em>DBID</span><Input onChange={(event) => setDatabase({ ...database, dbid: event.target.value })} value={database.dbid} /></label> : null}
            {database.mode === 'direct' ? <label><span>{editing?.type === 'database' && editing.hasPassword ? null : <em className={cx('data-source-required')}>*</em>}密码{editing?.type === 'database' && editing.hasPassword ? '（留空保持不变）' : ''}</span><Input.Password autoComplete="new-password" onChange={(event) => setDatabase({ ...database, password: event.target.value })} value={database.password} /></label> : null}
          </div> : <Text type="secondary">平台内置数据库不需要填写外部连接信息。</Text>}
          {database.mode === 'dbid' ? <Text type="secondary">DBID 当前只保存配置并执行静态校验，暂不发起连接检测。</Text> : null}
        </div>
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
