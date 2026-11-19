import { confirmWorkspaceAction } from '../workspaceDialogs'
import { ApiOutlined, CloseOutlined, DatabaseOutlined, PlusOutlined, ReloadOutlined, SearchOutlined, TableOutlined } from '@ant-design/icons'
import { Alert, Button, Empty, Input, Spin, Tooltip, message } from 'antd'
import { useEffect, useRef, useState } from 'react'
import type { ReactElement } from 'react'
import type { DataSourceCatalog, DataSourceOperation, DatabaseDataSource, DatabaseDataSourceInput, DataSourceValidation, ExternalApiDataSource, ExternalApiDataSourceInput } from '../../typings'
import { createDataSource, deleteDataSource, requestDataSources, requestDataSourceDetails, requestSelectedTables, requestSourceReferences, updateDataSource, validateDataSource } from '../../service/dataSources'
import type { SelectedDataTable } from '../../service/dataSources'
import { subscribeDataSourcesChanged } from '../../service/dataSourceEvents'
import DataSourceEditorModal from './DataSourceEditorModal'
import ExternalOperationWorkspace, { type OperationDetailTab } from './ExternalOperationWorkspace'
import type { OperationUpdater } from './operationEditorDraft'
import QuickExternalOperation from './QuickExternalOperation'
import ExternalApisPane, { type ExternalApiListItem } from './ExternalApisPane'
import { AddDatabaseTables, DatabaseTableDetail } from './DatabaseTables'
import { BuiltinAuthorizationConfigGate } from './BuiltinAuthorizationSection'
import DatabaseTableTabs from './DatabaseTableTabs'
import './DataSourcesDrawer.less'

type Detail =
  | { kind: 'table'; table: SelectedDataTable }
  | { kind: 'operation'; sourceId: string; directoryId: string; operationId?: string; operation?: DataSourceOperation }
  | { kind: 'empty-operation' }
type DrawerMode = 'database' | 'external_api'
type OperationWrite = { create: DataSourceOperation } | { id: string; update: OperationUpdater }
type Props = { allowDebug?: boolean; workspaceRoot: string; theme: 'light' | 'dark'; mode: DrawerMode; onClose: () => void; onNavigationGuard: (guard?: (action: () => void) => void) => void; onBackToFieldMapping?: () => void }

/** 用列表与右侧详情双层抽屉维护数据来源，正式目录结构保持不变。 */
export default function DataSourcesDrawer({ allowDebug, workspaceRoot, theme, mode, onClose, onNavigationGuard, onBackToFieldMapping }: Props): ReactElement {
  const [catalog, setCatalog] = useState<DataSourceCatalog>({ sources: [] })
  const [tables, setTables] = useState<SelectedDataTable[]>([])
  const [tab, setTab] = useState('')
  const [search, setSearch] = useState('')
  const [detail, setDetail] = useState<Detail>()
  const [operationTab, setOperationTab] = useState<OperationDetailTab>('basic')
  const [fullSource, setFullSource] = useState<ExternalApiDataSource>()
  const [loading, setLoading] = useState(true)
  const [detailLoading, setDetailLoading] = useState(false)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')
  const [dirty, setDirty] = useState(false)
  const [editor, setEditor] = useState<{ createType?: DrawerMode; editing?: DatabaseDataSource | ExternalApiDataSource; open: boolean }>({ open: false })
  const [addingTables, setAddingTables] = useState(false)
  const requestId = useRef(0)
  const listRequestId = useRef(0)
  const mounted = useRef(true)
  // 抽屉根节点引用，给"点击外部自动收起"判断用。
  const rootRef = useRef<HTMLDivElement | null>(null)
  // 用 ref 持有最新 onClose/guard，避免监听器因引用变化反复挂载/卸载。
  const onCloseRef = useRef(onClose)
  onCloseRef.current = onClose
  const guardRef = useRef<(action: () => void) => void>(() => {})
  const databases = catalog.sources.filter((source): source is DatabaseDataSource => source.type === 'database')
  const externals = catalog.sources.filter((source): source is ExternalApiDataSource => source.type === 'external_api')
  const activeExternal = externals.find((source) => source.id === tab) || externals[0]
  const database = databases.find((source) => source.id === tab)
  const defaultDirectory = activeExternal?.directories.find((directory) => directory.name === '默认目录')
  const tabIds = (mode === 'external_api' ? externals : databases).map((source) => source.id).join('|')

  /** 切换一级入口时清理搜索和详情，不保留另一类资源的选中状态。 */
  useEffect(() => {
    setTab('')
    setSearch(''); closeDetail()
  }, [mode])

  /** 列表加载或资源增删后只在当前选中失效时回退到首个页签。 */
  useEffect(() => {
    const sources = mode === 'external_api' ? externals : databases
    setTab((current) => sources.some((source) => source.id === current) ? current : sources[0]?.id || '')
  }, [mode, tabIds])

  /** 切换数据库后清空本地关键词，避免沿用上一连接的筛选而误以为没有表。 */
  useEffect(() => {
    if (mode === 'database') setSearch('')
  }, [mode, tab])

  /** 重新读取服务器事实，关闭后的响应不再写入界面。 */
  const load = async (): Promise<void> => {
    const revision = ++listRequestId.current
    setLoading(true); setError('')
    try { const [sources, selected] = await Promise.all([requestDataSources(workspaceRoot), requestSelectedTables(workspaceRoot)])
      if (mounted.current && revision === listRequestId.current) { setCatalog(sources); setTables(selected) } }
    catch (reason) { if (mounted.current && revision === listRequestId.current) { setTables([]); setError(reason instanceof Error ? reason.message : '读取数据源失败。') } }
    finally { if (mounted.current && revision === listRequestId.current) setLoading(false) }
  }
  useEffect(() => {
    mounted.current = true; void load()
    const unsubscribe = subscribeDataSourcesChanged(workspaceRoot, () => { void load() })
    return () => { mounted.current = false; requestId.current += 1; listRequestId.current += 1; unsubscribe() }
  }, [workspaceRoot])

  /** 关闭或切换详情前统一保护未保存输入。 */
  const guard = (action: () => void): void => {
    if (saving) return
    if (!dirty) { action(); return }
    confirmWorkspaceAction({ title: '放弃未保存修改？', okText: '放弃修改', cancelText: '继续编辑', onOk: () => { closeDetail(); action() } })
  }
  // 同步到 ref：供"点击外部自动收起"的 document 级 mousedown 监听使用（避免依赖闭包过期值）。
  guardRef.current = guard
  // 左侧导航离开抽屉时沿用相同保护，避免卸载详情导致输入丢失。
  useEffect(() => { onNavigationGuard(guard); return () => onNavigationGuard(undefined) }, [dirty, saving, onNavigationGuard])

  // 点击抽屉外部自动收起：与其他工作台抽屉（技能/文件/设置/对话管理）行为一致。
  // 关键：走 guard(onClose) 而不是直接 onClose——未保存修改时弹确认框，不静默丢失数据。
  // 用 mousedown 而非 click：click 在 mouseup 才触发，用户在抽屉内按下拖到外部松开
  // （选择文本）不应被误判为"点击外部"。
  // 排除 antd 浮层（Modal 确认框/Popconfirm/Select 下拉/Tooltip 等）：它们 portal 到 body，
  // DOM 在抽屉外但语义上属于抽屉内的交互，不应触发关闭。
  // 排除侧栏菜单按钮：菜单按钮本身有 toggle 逻辑，mousedown 关闭会让 click 又打开，导致闪烁。
  useEffect(() => {
    // 用 ref 持有最新 guard/onClose，避免监听器因 dirty/saving 变化反复挂载。
    const handleMouseDown = (event: MouseEvent): void => {
      const root = rootRef.current
      if (!root) return
      const target = event.target as Node | null
      if (!target) return
      if (root.contains(target)) return
      if (target instanceof Element) {
        const floating = target.closest(
          '.ant-modal-wrap, .ant-modal-mask, .ant-select-dropdown, ' +
            '.ant-picker-dropdown, .ant-popover, .ant-tooltip, .ant-dropdown, ' +
            '.ant-cascader-dropdown, .ant-tree-select-dropdown, .ant-message, ' +
            '.ant-notification, .ant-popconfirm'
        )
        if (floating) return
        // 侧栏菜单按钮（数据源/外部API/文件/设置/技能）是 toggle：mousedown 关闭后
        // click 又会打开，导致抽屉闪烁。跳过它们，让 click 阶段的 toggle 独立决定。
        if (target.closest('[data-sidebar-menu="true"]')) return
      }
      guardRef.current(onCloseRef.current)
    }
    document.addEventListener('mousedown', handleMouseDown, true)
    return () => {
      document.removeEventListener('mousedown', handleMouseDown, true)
    }
  }, [])
  /** 收起详情时取消在途响应，保持一级列表位置。 */
  const closeDetail = (): void => { requestId.current += 1; setDetail(undefined); setFullSource(undefined); setDetailLoading(false); setDirty(false) }

  /** 打开接口前按需读取完整域名，列表摘要绝不作为保存输入。 */
  const openOperation = async (sourceId: string, directoryId: string, operationId?: string): Promise<void> => {
    const id = ++requestId.current
    setOperationTab('basic')
    setDetailLoading(true); setError(''); setDetail({ kind: 'operation', sourceId, directoryId, operationId }); setFullSource(undefined)
    try {
      const response = await requestDataSourceDetails(workspaceRoot, sourceId)
      if (id !== requestId.current) return
      const source = response.sources.find((item): item is ExternalApiDataSource => item.type === 'external_api' && item.id === sourceId)
      if (!source) throw new Error('域名不存在，请刷新重试。')
      const operation = source.directories.flatMap((item) => item.operations).find((item) => item.id === operationId)
      if (operationId && !operation) throw new Error('接口不存在，请刷新重试。')
      setFullSource(source); setDetail({ kind: 'operation', sourceId, directoryId, operationId, operation }); setDirty(false)
    } catch (reason) { if (id === requestId.current) setError(reason instanceof Error ? reason.message : '读取详情失败。') }
    finally { if (id === requestId.current) setDetailLoading(false) }
  }
  /** 点选同一接口只回到基本信息，选中其他接口时沿用草稿保护。 */
  const selectOperation = ({ source, directory, operation }: ExternalApiListItem): void => {
    if (detail?.kind === 'operation' && detail.sourceId === source.id && detail.operation?.id === operation.id) {
      setOperationTab('basic')
      return
    }
    guard(() => { void openOperation(source.id, directory.id, operation.id) })
  }
  /** 连接列表只含摘要，编辑前单独读取完整连接字段。 */
  const openConnection = async (source: DatabaseDataSource): Promise<void> => {
    setLoading(true); setError('')
    try {
      const response = await requestDataSourceDetails(workspaceRoot, source.id)
      const current = response.sources.find((item): item is DatabaseDataSource => item.type === 'database' && item.id === source.id)
      if (!current) throw new Error('数据库连接不存在，请刷新。')
      if (mounted.current) setEditor({ createType: 'database', open: true, editing: current })
    } catch (reason) { if (mounted.current) setError(reason instanceof Error ? reason.message : '连接详情读取失败。') }
    finally { if (mounted.current) setLoading(false) }
  }
  /** 读取完整接口域后打开域设置，避免用列表摘要覆盖域内接口。 */
  const openDomain = async (source: ExternalApiDataSource): Promise<void> => {
    setLoading(true); setError('')
    try {
      const response = await requestDataSourceDetails(workspaceRoot, source.id)
      const current = response.sources.find((item): item is ExternalApiDataSource => item.type === 'external_api' && item.id === source.id)
      if (!current) throw new Error('接口域不存在，请刷新。')
      if (mounted.current) setEditor({ createType: 'external_api', open: true, editing: current })
    } catch (reason) { if (mounted.current) setError(reason instanceof Error ? reason.message : '接口域详情读取失败。') }
    finally { if (mounted.current) setLoading(false) }
  }
  /** 保存数据库连接或接口域，并将页签定位到服务端返回的目标资源。 */
  const saveSource = async (source: DatabaseDataSourceInput | ExternalApiDataSourceInput): Promise<void> => {
    setSaving(true)
    try {
      const previousIds = new Set(catalog.sources.map((item) => item.id))
      const next = editor.editing ? await updateDataSource(workspaceRoot, source) : await createDataSource(workspaceRoot, source)
      const selected = next.sources.find((item) => item.id === source.id)
        || next.sources.find((item) => item.type === source.type && !previousIds.has(item.id))
        || next.sources.find((item) => item.type === source.type)
      // 数据库定位信息可能改变表清单，保存后统一从服务端刷新最新投影。
      closeDetail(); setCatalog(next); setEditor({ open: false }); setTab(selected?.id || '')
      message.success(source.type === 'external_api' ? '接口域已保存' : '连接配置已保存')
      await load()
    } finally { setSaving(false) }
  }
  /** 将 AG-UI 检测原始结果交给配置弹窗，静态校验不冒充连接成功。 */
  const validate = async (source: DatabaseDataSourceInput | ExternalApiDataSourceInput): Promise<DataSourceValidation> => {
    setSaving(true)
    try { return await validateDataSource(workspaceRoot, source) }
    finally { setSaving(false) }
  }
  /** 删除数据库连接前展示真实引用，不操作真实数据库。 */
  const deleteConnection = async (): Promise<void> => {
    if (!database) return
    try { const refs = await requestSourceReferences(workspaceRoot, database.id)
      confirmWorkspaceAction({ title: `删除连接 ${database.name}？`, content: `不会删除真实数据库或正式映射。${refs.length ? `关联接口：${refs.join('、')}` : '暂无正式映射引用。'}`, okText: '删除', cancelText: '取消', okButtonProps: { danger: true },
        onOk: async () => { setCatalog(await deleteDataSource(workspaceRoot, database.id)); setEditor({ open: false }); setTab(''); closeDetail(); await load() } })
    } catch (reason) { setError(reason instanceof Error ? reason.message : '读取引用失败。') }
  }
  /** 删除接口域前展示正式映射引用，删除不联动修改已确认映射。 */
  const deleteDomain = async (source: ExternalApiDataSource): Promise<void> => {
    try {
      const refs = await requestSourceReferences(workspaceRoot, source.id)
      confirmWorkspaceAction({
        title: `删除接口域 ${source.name}？`,
        content: `将同时删除该域下的接口配置，不会自动删除正式映射。${refs.length ? `关联接口：${refs.join('、')}` : '暂无正式映射引用。'}`,
        okText: '删除域', cancelText: '取消', okButtonProps: { danger: true },
        onOk: async () => { setCatalog(await deleteDataSource(workspaceRoot, source.id)); setEditor({ open: false }); closeDetail(); await load() },
      })
    } catch (reason) { setError(reason instanceof Error ? reason.message : '读取引用失败。') }
  }
  /** 从最新完整域名创建接口或合并当前页的保存范围，保留同目录中的其他配置。 */
  const saveOperation = async (directoryId: string, change: OperationWrite): Promise<DataSourceOperation> => {
    if (detail?.kind !== 'operation') throw new Error('请重新打开接口详情。')
    const sourceId = detail.sourceId
    setSaving(true)
    try {
      const response = await requestDataSourceDetails(workspaceRoot, sourceId)
      const latest = response.sources.find((source): source is ExternalApiDataSource => source.type === 'external_api' && source.id === sourceId)
      const directory = latest?.directories.find((item) => item.id === directoryId)
      if (!latest || !directory) throw new Error('目标目录已不存在，请重新选择。')
      let next: DataSourceOperation
      if ('create' in change) next = change.create
      else {
        const current = directory.operations.find((item) => item.id === change.id)
        if (!current) throw new Error('接口已不存在，请重新打开详情。')
        next = change.update(current)
      }
      const operations = 'create' in change ? [...directory.operations, next]
        : directory.operations.map((item) => item.id === next.id ? next : item)
      const directories = latest.directories.map((item) => item.id === directoryId ? { ...item, operations } : item)
      const savedSource = { ...latest, directories }
      setCatalog(await updateDataSource(workspaceRoot, savedSource))
      setFullSource(savedSource)
      setDetail({ kind: 'operation', sourceId, directoryId, operationId: next.id, operation: next })
      if ('create' in change) setDirty(false)
      message.success('接口已保存')
      return next
    } finally { setSaving(false) }
  }
  /** 从列表条目确定删除目标；删掉其他接口时保留当前详情和草稿。 */
  const deleteOperation = async ({ source, operation }: ExternalApiListItem): Promise<void> => {
    if (saving) return
    const selected = detail?.kind === 'operation' && detail.sourceId === source.id && detail.operationId === operation.id
    setSaving(true)
    try {
      const refs = await requestSourceReferences(workspaceRoot, source.id, { operationId: operation.id })
      if (!mounted.current) return
      const referenceNotice = refs.length ? `关联映射：${refs.join('、')}。正式映射不会自动删除。` : '暂无正式映射引用。'
      confirmWorkspaceAction({
        title: `删除接口 ${operation.name}？`,
        content: referenceNotice + (selected && dirty ? ' 该接口未保存的编辑会一并丢弃。' : ''),
        okText: '删除',
        cancelText: '取消',
        okButtonProps: { danger: true },
        onCancel: () => setSaving(false),
        onOk: async () => {
          try {
            // 删除前读取最新完整域名，更新时保留该域下其他接口与设置。
            const response = await requestDataSourceDetails(workspaceRoot, source.id)
            const latest = response.sources.find((item): item is ExternalApiDataSource => item.type === 'external_api' && item.id === source.id)
            if (!latest) throw new Error('域名不存在。')
            const directories = latest.directories.map((directory) => ({
              ...directory,
              operations: directory.operations.filter((item) => item.id !== operation.id)
            }))
            setCatalog(await updateDataSource(workspaceRoot, { ...latest, directories }))
            if (selected) {
              closeDetail()
              setDetail({ kind: 'empty-operation' })
            }
          } catch (reason) {
            message.error(reason instanceof Error ? reason.message : '删除接口失败。')
          } finally {
            setSaving(false)
          }
        }
      })
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : '读取引用失败。')
      setSaving(false)
    }
  }
  const allOperations = (mode === 'external_api' && activeExternal ? [activeExternal] : []).flatMap((source) => source.directories.flatMap((directory) => directory.operations.map((operation) => ({ source, directory, operation }))))
  const operations = allOperations
    .filter(({ source, operation }) => `${operation.name} ${operation.description || ''} ${operation.path} ${source.name}`.toLowerCase().includes(search.toLowerCase()))
  const allVisibleTables = tables.filter((table) => mode === 'database' && table.sourceId === tab)
  // 只筛选已加载的表名和说明，不随输入调用服务端，也不改变右侧已选表详情。
  const tableKeyword = search.trim().toLowerCase()
  const visibleTables = allVisibleTables.filter((table) => `${table.table} ${table.description || ''}`.toLowerCase().includes(tableKeyword))
  const isBuiltinDatabase = mode === 'database' && database?.mode === 'builtin'
  const showSearch = mode === 'external_api' ? allOperations.length >= 5 : database?.mode === 'builtin' || allVisibleTables.length >= 5
  const selectedOperationDirectory = detail?.kind === 'operation' ? fullSource?.directories.find((directory) => directory.id === detail.directoryId) : undefined
  const selectedOperation = detail?.kind === 'operation' ? detail.operation : undefined

  /** 将表清单动作结果同步到数据源公开对象，避免列表计数等待下一次全量读取。 */
  const syncManagedTables = (next: SelectedDataTable[]): void => {
    setTables(next)
    setCatalog((current) => current ? {
      ...current,
      sources: current.sources.map((item) => item.type === 'database'
        ? { ...item, managedTables: next.filter((table) => table.sourceId === item.id).map(({ table, description }) => ({ table, description })) }
        : item),
    } : current)
  }

  return <div ref={rootRef} className={`source-drawer-stack ${detail ? 'with-detail' : ''}`} role="region" aria-label={mode === 'external_api' ? '外部 API' : '数据源'}>
    <section className={`source-drawer-list ${isBuiltinDatabase ? 'is-builtin-database-list' : ''}`}><header className="source-drawer-header">{mode === 'external_api' ? <ApiOutlined /> : <DatabaseOutlined />}<div><h3>{mode === 'external_api' ? '外部 API' : '数据源'}</h3><p>{mode === 'external_api' ? '已登记的接口域与外部接口，按出入参粒度绑定' : '已接入的数据库连接与数据表，按字段粒度绑定'}</p></div><Button type="text" aria-label="关闭数据来源" icon={<CloseOutlined />} onClick={() => guard(onClose)} /></header>
      {(mode === 'external_api' ? !externals.length : !databases.length) ? null : <nav className="source-drawer-tabs">{mode === 'external_api' ? <>{externals.map((source) => <button key={source.id} className={tab === source.id ? 'selected' : ''} onClick={() => guard(() => { closeDetail(); setTab(source.id) })}>{source.name}</button>)}<Tooltip title="新增接口域"><span><Button type="text" aria-label="新增接口域" icon={<PlusOutlined />} onClick={() => guard(() => setEditor({ createType: 'external_api', open: true }))} /></span></Tooltip></> : <>{databases.map((source) => <button key={source.id} className={tab === source.id ? 'selected' : ''} onClick={() => guard(() => { closeDetail(); setTab(source.id) })}>{source.name}</button>)}</>}</nav>}
      {isBuiltinDatabase &&
        <BuiltinAuthorizationConfigGate key={JSON.stringify([workspaceRoot, database.id])} workspaceRoot={workspaceRoot} />}
      <section className="source-drawer-items" aria-label={isBuiltinDatabase ? '数据表' : undefined}>
        <div className={`source-drawer-tools ${mode === 'external_api' ? !activeExternal ? 'source-drawer-tools-empty' : '' : !database ? 'source-drawer-tools-empty' : ''}`}>{mode === 'external_api' ? activeExternal ? <div className="source-drawer-tool-row"><span className="source-drawer-static-hint">接口按出入参粒度与应用 API 适配。</span><div className="source-drawer-tool-actions"><Button onClick={() => guard(() => { void openDomain(activeExternal) })}>域设置</Button><Button disabled={!defaultDirectory} type="primary" onClick={() => guard(() => { if (defaultDirectory) void openOperation(activeExternal.id, defaultDirectory.id) })}>新增接口</Button></div></div> : <div className="source-drawer-empty-guide"><span className="source-empty-icon"><ApiOutlined /></span><strong>暂无接口域</strong><span>创建接口域后，可统一配置 Base URL 并管理域下的外部接口。</span><Button icon={<PlusOutlined />} type="primary" onClick={() => setEditor({ createType: 'external_api', open: true })}>新增接口域</Button></div> : database ? <div className="source-drawer-tool-row"><span className="source-drawer-static-hint">{database.mode === 'builtin' ? '数据表' : '数据表按字段粒度参与应用 API 绑定。'}</span><div className="source-drawer-tool-actions">{database.mode === 'builtin' ? <Button type="text" size="small" className="source-drawer-refresh" aria-label="刷新数据表" loading={loading} icon={<ReloadOutlined />} onClick={() => void load()}>刷新</Button> : <><Button disabled={loading} onClick={() => guard(() => { void openConnection(database) })}>连接设置</Button>
          <Button type="primary" disabled={loading || saving} onClick={() => setAddingTables(true)}>添加数据表</Button></>}</div></div> : <div className="source-drawer-empty-guide"><span className="source-empty-icon"><DatabaseOutlined /></span><strong>暂无数据库连接</strong><span className="source-database-empty-description">创建连接后，可检测数据库并添加用于字段绑定的数据表。</span><Button icon={<PlusOutlined />} type="primary" onClick={() => setEditor({ createType: 'database', open: true })}>新增数据库连接</Button></div>}
          {database?.mode === 'builtin' ? <p className="source-drawer-table-hint">表结构在实体中维护。</p> : null}
          {showSearch ? isBuiltinDatabase ? (
            <Input allowClear prefix={<SearchOutlined />} className="source-drawer-search source-drawer-table-search"
              aria-label="搜索数据表" placeholder="搜索表名或说明" value={search}
              onChange={(event) => setSearch(event.target.value)} />
          ) : <Input.Search allowClear className="source-drawer-search" aria-label={mode === 'external_api' ? '搜索接口' : '搜索数据表'} placeholder={mode === 'external_api' ? '搜索接口名称、说明或路径' : '搜索表名或说明'} value={search} onChange={(event) => setSearch(event.target.value)} /> : null}
        </div>
        {onBackToFieldMapping && <Button className="source-drawer-return" onClick={() => guard(onBackToFieldMapping)}>返回字段映射</Button>}
        {error ? <Alert type="error" message={error} action={<Button onClick={() => {
          if (detail?.kind === 'operation' && !fullSource) void openOperation(detail.sourceId, detail.directoryId, detail.operationId)
          else void load()
        }}>重试</Button>} /> : null}
        <Spin spinning={loading}>{mode === 'external_api' ? activeExternal ? <ExternalApisPane
          items={operations} saving={saving} onOpen={selectOperation} onDelete={(item) => void deleteOperation(item)}
          selectedId={detail?.kind === 'operation' && detail.sourceId === activeExternal.id ? detail.operationId : undefined}
        /> : null : database ? <div className="source-drawer-rows">
          {visibleTables.map((table) => <button
            className={`source-table-row ${detail?.kind === 'table' && detail.table.sourceId === table.sourceId && detail.table.schema === table.schema && detail.table.table === table.table ? 'selected' : ''}`}
            key={`${table.sourceId}:${table.schema}:${table.table}`}
            onClick={() => guard(() => { closeDetail(); setDetail({ kind: 'table', table }) })} type="button">
            <TableOutlined />
            <span className="source-table-row-copy"><strong title={table.table}>{table.table}</strong><small title={table.description}>{table.description || '暂无说明'}</small></span>
          </button>)}
          {!loading && !error && !visibleTables.length && <Empty description={tableKeyword && allVisibleTables.length ? '未找到匹配的数据表' : database?.mode === 'builtin' ? '暂无已建成的数据表，请先在实体中执行建表' : '暂未添加数据表'} />}</div> : null}</Spin>
      </section>
    </section>
    {detail && <section className="source-drawer-detail">
      {fullSource && selectedOperation && selectedOperationDirectory
        ? <ExternalOperationWorkspace key={selectedOperation.id}
            allowDebug={Boolean(allowDebug)} workspaceRoot={workspaceRoot} theme={theme}
            source={fullSource} operation={selectedOperation} saving={saving}
            onDirtyChange={setDirty} activeTab={operationTab} onTabChange={setOperationTab}
            onClose={() => guard(closeDetail)}
            onSave={(update) => saveOperation(selectedOperationDirectory.id, { id: selectedOperation.id, update })} />
        : <>
          <header className={`source-drawer-header ${detail.kind === 'operation' ? 'source-drawer-operation-header' : ''}`}>
            {detail.kind === 'table' ? <TableOutlined /> : <span className="source-operation-header-icon"><ApiOutlined /><small>API</small></span>}
            <div><h3>{detail.kind === 'table' ? detail.table.table : detail.kind === 'operation' && !detail.operationId ? '新增接口' : '接口详情'}</h3>
              {detail.kind === 'table' && <p>{database?.mode === 'builtin' ? '查看表结构、调试数据和操作记录' : '查看数据表及字段信息'}</p>}
            </div>
            <div className="source-drawer-header-actions">
              <Button className="source-operation-header-close" type="text" aria-label="关闭详情" icon={<CloseOutlined />} onClick={() => guard(closeDetail)} />
            </div>
          </header>
          <div className="source-detail-content"><Spin spinning={detailLoading}>
            {detail.kind === 'table'
              ? database?.mode === 'builtin'
                ? <DatabaseTableTabs key={`${detail.table.sourceId}:${detail.table.table}`} workspaceRoot={workspaceRoot} table={detail.table} theme={theme} />
                : <DatabaseTableDetail workspaceRoot={workspaceRoot} table={detail.table} onRemoved={(next) => { syncManagedTables(next); closeDetail() }} />
              : detail.kind === 'empty-operation' ? <Empty className="source-operation-empty" description="请选择接口" />
              : fullSource && !detail.operationId
                ? <QuickExternalOperation key={`${detail.sourceId}:${detail.directoryId}`} saving={saving}
                    onClose={() => guard(closeDetail)} onDirtyChange={setDirty}
                    onSave={async (operation) => { await saveOperation(detail.directoryId, { create: operation }) }} />
                : null}
          </Spin></div>
        </>}
    </section>}
    {addingTables && database && <AddDatabaseTables theme={theme} workspaceRoot={workspaceRoot} source={database} selected={tables} onClose={() => setAddingTables(false)} onSaved={syncManagedTables} />}
    <DataSourceEditorModal createType={editor.createType} editing={editor.editing} open={editor.open} onClose={() => setEditor({ open: false })} onDelete={editor.editing ? editor.editing.type === 'external_api' ? () => void deleteDomain(editor.editing as ExternalApiDataSource) : () => void deleteConnection() : undefined} saving={saving} theme={theme} onSave={saveSource} onValidate={validate} />
  </div>
}
