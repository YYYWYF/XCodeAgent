import {
  AppstoreOutlined,
  BankOutlined,
  BgColorsOutlined,
  CloudOutlined,
  DatabaseOutlined,
  DashboardOutlined,
  DesktopOutlined,
  FolderOpenOutlined,
  FundOutlined,
  LayoutOutlined,
  LockOutlined,
  MenuOutlined,
  MessageOutlined,
  RadarChartOutlined,
  ShopOutlined,
  ShoppingOutlined,
  TeamOutlined,
  ToolOutlined,
  UserOutlined
} from '@ant-design/icons'
import { Button, Form, Input, message, Radio, Switch, Typography } from 'antd'
import type { FormInstance } from 'antd'
import type { ReactNode } from 'react'
import { useEffect, useMemo, useState } from 'react'
import type { ApplicationDraft } from '../../typings'
import { cx } from '../../utils'
import {
  applicationIconOptions,
  datasourceTypeOptions,
  initialApplicationDraft,
  terminalLabels,
  trackMethodOptions
} from './constants'
import { TabHintInput, TabHintAutoComplete } from './components/TabHintInput'

const { TextArea } = Input
const { Text } = Typography

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
  onSelectProjectParent: () => void
  selectingParent: boolean
}

function SectionTitle({ icon, children }: { icon: ReactNode; children: ReactNode }) {
  return (
    <div className={cx('application-form-section-title')}>
      <span className={cx('application-form-section-icon')}>{icon}</span>
      <span className={cx('application-form-section-text')}>{children}</span>
    </div>
  )
}

export default function ApplicationForm({ form, onSelectProjectParent, selectingParent }: Props) {
  const authEnabled = Form.useWatch(['auth', 'enable'], form) ?? true
  const trackEnabled = Form.useWatch(['track', 'enable'], form) ?? true
  const apiTrackEnabled = Form.useWatch(['apiTrack', 'enable'], form) ?? true
  const useHeaderEnabled = Form.useWatch(['layout', 'useHeader'], form) ?? true
  const useFooterEnabled = Form.useWatch(['layout', 'useFooter'], form) ?? false
  const menusEnabled = Form.useWatch(['menus', 'enable'], form) ?? true
  const themePrimaryColor = Form.useWatch(['theme', 'primaryColor'], form) ?? '#2c68ff'
  const datasourceType = Form.useWatch(['datasource', 'type'], form) ?? 'None'
  const dbUseBuiltin = Form.useWatch(['datasource', 'db', 'useBuiltin'], form) ?? false
  const dbConnectionMode = Form.useWatch(['datasource', 'db', 'connectionMode'], form) ?? 'plant'
  const datasourceIsDatabase = datasourceType === 'DataBase'
  const datasourceShowPlantFields =
    datasourceIsDatabase && !dbUseBuiltin && dbConnectionMode === 'plant'
  const datasourceDescription = datasourceTypeOptions.find(
    (option) => option.value === datasourceType
  )?.description

  // 未开放选项（外部 API / 平台内置数据库 / DBID 连接）不使用 disabled，
  // 改为视觉灰化 + 点击提示开发中并回退到可用值，避免「鼠标禁用」的挫败感。
  useEffect(() => {
    if (datasourceType === 'API') {
      message.info('外部 API 接入开发中，暂不可选')
      form.setFieldValue(['datasource', 'type'], 'DataBase')
    }
  }, [datasourceType, form])

  useEffect(() => {
    if (dbUseBuiltin) {
      message.info('平台内置数据库开发中，暂不可选')
      form.setFieldValue(['datasource', 'db', 'useBuiltin'], false)
    }
  }, [dbUseBuiltin, form])

  useEffect(() => {
    if (dbConnectionMode === 'dbid') {
      message.info('通过 DBID 连接开发中，暂不可选')
      form.setFieldValue(['datasource', 'db', 'connectionMode'], 'plant')
    }
  }, [dbConnectionMode, form])

  const [trackMethodSearch, setTrackMethodSearch] = useState('')
  const trackMethodFilteredOptions = useMemo(() => {
    const keyword = trackMethodSearch.trim().toLowerCase()
    if (!keyword) return trackMethodOptions
    return trackMethodOptions.filter((option) =>
      option.value.toLowerCase().includes(keyword)
    )
  }, [trackMethodSearch])

  const headerBar = useHeaderEnabled ? (
    <rect fill="#e8e8e8" height="6" rx="2" width="96" x="0" y="0" />
  ) : null
  const footerBar = useFooterEnabled ? (
    <rect fill="#e8e8e8" height="6" width="96" x="0" y="58" />
  ) : null
  return (
    <Form
      className={cx('application-form')}
      form={form}
      initialValues={initialApplicationDraft}
      layout="vertical"
    >
      <section className={cx('application-form-section', 'application-form-section--full')}>
        <SectionTitle icon={<FolderOpenOutlined />}>项目位置</SectionTitle>
        <Form.Item label="项目创建在哪个文件夹下？" required>
          <Input.Group compact>
            <Form.Item
              name="projectPath"
              noStyle
              rules={[{ required: true, message: '请选择项目创建位置' }]}
            >
              <Input style={{ width: 'calc(100% - 132px)' }} />
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
        </Form.Item>
      </section>

      <section className={cx('application-form-section', 'application-form-section--full')}>
        <SectionTitle icon={<AppstoreOutlined />}>基础信息</SectionTitle>
        <div className={cx('application-form-grid')}>
          <Form.Item
            label="应用名称"
            name="appName"
            rules={[{ required: true, message: '请输入应用名称' }]}
          >
            <Input />
          </Form.Item>
          <Form.Item label="版本号" extra="系统自动生成,首个版本">
            <Input value="v1.0" disabled className={cx('application-form-version-readonly')} />
          </Form.Item>
          <Form.Item label="终端类型" name="terminal">
            <Radio.Group>
              {Object.entries(terminalLabels).map(([value, label]) => (
                <Radio.Button key={value} value={value}>
                  {label}
                </Radio.Button>
              ))}
            </Radio.Group>
          </Form.Item>
        </div>
        <Form.Item label="应用图标" name="appIcon">
          <Radio.Group className={cx('application-icon-picker')} optionType="button" buttonStyle="solid">
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
        <Form.Item
          className={cx('application-form-scenario')}
          label="应用场景"
          name="senario"
          rules={[{ required: true, whitespace: true, message: '请输入应用场景' }]}
        >
          <TextArea rows={3} />
        </Form.Item>
      </section>

      <section className={cx('application-form-section', 'application-form-section--full')}>
        <SectionTitle icon={<LayoutOutlined />}>导航模式</SectionTitle>
        <div className={cx('nav-mode-row')}>
          <Form.Item label="布局方式" name={['layout', 'type']}>
            <Radio.Group className={cx('nav-mode-picker')} optionType="button" buttonStyle="solid">
              <Radio.Button value="side">
                <span className={cx('nav-preview')}>
                  <svg height="52" viewBox="0 0 96 64" width="78">
                    <defs>
                      <clipPath id="nav-side-clip">
                        <rect height="64" rx="5" width="96" x="0" y="0" />
                      </clipPath>
                    </defs>
                    <g clipPath="url(#nav-side-clip)">
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
                  <span className={cx('nav-preview-label')}>左侧导航</span>
                </span>
              </Radio.Button>
              <Radio.Button value="top">
                <span className={cx('nav-preview')}>
                  <svg height="52" viewBox="0 0 96 64" width="78">
                    <defs>
                      <clipPath id="nav-top-clip">
                        <rect height="64" rx="5" width="96" x="0" y="0" />
                      </clipPath>
                    </defs>
                    <g clipPath="url(#nav-top-clip)">
                      <rect fill="#fff" height="64" width="96" x="0" y="0" />
                      <rect fill="#2c2c2c" height="16" width="96" x="0" y="0" />
                      {headerBar}
                      {footerBar}
                    </g>
                    <rect fill="none" height="64" rx="5" stroke="#d9d9d9" strokeWidth="1" width="96" x="0" y="0" />
                  </svg>
                  <span className={cx('nav-preview-label')}>顶部导航</span>
                </span>
              </Radio.Button>
              <Radio.Button value="mix">
                <span className={cx('nav-preview')}>
                  <svg height="52" viewBox="0 0 96 64" width="78">
                    <defs>
                      <clipPath id="nav-mix-clip">
                        <rect height="64" rx="5" width="96" x="0" y="0" />
                      </clipPath>
                    </defs>
                    <g clipPath="url(#nav-mix-clip)">
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
                  <span className={cx('nav-preview-label')}>混合导航</span>
                </span>
              </Radio.Button>
            </Radio.Group>
          </Form.Item>
          <div className={cx('nav-mode-toggles')}>
            <Form.Item label="开启头部" name={['layout', 'useHeader']} valuePropName="checked">
              <Switch checkedChildren="开启" unCheckedChildren="关闭" />
            </Form.Item>
            <Form.Item label="开启底部" name={['layout', 'useFooter']} valuePropName="checked">
              <Switch checkedChildren="开启" unCheckedChildren="关闭" />
            </Form.Item>
          </div>
        </div>
      </section>

      <section className={cx('application-form-section', 'application-form-section--full')}>
        <div className={cx('application-form-section-head')}>
          <SectionTitle icon={<MenuOutlined />}>菜单</SectionTitle>
          <Form.Item
            className={cx('application-form-switch')}
            name={['menus', 'enable']}
            valuePropName="checked"
            noStyle
          >
            <Switch checkedChildren="启用" unCheckedChildren="关闭" />
          </Form.Item>
        </div>
        <Form.Item
          label="页面根路由"
          name={['menus', 'rootPath']}
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
          <TabHintInput form={form} fieldName={['menus', 'rootPath']} placeholder="请输入页面根路由" />
        </Form.Item>
      </section>

      <section
        className={cx(
          'application-form-section',
          'application-form-section--full'
        )}
      >
        <SectionTitle icon={<DatabaseOutlined />}>数据源</SectionTitle>
        <Form.Item label="数据源类型" name={['datasource', 'type']}>
          <Radio.Group buttonStyle="solid">
            {datasourceTypeOptions.map((option) => (
              <Radio.Button
                className={option.disabled ? cx('datasource-pending-option') : undefined}
                key={option.value}
                value={option.value}
              >
                {option.label}
              </Radio.Button>
            ))}
          </Radio.Group>
        </Form.Item>
        {datasourceDescription ? (
          <Text className={cx('datasource-type-hint')} type="secondary">
            {datasourceDescription}
          </Text>
        ) : null}
        {datasourceIsDatabase ? (
          <>
            <Form.Item label="数据库类型" name={['datasource', 'db', 'useBuiltin']}>
              <Radio.Group buttonStyle="solid">
                <Radio.Button className={cx('datasource-pending-option')} value={true}>
                  平台内置数据库
                </Radio.Button>
                <Radio.Button value={false}>外部数据库</Radio.Button>
              </Radio.Group>
            </Form.Item>
            <Form.Item label="连接方案" name={['datasource', 'db', 'connectionMode']}>
              <Radio.Group buttonStyle="solid">
                <Radio.Button className={cx('datasource-pending-option')} value="dbid">
                  通过 DBID 连接
                </Radio.Button>
                <Radio.Button value="plant">通过账号密码连接</Radio.Button>
              </Radio.Group>
            </Form.Item>
            {datasourceShowPlantFields ? (
              <div className={cx('datasource-db-grid')}>
                <Form.Item
                  label="数据库地址"
                  name={['datasource', 'db', 'plantMode', 'domain']}
                  rules={[{ required: true, whitespace: true, message: '请填写数据库地址' }]}
                >
                  <Input />
                </Form.Item>
                <Form.Item
                  label="端口"
                  name={['datasource', 'db', 'plantMode', 'port']}
                  rules={[{ required: true, message: '请填写端口' }]}
                >
                  <Input />
                </Form.Item>
                <Form.Item
                  label="用户名"
                  name={['datasource', 'db', 'plantMode', 'userName']}
                  rules={[{ required: true, whitespace: true, message: '请填写用户名' }]}
                >
                  <Input />
                </Form.Item>
                <Form.Item
                  label="密码"
                  name={['datasource', 'db', 'plantMode', 'pwd']}
                  rules={[{ required: true, whitespace: true, message: '请填写密码' }]}
                >
                  <Input.Password />
                </Form.Item>
                <Form.Item
                  label="Schema"
                  name={['datasource', 'db', 'plantMode', 'schema']}
                  rules={[{ required: true, whitespace: true, message: '请填写 Schema' }]}
                >
                  <Input />
                </Form.Item>
              </div>
            ) : null}
          </>
        ) : null}
      </section>

      <section className={cx('application-form-section', 'application-form-section--full')}>
        <SectionTitle icon={<BgColorsOutlined />}>主题</SectionTitle>
        <Form.Item
          label="主题色"
        >
          <Form.Item name={['theme', 'primaryColor']} hidden>
            <Input />
          </Form.Item>
          <div className={cx('theme-color-picker-row')}>
            <div className={cx('theme-color-swatch-wrapper')}>
              <div
                className={cx('theme-color-swatch')}
                style={{ backgroundColor: themePrimaryColor }}
              />
              <input
                type="color"
                className={cx('theme-color-input')}
                value={themePrimaryColor}
                onChange={(e) => {
                  const color = e.target.value
                  form.setFields([{ name: ['theme', 'primaryColor'], value: color }])
                }}
              />
            </div>
            <Input
              placeholder="请输入主题色色值，如：#2c68ff"
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

      <section
        className={cx('application-form-section', 'application-form-section--full', 'application-form-section--toggle', !authEnabled && 'application-form-section--disabled')}
      >
        <div className={cx('application-form-section-head')}>
          <SectionTitle icon={<LockOutlined />}>认证</SectionTitle>
          <Form.Item
            className={cx('application-form-switch')}
            name={['auth', 'enable']}
            valuePropName="checked"
            noStyle
          >
            <Switch checkedChildren="启用" unCheckedChildren="关闭" />
          </Form.Item>
        </div>
        <Form.Item
          label="认证来源"
          name={['auth', 'authnSource']}
          rules={[{ required: authEnabled, message: '启用认证后请填写认证来源' }]}
        >
          <Input disabled={!authEnabled} />
        </Form.Item>
        <Form.Item
          label="一号通clientId"
          name={['auth', 'yht', 'clientId']}
          rules={[{ required: authEnabled, message: '启用认证后请填写一号通clientId' }]}
        >
          <Input disabled={!authEnabled} />
        </Form.Item>
      </section>

      <section
        className={cx('application-form-section', 'application-form-section--full', 'application-form-section--toggle', !trackEnabled && 'application-form-section--disabled')}
      >
        <div className={cx('application-form-section-head')}>
          <SectionTitle icon={<RadarChartOutlined />}>页面埋点</SectionTitle>
          <Form.Item
            className={cx('application-form-switch')}
            name={['track', 'enable']}
            valuePropName="checked"
            noStyle
          >
            <Switch checkedChildren="启用" unCheckedChildren="关闭" />
          </Form.Item>
        </div>
        <Form.Item
          label="上传标识"
          name={['track', 'uploadId']}
          rules={[{ required: trackEnabled, message: '启用页面埋点后请填写上传标识' }]}
        >
          <Input disabled={!trackEnabled} />
        </Form.Item>
        <Form.Item label="上报地址" name={['track', 'apiHost']}>
          <Input disabled={!trackEnabled} />
        </Form.Item>
        <Form.Item label="请求方式" name={['track', 'method']}>
          <TabHintAutoComplete
            form={form}
            fieldName={['track', 'method']}
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
      </section>

      <section
        className={cx('application-form-section', 'application-form-section--full', 'application-form-section--toggle', !apiTrackEnabled && 'application-form-section--disabled')}
      >
        <div className={cx('application-form-section-head')}>
          <SectionTitle icon={<RadarChartOutlined />}>接口埋点</SectionTitle>
          <Form.Item
            className={cx('application-form-switch')}
            name={['apiTrack', 'enable']}
            valuePropName="checked"
            noStyle
          >
            <Switch checkedChildren="启用" unCheckedChildren="关闭" />
          </Form.Item>
        </div>
        <Form.Item
          label="业务标识"
          name={['apiTrack', 'businessId']}
          rules={[{ required: apiTrackEnabled, message: '启用接口埋点后请填写业务标识' }]}
        >
          <Input disabled={!apiTrackEnabled} />
        </Form.Item>
        <Form.Item label="链路透传信息" name={['apiTrack', 'traceBaggage']}>
          <Input disabled={!apiTrackEnabled} />
        </Form.Item>
        <Form.Item label="接口埋点地址" name={['apiTrack', 'apiTrackHost']}>
          <Input disabled={!apiTrackEnabled} />
        </Form.Item>
      </section>
    </Form>
  )
}
