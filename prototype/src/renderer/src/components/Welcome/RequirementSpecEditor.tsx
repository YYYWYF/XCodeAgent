import { AppstoreOutlined, DeleteOutlined, PlusOutlined, TeamOutlined } from '@ant-design/icons'
import { Button, Input, Select, Typography } from 'antd'
import type { ReactElement, ReactNode } from 'react'
import { cx } from '../../utils'
import './RequirementSpecEditor.less'

const { Text, Title } = Typography
const { TextArea } = Input

type Props = {
  onChange: (spec: Record<string, unknown>) => void
  spec: Record<string, unknown>
}

type OverviewListField = 'user_roles' | 'feature_modules'

/** 将未知值安全收窄为可编辑对象。 */
function asRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === 'object' && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : {}
}

/** 将集合收窄为对象数组，过滤无法编辑的值。 */
function recordList(value: unknown): Record<string, unknown>[] {
  return Array.isArray(value) ? value.map(asRecord).filter((item) => Object.keys(item).length) : []
}

/** 把字段值转换为表单需要的文本。 */
function textValue(value: unknown): string {
  return typeof value === 'string' ? value : value == null ? '' : String(value)
}

/** 渲染带图标、新增操作和可编辑内容的概览分区。 */
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

/** 渲染单个可删除的概览条目容器，条目头行携带序号标题。 */
function EditorItem({
  caption,
  children,
  onRemove
}: {
  caption: string
  children: ReactNode
  onRemove: () => void
}): ReactElement {
  return (
    <article className={cx('requirement-editor-item')}>
      <header>
        <Text>{caption}</Text>
        <Button
          aria-label="删除该需求项"
          className={cx('requirement-editor-remove')}
          icon={<DeleteOutlined />}
          onClick={onRemove}
          size="small"
          type="text"
        />
      </header>
      {children}
    </article>
  )
}

/** 编辑“概览”分区：应用定位、业务参与者与功能模块，与审阅态概览分栏对应。 */
export default function RequirementSpecEditor({ onChange, spec }: Props): ReactElement {
  const appInfo = asRecord(spec.app_info)
  const roles = recordList(spec.user_roles)
  const modules = recordList(spec.feature_modules)

  // 只更新应用定位字段，保留内部规划元数据。
  const updateApp = (field: string, value: string): void => {
    onChange({ ...spec, app_info: { ...appInfo, [field]: value } })
  }

  /** 替换指定概览集合的整个条目列表。 */
  const replaceList = (field: OverviewListField, items: Record<string, unknown>[]): void => {
    onChange({ ...spec, [field]: items })
  }

  /** 修改指定集合中单个条目的字段。 */
  const updateItem = (
    field: OverviewListField,
    index: number,
    key: string,
    value: unknown
  ): void => {
    replaceList(
      field,
      recordList(spec[field]).map((item, itemIndex) =>
        itemIndex === index ? { ...item, [key]: value } : item
      )
    )
  }

  /** 在集合末尾追加一个带默认值的条目。 */
  const addItem = (field: OverviewListField, item: Record<string, unknown>): void => {
    replaceList(field, [...recordList(spec[field]), item])
  }

  /** 从集合中删除指定条目。 */
  const removeItem = (field: OverviewListField, index: number): void => {
    replaceList(
      field,
      recordList(spec[field]).filter((_item, itemIndex) => itemIndex !== index)
    )
  }

  return (
    <div className={cx('requirement-editor')}>
      <section className={cx('requirement-editor-app')}>
        <Text type="secondary">应用定位</Text>
        <label className={cx('requirement-editor-field')}>
          应用名称
          <Input
            onChange={(event) => updateApp('name', event.target.value)}
            placeholder="请输入应用名称"
            value={textValue(appInfo.name)}
          />
        </label>
        <label className={cx('requirement-editor-field')}>
          应用目标和定位
          <TextArea
            autoSize={{ minRows: 2, maxRows: 5 }}
            onChange={(event) => updateApp('target', event.target.value)}
            placeholder="请输入应用目标和定位"
            value={textValue(appInfo.target || appInfo.description || appInfo.summary)}
          />
        </label>
      </section>

      <EditorSection
        icon={<TeamOutlined />}
        onAdd={() =>
          addItem('user_roles', {
            id: `role_${Date.now().toString(36)}`,
            name: '新角色',
            description: '',
            isSystemRole: false,
            isInitialAdminRole: false
          })
        }
        title="业务参与者"
      >
        {roles.map((item, index) => (
          <EditorItem
            caption={`角色 ${index + 1}`}
            key={textValue(item.id) || `role-${index}`}
            onRemove={() => removeItem('user_roles', index)}
          >
            <label className={cx('requirement-editor-field')}>
              角色名称
              <Input
                onChange={(event) => updateItem('user_roles', index, 'name', event.target.value)}
                placeholder="请输入角色名称"
                value={textValue(item.name)}
              />
            </label>
            <label className={cx('requirement-editor-field')}>
              角色说明
              <TextArea
                autoSize={{ minRows: 2, maxRows: 4 }}
                onChange={(event) => updateItem('user_roles', index, 'description', event.target.value)}
                placeholder="请输入角色说明"
                value={textValue(item.description)}
              />
            </label>
          </EditorItem>
        ))}
      </EditorSection>

      <EditorSection
        icon={<AppstoreOutlined />}
        onAdd={() =>
          addItem('feature_modules', {
            id: `module_${modules.length + 1}`,
            name: '新功能模块',
            description: '',
            priority: 'must'
          })
        }
        title="功能模块"
      >
        {modules.map((item, index) => (
          <EditorItem
            caption={`模块 ${index + 1}`}
            key={textValue(item.id) || `module-${index}`}
            onRemove={() => removeItem('feature_modules', index)}
          >
            <label className={cx('requirement-editor-field')}>
              模块名称
              <Input
                onChange={(event) => updateItem('feature_modules', index, 'name', event.target.value)}
                placeholder="请输入模块名称"
                value={textValue(item.name)}
              />
            </label>
            <label className={cx('requirement-editor-field')}>
              优先级
              <Select
                onChange={(value) => updateItem('feature_modules', index, 'priority', value)}
                options={[
                  { label: '必需', value: 'must' },
                  { label: '可选', value: 'should' }
                ]}
                value={textValue(item.priority) || 'must'}
              />
            </label>
            <label className={cx('requirement-editor-field')}>
              模块说明
              <TextArea
                autoSize={{ minRows: 2, maxRows: 4 }}
                onChange={(event) =>
                  updateItem('feature_modules', index, 'description', event.target.value)
                }
                placeholder="请输入模块说明"
                value={textValue(item.description)}
              />
            </label>
          </EditorItem>
        ))}
      </EditorSection>
    </div>
  )
}
