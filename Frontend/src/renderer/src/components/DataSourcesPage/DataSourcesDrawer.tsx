import { confirmWorkspaceAction } from '../workspaceDialogs'
import { ApiOutlined, CloseOutlined, DatabaseOutlined, PlusOutlined, TableOutlined } from '@ant-design/icons'
import { Alert, Button, Empty, Input, Spin, Tooltip, message } from 'antd'
import { useEffect, useRef, useState } from 'react'
import type { ReactElement } from 'react'
import type { DataSourceCatalog, DataSourceOperation, DatabaseDataSource, DatabaseDataSourceInput, ExternalApiDataSource, ExternalApiDataSourceInput } from '../../typings'
import { createDataSource, deleteDataSource, requestDataSources, requestDataSourceDetails, requestSelectedTables, requestSourceReferences, updateDataSource, validateDataSource } from '../../service/dataSources'
import type { SelectedDataTable } from '../../service/dataSources'
import DataSourceEditorModal from './DataSourceEditorModal'
import DataSourceOperationModal from './DataSourceOperationModal'
import ExternalApisPane, { type ExternalApiListItem } from './ExternalApisPane'
import { AddDatabaseTables, DatabaseTableDetail } from './DatabaseTables'
import './DataSourcesDrawer.less'

type Detail = { kind: 'table'; table: SelectedDataTable } | { kind: 'operation'; sourceId: string; directoryId: string; operationId?: string; operation?: DataSourceOperation }
type DrawerMode = 'database' | 'external_api'
type Props = { workspaceRoot: string; theme: 'light' | 'dark'; mode: DrawerMode; onClose: () => void; onNavigationGuard: (guard?: (action: () => void) => void) => void }

/** 用列表与右侧详情双层抽屉维护数据来源，正式目录结构保持不变。 */
export default function DataSourcesDrawer({ workspaceRoot, theme, mode, onClose, onNavigationGuard }: Props): ReactElement {
  const [catalog, setCatalog] = useState<DataSourceCatalog>({ sources: [] })
  const [tables, setTables] = useState<SelectedDataTable[]>([])
  const [tab, setTab] = useState('')
  const [search, setSearch] = useState('')
  const [detail, setDetail] = useState<Detail>()
  const [fullSource, setFullSource] = useState<ExternalApiDataSource>()
  const [loading, setLoading] = useState(true)
  const [detailLoading, setDetailLoading] = useState(false)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')
  const [dirty, setDirty] = useState(false)
  const [editor, setEditor] = useState<{ createType?: DrawerMode; editing?: DatabaseDataSource | ExternalApiDataSource; open: boolean }>({ open: false })
  const [addingTables, setAddingTables] = useState(false)
  const requestId = useRef(0)
  const mounted = useRef(true)
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

  /** 重新读取服务器事实，关闭后的响应不再写入界面。 */
  const load = async (): Promise<void> => {
    setLoading(true); setError('')
    try { const [sources, selected] = await Promise.all([requestDataSources(workspaceRoot), requestSelectedTables(workspaceRoot)])
      if (mounted.current) { setCatalog(sources); setTables(selected) } }
    catch (reason) { if (mounted.current) setError(reason instanceof Error ? reason.message : '读取数据源失败。') }
    finally { if (mounted.current) setLoading(false) }
  }
  useEffect(() => { mounted.current = true; void load(); return () => { mounted.current = false; requestId.current += 1 } }, [workspaceRoot])

  /** 关闭或切换详情前统一保护未保存输入。 */
  const guard = (action: () => void): void => {
    if (saving) return
    if (!dirty) { action(); return }
    confirmWorkspaceAction({ title: '放弃未保存修改？', okText: '放弃修改', cancelText: '继续编辑', onOk: () => { closeDetail(); action() } })
  }
  // 左侧导航离开抽屉时沿用相同保护，避免卸载详情导致输入丢失。
  useEffect(() => { onNavigationGuard(guard); return () => onNavigationGuard(undefined) }, [dirty, saving, onNavigationGuard])
  /** 收起详情时取消在途响应，保持一级列表位置。 */
  const closeDetail = (): void => { requestId.current += 1; setDetail(undefined); setFullSource(undefined); setDetailLoading(false); setDirty(false) }

  /** 打开接口前按需读取完整域名，列表摘要绝不作为保存输入。 */
  const openOperation = async (sourceId: string, directoryId: string, operationId?: string): Promise<void> => {
    const id = ++requestId.current
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
  /** 检测仅显示后端真实结果，静态校验不冒充连接成功。 */
  const validate = async (source: DatabaseDataSourceInput | ExternalApiDataSourceInput): Promise<void> => {
    setSaving(true)
    try { const result = await validateDataSource(workspaceRoot, source); message.success(result.connection === 'ok' ? '连接检测通过' : '配置静态校验通过') }
    finally { setSaving(false) }
  }
  /** 删除数据库连接前展示真实引用，不操作真实数据库。 */
  const deleteConnection = async (): Promise<void> => {
    if (!database) return
    try { const refs = await requestSourceReferences(workspaceRoot, database.id)
      confirmWorkspaceAction({ title: `删除连接 ${database.name}？`, content: `不会删除真实数据库或正式映射。${refs.length ? `关联接口：${refs.join('、')}` : '暂无正式映射引用。'}`, okText: '删除', cancelText: '取消', okButtonProps: { danger: true },
        onOk: async () => { setCatalog(await deleteDataSource(workspaceRoot, database.id)); setTab(''); closeDetail(); await load() } })
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
  /** 从最新完整域名中只替换目标接口，保留兄弟接口及并发目录修改。 */
  const saveOperation = async (operation: DataSourceOperation, directoryId: string): Promise<void> => {
    if (detail?.kind !== 'operation') return
    setSaving(true)
    try {
      const response = await requestDataSourceDetails(workspaceRoot, detail.sourceId)
      const latest = response.sources.find((source): source is ExternalApiDataSource => source.type === 'external_api' && source.id === detail.sourceId)
      if (!latest?.directories.some((directory) => directory.id === directoryId)) throw new Error('目标目录已不存在，请重新选择。')
      const next = { ...operation, id: operation.id || `operation-${crypto.randomUUID()}` }
      const directories = latest.directories.map((directory) => ({ ...directory, operations: [
        ...directory.operations.filter((item) => item.id !== next.id), ...(directory.id === directoryId ? [next] : [])] }))
      setCatalog(await updateDataSource(workspaceRoot, { ...latest, directories })); closeDetail(); message.success('接口已保存')
    } finally { setSaving(false) }
  }
  /** 单接口删除只移除对应资源，不覆盖同域名其他接口。 */
  const deleteOperation = async (): Promise<void> => {
    if (detail?.kind !== 'operation' || !detail.operation) return
    const operation = detail.operation
    try { const refs = await requestSourceReferences(workspaceRoot, detail.sourceId, { operationId: operation.id })
      confirmWorkspaceAction({ title: `删除接口 ${operation.name}？`, content: refs.length ? `关联映射：${refs.join('、')}。正式映射不会自动删除。` : '暂无正式映射引用。', okText: '删除', cancelText: '取消', okButtonProps: { danger: true }, onOk: async () => {
        const response = await requestDataSourceDetails(workspaceRoot, detail.sourceId)
        const latest = response.sources.find((source): source is ExternalApiDataSource => source.type === 'external_api' && source.id === detail.sourceId)
        if (!latest) throw new Error('域名不存在。')
        setCatalog(await updateDataSource(workspaceRoot, { ...latest, directories: latest.directories.map((directory) => ({ ...directory, operations: directory.operations.filter((item) => item.id !== operation.id) })) })); closeDetail()
      } })
    } catch (reason) { setError(reason instanceof Error ? reason.message : '读取引用失败。') }
  }
  const allOperations = (mode === 'external_api' && activeExternal ? [activeExternal] : []).flatMap((source) => source.directories.flatMap((directory) => directory.operations.map((operation) => ({ source, directory, operation }))))
  const operations = allOperations
    .filter(({ source, operation }) => `${operation.name} ${operation.path} ${source.name}`.toLowerCase().includes(search.toLowerCase()))
  const allVisibleTables = tables.filter((table) => mode === 'database' && table.sourceId === tab)
  const visibleTables = allVisibleTables.filter((table) => `${table.table} ${table.description}`.toLowerCase().includes(search.toLowerCase()))
  const showSearch = mode === 'external_api' ? allOperations.length >= 5 : allVisibleTables.length >= 5

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

  return <div className={`source-drawer-stack ${detail ? 'with-detail' : ''}`} role="region" aria-label={mode === 'external_api' ? '外部 API' : '数据源'}>
    <section className="source-drawer-list"><header className="source-drawer-header">{mode === 'external_api' ? <ApiOutlined /> : <DatabaseOutlined />}<div><h3>{mode === 'external_api' ? '外部 API' : '数据源'}</h3><p>{mode === 'external_api' ? '已登记的接口域与外部接口，按出入参粒度绑定' : '已接入的数据库连接与数据表，按字段粒度绑定'}</p></div><Button type="text" aria-label="关闭数据来源" icon={<CloseOutlined />} onClick={() => guard(onClose)} /></header>
      {(mode === 'external_api' ? !externals.length : !databases.length) ? null : <nav className="source-drawer-tabs">{mode === 'external_api' ? <>{externals.map((source) => <button key={source.id} className={tab === source.id ? 'selected' : ''} onClick={() => guard(() => { closeDetail(); setTab(source.id) })}>{source.name}</button>)}<Tooltip title="新增接口域"><span><Button type="text" aria-label="新增接口域" icon={<PlusOutlined />} onClick={() => guard(() => setEditor({ createType: 'external_api', open: true }))} /></span></Tooltip></> : <>{databases.map((source) => <button key={source.id} className={tab === source.id ? 'selected' : ''} onClick={() => guard(() => { closeDetail(); setTab(source.id) })}>{source.name}</button>)}</>}</nav>}
      <div className={`source-drawer-tools ${mode === 'external_api' ? !activeExternal ? 'source-drawer-tools-empty' : '' : !database ? 'source-drawer-tools-empty' : ''}`}>{mode === 'external_api' ? activeExternal ? <div className="source-drawer-tool-row"><span className="source-drawer-static-hint">接口按出入参粒度与应用 API 适配。</span><div className="source-drawer-tool-actions"><Button onClick={() => guard(() => { void openDomain(activeExternal) })}>域设置</Button><Button disabled={!defaultDirectory} type="primary" onClick={() => guard(() => { if (defaultDirectory) void openOperation(activeExternal.id, defaultDirectory.id) })}>新增接口</Button></div></div> : <div className="source-drawer-empty-guide"><span className="source-empty-icon"><ApiOutlined /></span><strong>暂无接口域</strong><span>创建接口域后，可统一配置 Base URL 并管理域下的外部接口。</span><Button icon={<PlusOutlined />} type="primary" onClick={() => setEditor({ createType: 'external_api', open: true })}>新增接口域</Button></div> : database ? <div className="source-drawer-tool-row"><span className="source-drawer-static-hint">数据表按字段粒度参与应用 API 绑定。</span><div className="source-drawer-tool-actions"><Button disabled={loading} onClick={() => guard(() => { void openConnection(database) })}>连接设置</Button>
        <Button type="primary" disabled={loading || saving || database.mode !== 'direct'} onClick={() => setAddingTables(true)}>添加数据表</Button></div></div> : <div className="source-drawer-empty-guide"><span className="source-empty-icon"><DatabaseOutlined /></span><strong>暂无数据库连接</strong><span>创建连接后，可检测数据库并添加用于字段绑定的数据表。</span><Button icon={<PlusOutlined />} type="primary" onClick={() => setEditor({ createType: 'database', open: true })}>新增数据库连接</Button></div>}
        {database && database.mode !== 'direct' ? <p>当前连接模式暂不支持读取数据表，仅支持保存连接配置。</p> : null}
        {showSearch ? <Input.Search className="source-drawer-search" placeholder={mode === 'external_api' ? '搜索接口名称、说明或路径' : '搜索表名或说明'} value={search} onChange={(event) => setSearch(event.target.value)} /> : null}
      </div>
      {error ? <Alert type="error" message={error} action={<Button onClick={() => {
        if (detail?.kind === 'operation' && !fullSource) void openOperation(detail.sourceId, detail.directoryId, detail.operationId)
        else void load()
      }}>重试</Button>} /> : null}
      <Spin spinning={loading}>{mode === 'external_api' ? activeExternal ? <ExternalApisPane items={operations as ExternalApiListItem[]} onOpen={(item) => guard(() => { void openOperation(item.source.id, item.directory.id, item.operation.id) })} /> : null : database ? <div className="source-drawer-rows">
        {visibleTables.map((table) => <button key={`${table.sourceId}:${table.schema}:${table.table}`} onClick={() => guard(() => { closeDetail(); setDetail({ kind: 'table', table }) })} type="button"><TableOutlined /><strong>{table.table}</strong><span>{table.description}</span></button>)}
        {!loading && !visibleTables.length && <Empty description="暂未添加数据表" />}</div> : null}</Spin>
    </section>
    {detail && <section className="source-drawer-detail"><header className="source-drawer-header">{detail.kind === 'table' ? <TableOutlined /> : <ApiOutlined />}<div><h3>{detail.kind === 'table' ? detail.table.table : detail.operation?.name || '新增接口'}</h3></div><Button type="text" aria-label="关闭详情" icon={<CloseOutlined />} onClick={() => guard(closeDetail)} /></header>
      <div className="source-detail-content"><Spin spinning={detailLoading}>{detail.kind === 'table' ? <DatabaseTableDetail workspaceRoot={workspaceRoot} table={detail.table} onRemoved={(next) => { syncManagedTables(next); closeDetail() }} /> : fullSource && <>
        <DataSourceOperationModal key={`${detail.sourceId}:${detail.operation?.id || 'new'}`} embedded hideDirectory open saving={saving} theme={theme} directories={fullSource.directories} editing={detail.operation} initialDirectoryId={detail.directoryId}
          onDirtyChange={setDirty} onClose={() => guard(closeDetail)} onSave={saveOperation} onDelete={() => void deleteOperation()} />
      </>}</Spin></div></section>}
    {addingTables && database && <AddDatabaseTables workspaceRoot={workspaceRoot} source={database} selected={tables} onClose={() => setAddingTables(false)} onSaved={syncManagedTables} />}
    <DataSourceEditorModal createType={editor.createType} editing={editor.editing} open={editor.open} onClose={() => setEditor({ open: false })} onDelete={editor.editing ? editor.editing.type === 'external_api' ? () => void deleteDomain(editor.editing as ExternalApiDataSource) : () => void deleteConnection() : undefined} saving={saving} theme={theme} onSave={saveSource} onValidate={validate} />
  </div>
}
