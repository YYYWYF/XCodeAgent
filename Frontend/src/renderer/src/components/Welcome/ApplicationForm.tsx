import {
  AppstoreOutlined,
  BankOutlined,
  CloudOutlined,
  DashboardOutlined,
  DesktopOutlined,
  FolderOpenOutlined,
  FundOutlined,
  LockOutlined,
  MessageOutlined,
  RadarChartOutlined,
  ShopOutlined,
  ShoppingOutlined,
  TeamOutlined,
  ToolOutlined,
  UserOutlined
} from '@ant-design/icons'
import type { AntdIconProps } from '@ant-design/icons/lib/components/AntdIcon'
import { AutoComplete, Button, Form, Input, Radio, Switch } from 'antd'
import type { FormInstance } from 'antd'
import type { ReactNode } from 'react'
import { useMemo, useState, type ComponentType } from 'react'
import type { ApplicationDraft } from '../../typings'
import { cx } from '../../utils'
import {
  applicationIconOptions,
  initialApplicationDraft,
  terminalLabels,
  trackMethodOptions
} from './constants'

const { TextArea } = Input

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
  const [trackMethodSearch, setTrackMethodSearch] = useState('')
  const trackMethodFilteredOptions = useMemo(() => {
    const keyword = trackMethodSearch.trim().toLowerCase()
    if (!keyword) return trackMethodOptions
    return trackMethodOptions.filter((option) =>
      option.value.toLowerCase().includes(keyword)
    )
  }, [trackMethodSearch])
  return (
    <Form
      className={cx('application-form')}
      form={form}
      initialValues={initialApplicationDraft}
      layout="vertical"
    >
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
        <Form.Item label="应用场景" name="senario">
          <TextArea autoSize={{ minRows: 2, maxRows: 4 }} />
        </Form.Item>
      </section>

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

      <section
        className={cx('application-form-section', 'application-form-section--toggle', !authEnabled && 'application-form-section--disabled')}
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
        <div className={cx('application-form-grid')}>
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
        </div>
      </section>

      <section
        className={cx('application-form-section', 'application-form-section--toggle', !trackEnabled && 'application-form-section--disabled')}
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
        <div className={cx('application-form-grid')}>
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
        </div>
      </section>

      <section
        className={cx('application-form-section', 'application-form-section--toggle', !apiTrackEnabled && 'application-form-section--disabled')}
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
        <div className={cx('application-form-grid')}>
          <Form.Item label="链路透传信息" name={['apiTrack', 'traceBaggage']}>
            <Input disabled={!apiTrackEnabled} />
          </Form.Item>
          <Form.Item label="接口埋点地址" name={['apiTrack', 'apiTrackHost']}>
            <Input disabled={!apiTrackEnabled} />
          </Form.Item>
        </div>
      </section>
    </Form>
  )
}
