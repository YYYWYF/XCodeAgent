import {
  DatabaseOutlined,
  DeleteOutlined,
  DesktopOutlined,
  PartitionOutlined,
  PlusOutlined,
  TeamOutlined
} from '@ant-design/icons'
import { Button, Input, Select, Typography } from 'antd'
import type { ReactElement, ReactNode } from 'react'
import { cx } from '../../utils'
import './RequirementSpecEditor.less'

const { Text, Title } = Typography
const { TextArea } = Input

type Props = {
  onChange: (spec: Record<string, unknown>) => void
  rootPath: string
  spec: Record<string, unknown>
}

type ListField = 'pages' | 'user_roles' | 'business_flows' | 'data_sources'

// 将未知值安全收窄为可编辑对象。
function asRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === 'object' && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : {}
}

// 将需求集合收窄为对象数组，过滤无法编辑的历史值。
function recordList(value: unknown): Record<string, unknown>[] {
  return Array.isArray(value) ? value.map(asRecord).filter((item) => Object.keys(item).length) : []
}

// 把字段值转换为表单需要的文本。
function textValue(value: unknown): string {
  return typeof value === 'string' ? value : value == null ? '' : String(value)
}

// 为新增条目生成当前草稿内稳定的标识。
function draftId(prefix: string): string {
  return `${prefix}_${Date.now().toString(36)}_${Math.random().toString(36).slice(2, 7)}`
}

// 渲染带图标、新增操作和可编辑内容的模块。
function EditorSection({
  children,
  icon,
  onAdd,
  title
}: {
  children: ReactNode
  icon: ReactNode
  onAdd: () => void
  title: string
}): ReactElement {
  return (
    <section className={cx('requirement-editor-section')}>
      <header>
        <span className={cx('requirement-summary-section-icon')}>{icon}</span>
        <Title level={5}>{title}</Title>
        <Button icon={<PlusOutlined />} onClick={onAdd} size="small" type="text">
          新增
        </Button>
      </header>
      <div className={cx('requirement-editor-grid')}>{children}</div>
    </section>
  )
}

// 渲染单个可删除的需求条目容器。
function EditorItem({
  children,
  onRemove
}: {
  children: ReactNode
  onRemove: () => void
}): ReactElement {
  return (
    <article className={cx('requirement-editor-item')}>
      <Button
        aria-label="删除该需求项"
        className={cx('requirement-editor-remove')}
        icon={<DeleteOutlined />}
        onClick={onRemove}
        size="small"
        type="text"
      />
      {children}
    </article>
  )
}

// 为每个编辑控件展示明确的字段名称，避免仅依赖占位文本表达含义。
function EditorField({ children, label }: { children: ReactNode; label: string }): ReactElement {
  return (
    <div className={cx('requirement-editor-field')}>
      <Text>{label}</Text>
      {children}
    </div>
  )
}

// 以结构化表单编辑概览中的应用、页面、角色、流程和数据源。
export default function RequirementSpecEditor({ onChange, rootPath, spec }: Props): ReactElement {
  const appInfo = asRecord(spec.app_info)

  // 只更新应用定位字段，保留内部规划元数据。
  const updateApp = (field: string, value: string): void => {
    onChange({ ...spec, app_info: { ...appInfo, [field]: value } })
  }

  // 替换指定模块的整个条目列表。
  const replaceList = (field: ListField, items: Record<string, unknown>[]): void => {
    onChange({ ...spec, [field]: items })
  }

  // 修改指定模块的单个条目字段。
  const updateItem = (field: ListField, index: number, key: string, value: unknown): void => {
    const items = recordList(spec[field])
    replaceList(
      field,
      items.map((item, itemIndex) => (itemIndex === index ? { ...item, [key]: value } : item))
    )
  }

  // 在模块末尾追加一个带默认值的条目。
  const addItem = (field: ListField, item: Record<string, unknown>): void => {
    replaceList(field, [...recordList(spec[field]), item])
  }

  // 从模块中删除指定条目。
  const removeItem = (field: ListField, index: number): void => {
    replaceList(
      field,
      recordList(spec[field]).filter((_item, itemIndex) => itemIndex !== index)
    )
  }

  return (
    <div className={cx('requirement-editor')}>
      <section className={cx('requirement-editor-app')}>
        <Text type="secondary">应用定位</Text>
        <EditorField label="应用名称">
          <Input
            onChange={(event) => updateApp('name', event.target.value)}
            placeholder="请输入应用名称"
            value={textValue(appInfo.name)}
          />
        </EditorField>
        <EditorField label="应用目标和定位">
          <TextArea
            autoSize={{ minRows: 2, maxRows: 5 }}
            onChange={(event) => updateApp('target', event.target.value)}
            placeholder="请输入应用目标和定位"
            value={textValue(appInfo.target || appInfo.description || appInfo.summary)}
          />
        </EditorField>
      </section>

      <EditorSection
        icon={<DesktopOutlined />}
        onAdd={() =>
          addItem('pages', {
            pageId: draftId('page'),
            name: '新页面',
            path: rootPath && rootPath !== '/' ? `${rootPath}/` : '/',
            description: ''
          })
        }
        title="页面"
      >
        {recordList(spec.pages).map((item, index) => (
          <EditorItem
            key={textValue(item.pageId) || `page-${index}`}
            onRemove={() => removeItem('pages', index)}
          >
            <EditorField label="页面名称">
              <Input
                onChange={(event) => updateItem('pages', index, 'name', event.target.value)}
                placeholder="请输入页面名称"
                value={textValue(item.name)}
              />
            </EditorField>
            <EditorField label="页面路由">
              <Input
                addonBefore={rootPath && rootPath !== '/' ? rootPath : undefined}
                onChange={(event) => {
                  const userInput = event.target.value
                  const fullPath = rootPath && rootPath !== '/'
                    ? (userInput.startsWith('/') ? `${rootPath}${userInput}` : `${rootPath}/${userInput}`)
                    : userInput
                  updateItem('pages', index, 'path', fullPath)
                }}
                placeholder="例如 /orders"
                value={
                  rootPath && rootPath !== '/'
                    ? (String(item.path || '').startsWith(rootPath)
                      ? String(item.path).slice(rootPath.length) || '/'
                      : String(item.path))
                    : textValue(item.path)
                }
              />
            </EditorField>
            <EditorField label="页面说明">
              <TextArea
                autoSize={{ minRows: 2, maxRows: 4 }}
                onChange={(event) => updateItem('pages', index, 'description', event.target.value)}
                placeholder="请输入页面说明"
                value={textValue(item.description)}
              />
            </EditorField>
          </EditorItem>
        ))}
      </EditorSection>

      <EditorSection
        icon={<TeamOutlined />}
        onAdd={() =>
          addItem('user_roles', {
            id: draftId('role'),
            name: '新角色',
            description: '',
            permissions: []
          })
        }
        title="用户角色"
      >
        {recordList(spec.user_roles).map((item, index) => (
          <EditorItem
            key={textValue(item.id) || `role-${index}`}
            onRemove={() => removeItem('user_roles', index)}
          >
            <EditorField label="角色名称">
              <Input
                onChange={(event) => updateItem('user_roles', index, 'name', event.target.value)}
                placeholder="请输入角色名称"
                value={textValue(item.name)}
              />
            </EditorField>
            <EditorField label="角色说明">
              <TextArea
                autoSize={{ minRows: 2, maxRows: 4 }}
                onChange={(event) =>
                  updateItem('user_roles', index, 'description', event.target.value)
                }
                placeholder="请输入角色说明"
                value={textValue(item.description)}
              />
            </EditorField>
          </EditorItem>
        ))}
      </EditorSection>

      <EditorSection
        icon={<PartitionOutlined />}
        onAdd={() =>
          addItem('business_flows', {
            id: draftId('flow'),
            name: '新业务流程',
            description: '',
            steps: []
          })
        }
        title="核心业务流程"
      >
        {recordList(spec.business_flows).map((item, index) => (
          <EditorItem
            key={textValue(item.id) || `flow-${index}`}
            onRemove={() => removeItem('business_flows', index)}
          >
            <EditorField label="流程名称">
              <Input
                onChange={(event) =>
                  updateItem('business_flows', index, 'name', event.target.value)
                }
                placeholder="请输入流程名称"
                value={textValue(item.name)}
              />
            </EditorField>
            <EditorField label="流程说明">
              <TextArea
                autoSize={{ minRows: 2, maxRows: 4 }}
                onChange={(event) =>
                  updateItem('business_flows', index, 'description', event.target.value)
                }
                placeholder="请输入流程说明"
                value={textValue(item.description)}
              />
            </EditorField>
          </EditorItem>
        ))}
      </EditorSection>

      <EditorSection
        icon={<DatabaseOutlined />}
        onAdd={() =>
          addItem('data_sources', {
            id: draftId('source'),
            name: '新数据源',
            type: 'mock',
            description: '',
            entities: []
          })
        }
        title="数据来源"
      >
        {recordList(spec.data_sources).map((item, index) => (
          <EditorItem
            key={textValue(item.id) || `source-${index}`}
            onRemove={() => removeItem('data_sources', index)}
          >
            <EditorField label="数据源名称">
              <Input
                onChange={(event) => updateItem('data_sources', index, 'name', event.target.value)}
                placeholder="请输入数据源名称"
                value={textValue(item.name)}
              />
            </EditorField>
            <EditorField label="数据源类型">
              <Select
                onChange={(value) => updateItem('data_sources', index, 'type', value)}
                options={[
                  { label: '模拟数据', value: 'mock' },
                  { label: '数据库', value: 'database' },
                  { label: '外部接口', value: 'external_api' },
                  { label: '静态数据', value: 'static' }
                ]}
                value={textValue(item.type) || 'mock'}
              />
            </EditorField>
            <EditorField label="数据源说明">
              <TextArea
                autoSize={{ minRows: 2, maxRows: 4 }}
                onChange={(event) =>
                  updateItem('data_sources', index, 'description', event.target.value)
                }
                placeholder="请输入数据源说明"
                value={textValue(item.description)}
              />
            </EditorField>
          </EditorItem>
        ))}
      </EditorSection>
    </div>
  )
}
