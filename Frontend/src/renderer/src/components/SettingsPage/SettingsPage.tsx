import {
  AppstoreOutlined,
  BankOutlined,
  CheckCircleFilled,
  CloudOutlined,
  DashboardOutlined,
  DatabaseOutlined,
  DesktopOutlined,
  FundOutlined,
  LayoutOutlined,
  LinkOutlined,
  LockOutlined,
  MessageOutlined,
  RadarChartOutlined,
  SafetyCertificateOutlined,
  SaveOutlined,
  SettingOutlined,
  ShopOutlined,
  ShoppingOutlined,
  TeamOutlined,
  ToolOutlined,
  UserOutlined
} from '@ant-design/icons'
import type { AntdIconProps } from '@ant-design/icons/lib/components/AntdIcon'
import { Anchor, AutoComplete, Button, Form, Input, Radio, Switch, Typography, message } from 'antd'
import type { ReactElement, ReactNode } from 'react'
import { useMemo, useState, type ComponentType } from 'react'
import type { ApplicationConfig } from '../../typings'
import { cx } from '../../utils'
import { applicationIconOptions, trackMethodOptions } from '../Welcome/constants'
import { saveApplication } from '../Welcome/applicationService'
import './SettingsPage.less'

const { Title, Text } = Typography

const iconComponents: Record<string, ComponentType<AntdIconProps>> = {
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
  application: ApplicationConfig
  onSaved: (application: ApplicationConfig) => void
}

type SettingsFormValues = Pick<
  ApplicationConfig,
  'appName' | 'appIcon' | 'senario' | 'layout' | 'auth' | 'track' | 'apiTrack' | 'database'
>

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
      <div className={cx('settings-card-body', compact && 'settings-card-body--compact')}>{children}</div>
    </section>
  )
}

export default function SettingsPage({ application, onSaved }: Props): ReactElement {
  const [form] = Form.useForm<SettingsFormValues>()
  const [saving, setSaving] = useState(false)

  // antd v4 的 Form.useWatch 在 Form 挂载前可能返回 undefined，用 getFieldValue 兜底更安全
  const authEnabled = Form.useWatch(['auth', 'enable'], form) ?? application?.auth?.enable ?? false
  const trackEnabled = Form.useWatch(['track', 'enable'], form) ?? application?.track?.enable ?? false
  const apiTrackEnabled = Form.useWatch(['apiTrack', 'enable'], form) ?? application?.apiTrack?.enable ?? false
  const useHeaderEnabled = Form.useWatch(['layout', 'useHeader'], form) ?? application?.layout?.useHeader ?? true
  const useFooterEnabled = Form.useWatch(['layout', 'useFooter'], form) ?? application?.layout?.useFooter ?? false
  const dbConnectionMode = Form.useWatch(['database', 'connectionMode'], form) ?? application?.database?.connectionMode ?? 'dbid'

  const [trackMethodSearch, setTrackMethodSearch] = useState('')
  const trackMethodFilteredOptions = useMemo(() => {
    const keyword = trackMethodSearch.trim().toLowerCase()
    if (!keyword) return trackMethodOptions
    return trackMethodOptions.filter((option) =>
      option.value.toLowerCase().includes(keyword)
    )
  }, [trackMethodSearch])

  const headerBar = useHeaderEnabled ? (
    <rect fill="#bfbfbf" height="6" rx="1" width="96" x="0" y="0" />
  ) : null
  const footerBar = useFooterEnabled ? (
    <rect fill="#bfbfbf" height="6" rx="1" width="96" x="0" y="58" />
  ) : null

  const handleSave = async (): Promise<void> => {
    try {
      const values = await form.validateFields()
      setSaving(true)
      const updatedApplication: ApplicationConfig = {
        ...application,
        ...values,
        schema: { ...application.schema, ...values }
      }
      await saveApplication(updatedApplication)
      onSaved(updatedApplication)
      message.success('保存成功')
    } catch (error) {
      if (error instanceof Error) {
        message.error(`保存失败：${error.message}`)
      }
    } finally {
      setSaving(false)
    }
  }

  // 兜底 initialValues，防止 application 字段缺失导致 Form 报错
  const safeLayout = application?.layout ?? { type: '', useHeader: true, useFooter: false }
  const safeAuth = application?.auth ?? { enable: false, authnSource: '', yht: { clientId: '' } }
  const safeTrack = application?.track ?? { enable: false, uploadId: '', apiHost: '', method: 'post' }
  const safeApiTrack = application?.apiTrack ?? { enable: false, businessId: '', traceBaggage: '', apiTrackHost: '' }
  const safeDatabase = application?.database ?? {
    connectionMode: 'dbid' as const,
    schema: '',
    devDbid: '',
    prodDbid: '',
    host: '',
    port: '',
    username: '',
    password: ''
  }

  return (
    <div className={cx('settings-page')}>
      <header className={cx('settings-page-header')}>
        <div className={cx('settings-page-title-line')}>
          <SettingOutlined className={cx('settings-page-title-icon')} />
          <Title level={4} style={{ margin: 0 }}>应用设置</Title>
        </div>
        <div className={cx('settings-page-header-right')}>
          <Button
            className={cx('settings-save-btn')}
            icon={<SaveOutlined />}
            loading={saving}
            onClick={handleSave}
            type="primary"
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
            <Anchor.Link href="#settings-basic" title={<span className={cx('settings-anchor-item')}><AppstoreOutlined /><span>基础信息</span></span>} />
            <Anchor.Link href="#settings-layout" title={<span className={cx('settings-anchor-item')}><LayoutOutlined /><span>导航模式</span></span>} />
            <Anchor.Link href="#settings-auth" title={<span className={cx('settings-anchor-item')}><LockOutlined /><span>认证</span></span>} />
            <Anchor.Link href="#settings-page-track" title={<span className={cx('settings-anchor-item')}><RadarChartOutlined /><span>页面埋点</span></span>} />
            <Anchor.Link href="#settings-api-track" title={<span className={cx('settings-anchor-item')}><RadarChartOutlined /><span>接口埋点</span></span>} />
            <Anchor.Link href="#settings-database" title={<span className={cx('settings-anchor-item')}><DatabaseOutlined /><span>数据库</span></span>} />
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
            database: safeDatabase
          }}
          labelCol={{ flex: '0 0 155px' }}
          wrapperCol={{ flex: 'auto' }}
          className={cx('settings-form')}
        >
          <SettingsCard id="settings-basic" icon={<AppstoreOutlined />} title="基础信息">
            <Form.Item label="应用名称" name="appName" rules={[{ required: true, message: '请输入应用名称' }]}>
              <Input />
            </Form.Item>
            <Form.Item label="应用图标" name="appIcon">
              <Radio.Group className={cx('settings-icon-picker')} optionType="button" buttonStyle="solid">
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
            <Form.Item label="应用场景" name="senario">
              <Input.TextArea rows={2} style={{ resize: 'vertical' }} />
            </Form.Item>
          </SettingsCard>

          <SettingsCard id="settings-layout" icon={<LayoutOutlined />} title="导航模式">
            <Form.Item label="布局方式" name={['layout', 'type']}>
              <Radio.Group className={cx('settings-nav-picker')} optionType="button" buttonStyle="solid">
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
                      <rect fill="none" height="64" rx="5" stroke="#d9d9d9" strokeWidth="1" width="96" x="0" y="0" />
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
                      <rect fill="none" height="64" rx="5" stroke="#d9d9d9" strokeWidth="1" width="96" x="0" y="0" />
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
                      <rect fill="none" height="64" rx="5" stroke="#d9d9d9" strokeWidth="1" width="96" x="0" y="0" />
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
            <Form.Item label="认证来源" name={['auth', 'authnSource']} rules={[{ required: authEnabled, message: '请填写认证来源' }]}>
              <Input disabled={!authEnabled} />
            </Form.Item>
            <Form.Item label="一号通clientId" name={['auth', 'yht', 'clientId']} rules={[{ required: authEnabled, message: '请填写clientId' }]}>
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
            <Form.Item label="上传标识" name={['track', 'uploadId']} rules={[{ required: trackEnabled, message: '请填写上传标识' }]}>
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
            <Form.Item label="业务标识" name={['apiTrack', 'businessId']} rules={[{ required: apiTrackEnabled, message: '请填写业务标识' }]}>
              <Input disabled={!apiTrackEnabled} />
            </Form.Item>
            <Form.Item label="链路透传信息" name={['apiTrack', 'traceBaggage']}>
              <Input disabled={!apiTrackEnabled} />
            </Form.Item>
            <Form.Item label="接口埋点地址" name={['apiTrack', 'apiTrackHost']}>
              <Input disabled={!apiTrackEnabled} />
            </Form.Item>
          </SettingsCard>

          <SettingsCard id="settings-database" icon={<DatabaseOutlined />} title="数据库">
            <Form.Item label="数据库类型">
              <div style={{ lineHeight: '22px' }}>
                <Text>TDSQL</Text>
                <br />
                <Text type="secondary" style={{ fontSize: 12 }}>符合我行国产化验收标准</Text>
              </div>
            </Form.Item>

            <Form.Item
              label="连接方式"
              name={['database', 'connectionMode']}
              rules={[{ required: true, message: '请选择连接方式' }]}
            >
              <div className={cx('settings-db-mode-cards')}>
                <div
                  className={cx('settings-db-mode-card', dbConnectionMode === 'dbid' && 'settings-db-mode-card--selected')}
                  onClick={() => form.setFieldValue(['database', 'connectionMode'], 'dbid')}
                >
                  {dbConnectionMode === 'dbid' && (
                    <CheckCircleFilled className={cx('settings-db-mode-check')} />
                  )}
                  <SafetyCertificateOutlined className={cx('settings-db-mode-icon')} />
                  <div className={cx('settings-db-mode-body')}>
                    <Text strong className={cx('settings-db-mode-title')}>DBID密码服务</Text>
                    <Text type="secondary" className={cx('settings-db-mode-desc')}>安全连接方式，须通过审批流程获取</Text>
                  </div>
                </div>
                <div
                  className={cx('settings-db-mode-card', dbConnectionMode === 'connectionString' && 'settings-db-mode-card--selected')}
                  onClick={() => form.setFieldValue(['database', 'connectionMode'], 'connectionString')}
                >
                  {dbConnectionMode === 'connectionString' && (
                    <CheckCircleFilled className={cx('settings-db-mode-check')} />
                  )}
                  <LinkOutlined className={cx('settings-db-mode-icon')} />
                  <div className={cx('settings-db-mode-body')}>
                    <Text strong className={cx('settings-db-mode-title')}>数据库连接字符串</Text>
                    <Text type="secondary" className={cx('settings-db-mode-desc')}>传统连接方式，通过环境变量配置</Text>
                  </div>
                </div>
              </div>
            </Form.Item>

            {dbConnectionMode === 'dbid' ? (
              <>
                <Form.Item
                  label="数据库名称(Schema名)"
                  name={['database', 'schema']}
                  rules={[{ required: true, message: '请输入数据库名称' }]}
                >
                  <Input />
                </Form.Item>
                <Form.Item
                  label="开发环境DBID"
                  name={['database', 'devDbid']}
                  rules={[{ required: true, message: '请输入开发环境DBID' }]}
                >
                  <Input />
                </Form.Item>
                <Form.Item
                  label="生产环境DBID"
                  name={['database', 'prodDbid']}
                  rules={[{ required: true, message: '请输入生产环境DBID' }]}
                >
                  <Input />
                </Form.Item>
              </>
            ) : (
              <>
                <Form.Item
                  label="数据库地址"
                  name={['database', 'host']}
                  rules={[{ required: true, message: '请输入数据库地址' }]}
                >
                  <Input />
                </Form.Item>
                <Form.Item
                  label="端口号"
                  name={['database', 'port']}
                  rules={[{ required: true, message: '请输入端口号' }]}
                >
                  <Input />
                </Form.Item>
                <Form.Item
                  label="用户名"
                  name={['database', 'username']}
                  rules={[{ required: true, message: '请输入用户名' }]}
                >
                  <Input />
                </Form.Item>
                <Form.Item
                  label="密码"
                  name={['database', 'password']}
                  rules={[{ required: true, message: '请输入密码' }]}
                >
                  <div>
                    <Input.Password />
                    <div className={cx('settings-db-pwd-tip')}>
                      <Text type="secondary">仅限密文类型</Text>
                    </div>
                  </div>
                </Form.Item>
              </>
            )}
          </SettingsCard>
        </Form>
      </div>
      </div>

    </div>
  )
}
