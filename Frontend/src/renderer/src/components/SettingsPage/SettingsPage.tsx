import {
  AppstoreOutlined,
  BankOutlined,
  CloudOutlined,
  CodeOutlined,
  DashboardOutlined,
  DeleteOutlined,
  DesktopOutlined,
  FundOutlined,
  LayoutOutlined,
  LockOutlined,
  MessageOutlined,
  PlusOutlined,
  RadarChartOutlined,
  SaveOutlined,
  SettingOutlined,
  ShopOutlined,
  ShoppingOutlined,
  TeamOutlined,
  ToolOutlined,
  UserOutlined
} from '@ant-design/icons'
import {
  Anchor,
  AutoComplete,
  Button,
  Form,
  Input,
  Radio,
  Switch,
  Typography,
  message
} from 'antd'
import type { ReactElement, ReactNode } from 'react'
import { useMemo, useState } from 'react'
import type { ApplicationConfig } from '../../typings'
import { cx } from '../../utils'
import { applicationIconOptions, trackMethodOptions } from '../Welcome/constants'
import { saveApplication } from '../Welcome/applicationService'
import './SettingsPage.less'

const { Title, Text } = Typography

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

type EnvVariable = {
  key: string
  devValue: string
  prodValue: string
  encrypted: boolean
}

type Props = {
  application: ApplicationConfig
  onSaved: (application: ApplicationConfig) => void
}

type SettingsFormValues = Pick<
  ApplicationConfig,
  'appName' | 'appIcon' | 'senario' | 'layout' | 'auth' | 'track' | 'apiTrack'
> & {
  envVariables: EnvVariable[]
}

function SettingsCard({
  icon,
  title,
  children,
  extra,
  disabled,
  id,
  compact
}: {
  icon: ReactNode
  title: string
  children: ReactNode
  extra?: ReactNode
  disabled?: boolean
  id?: string
  compact?: boolean
}) {
  return (
    <section id={id} className={cx('settings-card', disabled && 'settings-card--disabled')}>
      <header className={cx('settings-card-header')}>
        <span className={cx('settings-card-title')}>
          <span className={cx('settings-card-icon')}>{icon}</span>
          <Text strong>{title}</Text>
        </span>
        {extra ? <span className={cx('settings-card-extra')}>{extra}</span> : null}
      </header>
      <div className={cx('settings-card-body', compact && 'settings-card-body--compact')}>
        {children}
      </div>
    </section>
  )
}

/** 组织并保存应用级基础能力与环境配置。 */
export default function SettingsPage({ application, onSaved }: Props): ReactElement {
  const [form] = Form.useForm<SettingsFormValues>()
  const [saving, setSaving] = useState(false)

  // antd v4 的 Form.useWatch 在 Form 挂载前可能返回 undefined，用 getFieldValue 兜底更安全
  const authEnabled = Form.useWatch(['auth', 'enable'], form) ?? application?.auth?.enable ?? false
  const trackEnabled =
    Form.useWatch(['track', 'enable'], form) ?? application?.track?.enable ?? false
  const apiTrackEnabled =
    Form.useWatch(['apiTrack', 'enable'], form) ?? application?.apiTrack?.enable ?? false
  const useHeaderEnabled =
    Form.useWatch(['layout', 'useHeader'], form) ?? application?.layout?.useHeader ?? true
  const useFooterEnabled =
    Form.useWatch(['layout', 'useFooter'], form) ?? application?.layout?.useFooter ?? false
  // 环境变量 — 将 { dev:[], prod:[] } 合并为统一的 flat list
  const safeEnvVariables = useMemo<EnvVariable[]>(() => {
    const devVars = application?.environment?.dev ?? []
    const prodVars = application?.environment?.prod ?? []
    const maxLen = Math.max(devVars.length, prodVars.length)
    return Array.from({ length: maxLen }, (_, i) => ({
      key: devVars[i]?.key ?? prodVars[i]?.key ?? '',
      devValue: devVars[i]?.value ?? '',
      prodValue: prodVars[i]?.value ?? '',
      encrypted: devVars[i]?.encrypted ?? prodVars[i]?.encrypted ?? false
    }))
  }, [application?.environment?.dev, application?.environment?.prod])

  const envVars: EnvVariable[] = Form.useWatch('envVariables', form) ?? safeEnvVariables

  const [trackMethodSearch, setTrackMethodSearch] = useState('')
  const trackMethodFilteredOptions = useMemo(() => {
    const keyword = trackMethodSearch.trim().toLowerCase()
    if (!keyword) return trackMethodOptions
    return trackMethodOptions.filter((option) => option.value.toLowerCase().includes(keyword))
  }, [trackMethodSearch])

  const headerBar = useHeaderEnabled ? (
    <rect fill="#bfbfbf" height="6" rx="1" width="96" x="0" y="0" />
  ) : null
  const footerBar = useFooterEnabled ? (
    <rect fill="#bfbfbf" height="6" rx="1" width="96" x="0" y="58" />
  ) : null

  // 校验设置并通过统一应用保存入口加密数据库凭据。
  const handleSave = async (): Promise<void> => {
    try {
      const values = await form.validateFields()
      setSaving(true)
      const { envVariables, ...rest } = values
      const environment = {
        dev: (envVariables ?? []).map((v) => ({
          key: v.key,
          value: v.devValue,
          encrypted: v.encrypted
        })),
        prod: (envVariables ?? []).map((v) => ({
          key: v.key,
          value: v.prodValue,
          encrypted: v.encrypted
        }))
      }
      const updatedApplication: ApplicationConfig = {
        ...application,
        ...rest,
        environment,
        schema: { ...application.schema, ...rest, environment }
      }
      const persistedApplication = await saveApplication(updatedApplication)
      onSaved(persistedApplication)
      message.success('保存成功')
    } catch (error: any) {
      // antd validateFields 校验失败时返回 { errorFields, values }，非 Error 实例
      // errorFields 按字段注册顺序排列（静态字段先于 Form.List 动态字段），
      // 需要按 DOM 视觉位置（从上到下）找出第一个可见的错误
      if (error?.errorFields?.length) {
        // 扫描所有带 .ant-form-item-has-error 的 DOM 元素，按 Y 坐标排序
        const errorNodes = document.querySelectorAll('.ant-form-item-has-error')
        const byPosition = Array.from(errorNodes)
          .map((el) => ({ el, top: el.getBoundingClientRect().top }))
          .filter(({ top }) => top > 0) // 过滤 hidden 面板中的元素（top≈0）
          .sort((a, b) => a.top - b.top)

        const firstVisible = byPosition[0]?.el
        if (firstVisible) {
          const errorText = firstVisible.querySelector('.ant-form-item-explain-error')?.textContent
          if (errorText) {
            message.error(errorText)
          }
          firstVisible.scrollIntoView({ behavior: 'smooth', block: 'center' })
        } else {
          // 回退：无可视错误元素时用 errorFields[0]
          const firstErr = error.errorFields[0]?.errors?.[0]
          if (firstErr) message.error(firstErr)
        }
      } else if (error instanceof Error) {
        message.error(`保存失败：${error.message}`)
      }
    } finally {
      setSaving(false)
    }
  }

  // 兜底 initialValues，防止 application 字段缺失导致 Form 报错
  const safeLayout = application?.layout ?? { type: '', useHeader: true, useFooter: false }
  const safeAuth = application?.auth ?? { enable: false, authnSource: '', yht: { clientId: '' } }
  const safeTrack = application?.track ?? {
    enable: false,
    uploadId: '',
    apiHost: '',
    method: 'post'
  }
  const safeApiTrack = application?.apiTrack ?? {
    enable: false,
    businessId: '',
    traceBaggage: '',
    apiTrackHost: ''
  }
  return (
    <div className={cx('settings-page')}>
      <header className={cx('settings-page-header')}>
        <div className={cx('settings-page-title-line')}>
          <SettingOutlined className={cx('settings-page-title-icon')} />
          <Title level={4} style={{ margin: 0 }}>
            应用设置
          </Title>
        </div>
        <div className={cx('settings-page-header-right')}>
          <Button
            className={cx('settings-save-btn')}
            icon={<SaveOutlined />}
            loading={saving}
            onClick={handleSave}
          >
            保存设置
          </Button>
        </div>
      </header>

      <div className={cx('settings-page-body')}>
        <aside className={cx('settings-page-anchor')}>
          <Anchor
            affix={false}
            getCurrentAnchor={(active) => active || '#settings-basic'}
            onClick={(e, link) => {
              e.preventDefault()
              const el = document.querySelector(link.href)
              if (el) el.scrollIntoView({ behavior: 'smooth', block: 'start' })
            }}
          >
            <Anchor.Link
              href="#settings-basic"
              title={
                <span className={cx('settings-anchor-item')}>
                  <AppstoreOutlined />
                  <span>基础信息</span>
                </span>
              }
            />
            <Anchor.Link
              href="#settings-layout"
              title={
                <span className={cx('settings-anchor-item')}>
                  <LayoutOutlined />
                  <span>导航模式</span>
                </span>
              }
            />
            <Anchor.Link
              href="#settings-auth"
              title={
                <span className={cx('settings-anchor-item')}>
                  <LockOutlined />
                  <span>认证</span>
                </span>
              }
            />
            <Anchor.Link
              href="#settings-page-track"
              title={
                <span className={cx('settings-anchor-item')}>
                  <RadarChartOutlined />
                  <span>页面埋点</span>
                </span>
              }
            />
            <Anchor.Link
              href="#settings-api-track"
              title={
                <span className={cx('settings-anchor-item')}>
                  <RadarChartOutlined />
                  <span>接口埋点</span>
                </span>
              }
            />
            <Anchor.Link
              href="#settings-environment"
              title={
                <span className={cx('settings-anchor-item')}>
                  <CodeOutlined />
                  <span>环境变量</span>
                </span>
              }
            />
          </Anchor>
        </aside>
        <div className={cx('settings-page-scroll')}>
          <Form
            form={form}
            layout="horizontal"
            initialValues={{
              appName: application.appName ?? '',
              appIcon: application.appIcon ?? 'AppstoreOutlined',
              senario: application.senario ?? '',
              layout: safeLayout,
              auth: safeAuth,
              track: safeTrack,
              apiTrack: safeApiTrack,
              envVariables: safeEnvVariables
            }}
            labelCol={{ flex: '0 0 170px' }}
            wrapperCol={{ flex: 'auto' }}
            className={cx('settings-form')}
          >
            <SettingsCard id="settings-basic" icon={<AppstoreOutlined />} title="基础信息">
              <Form.Item
                label="应用名称"
                name="appName"
                rules={[{ required: true, message: '请输入应用名称' }]}
              >
                <Input />
              </Form.Item>
              <Form.Item label="应用图标" name="appIcon">
                <Radio.Group
                  className={cx('settings-icon-picker')}
                  optionType="button"
                  buttonStyle="solid"
                >
                  {applicationIconOptions.map((option) => {
                    const Icon = iconComponents[option.value]
                    return (
                      <Radio.Button
                        key={option.value}
                        aria-label={option.label}
                        value={option.value}
                      >
                        {Icon ? <Icon /> : null}
                      </Radio.Button>
                    )
                  })}
                </Radio.Group>
              </Form.Item>
              <Form.Item label="应用场景" name="senario">
                <Input.TextArea rows={2} style={{ resize: 'vertical' }} />
              </Form.Item>
            </SettingsCard>

            <SettingsCard id="settings-layout" icon={<LayoutOutlined />} title="导航模式">
              <Form.Item label="布局方式" name={['layout', 'type']}>
                <Radio.Group
                  className={cx('settings-nav-picker')}
                  optionType="button"
                  buttonStyle="solid"
                >
                  <Radio.Button value="side">
                    <span className={cx('settings-nav-preview')}>
                      <svg height="52" viewBox="0 0 96 64" width="78">
                        <defs>
                          <clipPath id="settings-nav-side-clip">
                            <rect height="64" rx="5" width="96" x="0" y="0" />
                          </clipPath>
                        </defs>
                        <g clipPath="url(#settings-nav-side-clip)">
                          <rect fill="#fff" height="64" width="96" x="0" y="0" />
                          <rect fill="#2c2c2c" height="64" width="24" x="0" y="0" />
                          <rect fill="#444" height="3" rx="1.5" width="14" x="5" y="9" />
                          <rect fill="#555" height="2" rx="1" width="8" x="5" y="15" />
                          <rect fill="#444" height="3" rx="1.5" width="14" x="5" y="22" />
                          <rect fill="#555" height="2" rx="1" width="6" x="5" y="28" />
                          <rect fill="#444" height="3" rx="1.5" width="14" x="5" y="35" />
                          <rect fill="#555" height="2" rx="1" width="9" x="5" y="41" />
                          <rect fill="#444" height="3" rx="1.5" width="14" x="5" y="48" />
                          <rect fill="#555" height="2" rx="1" width="7" x="5" y="54" />
                          {headerBar}
                          {footerBar}
                        </g>
                        <rect
                          fill="none"
                          height="64"
                          rx="5"
                          stroke="#d9d9d9"
                          strokeWidth="1"
                          width="96"
                          x="0"
                          y="0"
                        />
                      </svg>
                      <span className={cx('settings-nav-label')}>左侧导航</span>
                    </span>
                  </Radio.Button>
                  <Radio.Button value="top">
                    <span className={cx('settings-nav-preview')}>
                      <svg height="52" viewBox="0 0 96 64" width="78">
                        <defs>
                          <clipPath id="settings-nav-top-clip">
                            <rect height="64" rx="5" width="96" x="0" y="0" />
                          </clipPath>
                        </defs>
                        <g clipPath="url(#settings-nav-top-clip)">
                          <rect fill="#fff" height="64" width="96" x="0" y="0" />
                          <rect fill="#2c2c2c" height="16" width="96" x="0" y="0" />
                          {headerBar}
                          {footerBar}
                        </g>
                        <rect
                          fill="none"
                          height="64"
                          rx="5"
                          stroke="#d9d9d9"
                          strokeWidth="1"
                          width="96"
                          x="0"
                          y="0"
                        />
                      </svg>
                      <span className={cx('settings-nav-label')}>顶部导航</span>
                    </span>
                  </Radio.Button>
                  <Radio.Button value="mix">
                    <span className={cx('settings-nav-preview')}>
                      <svg height="52" viewBox="0 0 96 64" width="78">
                        <defs>
                          <clipPath id="settings-nav-mix-clip">
                            <rect height="64" rx="5" width="96" x="0" y="0" />
                          </clipPath>
                        </defs>
                        <g clipPath="url(#settings-nav-mix-clip)">
                          <rect fill="#fff" height="64" width="96" x="0" y="0" />
                          <rect fill="#2c2c2c" height="14" width="96" x="0" y="0" />
                          <rect fill="#2c2c2c" height="50" width="24" x="0" y="14" />
                          <rect fill="#444" height="3" rx="1.5" width="14" x="5" y="21" />
                          <rect fill="#555" height="2" rx="1" width="8" x="5" y="27" />
                          <rect fill="#444" height="3" rx="1.5" width="14" x="5" y="33" />
                          <rect fill="#555" height="2" rx="1" width="7" x="5" y="39" />
                          <rect fill="#444" height="3" rx="1.5" width="14" x="5" y="45" />
                          <rect fill="#555" height="2" rx="1" width="9" x="5" y="51" />
                          {headerBar}
                          {footerBar}
                        </g>
                        <rect
                          fill="none"
                          height="64"
                          rx="5"
                          stroke="#d9d9d9"
                          strokeWidth="1"
                          width="96"
                          x="0"
                          y="0"
                        />
                      </svg>
                      <span className={cx('settings-nav-label')}>混合导航</span>
                    </span>
                  </Radio.Button>
                </Radio.Group>
              </Form.Item>
              <div className={cx('settings-nav-toggles')}>
                <Form.Item label="开启头部" name={['layout', 'useHeader']} valuePropName="checked">
                  <Switch checkedChildren="开启" unCheckedChildren="关闭" />
                </Form.Item>
                <Form.Item label="开启底部" name={['layout', 'useFooter']} valuePropName="checked">
                  <Switch checkedChildren="开启" unCheckedChildren="关闭" />
                </Form.Item>
              </div>
            </SettingsCard>

            <SettingsCard
              id="settings-auth"
              icon={<LockOutlined />}
              title="认证"
              compact
              disabled={!authEnabled}
              extra={
                <Form.Item name={['auth', 'enable']} valuePropName="checked" noStyle>
                  <Switch checkedChildren="开启" unCheckedChildren="关闭" />
                </Form.Item>
              }
            >
              <Form.Item
                label="认证来源"
                name={['auth', 'authnSource']}
                rules={[{ required: authEnabled, message: '请填写认证来源' }]}
              >
                <Input disabled={!authEnabled} />
              </Form.Item>
              <Form.Item
                label="一号通clientId"
                name={['auth', 'yht', 'clientId']}
                rules={[{ required: authEnabled, message: '请填写clientId' }]}
              >
                <Input disabled={!authEnabled} />
              </Form.Item>
            </SettingsCard>

            <SettingsCard
              id="settings-page-track"
              icon={<RadarChartOutlined />}
              title="页面埋点"
              compact
              disabled={!trackEnabled}
              extra={
                <Form.Item name={['track', 'enable']} valuePropName="checked" noStyle>
                  <Switch checkedChildren="开启" unCheckedChildren="关闭" />
                </Form.Item>
              }
            >
              <Form.Item
                label="上传标识"
                name={['track', 'uploadId']}
                rules={[{ required: trackEnabled, message: '请填写上传标识' }]}
              >
                <Input disabled={!trackEnabled} />
              </Form.Item>
              <Form.Item label="上报地址" name={['track', 'apiHost']}>
                <Input disabled={!trackEnabled} />
              </Form.Item>
              <Form.Item label="请求方式" name={['track', 'method']}>
                <AutoComplete
                  allowClear={false}
                  defaultActiveFirstOption={false}
                  disabled={!trackEnabled}
                  filterOption={false}
                  onSearch={setTrackMethodSearch}
                  onSelect={() => setTrackMethodSearch('')}
                  options={trackMethodFilteredOptions}
                  placeholder="post"
                />
              </Form.Item>
            </SettingsCard>

            <SettingsCard
              id="settings-api-track"
              icon={<RadarChartOutlined />}
              title="接口埋点"
              compact
              disabled={!apiTrackEnabled}
              extra={
                <Form.Item name={['apiTrack', 'enable']} valuePropName="checked" noStyle>
                  <Switch checkedChildren="开启" unCheckedChildren="关闭" />
                </Form.Item>
              }
            >
              <Form.Item
                label="业务标识"
                name={['apiTrack', 'businessId']}
                rules={[{ required: apiTrackEnabled, message: '请填写业务标识' }]}
              >
                <Input disabled={!apiTrackEnabled} />
              </Form.Item>
              <Form.Item label="链路透传信息" name={['apiTrack', 'traceBaggage']}>
                <Input disabled={!apiTrackEnabled} />
              </Form.Item>
              <Form.Item label="接口埋点地址" name={['apiTrack', 'apiTrackHost']}>
                <Input disabled={!apiTrackEnabled} />
              </Form.Item>
            </SettingsCard>

            <SettingsCard id="settings-environment" icon={<CodeOutlined />} title="环境变量">
              {/* 第一行：开发/生产环境标签 — 纯展示 */}
              <div className={cx('settings-env-labels')}>
                <span className={cx('settings-env-labels-key')} />
                <div className={cx('settings-env-labels-key')}>
                  <span className={cx('settings-env-badge', 'settings-env-badge--dev')}>
                    <Text strong>开发环境</Text>
                  </span>
                </div>
                <div className={cx('settings-env-labels-key')}>
                  <span className={cx('settings-env-badge', 'settings-env-badge--prod')}>
                    <Text strong>生产环境</Text>
                  </span>
                </div>
                <span className={cx('settings-env-var-header-spacer')} />
              </div>

              {/* 第二行：变量名 / 变量值 列头 */}
              <div className={cx('settings-env-labels')}>
                <span className={cx('settings-env-labels-key')}>变量名</span>
                <span className={cx('settings-env-labels-key')}>变量值</span>
                <span className={cx('settings-env-labels-key')}>变量值</span>
                <span className={cx('settings-env-var-header-spacer')} />
              </div>

              <Form.List name="envVariables">
                {(fields, { add, remove }) => (
                  <>
                    <div className={cx('settings-env-var-scroll')}>
                      {fields.length === 0 ? (
                        <div className={cx('settings-env-var-empty')}>
                          <Text type="secondary">暂无数据</Text>
                        </div>
                      ) : (
                        fields.map(({ key, name, ...restField }, index) => {
                          const encrypted = envVars[index]?.encrypted ?? false

                          return (
                            <div key={key} className={cx('settings-env-var-row')}>
                              <Form.Item
                                {...restField}
                                className={cx('settings-env-var-field-item')}
                                name={[name, 'key']}
                                rules={[
                                  { required: true, message: '请输入变量名' },
                                  {
                                    pattern: /^[A-Z][A-Z0-9_]*$/,
                                    message: '仅限大写字母、下划线和数字，且大写字母开头'
                                  }
                                ]}
                              >
                                <Input
                                  placeholder="变量名，如NAME_1"
                                  prefix={
                                    encrypted ? (
                                      <LockOutlined className={cx('settings-env-var-lock')} />
                                    ) : undefined
                                  }
                                />
                              </Form.Item>
                              <Form.Item
                                {...restField}
                                className={cx('settings-env-var-field-item')}
                                name={[name, 'devValue']}
                              >
                                {encrypted ? (
                                  <Input.Password placeholder="值" visibilityToggle />
                                ) : (
                                  <Input placeholder="值" />
                                )}
                              </Form.Item>
                              <Form.Item
                                {...restField}
                                className={cx('settings-env-var-field-item')}
                                name={[name, 'prodValue']}
                              >
                                {encrypted ? (
                                  <Input.Password placeholder="值" visibilityToggle />
                                ) : (
                                  <Input placeholder="值" />
                                )}
                              </Form.Item>
                              <Button
                                className={cx('settings-env-var-delete')}
                                danger
                                icon={<DeleteOutlined />}
                                onClick={() => remove(name)}
                                type="text"
                              />
                            </div>
                          )
                        })
                      )}
                    </div>

                    <div className={cx('settings-env-actions')}>
                      <Button
                        icon={<PlusOutlined />}
                        onClick={() =>
                          add({ key: '', devValue: '', prodValue: '', encrypted: false })
                        }
                      >
                        新增环境变量
                      </Button>
                      <Button
                        icon={<PlusOutlined />}
                        onClick={() =>
                          add({ key: '', devValue: '', prodValue: '', encrypted: true })
                        }
                      >
                        新增加密环境变量
                      </Button>
                    </div>
                  </>
                )}
              </Form.List>
            </SettingsCard>

          </Form>
        </div>
      </div>
    </div>
  )
}
