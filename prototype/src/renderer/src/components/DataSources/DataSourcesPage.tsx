import {
  ApiOutlined,
  DatabaseOutlined,
  DeleteOutlined,
  EditOutlined,
  LinkOutlined,
  PlusOutlined
} from '@ant-design/icons'
import { Button, Empty, Modal, Tag, Typography, message } from 'antd'
import type { KeyboardEvent, ReactElement } from 'react'
import { useState } from 'react'
import { cx } from '../../utils'
import DataSourceEditorModal from './DataSourceEditorModal'
import {
  DATABASE_MODE_LABEL,
  externalOperationCount,
  type DatabaseDataSource,
  type DataSource,
  type ExternalApiSource,
  useDataSources
} from './catalog'
import './DataSourcesPage.less'

const { Text } = Typography

type RowAction = {
  key: string
  label: string
  icon: ReactElement
  danger?: boolean
  onClick: () => void
}

/** 行内常驻操作按钮：与任务管理抽屉的行内删除一致，不再使用难用的下拉菜单。 */
function RowActions({ actions }: { actions: RowAction[] }): ReactElement {
  return (
    <span className={cx('ds-row-actions')} onClick={(event) => event.stopPropagation()}>
      {actions.map((action) => (
        <Button
          aria-label={action.label}
          danger={action.danger}
          icon={action.icon}
          key={action.key}
          onClick={action.onClick}
          size="small"
          title={action.label}
          type="text"
        />
      ))}
    </span>
  )
}

/** 渲染数据库连接行：与原工程一致展示名称 + 连接模式与密码状态标签，行内不展示连接串。 */
function DatabaseRow({
  onDelete,
  onEdit,
  onValidate,
  source
}: {
  source: DatabaseDataSource
  onDelete: (source: DataSource) => void
  onEdit: (source: DataSource) => void
  onValidate: (source: DataSource) => void
}): ReactElement {
  /** 支持键盘打开数据库编辑弹窗。 */
  const onKeyDown = (event: KeyboardEvent<HTMLDivElement>): void => {
    if (event.key === 'Enter' || event.key === ' ') {
      event.preventDefault()
      onEdit(source)
    }
  }
  return (
    <div
      className={cx('ds-row')}
      onClick={() => onEdit(source)}
      onKeyDown={onKeyDown}
      role="button"
      tabIndex={0}
    >
      <span className={cx('ds-row-icon')}>
        <DatabaseOutlined />
      </span>
      <span className={cx('ds-row-main')}>
        <span className={cx('ds-row-name')}>{source.name}</span>
        <span className={cx('ds-row-tags')}>
          <Tag>{DATABASE_MODE_LABEL[source.mode]}</Tag>
          {source.mode === 'direct' ? (
            <Tag color={source.hasPassword ? 'green' : 'red'}>
              {source.hasPassword ? '密码已配置' : '缺少密码'}
            </Tag>
          ) : null}
        </span>
      </span>
      <span className={cx('ds-row-actions')} onClick={(event) => event.stopPropagation()}>
        <RowActions
          actions={[
            ...(source.mode === 'direct'
              ? [
                  {
                    key: 'validate',
                    label: '检测连接',
                    icon: <LinkOutlined />,
                    onClick: () => onValidate(source)
                  }
                ]
              : []),
            { key: 'edit', label: '编辑', icon: <EditOutlined />, onClick: () => onEdit(source) },
            {
              key: 'delete',
              label: '删除',
              danger: true,
              icon: <DeleteOutlined />,
              onClick: () => onDelete(source)
            }
          ]}
        />
      </span>
    </div>
  )
}

/** 渲染外部 API 站点行：名称加站点 URL。 */
function ApiDomainRow({
  onDelete,
  onEdit,
  source
}: {
  source: ExternalApiSource
  onDelete: (source: DataSource) => void
  onEdit: (source: DataSource) => void
}): ReactElement {
  /** 支持键盘打开编辑弹窗。 */
  const onKeyDown = (event: KeyboardEvent<HTMLDivElement>): void => {
    if (event.key === 'Enter' || event.key === ' ') {
      event.preventDefault()
      onEdit(source)
    }
  }
  return (
    <div
      className={cx('ds-row')}
      onClick={() => onEdit(source)}
      onKeyDown={onKeyDown}
      role="button"
      tabIndex={0}
    >
      <span className={cx('ds-row-icon')}>
        <ApiOutlined />
      </span>
      <span className={cx('ds-row-main')}>
        <span className={cx('ds-row-name')}>{source.name}</span>
        <span className={cx('ds-row-meta')}>
          <Text
            className={cx('ds-row-url')}
            ellipsis={{ tooltip: source.baseUrl }}
            type="secondary"
          >
            {source.baseUrl}
          </Text>
        </span>
      </span>
      <span className={cx('ds-row-actions')} onClick={(event) => event.stopPropagation()}>
        <RowActions
          actions={[
            { key: 'edit', label: '编辑', icon: <EditOutlined />, onClick: () => onEdit(source) },
            {
              key: 'delete',
              label: '删除',
              danger: true,
              icon: <DeleteOutlined />,
              onClick: () => onDelete(source)
            }
          ]}
        />
      </span>
    </div>
  )
}

/** 数据来源抽屉内容：按「数据库 / 外部 API」分页的连接列表，新增编辑走表单弹窗。 */
export default function DataSourcesPage(): JSX.Element {
  const [sources, saveSources] = useDataSources()
  const [tab, setTab] = useState<DataSource['type']>('database')
  const [editorOpen, setEditorOpen] = useState(false)
  const [editing, setEditing] = useState<DataSource | null>(null)
  const [creatingType, setCreatingType] = useState<DataSource['type'] | null>(null)
  const databases = sources.filter(
    (source): source is DatabaseDataSource => source.type === 'database'
  )
  const externalApis = sources.filter(
    (source): source is ExternalApiSource => source.type === 'external_service'
  )
  const showingDatabase = tab === 'database'

  /** 新增当前分页类型的连接。 */
  const createSource = (): void => {
    setEditing(null)
    setCreatingType(tab)
    setEditorOpen(true)
  }

  /** 行点击或菜单进入编辑弹窗。 */
  const editSource = (source: DataSource): void => {
    setEditing(source)
    setCreatingType(null)
    setEditorOpen(true)
  }

  /** 保存来源连接，弹窗关闭后列表以最新目录呈现。 */
  const saveSource = (source: DataSource): void => {
    saveSources(
      editing
        ? sources.map((item) => (item.id === source.id ? source : item))
        : [...sources, source]
    )
    setEditorOpen(false)
    message.success(editing ? '数据来源已更新' : '数据来源已创建')
  }

  /** 删除前二次确认；外部 API 提示级联删除的目录与接口规模。 */
  const deleteSource = (source: DataSource): void => {
    const detail =
      source.type === 'external_service'
        ? `将同时删除 ${source.directories.length} 个目录和 ${externalOperationCount(source)} 个接口。`
        : '将移除该数据来源配置。'
    Modal.confirm({
      centered: true,
      cancelText: '取消',
      content: `${detail} 此操作无法恢复。`,
      okButtonProps: { danger: true },
      okText: '删除',
      onOk: () => {
        saveSources(sources.filter((item) => item.id !== source.id))
        message.success('数据来源已删除')
      },
      title: '确认删除数据来源？'
    })
  }

  /** 演示环境的连接检测：数据库只做登记信息自检提示。 */
  const validateSource = (): void => {
    message.success('数据库连接检测通过')
  }

  return (
    <section className={cx('data-sources-page')}>
      {/* 与任务管理抽屉同款 Tab 栏：下划线选中态 + 数量角标，右侧挂新增按钮。 */}
      <div className={cx('conversation-tabbar')}>
        <nav aria-label="数据来源分段" className={cx('conversation-tabs')}>
          <button
            aria-pressed={showingDatabase}
            className={cx('conversation-tab', showingDatabase && 'active')}
            onClick={() => setTab('database')}
            type="button"
          >
            数据库
            <em>{databases.length}</em>
          </button>
          <button
            aria-pressed={!showingDatabase}
            className={cx('conversation-tab', !showingDatabase && 'active')}
            onClick={() => setTab('external_service')}
            type="button"
          >
            外部 API
            <em>{externalApis.length}</em>
          </button>
        </nav>
        <button
          aria-label={showingDatabase ? '新增数据库' : '新增外部 API'}
          className={cx('conversation-create-btn')}
          onClick={createSource}
          type="button"
        >
          <PlusOutlined />
          <span>新增连接</span>
        </button>
      </div>
      <div className={cx('ds-list')}>
        {showingDatabase
          ? databases.map((source) => (
              <DatabaseRow
                key={source.id}
                onDelete={deleteSource}
                onEdit={editSource}
                onValidate={validateSource}
                source={source}
              />
            ))
          : externalApis.map((source) => (
              <ApiDomainRow
                key={source.id}
                onDelete={deleteSource}
                onEdit={editSource}
                source={source}
              />
            ))}
        {(showingDatabase ? databases : externalApis).length === 0 ? (
          <Empty
            description={showingDatabase ? '暂无数据库连接' : '暂无外部 API 站点'}
            image={Empty.PRESENTED_IMAGE_SIMPLE}
          />
        ) : null}
      </div>
      <DataSourceEditorModal
        creatingType={creatingType}
        editing={editing}
        onClose={() => setEditorOpen(false)}
        onSave={saveSource}
        open={editorOpen}
      />
    </section>
  )
}
