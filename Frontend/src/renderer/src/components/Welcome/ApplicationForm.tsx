import { FolderAddOutlined, FolderOpenOutlined } from '@ant-design/icons'
import { Button, Form, Input, Radio, Select, Switch, Typography } from 'antd'
import type { FormInstance } from 'antd'
import type { ApplicationDraft } from '../../typings'
import { cx } from '../../utils'
import { initialApplicationDraft, terminalLabels, trackMethodLabels } from './constants'
import { joinLocalPath, validateProjectDirectoryName } from './utils'

const { Text, Title } = Typography
const { TextArea } = Input

type Props = {
  form: FormInstance<ApplicationDraft>
  onSelectProjectParent: () => void
  selectingParent: boolean
}

export default function ApplicationForm({ form, onSelectProjectParent, selectingParent }: Props) {
  return (
    <Form
      form={form}
      initialValues={initialApplicationDraft}
      layout="vertical"
    >
      <section className={cx('application-form-section')}>
        <Title level={5}>基础信息</Title>
        <Form.Item
          label="应用名称"
          name="appName"
          rules={[{ required: true, message: '请输入应用名称' }]}
        >
          <Input />
        </Form.Item>
        <Form.Item label="应用图标" name="appIcon">
          <Input />
        </Form.Item>
        <Form.Item label="应用场景" name="senario">
          <TextArea autoSize={{ minRows: 2, maxRows: 4 }} />
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
      </section>

      <section className={cx('application-form-section')}>
        <Title level={5}>项目位置</Title>
        <Form.Item label="项目创建在哪个文件夹下？" required>
          <Input.Group compact>
            <Form.Item
              name="projectParentPath"
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
        <Form.Item
          label="项目文件夹名"
          name="projectDirectoryName"
          rules={[{ validator: validateProjectDirectoryName }]}
        >
          <Input prefix={<FolderAddOutlined />} />
        </Form.Item>
        <Form.Item noStyle shouldUpdate>
          {({ getFieldValue }) => {
            const finalPath = joinLocalPath(
              String(getFieldValue('projectParentPath') || ''),
              String(getFieldValue('projectDirectoryName') || '')
            )
            return finalPath ? (
              <Text className={cx('project-path-preview')} type="secondary">
                将创建在：{finalPath}
              </Text>
            ) : null
          }}
        </Form.Item>
      </section>

      <section className={cx('application-form-section')}>
        <Title level={5}>认证</Title>
        <Form.Item label="启用认证" name={['auth', 'enable']} valuePropName="checked">
          <Switch checkedChildren="启用" unCheckedChildren="关闭" />
        </Form.Item>
        <Form.Item label="认证来源" name={['auth', 'authnSource']}>
          <Input />
        </Form.Item>
        <Form.Item label="一号通clientId" name={['auth', 'yht', 'clientId']}>
          <Input />
        </Form.Item>
      </section>

      <section className={cx('application-form-section')}>
        <Title level={5}>页面埋点</Title>
        <Form.Item label="启用页面埋点" name={['track', 'enable']} valuePropName="checked">
          <Switch checkedChildren="启用" unCheckedChildren="关闭" />
        </Form.Item>
        <Form.Item label="上传标识" name={['track', 'uploadId']}>
          <Input />
        </Form.Item>
        <Form.Item label="上报地址" name={['track', 'apiHost']}>
          <Input />
        </Form.Item>
        <Form.Item label="请求方式" name={['track', 'method']}>
          <Select
            options={Object.entries(trackMethodLabels).map(([value, label]) => ({ label, value }))}
          />
        </Form.Item>
      </section>

      <section className={cx('application-form-section')}>
        <Title level={5}>接口埋点</Title>
        <Form.Item label="启用接口埋点" name={['apiTrack', 'enable']} valuePropName="checked">
          <Switch checkedChildren="启用" unCheckedChildren="关闭" />
        </Form.Item>
        <Form.Item label="业务标识" name={['apiTrack', 'businessId']}>
          <Input />
        </Form.Item>
        <Form.Item label="链路透传信息" name={['apiTrack', 'traceBaggage']}>
          <Input />
        </Form.Item>
        <Form.Item label="接口埋点地址" name={['apiTrack', 'apiTrackHost']}>
          <Input />
        </Form.Item>
      </section>
    </Form>
  )
}
