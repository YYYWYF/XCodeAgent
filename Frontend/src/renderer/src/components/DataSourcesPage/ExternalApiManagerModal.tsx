import {
  DeleteOutlined,
  DownOutlined,
  EditOutlined,
  FolderOpenOutlined,
  FolderOutlined,
  MoreOutlined,
  PlusOutlined,
  RightOutlined
} from '@ant-design/icons'
import { Alert, Button, Dropdown, Empty, Menu, Modal, Spin, Tag, Typography } from 'antd'
import type { KeyboardEvent, ReactElement } from 'react'
import { useEffect, useState } from 'react'
import type { DataSourceDirectory, DataSourceOperation, ExternalApiDataSource } from '../../typings'
import { cx } from '../../utils'
import { JsonSampleTabs } from './JsonStructureViewer'

const { Text, Title } = Typography
type MoreAction = { key: string; label: string; danger?: boolean; onClick: () => void }

/** 渲染目录或接口的更多操作菜单。 */
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

/** 计算当前域名下的接口总数。 */
function operationCount(source: ExternalApiDataSource): number { return source.directories.reduce((total, directory) => total + directory.operations.length, 0) }

/** 渲染管理弹窗左侧的接口列表项。 */
function OperationRow({ operation, selected, onDelete, onSelect, onEdit }: { operation: DataSourceOperation; selected: boolean; onDelete: () => void; onSelect: () => void; onEdit: () => void }): ReactElement {
  /** 支持键盘选中当前接口。 */
  const onKeyDown = (event: KeyboardEvent<HTMLDivElement>): void => { if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); onSelect() } }
  return (
    <div className={cx('data-source-manager-operation-row', selected && 'data-source-manager-operation-row-selected')} onClick={onSelect} onKeyDown={onKeyDown} role="button" tabIndex={0}>
      <Tag>{operation.method}</Tag>
      <span className={cx('data-source-manager-operation-main')}><span className={cx('data-source-manager-operation-name')}>{operation.name}</span><span className={cx('data-source-manager-operation-path')}>{operation.path}</span></span>
      <span className={cx('data-source-manager-row-actions')} onClick={(event) => event.stopPropagation()}><MoreActions actions={[{ key: 'edit', label: '编辑接口', onClick: onEdit }, { key: 'delete', label: '删除接口', danger: true, onClick: onDelete }]} /></span>
    </div>
  )
}

/** 渲染管理弹窗左侧的目录及接口列表。 */
function DirectorySection({ directory, expanded, selected, selectedOperationId, onToggle, onSelect, onCreateOperation, onDelete, onEdit, onDeleteOperation, onEditOperation, onSelectOperation }: { directory: DataSourceDirectory; expanded: boolean; selected: boolean; selectedOperationId: string; onToggle: () => void; onSelect: () => void; onCreateOperation: () => void; onDelete: () => void; onEdit: () => void; onDeleteOperation: (operation: DataSourceOperation) => void; onEditOperation: (operation: DataSourceOperation) => void; onSelectOperation: (operation: DataSourceOperation) => void }): ReactElement {
  return (
    <section className={cx('data-source-manager-directory', selected && 'data-source-manager-directory-selected')}>
      <div className={cx('data-source-manager-directory-header')}>
        <button aria-label={expanded ? '收起目录' : '展开目录'} className={cx('data-source-manager-directory-collapse')} onClick={onToggle} type="button">{expanded ? <DownOutlined /> : <RightOutlined />}</button>
        <button className={cx('data-source-manager-directory-select')} onClick={onSelect} type="button"><span className={cx('data-source-directory-folder-icon')}>{expanded ? <FolderOpenOutlined /> : <FolderOutlined />}</span><span className={cx('data-source-manager-directory-name')}>{directory.name}</span><Tag className={cx('data-source-count-tag')}>{directory.operations.length} 个接口</Tag></button>
        <span className={cx('data-source-manager-row-actions')}>
          <Button icon={<PlusOutlined />} onClick={onCreateOperation} size="small" type="text">新增接口</Button>
          <MoreActions actions={[{ key: 'edit', label: '重命名目录', onClick: onEdit }, { key: 'delete', label: '删除目录', danger: true, onClick: onDelete }]} />
        </span>
      </div>
      {expanded ? <div className={cx('data-source-manager-directory-children')}>
        {directory.operations.length ? directory.operations.map((operation) => <OperationRow key={operation.id} onDelete={() => onDeleteOperation(operation)} onEdit={() => onEditOperation(operation)} onSelect={() => onSelectOperation(operation)} operation={operation} selected={selectedOperationId === operation.id} />) : <div className={cx('data-source-manager-directory-empty')}>暂无接口</div>}
      </div> : null}
    </section>
  )
}

/** 渲染接口详情中的列表字段。 */
function DetailList({ emptyText = '未配置', items, renderItem }: { emptyText?: string; items: unknown[]; renderItem: (item: unknown, index: number) => ReactElement }): ReactElement {
  return items.length ? <div className={cx('data-source-manager-detail-list')}>{items.map((item, index) => renderItem(item, index))}</div> : <Text className={cx('data-source-manager-detail-empty')} type="secondary">{emptyText}</Text>
}

/** 渲染选中接口的只读配置详情。 */
function OperationDetails({ directory, operation, onDelete, onEdit }: { directory: DataSourceDirectory; operation: DataSourceOperation; onDelete: () => void; onEdit: () => void }): ReactElement {
  return (
    <article className={cx('data-source-manager-detail')}>
      <header className={cx('data-source-manager-detail-header')}>
        <div className={cx('data-source-manager-detail-title')}><div><Title level={4}>{operation.name}</Title><Text type="secondary">{directory.name}</Text></div><Tag>{operation.method}</Tag></div>
        <div className={cx('data-source-manager-detail-actions')}><Button icon={<EditOutlined />} onClick={onEdit}>编辑接口</Button><Button danger icon={<DeleteOutlined />} onClick={onDelete} type="text">删除</Button></div>
      </header>
      <div className={cx('data-source-manager-detail-body')}>
        <section className={cx('data-source-manager-detail-section')}><div className={cx('data-source-manager-detail-section-title')}>请求路径</div><code className={cx('data-source-manager-detail-path')}>{operation.path}</code></section>
        {(['path', 'query'] as const).map((location) => <section className={cx('data-source-manager-detail-section')} key={location}>
          <div className={cx('data-source-manager-detail-section-title')}>{location === 'path' ? 'Path 参数' : 'Query 参数'}</div>
          <DetailList items={location === 'path' ? operation.pathParameters : operation.queryParameters} renderItem={(item, index) => {
            const parameter = item as DataSourceOperation['pathParameters'][number]
            return <div className={cx('data-source-manager-detail-list-row')} key={`${parameter.name}-${index}`}><span>{parameter.name}</span><code>{parameter.type}</code><span>{parameter.required ? '必填' : '可选'}</span><Text type="secondary">{parameter.description || '无描述'}</Text></div>
          }} />
        </section>)}
        <section className={cx('data-source-manager-detail-section')}><div className={cx('data-source-manager-detail-section-title')}>接口 Header</div><DetailList items={operation.headers} renderItem={(item, index) => { const header = item as DataSourceOperation['headers'][number]; return <div className={cx('data-source-manager-detail-list-row')} key={`${header.name}-${index}`}><span>{header.name || `Header ${index + 1}`}</span><Text className={cx('data-source-manager-detail-value')} ellipsis={{ tooltip: header.value }} type="secondary">{header.value || '空值'}</Text></div> }} /></section>
        <JsonSampleTabs descriptions={operation.requestFieldDescriptions} fieldTypes={operation.requestFieldTypes} label="请求体" value={operation.requestSample} />
        <JsonSampleTabs descriptions={operation.responseFieldDescriptions} fieldTypes={operation.responseFieldTypes} label="响应体" value={operation.responseSample} />
      </div>
    </article>
  )
}

/** 渲染右侧未选中接口时的引导占位页。 */
function ManagerPlaceholder({ directory, hasDirectories, onCreateDirectory, onCreateOperation }: { directory?: DataSourceDirectory; hasDirectories: boolean; onCreateDirectory: () => void; onCreateOperation: (directoryId: string) => void }): ReactElement {
  const title = directory ? '当前目录暂无接口' : hasDirectories ? '请选择一个接口' : '暂无目录'
  const hint = directory ? `在“${directory.name}”中新增接口后，可在这里查看完整配置。` : hasDirectories ? '从左侧目录中选择一个接口以查看配置详情。' : '请先新建目录，再添加需要管理的接口。'
  return (
    <div className={cx('data-source-manager-placeholder')}>
      <Empty description={<div className={cx('data-source-manager-placeholder-copy')}><strong>{title}</strong><span>{hint}</span></div>} image={Empty.PRESENTED_IMAGE_SIMPLE} />
      {directory ? <Button icon={<PlusOutlined />} onClick={() => onCreateOperation(directory.id)} type="primary">新增接口</Button> : !hasDirectories ? <Button icon={<PlusOutlined />} onClick={onCreateDirectory} type="primary">新增目录</Button> : null}
    </div>
  )
}

type Props = {
  source: ExternalApiDataSource
  selectedDirectoryId: string
  selectedOperationId: string
  onClose: () => void
  onCreateDirectory: () => void
  onCreateOperation: (directoryId: string) => void
  onDeleteDirectory: (directory: DataSourceDirectory) => void
  onDeleteOperation: (directory: DataSourceDirectory, operation: DataSourceOperation) => void
  onEditDirectory: (directory: DataSourceDirectory) => void
  onEditOperation: (directory: DataSourceDirectory, operation: DataSourceOperation) => void
  onSelectDirectory: (directoryId: string) => void
  onSelectOperation: (directoryId: string, operationId: string) => void
  open: boolean
  theme: 'light' | 'dark'
  operationLoading?: boolean
  operationDetail?: DataSourceOperation
  operationError?: string
  onRetryOperation: () => void
}

/** 管理单个外部 API 域名下目录和接口的左右分栏弹窗。 */
export default function ExternalApiManagerModal({ source, selectedDirectoryId, selectedOperationId, onClose, onCreateDirectory, onCreateOperation, onDeleteDirectory, onDeleteOperation, onEditDirectory, onEditOperation, onSelectDirectory, onSelectOperation, onRetryOperation, open, operationLoading = false, operationDetail, operationError, theme }: Props): ReactElement {
  const [expandedDirectories, setExpandedDirectories] = useState<Record<string, boolean>>({})
  const selectedDirectory = source.directories.find((directory) => directory.id === selectedDirectoryId)
  const selectedOperation = selectedDirectory?.operations.find((operation) => operation.id === selectedOperationId)
  const detail = operationDetail?.id === selectedOperation?.id ? operationDetail : undefined

  useEffect(() => {
    setExpandedDirectories((current) => Object.fromEntries(source.directories.map((directory) => [directory.id, current[directory.id] ?? true])))
  }, [source.id, source.directories])

  /** 切换左侧目录展开状态。 */
  const toggleDirectory = (directoryId: string): void => { setExpandedDirectories((current) => ({ ...current, [directoryId]: !current[directoryId] })) }

  return (
    <Modal
      bodyStyle={{ maxHeight: 'calc(100vh - 150px)', overflow: 'hidden', padding: 0 }}
      centered
      className={cx('data-source-editor-modal', 'data-source-manager-modal')}
      destroyOnClose
      footer={<div className={cx('data-source-modal-footer')}><Button onClick={onClose}>关闭</Button></div>}
      onCancel={onClose}
      title={`接口管理 · ${source.name}`}
      visible={open}
      width={1200}
      wrapClassName={cx('data-source-editor-modal-wrap', `theme-${theme}`)}
    >
      <div className={cx('data-source-manager')}>
        <div className={cx('data-source-manager-summary')}><div className={cx('data-source-manager-summary-endpoint')}><span>Base URL</span><Text className={cx('data-source-manager-summary-url')} ellipsis={{ tooltip: source.baseUrl }}>{source.baseUrl}</Text></div><div className={cx('data-source-manager-summary-stats')}><span>{source.directories.length} 个目录</span><span>{operationCount(source)} 个接口</span></div></div>
        <div className={cx('data-source-manager-layout')}>
          <aside className={cx('data-source-manager-sidebar')}>
            <div className={cx('data-source-manager-sidebar-header')}><span>目录</span><Button icon={<PlusOutlined />} onClick={onCreateDirectory} size="small">新增目录</Button></div>
            <div className={cx('data-source-manager-sidebar-list')}>
              {source.directories.length ? source.directories.map((directory) => <DirectorySection key={directory.id} directory={directory} expanded={expandedDirectories[directory.id] !== false} onCreateOperation={() => onCreateOperation(directory.id)} onDelete={() => onDeleteDirectory(directory)} onDeleteOperation={(operation) => onDeleteOperation(directory, operation)} onEdit={() => onEditDirectory(directory)} onEditOperation={(operation) => onEditOperation(directory, operation)} onSelect={() => onSelectDirectory(directory.id)} onSelectOperation={(operation) => onSelectOperation(directory.id, operation.id)} selected={selectedDirectoryId === directory.id} selectedOperationId={selectedOperationId} onToggle={() => toggleDirectory(directory.id)} />) : <div className={cx('data-source-manager-empty')}>暂无目录</div>}
            </div>
          </aside>
          <main className={cx('data-source-manager-detail-pane')}>
            {selectedOperation && selectedDirectory ? operationError ? <div className={cx('data-source-manager-placeholder')}><Alert message="接口详情读取失败" description={operationError} showIcon type="error" action={<Button onClick={onRetryOperation}>重试</Button>} /></div> : detail ? <Spin className={cx('data-source-manager-detail-loading')} spinning={operationLoading}><OperationDetails directory={selectedDirectory} onDelete={() => onDeleteOperation(selectedDirectory, detail)} onEdit={() => onEditOperation(selectedDirectory, detail)} operation={detail} /></Spin> : <div aria-live="polite" className={cx('data-source-manager-placeholder')}><Spin tip="正在读取接口配置..." /></div> : <ManagerPlaceholder directory={selectedDirectory} hasDirectories={source.directories.length > 0} onCreateDirectory={onCreateDirectory} onCreateOperation={onCreateOperation} />}
          </main>
        </div>
      </div>
    </Modal>
  )
}
