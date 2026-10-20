import {
  AppstoreOutlined,
  BankOutlined,
  BgColorsOutlined,
  CloudOutlined,
  DashboardOutlined,
  DesktopOutlined,
  FolderOpenOutlined,
  FundOutlined,
  GithubOutlined,
  MenuOutlined,
  MessageOutlined,
  ShopOutlined,
  ShoppingOutlined,
  TeamOutlined,
  ToolOutlined,
  UserOutlined
} from '@ant-design/icons'
import { Button, Form, Input, Radio, Typography } from 'antd'
import type { FormInstance } from 'antd'
import type { ReactElement, ReactNode } from 'react'
import type { ApplicationDraft } from '../../typings'
import { validateBranchName, validateRepoUrl } from '../../service/repositoryBranch'
import { cx } from '../../utils'
import {
  applicationIconOptions,
  defaultRepoUrl,
  initialApplicationDraft,
  terminalLabels
} from './constants'
import { TabHintInput } from './components/TabHintInput'
import DatasourceConfigFields from './DatasourceConfigFields'
import ApplicationConfigEditor from './ApplicationConfigEditor'

const { TextArea } = Input
const { Text } = Typography

/** 新建应用可直接选用的主题色，默认紫保持现有默认值。 */
const themeColorPresets = [
  { label: '默认紫', value: '#6b3cf0' },
  { label: '招商红', value: '#e60012' },
  { label: '蓝色', value: '#1677ff' }
] as const

const iconComponents: Record<string, typeof AppstoreOutlined> = {
  AppstoreOutlined,
  DesktopOutlined,
  DashboardOutlined,
  ShopOutlined,
  ShoppingOutlined,
  TeamOutlined,
  UserOutlined,
  ToolOutlined,
  CloudOutlined,
  MessageOutlined,
  BankOutlined,
  FundOutlined
}

type Props = {
  form: FormInstance<ApplicationDraft>
  /** 设置页复用新建表单时，创建期字段仅展示不允许编辑。 */
  mode?: 'create' | 'settings'
  /** 设置页传入已持久化配置转换出的初始草稿。 */
  initialValues?: ApplicationDraft & { envVariables?: unknown }
  onSelectProjectParent?: () => void
  selectingParent?: boolean
  children?: ReactNode
}

/** 渲染应用创建表单的分组标题。 */
function SectionTitle({
  icon,
  children,
  className
}: {
  icon: ReactNode
  children: ReactNode
  className?: string
}): ReactElement {
  return (
    <div className={cx('application-form-section-title', className)}>
      <span className={cx('application-form-section-icon')}>{icon}</span>
      <span className={cx('application-form-section-text')}>{children}</span>
    </div>
  )
}

/** 渲染应用创建配置与项目目录提示。 */
export default function ApplicationForm({
  form,
  onSelectProjectParent,
  selectingParent = false,
  mode = 'create',
  initialValues,
  children
}: Props): ReactElement {
  const isSettingsMode = mode === 'settings'
  const menusEnabled = Form.useWatch(['menus', 'enable'], form) ?? true
  const themePrimaryColor = Form.useWatch(['theme', 'primaryColor'], form) ?? '#6b3cf0'
  // 只读字段没有注册为 Form.Item 时，直接回退到 application.json 对应的初始配置。
  const persistedValues = initialValues ?? initialApplicationDraft
  const projectPath = Form.useWatch('projectPath', form) ?? persistedValues.projectPath
  const terminal = Form.useWatch('terminal', form) ?? persistedValues.terminal
  const repoUrl = Form.useWatch('repoUrl', form) ?? persistedValues.repoUrl
  const branchName = Form.useWatch('branchName', form) ?? persistedValues.branchName
  const rootPath = Form.useWatch(['menus', 'rootPath'], form) ?? persistedValues.menus.rootPath

  /** 将选中的预设主题色写入应用表单。 */
  const applyThemeColor = (color: string): void => {
    form.setFields([{ name: ['theme', 'primaryColor'], value: color }])
  }

  return (
    <Form
      className={cx('application-form')}
      form={form}
      initialValues={initialValues ?? initialApplicationDraft}
      layout="vertical"
    >
      <section
        id={isSettingsMode ? 'settings-project' : undefined}
        className={cx('application-form-section', 'application-form-section--full')}
      >
        <SectionTitle className="application-form-section-title--with-content" icon={<FolderOpenOutlined />}>
          项目目录
        </SectionTitle>
        <div className={cx('application-form-section-description')}>
          在创建应用时选择，保存后无法修改。
        </div>
        <Form.Item
          extra={
            isSettingsMode
              ? undefined
              : '请输入一个新的项目目录，或选择一个空目录；已有 DevAgent Studio 应用目录不能复用。'
          }
        >
          {isSettingsMode ? (
            <Text className={cx('application-form-readonly-value')}>{projectPath || '—'}</Text>
          ) : (
            <Input.Group compact>
              <Form.Item
                name="projectPath"
                noStyle
                rules={[{ required: true, message: '请选择项目创建位置' }]}
              >
                <Input aria-label="项目目录" style={{ width: 'calc(100% - 132px)' }} />
              </Form.Item>
              <Button
                icon={<FolderOpenOutlined />}
                loading={selectingParent}
                onClick={onSelectProjectParent}
                style={{ width: 132 }}
              >
                选择文件夹
              </Button>
            </Input.Group>
          )}
        </Form.Item>
      </section>

      <section
        id={isSettingsMode ? 'settings-basic' : undefined}
        className={cx('application-form-section', 'application-form-section--full')}
      >
        <SectionTitle icon={<AppstoreOutlined />}>基础信息</SectionTitle>
        <div className={cx('application-form-grid')}>
          <Form.Item
            label="应用名称"
            name="appName"
            rules={[{ required: true, message: '请输入应用名称' }]}
          >
            <Input />
          </Form.Item>
          <Form.Item label="终端类型">
            {isSettingsMode ? (
              <Text className={cx('application-form-readonly-value')}>
                {terminalLabels[terminal] ?? terminal}
              </Text>
            ) : (
              <Form.Item name="terminal" noStyle>
                <Radio.Group>
                  {Object.entries(terminalLabels).map(([value, label]) => (
                    <Radio.Button key={value} value={value}>
                      {label}
                    </Radio.Button>
                  ))}
                </Radio.Group>
              </Form.Item>
            )}
          </Form.Item>
        </div>
        <Form.Item label="应用图标" name="appIcon">
          <Radio.Group
            className={cx('application-icon-picker')}
            optionType="button"
            buttonStyle="solid"
          >
            {applicationIconOptions.map((option) => {
              const Icon = iconComponents[option.value]
              return (
                <Radio.Button key={option.value} aria-label={option.label} value={option.value}>
                  {Icon ? <Icon /> : null}
                </Radio.Button>
              )
            })}
          </Radio.Group>
        </Form.Item>
        <Form.Item
          className={cx('application-form-scenario')}
          label="应用场景"
          name="senario"
          rules={[{ required: true, whitespace: true, message: '请输入应用场景' }]}
        >
          <TextArea rows={3} />
        </Form.Item>
        <Form.Item label="码云地址">
          {isSettingsMode ? (
            <Text className={cx('application-form-readonly-value')}>{repoUrl || '—'}</Text>
          ) : (
            <Form.Item
              name="repoUrl"
              noStyle
              rules={[
                {
                  validator: (_rule, value: string) => {
                    const invalidReason = validateRepoUrl(value)
                    return invalidReason ? Promise.reject(new Error(invalidReason)) : Promise.resolve()
                  }
                }
              ]}
            >
              <Input prefix={<GithubOutlined />} placeholder={defaultRepoUrl} />
            </Form.Item>
          )}
        </Form.Item>
        <Form.Item
          extra="应用代码会推送到远端的这个分支。如果远端已经有同名分支，它上面的代码会被覆盖。"
          label="版本号（对应码云仓库中的分支名）"
        >
          {isSettingsMode ? (
            <Text className={cx('application-form-readonly-value')}>{branchName || '—'}</Text>
          ) : (
            <Form.Item
              name="branchName"
              noStyle
              rules={[
                { required: true, whitespace: true, message: '请输入版本号' },
                {
                  validator: (_rule, value: string) => {
                    const invalidReason = validateBranchName(value)
                    return invalidReason ? Promise.reject(new Error(invalidReason)) : Promise.resolve()
                  }
                }
              ]}
            >
              <Input placeholder="v1_0" />
            </Form.Item>
          )}
        </Form.Item>
      </section>

      <section
        id={isSettingsMode ? 'settings-menu' : undefined}
        className={cx('application-form-section', 'application-form-section--full')}
      >
        <SectionTitle className="application-form-section-title--with-content" icon={<MenuOutlined />}>
          菜单根路由
        </SectionTitle>
        <div className={cx('application-form-section-description')}>
          用于确定应用菜单页面的统一访问路径前缀。
        </div>
        <Form.Item>
          {isSettingsMode ? (
            <Text className={cx('application-form-readonly-value')}>{rootPath || '—'}</Text>
          ) : (
            <Form.Item
              name={['menus', 'rootPath']}
              noStyle
              rules={[
                { required: true, message: '请输入页面根路由' },
                {
                  validator: (_rule, value: string) => {
                    if ((menusEnabled || authEnabled) && value === '/') {
                      return Promise.reject(new Error('启用默认菜单或认证时，根路由不能为 /'))
                    }
                    return Promise.resolve()
                  }
                }
              ]}
            >
              <TabHintInput
                aria-label="页面根路由"
                form={form}
                fieldName={['menus', 'rootPath']}
                placeholder="请输入页面根路由"
              />
            </Form.Item>
          )}
        </Form.Item>
      </section>

      <section
        id={isSettingsMode ? 'settings-theme' : undefined}
        className={cx('application-form-section', 'application-form-section--full')}
      >
        <SectionTitle className="application-form-section-title--with-content" icon={<BgColorsOutlined />}>
          主题
        </SectionTitle>
        <Form.Item>
          <Form.Item name={['theme', 'primaryColor']} hidden>
            <Input />
          </Form.Item>
          <div className={cx('theme-color-picker-row')}>
            <div className={cx('theme-color-presets')} role="group" aria-label="预设主题色">
              {themeColorPresets.map((preset) => (
                <button
                  aria-pressed={themePrimaryColor.toLowerCase() === preset.value}
                  className={cx('theme-color-preset')}
                  key={preset.value}
                  onClick={() => applyThemeColor(preset.value)}
                  title={preset.label}
                  type="button"
                >
                  <span
                    aria-hidden="true"
                    className={cx('theme-color-preset-swatch')}
                    style={{ backgroundColor: preset.value }}
                  />
                </button>
              ))}
            </div>
            <span className={cx('theme-color-custom-label')}>自定义</span>
            <div className={cx('theme-color-swatch-wrapper')}>
              <div
                className={cx('theme-color-swatch')}
                style={{ backgroundColor: themePrimaryColor }}
              />
              <input
                type="color"
                aria-label="自定义主题色"
                className={cx('theme-color-input')}
                value={themePrimaryColor}
                onChange={(e) => {
                  const color = e.target.value
                  form.setFields([{ name: ['theme', 'primaryColor'], value: color }])
                }}
              />
            </div>
            <Input
              placeholder="自定义主题色色值，如：#6b3cf0"
              style={{ flex: 1 }}
              value={themePrimaryColor || ''}
              onChange={(e) => {
                const color = e.target.value
                form.setFields([{ name: ['theme', 'primaryColor'], value: color }])
              }}
            />
          </div>
        </Form.Item>
      </section>

      <DatasourceConfigFields form={form} />

      <ApplicationConfigEditor form={form} mode={mode} />

      {children}

    </Form>
  )
}
