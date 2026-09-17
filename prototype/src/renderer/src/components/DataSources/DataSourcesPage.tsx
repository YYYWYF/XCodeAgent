import { PlusOutlined, TableOutlined } from '@ant-design/icons'
import { Button, Empty, Modal, message } from 'antd'
import type { ReactElement } from 'react'
import { useState } from 'react'
import { cx } from '../../utils'
import type { DataSourcesDetailTarget } from '../AiChatPanel/components/AuxiliaryDrawer'
import DatabaseConnectionModal from './DatabaseConnectionModal'
import DatabaseImportModal from './DatabaseImportModal'
import {
  DATABASE_MODE_LABEL,
  importedTables,
  type DatabaseDataSource,
  type ExternalApiSource,
  useDataSources
} from './catalog'
import './DataSourcesPage.less'

type Props = {
  /** 打开右侧衔接的详情维护层：接口（含新增）或数据表。 */
  onOpenDetail: (target: DataSourcesDetailTarget) => void
}

/** 列表条目：与任务管理抽屉的对话卡片同款设计，只标识“是什么”，详情进右侧衔接层。 */
function ListRow({
  icon,
  label,
  meta,
  onOpen
}: {
  icon: ReactElement
  label: string
  meta?: string
  onOpen: () => void
}): ReactElement {
  return (
    <button
      className={cx('conversation-item')}
      onClick={onOpen}
      onKeyDown={(event) => {
        if (event.key === 'Enter' || event.key === ' ') {
          event.preventDefault()
          onOpen()
        }
      }}
      title={label}
      type="button"
    >
      {icon}
      <span className={cx('conversation-item-title')}>{label}</span>
      {meta ? <span className={cx('conversation-item-meta')}>{meta}</span> : null}
    </button>
  )
}

/**
 * 数据来源抽屉列表：第一个 Tab 固定「外部 API」（平铺接口），其后一个数据库连接一个 Tab
 * （该连接已添加的表），末尾「＋」添加数据库。行点击在右侧衔接的详情层中维护。
 */
export default function DataSourcesPage({ onOpenDetail }: Props): JSX.Element {
  const [sources, saveSources] = useDataSources()
  /** 当前 Tab：'external' 为外部 API，其余值为数据库连接 id。 */
  const [activeTab, setActiveTab] = useState<string>('external')
  const [importOpen, setImportOpen] = useState(false)
  const [connectionModal, setConnectionModal] = useState<{ open: boolean; editing: DatabaseDataSource | null }>({
    open: false,
    editing: null
  })

  const databases = sources.filter((source): source is DatabaseDataSource => source.type === 'database')
  const externalApis = sources.filter((source): source is ExternalApiSource => source.type === 'external_service')
  const activeSource = databases.find((source) => source.id === activeTab)
  const activeTables = importedTables(sources).filter((item) => item.sourceId === activeTab)
  const showingExternal = !activeSource

  /** 保存数据库连接：保留连接下已发现/已添加的表结构，新建后切到该连接的 Tab。 */
  const saveConnection = (source: DatabaseDataSource): void => {
    const isNew = !connectionModal.editing
    saveSources(isNew ? [...sources, source] : sources.map((item) => (item.id === source.id ? source : item)))
    setConnectionModal({ open: false, editing: null })
    if (isNew) setActiveTab(source.id)
    message.success(isNew ? '数据库连接已创建' : '数据库连接已更新')
  }

  /** 删除数据库连接前二次确认：连接与其已添加表一并移除，Tab 回到外部 API。 */
  const deleteConnection = (source: DatabaseDataSource): void => {
    Modal.confirm({
      centered: true,
      cancelText: '取消',
      content: '将同时移除该连接与已添加的数据表；如已有应用API绑定相关表，将提示重新绑定。',
      okButtonProps: { danger: true },
      okText: '删除',
      onOk: () => {
        saveSources(sources.filter((item) => item.id !== source.id))
        setConnectionModal({ open: false, editing: null })
        if (activeTab === source.id) setActiveTab('external')
        message.success('数据库连接已删除')
      },
      title: `删除数据库连接「${source.name}」？`
    })
  }

  /** 演示环境的连接检测：数据库只做登记信息自检提示。 */
  const validateConnection = (): void => {
    message.success('数据库连接检测通过')
  }

  return (
    <section className={cx('data-sources-page')}>
      {/* 活跃 Tab 栏：第一个固定「外部 API」，其后一个数据库连接一个 Tab，末尾「＋」添加数据库。 */}
      <div className={cx('conversation-tabbar', 'ds-tabbar')}>
        <nav aria-label="数据来源分段" className={cx('conversation-tabs')}>
          <button
            aria-pressed={showingExternal}
            className={cx('conversation-tab', showingExternal && 'active')}
            onClick={() => setActiveTab('external')}
            type="button"
          >
            外部 API
          </button>
          {databases.map((source) => (
            <button
              aria-pressed={activeTab === source.id}
              className={cx('conversation-tab', activeTab === source.id && 'active')}
              key={source.id}
              onClick={() => setActiveTab(source.id)}
              title={source.name}
              type="button"
            >
              <span className={cx('conversation-tab-label')}>{source.name}</span>
            </button>
          ))}
          <button
            aria-label="新建数据库连接"
            className={cx('conversation-tab', 'ds-tab-add')}
            onClick={() => setConnectionModal({ open: true, editing: null })}
            title="新建数据库连接"
            type="button"
          >
            <PlusOutlined aria-hidden="true" />
          </button>
        </nav>
      </div>
      {showingExternal ? (
        <p className={cx('ds-hint')}>
          <span className={cx('ds-hint-text')}>每个外部 API 按接口登记，点击进入接口维护页。</span>
          <span className={cx('ds-hint-actions')}>
            <Button onClick={() => onOpenDetail({ kind: 'api-new' })} size="small" type="primary">
              新增接口
            </Button>
          </span>
        </p>
      ) : (
        <p className={cx('ds-hint')}>
          <span className={cx('ds-hint-text')}>
            {DATABASE_MODE_LABEL[activeSource.mode]} · 已添加 {activeTables.length} 张表，点击查看与移除。
          </span>
          <span className={cx('ds-hint-actions')}>
            <Button onClick={() => setConnectionModal({ open: true, editing: activeSource })} size="small">
              连接设置
            </Button>
            <Button
              onClick={() => setImportOpen(true)}
              size="small"
              type="primary"
            >
              添加数据表
            </Button>
          </span>
        </p>
      )}
      <div className={cx('ds-list')}>
        {showingExternal
          ? externalApis.map((source) => (
              <ListRow
                icon={<span className={cx('ds-method')}>{source.method}</span>}
                key={source.id}
                label={source.name}
                meta={source.description}
                onOpen={() => onOpenDetail({ kind: 'api', id: source.id })}
              />
            ))
          : activeTables.map((item) => (
              <ListRow
                icon={<TableOutlined />}
                key={`${item.sourceId}:${item.table.name}`}
                label={item.table.name}
                meta={item.table.comment}
                onOpen={() => onOpenDetail({ kind: 'table', sourceId: item.sourceId, name: item.table.name })}
              />
            ))}
        {showingExternal && externalApis.length === 0 ? (
          <Empty description="暂无外部 API 接口" image={Empty.PRESENTED_IMAGE_SIMPLE} />
        ) : null}
        {!showingExternal && activeTables.length === 0 ? (
          <Empty description="该连接暂未添加数据表" image={Empty.PRESENTED_IMAGE_SIMPLE} />
        ) : null}
      </div>
      <DatabaseImportModal
        onClose={() => setImportOpen(false)}
        onImport={(next) => {
          saveSources(next)
          setImportOpen(false)
          message.success('数据表已添加')
        }}
        open={importOpen}
        sourceId={activeTab}
        sources={sources}
      />
      <DatabaseConnectionModal
        editing={connectionModal.editing}
        onClose={() => setConnectionModal({ open: false, editing: null })}
        onDelete={deleteConnection}
        onSave={saveConnection}
        onValidate={validateConnection}
        open={connectionModal.open}
      />
    </section>
  )
}
