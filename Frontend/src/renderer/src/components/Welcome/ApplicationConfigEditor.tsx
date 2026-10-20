import { LockOutlined } from '@ant-design/icons'
import { Form, Input, Select, Switch } from 'antd'
import type { FormInstance } from 'antd'
import type { ReactElement } from 'react'
import type { ApplicationDraft } from '../../typings'
import { cx } from '../../utils'

const { TextArea } = Input

type Props = {
  form: FormInstance<ApplicationDraft>
  mode?: 'create' | 'settings' | 'conversation'
  showAppName?: boolean
}

/** 在创建、设置和对话配置卡中复用认证与权限的字段和验证规则。 */
export default function ApplicationConfigEditor({ form, mode = 'create', showAppName = false }: Props): ReactElement {
  const authEnabled = Form.useWatch(['auth', 'enable'], form) ?? true
  const authorizationEnabled = Form.useWatch(['authorization', 'enabled'], form) ?? false
  return (
    <>
      {showAppName ? (
        <Form.Item label="应用名称" name="appName" rules={[{ required: true, message: '请填写应用名称' }]}>
          <Input maxLength={128} />
        </Form.Item>
      ) : null}
      <section
        id={mode === 'settings' ? 'settings-auth' : undefined}
        className={cx('application-form-section', 'application-form-section--full', 'application-form-section--toggle')}
      >
        <div className={cx('application-form-section-head')}>
          <div className={cx('application-form-section-title')}><span className={cx('application-form-section-icon')}><LockOutlined /></span><span className={cx('application-form-section-text')}>一号通登录</span></div>
          <Form.Item className={cx('application-form-switch')} name={['auth', 'enable']} valuePropName="checked" noStyle>
            <Switch checkedChildren="启用" disabled={authorizationEnabled} unCheckedChildren="关闭" />
          </Form.Item>
        </div>
        {authEnabled ? (
          <>
            <Form.Item label="clientId" name={['auth', 'yht', 'clientId']} rules={[{ required: true, message: '启用一号通登录后请填写 clientId' }]}><Input /></Form.Item>
            <Form.Item extra="请填写加密后的内容。" label="privateKey" name={['auth', 'yht', 'privateKey']} rules={[{ required: true, message: '启用一号通登录后请填写 privateKey' }]}><TextArea autoSize={{ minRows: 2, maxRows: 6 }} /></Form.Item>
            <Form.Item label="publicKey" name={['auth', 'yht', 'publicKey']} rules={[{ required: true, message: '启用一号通登录后请填写 publicKey' }]}><TextArea autoSize={{ minRows: 2, maxRows: 6 }} /></Form.Item>
            <Form.Item label="centerPublicKey" name={['auth', 'yht', 'centerPublicKey']} rules={[{ required: true, message: '启用一号通登录后请填写 centerPublicKey' }]}><TextArea autoSize={{ minRows: 2, maxRows: 6 }} /></Form.Item>
          </>
        ) : null}
      </section>
      <section
        id={mode === 'settings' ? 'settings-authorization' : undefined}
        className={cx('application-form-section', 'application-form-section--full', 'application-form-section--toggle', !authorizationEnabled && 'application-form-section--disabled')}
      >
        <div className={cx('application-form-section-head')}>
          <div className={cx('application-form-section-title')}><span className={cx('application-form-section-icon')}><LockOutlined /></span><span className={cx('application-form-section-text')}>权限控制</span></div>
          <Form.Item className={cx('application-form-switch')} name={['authorization', 'enabled']} valuePropName="checked" noStyle>
            <Switch
              checkedChildren="涉及"
              unCheckedChildren="不涉及"
              onChange={(enabled) => {
                // 权限依赖身份认证，关闭权限时同步清空管理员种子。
                if (enabled) {
                  form.setFields([{ name: ['auth', 'enable'], value: true }])
                } else {
                  form.setFields([{ name: ['authorization', 'initialAdministratorSubjects'], value: [] }])
                }
              }}
            />
          </Form.Item>
        </div>
        <Form.Item
          extra="每个成员标识输入后按回车确认；必须填写认证系统中的真实 subjectId。"
          label="初始管理员成员标识"
          name={['authorization', 'initialAdministratorSubjects']}
          rules={[{
            validator: (_rule, value: string[] | undefined) => {
              if (!authorizationEnabled) return Promise.resolve()
              const subjects = Array.isArray(value) ? value.map((subject) => subject.trim()).filter(Boolean) : []
              if (!subjects.length) return Promise.reject(new Error('启用权限后至少填写一个初始管理员成员标识'))
              if (subjects.includes('current-user')) return Promise.reject(new Error('请填写真实 subjectId，不能使用 current-user'))
              return Promise.resolve()
            }
          }]}
        >
          <Select disabled={!authorizationEnabled} mode="tags" placeholder="8位工号，输入后按回车" tokenSeparators={[',', '，', '\n']} />
        </Form.Item>
      </section>
    </>
  )
}
