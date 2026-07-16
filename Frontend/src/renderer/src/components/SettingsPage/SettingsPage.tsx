import {
  AppstoreOutlined,
  BankOutlined,
  CloudOutlined,
  DashboardOutlined,
  DesktopOutlined,
  FundOutlined,
  LayoutOutlined,
  LockOutlined,
  MessageOutlined,
  RadarChartOutlined,
  SaveOutlined,
  SettingOutlined,
  ShopOutlined,
  ShoppingOutlined,
  TeamOutlined,
  ToolOutlined,
  UserOutlined
} from '@ant-design/icons'
import type { AntdIconProps } from '@ant-design/icons/lib/components/AntdIcon'
import { AutoComplete, Button, Form, Input, Radio, Switch, Typography, message } from 'antd'
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
  'appName' | 'appIcon' | 'senario' | 'layout' | 'auth' | 'track' | 'apiTrack'
>

function SettingsCard({
  icon,
  title,
  children,
  extra,
  disabled
}: {
  icon: ReactNode
  title: string
  children: ReactNode
  extra?: ReactNode
  disabled?: boolean
}) {
  return (
    <section className={cx('settings-card', disabled && 'settings-card--disabled')}>
      <header className={cx('settings-card-header')}>
        <span className={cx('settings-card-title')}>
          <span className={cx('settings-card-icon')}>{icon}</span>
          <Text strong>{title}</Text>
        </span>
        {extra ? <span className={cx('settings-card-extra')}>{extra}</span> : null}
      </header>
      <div className={cx('settings-card-body')}>{children}</div>
    </section>
  )
}

export default function SettingsPage({ application, onSaved }: Props): ReactElement {
  const [form] = Form.useForm<SettingsFormValues>()
  const [saving, setSaving] = useState(false)

  // antd v4 的 Form.useWatch 在 Form 挂载前可能返回 undefined，用 getFieldValue 兜底更安全
  const authEnabled = Form.useWatch(['auth', 'enable'], form) ?? application?.auth?.enable ?? true
  const trackEnabled = Form.useWatch(['track', 'enable'], form) ?? application?.track?.enable ?? true
  const apiTrackEnabled = Form.useWatch(['apiTrack', 'enable'], form) ?? application?.apiTrack?.enable ?? true
  const useHeaderEnabled = Form.useWatch(['layout', 'useHeader'], form) ?? application?.layout?.useHeader ?? true
  const useFooterEnabled = Form.useWatch(['layout', 'useFooter'], form) ?? application?.layout?.useFooter ?? false

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
  const safeAuth = application?.auth ?? { enable: true, authnSource: '', yht: { clientId: '' } }
  const safeTrack = application?.track ?? { enable: true, uploadId: '', apiHost: '', method: 'post' }
  const safeApiTrack = application?.apiTrack ?? { enable: true, businessId: '', traceBaggage: '', apiTrackHost: '' }

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
            apiTrack: safeApiTrack
          }}
          labelCol={{ flex: '100px' }}
          wrapperCol={{ flex: 'auto' }}
          className={cx('settings-form')}
        >
          <SettingsCard icon={<AppstoreOutlined />} title="基础信息">
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

          <SettingsCard icon={<LayoutOutlined />} title="导航模式">
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
            icon={<LockOutlined />}
            title="认证"
            disabled={!authEnabled}
            extra={
              <Form.Item name={['auth', 'enable']} valuePropName="checked" noStyle>
                <Switch size="small" checkedChildren="开" unCheckedChildren="关" />
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
            icon={<RadarChartOutlined />}
            title="页面埋点"
            disabled={!trackEnabled}
            extra={
              <Form.Item name={['track', 'enable']} valuePropName="checked" noStyle>
                <Switch size="small" checkedChildren="开" unCheckedChildren="关" />
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
            icon={<RadarChartOutlined />}
            title="接口埋点"
            disabled={!apiTrackEnabled}
            extra={
              <Form.Item name={['apiTrack', 'enable']} valuePropName="checked" noStyle>
                <Switch size="small" checkedChildren="开" unCheckedChildren="关" />
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
        </Form>
      </div>

    </div>
  )
}
