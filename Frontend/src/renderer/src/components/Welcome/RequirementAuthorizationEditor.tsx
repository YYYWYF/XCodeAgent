import { DeleteOutlined, PlusOutlined, SafetyCertificateOutlined } from '@ant-design/icons'
import { Button, Input, Select, Switch, Typography } from 'antd'
import type { ReactElement } from 'react'
import { cx } from '../../utils'

const { Text, Title } = Typography
const { TextArea } = Input

type Props = {
  onChange: (spec: Record<string, unknown>) => void
  spec: Record<string, unknown>
}

type AuthorizationListField = 'restrictedPages' | 'restrictedOperations' | 'dataRules'

// 将未知值安全收窄为权限编辑器可读的对象。
function asRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === 'object' && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : {}
}

// 将权限候选集合归一为对象数组，避免空值破坏编辑器操作。
function recordList(value: unknown): Record<string, unknown>[] {
  return Array.isArray(value) ? value.map(asRecord).filter((item) => Object.keys(item).length) : []
}

// 将未知输入转换为可控文本，避免 React 输入控件收到对象。
function textValue(value: unknown): string {
  return typeof value === 'string' ? value : value == null ? '' : String(value)
}

// 渲染权限候选的字段标签与输入控件。
function EditorField({ children, label }: { children: ReactElement; label: string }): ReactElement {
  return (
    <div className={cx('requirement-editor-field')}>
      <Text>{label}</Text>
      {children}
    </div>
  )
}

// 编辑单个权限候选并提供删除动作。
function AuthorizationItem({
  children,
  onRemove
}: {
  children: ReactElement[]
  onRemove: () => void
}): ReactElement {
  return (
    <article className={cx('requirement-editor-item')}>
      <Button
        aria-label="删除该权限候选"
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

// 编辑 RequirementSpec 权限业务候选、首次默认角色授权和初始系统管理员选择。
export default function RequirementAuthorizationEditor({ onChange, spec }: Props): ReactElement {
  const authorization = asRecord(spec.authorization_requirements)
  const enabled = authorization.enabled === true
  const roles = recordList(spec.user_roles)
  const roleOptions = roles
    .map((role) => ({ label: textValue(role.name) || textValue(role.id), value: textValue(role.id) }))
    .filter((option) => option.value)

  // 修改权限顶层事实并保留未展示的内部字段。
  const updateAuthorization = (field: string, value: unknown): void => {
    onChange({
      ...spec,
      authorization_requirements: { ...authorization, [field]: value }
    })
  }

  // 替换某一类权限候选集合，支持新增与删除。
  const replaceList = (field: AuthorizationListField, items: Record<string, unknown>[]): void => {
    updateAuthorization(field, items)
  }

  // 修改指定权限候选的业务字段。
  const updateItem = (
    field: AuthorizationListField,
    index: number,
    key: string,
    value: unknown
  ): void => {
    const items = recordList(authorization[field])
    replaceList(
      field,
      items.map((item, itemIndex) => (itemIndex === index ? { ...item, [key]: value } : item))
    )
  }

  // 删除指定权限候选。
  const removeItem = (field: AuthorizationListField, index: number): void => {
    replaceList(
      field,
      recordList(authorization[field]).filter((_item, itemIndex) => itemIndex !== index)
    )
  }

  // 选择初始系统管理员角色，并将系统属性作为角色种子事实同步写回。
  const updateInitialAdminRole = (roleId: string): void => {
    let nextRoles = roles
    let selectedRoleId = roleId
    if (roleId === '__create_system_administrator__') {
      const usedIds = new Set(roles.map((role) => textValue(role.id)))
      selectedRoleId = 'system_administrator'
      let suffix = 2
      while (usedIds.has(selectedRoleId)) {
        selectedRoleId = `system_administrator_${suffix}`
        suffix += 1
      }
      nextRoles = [
        ...roles,
        {
          id: selectedRoleId,
          name: '系统管理员',
          description: '首次负责系统权限管理的角色。',
          isSystemRole: true,
          isInitialAdminRole: true
        }
      ]
    }
    onChange({
      ...spec,
      user_roles: nextRoles.map((role) => {
        const isSelected = textValue(role.id) === selectedRoleId
        return {
          ...role,
          isSystemRole: isSelected || role.isSystemRole === true,
          isInitialAdminRole: isSelected
        }
      }),
      authorization_requirements: {
        ...authorization,
        initialAdminRoleId: selectedRoleId
      }
    })
  }

  // 为权限章节追加一个空的业务候选，只要求填写业务语义，不要求技术绑定。
  const addItem = (field: AuthorizationListField): void => {
    if (field === 'restrictedPages') {
      replaceList(field, [
        ...recordList(authorization[field]),
        {
          name: '新受控页面',
          description: '',
          rationale: '',
          defaultGrantedRoleIds: [],
          sourceRefs: ['RequirementSpec 确认修改']
        }
      ])
      return
    }
    if (field === 'restrictedOperations') {
      replaceList(field, [
        ...recordList(authorization[field]),
        {
          name: '新受控操作',
          description: '',
          rationale: '',
          defaultGrantedRoleIds: [],
          sourceRefs: ['RequirementSpec 确认修改']
        }
      ])
      return
    }
    replaceList(field, [
      ...recordList(authorization[field]),
      {
        name: '新数据范围',
        description: '',
        includes: '',
        excludes: '',
        defaultGrantedRoleIds: [],
        sourceRefs: ['RequirementSpec 确认修改']
      }
    ])
  }

  return (
    <section className={cx('requirement-editor-section')}>
      <header>
        <span className={cx('requirement-summary-section-icon')}>
          <SafetyCertificateOutlined />
        </span>
        <Title level={5}>权限需求</Title>
      </header>
      <Text type="secondary">
        只保留用户需求明确提及的业务页面、操作和数据范围；未提及的候选保持为空，不进行 RBAC
        资源控制。页面和操作入口对无权限成员固定隐藏，直接访问固定返回 403。
      </Text>
      <div className={cx('requirement-editor-field')}>
        <Text>涉及应用级资源授权（由新建应用设置决定）</Text>
        <Switch checked={enabled} disabled />
      </div>
      {enabled ? (
        <>
          <div className={cx('requirement-editor-field')}>
            <Text>初始系统管理员角色</Text>
            <Select
              onChange={updateInitialAdminRole}
              options={[
                ...roleOptions,
                { label: '新建独立系统管理员', value: '__create_system_administrator__' }
              ]}
              value={textValue(authorization.initialAdminRoleId)}
            />
          </div>
          <div className={cx('requirement-editor-grid')}>
            <section className={cx('requirement-editor-section')}>
              <header>
                <Title level={5}>受控页面</Title>
                <Button
                  icon={<PlusOutlined />}
                  onClick={() => addItem('restrictedPages')}
                  size="small"
                  type="text"
                >
                  新增
                </Button>
              </header>
              {recordList(authorization.restrictedPages).map((item, index) => (
                <AuthorizationItem
                  key={`page-${index}`}
                  onRemove={() => removeItem('restrictedPages', index)}
                >
                  <EditorField label="页面名称">
                    <Input
                      onChange={(event) =>
                        updateItem('restrictedPages', index, 'name', event.target.value)
                      }
                      value={textValue(item.name)}
                    />
                  </EditorField>
                  <EditorField label="业务说明">
                    <TextArea
                      autoSize={{ minRows: 2, maxRows: 4 }}
                      onChange={(event) =>
                        updateItem('restrictedPages', index, 'description', event.target.value)
                      }
                      value={textValue(item.description)}
                    />
                  </EditorField>
                  <EditorField label="限制理由">
                    <TextArea
                      autoSize={{ minRows: 2, maxRows: 4 }}
                      onChange={(event) =>
                        updateItem('restrictedPages', index, 'rationale', event.target.value)
                      }
                      value={textValue(item.rationale)}
                    />
                  </EditorField>
                  <EditorField label="首次默认授权角色">
                    <Select
                      mode="multiple"
                      onChange={(value) =>
                        updateItem('restrictedPages', index, 'defaultGrantedRoleIds', value)
                      }
                      options={roleOptions}
                      value={Array.isArray(item.defaultGrantedRoleIds) ? item.defaultGrantedRoleIds : []}
                    />
                  </EditorField>
                </AuthorizationItem>
              ))}
            </section>
            <section className={cx('requirement-editor-section')}>
              <header>
                <Title level={5}>受控操作</Title>
                <Button
                  icon={<PlusOutlined />}
                  onClick={() => addItem('restrictedOperations')}
                  size="small"
                  type="text"
                >
                  新增
                </Button>
              </header>
              {recordList(authorization.restrictedOperations).map((item, index) => (
                <AuthorizationItem
                  key={`operation-${index}`}
                  onRemove={() => removeItem('restrictedOperations', index)}
                >
                  <EditorField label="操作名称">
                    <Input
                      onChange={(event) =>
                        updateItem('restrictedOperations', index, 'name', event.target.value)
                      }
                      value={textValue(item.name)}
                    />
                  </EditorField>
                  <EditorField label="业务说明">
                    <TextArea
                      autoSize={{ minRows: 2, maxRows: 4 }}
                      onChange={(event) =>
                        updateItem('restrictedOperations', index, 'description', event.target.value)
                      }
                      value={textValue(item.description)}
                    />
                  </EditorField>
                  <EditorField label="限制理由">
                    <TextArea
                      autoSize={{ minRows: 2, maxRows: 4 }}
                      onChange={(event) =>
                        updateItem('restrictedOperations', index, 'rationale', event.target.value)
                      }
                      value={textValue(item.rationale)}
                    />
                  </EditorField>
                  <EditorField label="首次默认授权角色">
                    <Select
                      mode="multiple"
                      onChange={(value) =>
                        updateItem('restrictedOperations', index, 'defaultGrantedRoleIds', value)
                      }
                      options={roleOptions}
                      value={Array.isArray(item.defaultGrantedRoleIds) ? item.defaultGrantedRoleIds : []}
                    />
                  </EditorField>
                </AuthorizationItem>
              ))}
            </section>
          </div>
          <section className={cx('requirement-editor-section')}>
            <header>
              <Title level={5}>数据范围</Title>
              <Button
                icon={<PlusOutlined />}
                onClick={() => addItem('dataRules')}
                size="small"
                type="text"
              >
                新增
              </Button>
            </header>
            {recordList(authorization.dataRules).map((item, index) => (
              <AuthorizationItem
                key={`data-${index}`}
                onRemove={() => removeItem('dataRules', index)}
              >
                <EditorField label="业务对象名称">
                  <Input
                    onChange={(event) =>
                      updateItem('dataRules', index, 'name', event.target.value)
                    }
                    value={textValue(item.name)}
                  />
                </EditorField>
                <EditorField label="业务对象说明">
                  <TextArea
                    autoSize={{ minRows: 2, maxRows: 4 }}
                    onChange={(event) =>
                      updateItem('dataRules', index, 'description', event.target.value)
                    }
                    value={textValue(item.description)}
                  />
                </EditorField>
                <EditorField label="包含的数据">
                  <TextArea
                    autoSize={{ minRows: 2, maxRows: 4 }}
                    onChange={(event) =>
                      updateItem('dataRules', index, 'includes', event.target.value)
                    }
                    value={textValue(item.includes)}
                  />
                </EditorField>
                <EditorField label="不包含的数据">
                  <TextArea
                    autoSize={{ minRows: 2, maxRows: 4 }}
                    onChange={(event) =>
                      updateItem('dataRules', index, 'excludes', event.target.value)
                    }
                    value={textValue(item.excludes)}
                  />
                </EditorField>
                <EditorField label="首次默认授权角色">
                  <Select
                    mode="multiple"
                    onChange={(value) =>
                      updateItem('dataRules', index, 'defaultGrantedRoleIds', value)
                    }
                    options={roleOptions}
                    value={Array.isArray(item.defaultGrantedRoleIds) ? item.defaultGrantedRoleIds : []}
                  />
                </EditorField>
              </AuthorizationItem>
            ))}
          </section>
        </>
      ) : (
        <Text type="secondary">不涉及应用级资源授权；候选规则会保持为空。</Text>
      )}
    </section>
  )
}
