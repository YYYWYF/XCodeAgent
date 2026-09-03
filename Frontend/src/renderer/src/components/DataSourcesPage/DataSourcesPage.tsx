import {
  ApiOutlined,
  DatabaseOutlined,
  DownOutlined,
  FolderOpenOutlined,
  FolderOutlined,
  MoreOutlined,
  PlusOutlined,
  ReloadOutlined,
  RightOutlined
} from '@ant-design/icons'
import { Alert, Button, Dropdown, Menu, Modal, Spin, Tag, Typography, message } from 'antd'
import { Children, type KeyboardEvent, type ReactElement, type ReactNode } from 'react'
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { isAuthenticationFailure } from '../../service/authentication'
import { createDataSource, deleteDataSource, requestDataSourceDetails, requestDataSourceOperation, requestDataSources, updateDataSource, validateDataSource } from '../../service/dataSources'
import type { DataSourceCatalog, DataSourceDirectory, DataSourceOperation, DatabaseDataSource, DatabaseDataSourceInput, ExternalApiDataSource, ExternalApiDataSourceInput } from '../../typings'
import { cx } from '../../utils'
import DataSourceDirectoryModal from './DataSourceDirectoryModal'
import DataSourceEditorModal from './DataSourceEditorModal'
import DataSourceOperationModal from './DataSourceOperationModal'
import ExternalApiManagerModal from './ExternalApiManagerModal'
import { mergeExternalSourceChanges, requireOperationDetails } from './dataSourceOperations'
import { useOperationDetails } from './useOperationDetails'
import './DataSourcesPage.less'

const { Text, Title } = Typography
type Source = DatabaseDataSource | ExternalApiDataSource
type CreateType = 'database' | 'external_api'
type MoreAction = { key: string; label: string; danger?: boolean; onClick: () => void }

/** 渲染收敛后的更多操作菜单，避免常驻按钮堆叠。 */
function MoreActions({ actions }: { actions: MoreAction[] }): ReactElement {
  return (
    <Dropdown
      overlay={<Menu onClick={({ key }) => actions.find((action) => action.key === key)?.onClick()}>{actions.map((action) => <Menu.Item danger={action.danger} key={action.key}>{action.label}</Menu.Item>)}</Menu>}
      placement="bottomRight"
      trigger={['click']}
    >
      <Button aria-label="更多操作" icon={<MoreOutlined />} onClick={(event) => event.stopPropagation()} size="small" type="text" />
    </Dropdown>
  )
}

/** 返回数据库模式的人类可读名称。 */
function databaseModeLabel(mode: DatabaseDataSource['mode']): string { return { builtin: '平台内置', dbid: 'DBID', direct: '数据库直连' }[mode] }

/** 返回外部 API 域名下的接口总数。 */
function operationCount(source: ExternalApiDataSource): number { return source.directories.reduce((total, directory) => total + directory.operations.length, 0) }

/** 创建一个用于新增目录的普通稳定 ID。 */
function newDirectoryId(): string { return `directory-${Date.now()}-${Math.random().toString(16).slice(2, 8)}` }

/** 创建一个用于新增接口的普通稳定 ID。 */
function newOperationId(): string { return `operation-${Date.now()}-${Math.random().toString(16).slice(2, 8)}` }

/** 渲染数据库资源行。 */
function DatabaseRow({ source, onDelete, onEdit, onValidate }: { source: DatabaseDataSource; onDelete: (source: Source) => void; onEdit: (source: Source) => void; onValidate: (source: Source) => void }): ReactElement {
  /** 支持键盘打开数据库编辑弹窗。 */
  const onKeyDown = (event: KeyboardEvent<HTMLDivElement>): void => { if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); onEdit(source) } }
  return (
    <div className={cx('data-source-directory-row')} onClick={() => onEdit(source)} onKeyDown={onKeyDown} role="button" tabIndex={0}>
      <span className={cx('data-source-directory-row-icon')}><DatabaseOutlined /></span>
      <span className={cx('data-source-directory-row-main')}><span className={cx('data-source-directory-row-name')}>{source.name}</span></span>
      <span className={cx('data-source-directory-row-tags')}><Tag>{databaseModeLabel(source.mode)}</Tag>{source.mode === 'direct' ? <Tag color={source.hasPassword ? 'green' : 'red'}>{source.hasPassword ? '密码已配置' : '缺少密码'}</Tag> : null}</span>
      <span className={cx('data-source-directory-row-actions')} onClick={(event) => event.stopPropagation()}><MoreActions actions={[...(source.mode === 'direct' ? [{ key: 'validate', label: '检测连接', onClick: () => onValidate(source) }] : []), { key: 'edit', label: '编辑', onClick: () => onEdit(source) }, { key: 'delete', label: '删除', danger: true, onClick: () => onDelete(source) }]} /></span>
    </div>
  )
}

/** 渲染外部 API 域名资源行，并把目录管理入口收敛到独立弹窗。 */
function ApiDomainRow({ source, onDelete, onEdit, onManage, onValidate }: { source: ExternalApiDataSource; onDelete: (source: Source) => void; onEdit: (source: Source) => void; onManage: (source: ExternalApiDataSource) => void; onValidate: (source: Source) => void }): ReactElement {
  /** 支持键盘打开当前域名的目录管理弹窗。 */
  const onKeyDown = (event: KeyboardEvent<HTMLDivElement>): void => { if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); onManage(source) } }
  return (
    <div className={cx('data-source-directory-row', 'data-source-api-domain-row')} onClick={() => onManage(source)} onKeyDown={onKeyDown} role="button" tabIndex={0}>
      <span className={cx('data-source-directory-row-icon')}><ApiOutlined /></span>
      <span className={cx('data-source-directory-row-main')}><span className={cx('data-source-directory-row-name', 'data-source-api-domain-row-name')}><span>{source.name}</span><Text className={cx('data-source-api-domain-row-url')} ellipsis={{ tooltip: source.baseUrl }} type="secondary">{source.baseUrl}</Text></span></span>
      <span className={cx('data-source-directory-row-tags')}><Tag>{source.directories.length} 个目录</Tag><Tag>{operationCount(source)} 个接口</Tag></span>
      <span className={cx('data-source-directory-row-actions')} onClick={(event) => event.stopPropagation()}><Button onClick={() => onManage(source)} size="small" type="text">管理目录</Button><MoreActions actions={[{ key: 'validate', label: '校验配置', onClick: () => onValidate(source) }, { key: 'edit', label: '编辑域名', onClick: () => onEdit(source) }, { key: 'delete', label: '删除域名', danger: true, onClick: () => onDelete(source) }]} /></span>
    </div>
  )
}

/** 渲染数据库或外部 API 的一级目录分组。 */
function DirectorySection({ title, countLabel, expanded, onToggle, onCreate, emptyText, children }: { title: string; countLabel: string; expanded: boolean; onToggle: () => void; onCreate: () => void; emptyText: string; children: ReactNode }): ReactElement {
  return <section className={cx('data-source-directory-section')}><div className={cx('data-source-directory-section-header')}><button aria-expanded={expanded} className={cx('data-source-directory-toggle')} onClick={onToggle} type="button">{expanded ? <DownOutlined /> : <RightOutlined />}<span className={cx('data-source-directory-folder-icon')}>{expanded ? <FolderOpenOutlined /> : <FolderOutlined />}</span><span>{title}</span><Tag className={cx('data-source-count-tag')}>{countLabel}</Tag></button><Button icon={<PlusOutlined />} onClick={onCreate} size="small" type="text">新增</Button></div>{expanded ? <div className={cx('data-source-directory-children')}>{Children.count(children) ? children : <div className={cx('data-source-directory-empty')}>{emptyText}</div>}</div> : null}</section>
}

/** 管理独立数据源目录页及域名、目录和接口的 AG-UI 保存。 */
export default function DataSourcesPage({ theme, workspaceRoot }: { theme: 'light' | 'dark'; workspaceRoot: string }): ReactElement {
  const [catalog, setCatalog] = useState<DataSourceCatalog>()
  const [loading, setLoading] = useState(true)
  const [refreshing, setRefreshing] = useState(false)
  const [error, setError] = useState('')
  const [editorOpen, setEditorOpen] = useState(false)
  const [editing, setEditing] = useState<Source>()
  const [creatingType, setCreatingType] = useState<CreateType>()
  const [managerOpen, setManagerOpen] = useState(false)
  const [managerSourceId, setManagerSourceId] = useState('')
  const [managerDirectoryId, setManagerDirectoryId] = useState('')
  const [managerOperationId, setManagerOperationId] = useState('')
  const [managerOperationLoading, setManagerOperationLoading] = useState(false)
  const [directoryModalOpen, setDirectoryModalOpen] = useState(false)
  const [directorySourceId, setDirectorySourceId] = useState('')
  const [editingDirectory, setEditingDirectory] = useState<DataSourceDirectory>()
  const [operationModalOpen, setOperationModalOpen] = useState(false)
  const [operationSourceId, setOperationSourceId] = useState('')
  const [operationDirectoryId, setOperationDirectoryId] = useState('')
  const [editingOperation, setEditingOperation] = useState<DataSourceOperation>()
  const [saving, setSaving] = useState(false)
  const [databaseExpanded, setDatabaseExpanded] = useState(true)
  const [externalApiExpanded, setExternalApiExpanded] = useState(true)
  const mountedRef = useRef(true)
  const catalogRequestRef = useRef(0)
  const operationRequestRef = useRef(0)
  const databaseSources = useMemo(() => (catalog?.sources || []).filter((source): source is DatabaseDataSource => source.type === 'database'), [catalog])
  const apiSources = useMemo(() => (catalog?.sources || []).filter((source): source is ExternalApiDataSource => source.type === 'external_api'), [catalog])
  const managerDetails = useOperationDetails({ workspaceRoot, sourceId: managerSourceId, operationId: managerOperationId, open: managerOpen, catalog })

  /** 读取最新目录并在工作区切换时丢弃过期响应。 */
  const loadCatalog = useCallback(async (manual = false): Promise<void> => {
    const requestId = ++catalogRequestRef.current
    operationRequestRef.current += 1
    setManagerOperationLoading(false)
    if (manual) setRefreshing(true); else setLoading(true)
    setError('')
    try { const next = await requestDataSources(workspaceRoot); if (mountedRef.current && requestId === catalogRequestRef.current) setCatalog(next) } catch (caughtError) { if (mountedRef.current && requestId === catalogRequestRef.current) setError(isAuthenticationFailure(caughtError) ? '请重新登录后重试。' : caughtError instanceof Error ? caughtError.message : '数据源目录读取失败。') } finally { if (mountedRef.current && requestId === catalogRequestRef.current) { setLoading(false); setRefreshing(false) } }
  }, [workspaceRoot])

  useEffect(() => { mountedRef.current = true; void loadCatalog(); return () => { mountedRef.current = false; catalogRequestRef.current += 1; operationRequestRef.current += 1 } }, [loadCatalog])

  /** 打开域名或数据库新增弹窗。 */
  const openCreate = (type?: CreateType): void => { setEditing(undefined); setCreatingType(type); setEditorOpen(true) }
  /** 打开数据库或域名编辑弹窗。 */
  const openEdit = async (source: Source): Promise<void> => {
    let resolvedSource = source
    // 列表接口只返回索引摘要，编辑前统一读取目标数据源的完整配置。
    try {
      const details = await requestDataSourceDetails(workspaceRoot, source.id)
      const target = details.sources.find((item) => item.id === source.id)
      if (!target) throw new Error('目标数据源不存在，请刷新后重试。')
      resolvedSource = target
    } catch (caughtError) {
      message.error(caughtError instanceof Error ? caughtError.message : '数据源配置读取失败。')
      return
    }
    setEditing(resolvedSource)
    setCreatingType(undefined)
    setEditorOpen(true)
  }
  /** 关闭一级数据源编辑弹窗。 */
  const closeEditor = (): void => { setEditorOpen(false); setCreatingType(undefined); setEditing(undefined) }
  /** 编辑前独立读取接口配置，不改变管理弹窗的选中详情或主目录摘要。 */
  const loadOperationDetails = useCallback(async (sourceId: string, operationId: string): Promise<DataSourceOperation | undefined> => {
    const requestId = ++operationRequestRef.current
    setManagerOperationLoading(true)
    try {
      const next = await requestDataSourceOperation(workspaceRoot, sourceId, operationId)
      if (mountedRef.current && requestId === operationRequestRef.current) {
        return requireOperationDetails(next, sourceId, operationId)
      }
    } catch (caughtError) {
      if (mountedRef.current && requestId === operationRequestRef.current) message.error(caughtError instanceof Error ? caughtError.message : '接口详情读取失败。')
    } finally {
      if (mountedRef.current && requestId === operationRequestRef.current) setManagerOperationLoading(false)
    }
    return undefined
  }, [workspaceRoot])
  /** 打开指定外部 API 域名的目录管理弹窗。 */
  const openManager = (source: ExternalApiDataSource): void => {
    const initialDirectory = source.directories.find((directory) => directory.operations.length) || source.directories[0]
    setManagerSourceId(source.id)
    setManagerDirectoryId(initialDirectory?.id || '')
    setManagerOperationId(initialDirectory?.operations[0]?.id || '')
    setManagerOpen(true)
  }
  /** 关闭域名管理弹窗并清理其子编辑上下文。 */
  const closeManager = (): void => { operationRequestRef.current += 1; setManagerOpen(false); setManagerSourceId(''); setManagerDirectoryId(''); setManagerOperationId(''); setManagerOperationLoading(false); setDirectoryModalOpen(false); setDirectorySourceId(''); setEditingDirectory(undefined); setOperationModalOpen(false); setOperationSourceId(''); setOperationDirectoryId(''); setEditingOperation(undefined) }

  /** 在重复 Base URL 时提示用户，但不阻止继续保存。 */
  const confirmDuplicateBaseUrl = (source: Pick<ExternalApiDataSourceInput, 'id' | 'name' | 'baseUrl'>): Promise<boolean> => {
    const duplicate = apiSources.find((item) => item.id !== source.id && item.baseUrl.trim().toLowerCase() === source.baseUrl.trim().toLowerCase())
    if (!duplicate) return Promise.resolve(true)
    return new Promise((resolve) => Modal.confirm({ centered: true, title: '发现重复 Base URL', content: `“${duplicate.name}”已使用相同 Base URL，仍要保存当前域名配置吗？`, cancelText: '取消', okText: '仍然保存', onCancel: () => resolve(false), onOk: () => resolve(true) }))
  }

  /** 保存一级数据源，并以服务端目录作为唯一页面状态。 */
  const handleSave = async (source: DatabaseDataSourceInput | ExternalApiDataSourceInput): Promise<void> => {
    if (!catalog) return
    if (source.type === 'external_api' && !(await confirmDuplicateBaseUrl(source))) return
    setSaving(true)
    try { const next = editing ? await updateDataSource(workspaceRoot, source) : await createDataSource(workspaceRoot, source); setCatalog(next); closeEditor(); message.success(editing ? '数据源已更新' : source.type === 'external_api' ? '域名已创建，默认目录已生成' : '数据源已创建') } finally { setSaving(false) }
  }

  /** 保存一个域名的目录或接口变更。 */
  const persistExternalSource = async (source: ExternalApiDataSource, successMessage: string, editedOperation?: DataSourceOperation): Promise<void> => {
    setSaving(true)
    try {
      const latestCatalog = await requestDataSourceDetails(workspaceRoot, source.id)
      const latest = latestCatalog.sources.find((item): item is ExternalApiDataSource => item.type === 'external_api' && item.id === source.id)
      if (!latest) throw new Error('目标域名不存在，请刷新后重试。')
      const merged = mergeExternalSourceChanges(latest, source, editedOperation)
      const next = await updateDataSource(workspaceRoot, merged)
      setCatalog(next)
      message.success(successMessage)
    } finally { setSaving(false) }
  }

  /** 校验草稿或已保存数据源。 */
  const handleValidate = async (source: DatabaseDataSourceInput | ExternalApiDataSourceInput | { sourceId: string }): Promise<void> => { setSaving(true); try { const result = await validateDataSource(workspaceRoot, source); message.success(result.connection === 'ok' ? '数据库连接检测通过' : '数据源静态校验通过') } finally { setSaving(false) } }
  /** 通过资源 ID 校验已保存数据源。 */
  const handleValidateSaved = async (source: Source): Promise<void> => { try { await handleValidate({ sourceId: source.id }) } catch (caughtError) { message.error(caughtError instanceof Error ? caughtError.message : '数据源校验失败。') } }

  /** 打开目录新增或编辑弹窗。 */
  const openDirectory = (source: ExternalApiDataSource, directory?: DataSourceDirectory): void => { setDirectorySourceId(source.id); setEditingDirectory(directory); setDirectoryModalOpen(true) }
  /** 关闭目录弹窗并清理上下文。 */
  const closeDirectory = (): void => { setDirectoryModalOpen(false); setDirectorySourceId(''); setEditingDirectory(undefined) }
  /** 保存目录名称并保留该域名下其他目录。 */
  const handleDirectorySave = async (directory: DataSourceDirectory): Promise<void> => {
    const source = apiSources.find((item) => item.id === directorySourceId); if (!source) throw new Error('目标域名不存在，请刷新后重试。')
    const nextDirectory = { ...directory, id: directory.id || newDirectoryId() }
    const directories = editingDirectory ? source.directories.map((item) => item.id === editingDirectory.id ? nextDirectory : item) : [...source.directories, nextDirectory]
    const preservedOperation = managerDirectoryId === nextDirectory.id && nextDirectory.operations.some((operation) => operation.id === managerOperationId) ? managerOperationId : nextDirectory.operations[0]?.id || ''
    await persistExternalSource({ ...source, directories }, editingDirectory ? '目录已更新' : '目录已创建')
    setManagerDirectoryId(nextDirectory.id)
    setManagerOperationId(preservedOperation)
    closeDirectory()
  }

  /** 打开接口新增或编辑弹窗，并记录接口所在域名和目录。 */
  const openOperation = async (source: ExternalApiDataSource, directoryId: string, operation?: DataSourceOperation): Promise<void> => {
    const resolvedOperation = operation ? await loadOperationDetails(source.id, operation.id) : undefined
    if (operation && !resolvedOperation) return
    setOperationSourceId(source.id)
    setOperationDirectoryId(directoryId)
    setEditingOperation(resolvedOperation)
    setOperationModalOpen(true)
  }
  /** 关闭接口弹窗并清理上下文。 */
  const closeOperation = (): void => { setOperationModalOpen(false); setOperationSourceId(''); setOperationDirectoryId(''); setEditingOperation(undefined) }
  /** 保存接口并支持在同一域名的目录间移动。 */
  const handleOperationSave = async (operation: DataSourceOperation, targetDirectoryId: string): Promise<void> => {
    const source = apiSources.find((item) => item.id === operationSourceId); if (!source) throw new Error('目标域名不存在，请刷新后重试。')
    const operationId = operation.id || newOperationId()
    const nextOperation = { ...operation, id: operationId }
    const directories = source.directories.map((directory) => {
      const withoutOperation = directory.operations.filter((item) => item.id !== operationId)
      return directory.id === targetDirectoryId ? { ...directory, operations: [...withoutOperation, nextOperation] } : { ...directory, operations: withoutOperation }
    })
    await persistExternalSource({ ...source, directories }, editingOperation ? '接口已更新' : '接口已创建', nextOperation)
    setManagerDirectoryId(targetDirectoryId)
    setManagerOperationId(operationId)
    closeOperation()
  }

  /** 删除域名并提示级联删除的目录和接口数量。 */
  const handleDeleteSource = (source: Source): void => { const detail = source.type === 'external_api' ? `将同时删除 ${source.directories.length} 个目录和 ${operationCount(source)} 个接口。` : '将移除该独立数据源配置。'; Modal.confirm({ centered: true, title: '确认删除数据源？', content: `${detail} 此操作无法恢复。`, cancelText: '取消', okText: '删除', okButtonProps: { danger: true }, onOk: async () => { try { const next = await deleteDataSource(workspaceRoot, source.id); setCatalog(next); if (source.id === managerSourceId) closeManager(); message.success('数据源已删除') } catch (caughtError) { message.error(caughtError instanceof Error ? caughtError.message : '数据源删除失败。') } } }) }
  /** 删除目录并提示级联删除的接口数量，仅在删除当前目录时调整右侧选择。 */
  const handleDeleteDirectory = (source: ExternalApiDataSource, directory: DataSourceDirectory): void => {
    Modal.confirm({
      centered: true,
      title: '确认删除目录？',
      content: `目录“${directory.name}”包含 ${directory.operations.length} 个接口，删除后无法恢复。`,
      cancelText: '取消',
      okText: '删除',
      okButtonProps: { danger: true },
      onOk: async () => {
        try {
          const nextDirectories = source.directories.filter((item) => item.id !== directory.id)
          const deletedIndex = source.directories.findIndex((item) => item.id === directory.id)
          const nextDirectory = nextDirectories[deletedIndex] || nextDirectories[deletedIndex - 1] || nextDirectories[0]
          await persistExternalSource({ ...source, directories: nextDirectories }, '目录已删除')
          if (managerDirectoryId === directory.id) {
            setManagerDirectoryId(nextDirectory?.id || '')
            setManagerOperationId(nextDirectory?.operations[0]?.id || '')
          }
        } catch (caughtError) {
          message.error(caughtError instanceof Error ? caughtError.message : '目录删除失败。')
        }
      }
    })
  }
  /** 删除单个接口，并在删除当前接口后选择相邻接口。 */
  const handleDeleteOperation = (source: ExternalApiDataSource, directory: DataSourceDirectory, operation: DataSourceOperation): void => {
    Modal.confirm({
      centered: true,
      title: '确认删除接口？',
      content: `删除“${operation.name}”后无法恢复。`,
      cancelText: '取消',
      okText: '删除',
      okButtonProps: { danger: true },
      onOk: async () => {
        try {
          const nextOperations = directory.operations.filter((candidate) => candidate.id !== operation.id)
          const deletedIndex = directory.operations.findIndex((candidate) => candidate.id === operation.id)
          const nextOperation = nextOperations[deletedIndex] || nextOperations[deletedIndex - 1] || nextOperations[0]
          await persistExternalSource({ ...source, directories: source.directories.map((item) => item.id === directory.id ? { ...item, operations: nextOperations } : item) }, '接口已删除')
          if (managerDirectoryId === directory.id && managerOperationId === operation.id) {
            setManagerDirectoryId(directory.id)
            setManagerOperationId(nextOperation?.id || '')
          }
        } catch (caughtError) {
          message.error(caughtError instanceof Error ? caughtError.message : '接口删除失败。')
        }
      }
    })
  }

  const operationSource = apiSources.find((source) => source.id === operationSourceId)
  const managerSource = apiSources.find((source) => source.id === managerSourceId)

  useEffect(() => {
    if (!managerOpen || !managerSource) return
    const selectedDirectory = managerSource.directories.find((directory) => directory.id === managerDirectoryId)
    if (!selectedDirectory) {
      const fallbackDirectory = managerSource.directories.find((directory) => directory.operations.length) || managerSource.directories[0]
      const fallbackOperation = fallbackDirectory?.operations[0]
      if (managerDirectoryId !== (fallbackDirectory?.id || '')) setManagerDirectoryId(fallbackDirectory?.id || '')
      if (managerOperationId !== (fallbackOperation?.id || '')) setManagerOperationId(fallbackOperation?.id || '')
      return
    }
    const selectedOperation = selectedDirectory.operations.find((operation) => operation.id === managerOperationId)
    if (selectedOperation || selectedDirectory.operations.length === 0) {
      if (!selectedOperation && managerOperationId) setManagerOperationId('')
      return
    }
    setManagerOperationId(selectedDirectory.operations[0].id)
  }, [managerDirectoryId, managerOperationId, managerOpen, managerSource])

  /** 选择管理弹窗中的目录，并优先展示该目录的首个接口。 */
  const selectManagerDirectory = (directoryId: string): void => {
    const directory = managerSource?.directories.find((item) => item.id === directoryId)
    setManagerDirectoryId(directoryId)
    const operationId = directory?.operations[0]?.id || ''
    setManagerOperationId(operationId)
  }
  /** 选择管理弹窗中的接口及其所属目录。 */
  const selectManagerOperation = (directoryId: string, operationId: string): void => {
    setManagerDirectoryId(directoryId)
    setManagerOperationId(operationId)
  }

  return <section aria-label="数据源" className={cx('data-sources-page')}>
    <header className={cx('data-sources-header')}><div className={cx('data-sources-title')}><span className={cx('data-sources-title-icon')}><DatabaseOutlined /></span><div><Title level={4}>数据源</Title><Text>独立管理数据库和外部 API 配置</Text></div></div><div className={cx('data-sources-actions')}><Button icon={<ReloadOutlined />} loading={refreshing} onClick={() => void loadCatalog(true)}>刷新</Button><Button icon={<PlusOutlined />} onClick={() => openCreate()} type="primary">新增数据源</Button></div></header>
    <div aria-live="polite" className={cx('data-sources-content')}>
      {loading ? <div className={cx('data-sources-state')}><Spin /><Text type="secondary">正在读取数据源目录...</Text></div> : error && !catalog ? <div className={cx('data-sources-state')}><Alert action={<Button onClick={() => void loadCatalog()}>重试</Button>} description={error} message="无法读取数据源" showIcon type="error" /></div> : <>
        {error ? <Alert className={cx('data-sources-inline-error')} closable onClose={() => setError('')} showIcon description={error} message="刷新数据源目录失败" type="error" /> : null}
        <div className={cx('data-source-directory')}>
          <DirectorySection countLabel={`${databaseSources.length} / 1`} emptyText="暂无数据库数据源，点击右侧新增" onCreate={() => openCreate('database')} onToggle={() => setDatabaseExpanded((current) => !current)} title="数据库" expanded={databaseExpanded}>{databaseSources.map((source) => <DatabaseRow key={source.id} onDelete={handleDeleteSource} onEdit={openEdit} onValidate={(item) => void handleValidateSaved(item)} source={source} />)}</DirectorySection>
          <DirectorySection countLabel={`${apiSources.length}`} emptyText="暂无外部 API 域名，点击右侧新增" onCreate={() => openCreate('external_api')} onToggle={() => setExternalApiExpanded((current) => !current)} title="外部 API" expanded={externalApiExpanded}>{apiSources.map((source) => <ApiDomainRow key={source.id} onDelete={handleDeleteSource} onEdit={openEdit} onManage={openManager} onValidate={(item) => void handleValidateSaved(item)} source={source} />)}</DirectorySection>
        </div>
      </>}
    </div>
    <DataSourceEditorModal createType={creatingType} editing={editing} onClose={closeEditor} onSave={handleSave} onValidate={handleValidate} open={editorOpen} saving={saving} theme={theme} />
    {managerSource ? <ExternalApiManagerModal onClose={closeManager} onCreateDirectory={() => openDirectory(managerSource)} onCreateOperation={(directoryId) => openOperation(managerSource, directoryId)} onDeleteDirectory={(directory) => handleDeleteDirectory(managerSource, directory)} onDeleteOperation={(directory, operation) => handleDeleteOperation(managerSource, directory, operation)} onEditDirectory={(directory) => openDirectory(managerSource, directory)} onEditOperation={(directory, operation) => openOperation(managerSource, directory.id, operation)} onSelectDirectory={selectManagerDirectory} onSelectOperation={selectManagerOperation} open={managerOpen} operationLoading={managerDetails.loading || managerOperationLoading} operationDetail={managerDetails.operation} operationError={managerDetails.error} onRetryOperation={managerDetails.retry} selectedDirectoryId={managerDirectoryId} selectedOperationId={managerOperationId} source={managerSource} theme={theme} /> : null}
    <DataSourceDirectoryModal editing={editingDirectory} onClose={closeDirectory} onSave={handleDirectorySave} open={directoryModalOpen} saving={saving} theme={theme} />
    <DataSourceOperationModal directories={operationSource?.directories || []} editing={editingOperation} initialDirectoryId={operationDirectoryId} onClose={closeOperation} onSave={handleOperationSave} open={operationModalOpen} saving={saving} theme={theme} />
  </section>
}
