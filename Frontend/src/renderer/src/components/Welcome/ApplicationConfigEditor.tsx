import {
  AppstoreOutlined,
  BankOutlined,
  CloudOutlined,
  DashboardOutlined,
  DesktopOutlined,
  FundOutlined,
  LockOutlined,
  MessageOutlined,
  ShopOutlined,
  ShoppingOutlined,
  TeamOutlined,
  ToolOutlined,
  UserOutlined
} from '@ant-design/icons'
import { Form, Input, Radio, Select, Switch } from 'antd'
import type { FormInstance } from 'antd'
import type { ReactElement } from 'react'
import type { ApplicationDraft } from '../../typings'
import { cx } from '../../utils'
import { applicationIconOptions } from './constants'
import ThemeColorField from './ThemeColorField'

const { TextArea } = Input

/** 将持久化的图标标识映射为旧版图标按钮组使用的图标组件。 */
const applicationIconComponents: Record<string, typeof AppstoreOutlined> = {
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
  mode?: 'create' | 'settings' | 'conversation'
  /** 需要渲染的受管配置字段；省略时渲染完整的受管配置表单。 */
  visiblePaths?: readonly ApplicationConfigFieldPath[]
  /** 已写入工程的能力不可关闭；仅设置页传入已持久化的能力状态。 */
  lockedCapabilities?: { login?: boolean; authorization?: boolean }
}

/** 应用配置事务允许编辑、并在三个入口复用的字段路径。 */
export type ApplicationConfigFieldPath =
  | 'appName'
  | 'appIcon'
  | 'theme.primaryColor'
  | 'auth.enable'
  | 'auth.yht.clientId'
  | 'auth.yht.privateKey'
  | 'auth.yht.publicKey'
  | 'auth.yht.centerPublicKey'
  | 'auth.yht.centerUri'
  | 'auth.yht.authorizeUri'
  | 'auth.yht.redirectUri'
  | 'auth.yht.endUrl'
  | 'auth.yht.logoutShareJs'
  | 'authorization.enabled'
  | 'authorization.initialAdministratorSubjects'
  | 'authorization.api.baseUrl'
  | 'authorization.api.viewId'

/** 根据已持久化能力及权限对登录的依赖确定开关是否不可操作。 */
export function capabilityToggleDisabled(locked: boolean | undefined, requiredByDependency = false): boolean {
  return Boolean(locked || requiredByDependency)
}

/** 在创建、设置和对话配置卡中复用受管应用配置字段及其校验和依赖交互。 */
export default function ApplicationConfigEditor({
  form, mode = 'create', lockedCapabilities, visiblePaths
}: Props): ReactElement {
  const visible = visiblePaths ? new Set<ApplicationConfigFieldPath>(visiblePaths) : undefined
  /** 未指定字段集合时输出完整表单；对话卡传入白名单以限制本次提案范围。 */
  const show = (path: ApplicationConfigFieldPath): boolean => visible === undefined || visible.has(path)
  const authEnabled = Form.useWatch(['auth', 'enable'], form) ?? true
  const authorizationEnabled = Form.useWatch(['authorization', 'enabled'], form) ?? false
  return (
    <>
      {show('appName') ? (
        <Form.Item label="应用名称" name="appName" rules={[{ required: true, message: '请填写应用名称' }]}>
          <Input maxLength={128} />
        </Form.Item>
      ) : null}
      {show('appIcon') ? (
        <Form.Item label="应用图标" name="appIcon">
          <Radio.Group
            className={cx('application-icon-picker')}
            optionType="button"
            buttonStyle="solid"
          >
            {applicationIconOptions.map((option) => {
              const Icon = applicationIconComponents[option.value]
              return (
                <Radio.Button key={option.value} aria-label={option.label} value={option.value}>
                  {Icon ? <Icon /> : null}
                </Radio.Button>
              )
            })}
          </Radio.Group>
        </Form.Item>
      ) : null}
      {show('theme.primaryColor') ? <section className={cx('application-form-section', 'application-form-section--full')}><div className={cx('application-form-section-title')}>应用主题色</div><ThemeColorField form={form} /></section> : null}
      {show('auth.enable') || show('auth.yht.clientId') || show('auth.yht.privateKey') || show('auth.yht.publicKey') || show('auth.yht.centerPublicKey') || show('auth.yht.centerUri') || show('auth.yht.authorizeUri') || show('auth.yht.redirectUri') || show('auth.yht.endUrl') || show('auth.yht.logoutShareJs') ? (
      <section
        id={mode === 'settings' ? 'settings-auth' : undefined}
        className={cx('application-form-section', 'application-form-section--full', 'application-form-section--toggle')}
      >
        <div className={cx('application-form-section-head')}>
          <div className={cx('application-form-section-title')}><span className={cx('application-form-section-icon')}><LockOutlined /></span><span className={cx('application-form-section-text')}>一号通登录</span></div>
          {show('auth.enable') ? (
            <div className={cx('application-form-toggle-control')}>
              {authorizationEnabled ? <span className={cx('application-form-toggle-hint')}>权限已启用，无法关闭登录开关，请先关闭权限。</span> : null}
              <Form.Item className={cx('application-form-switch')} name={['auth', 'enable']} valuePropName="checked" noStyle>
                <Switch checkedChildren="启用" disabled={capabilityToggleDisabled(lockedCapabilities?.login, authorizationEnabled)} unCheckedChildren="关闭" />
              </Form.Item>
            </div>
          ) : null}
        </div>
        {lockedCapabilities?.login ? <p className={cx('application-form-section-description')}>已启用登录。关闭会删除已注入的工程代码，现阶段暂不支持。</p> : null}
        {authEnabled ? (
          <>
            {show('auth.yht.clientId') ? <Form.Item label="clientId" name={['auth', 'yht', 'clientId']} rules={[{ required: true, message: '启用一号通登录后请填写 clientId' }]}><Input /></Form.Item> : null}
            {show('auth.yht.privateKey') ? <Form.Item extra="请填写加密后的内容。" label="privateKey" name={['auth', 'yht', 'privateKey']} rules={[{ required: true, message: '启用一号通登录后请填写 privateKey' }]}><TextArea autoSize={{ minRows: 2, maxRows: 6 }} /></Form.Item> : null}
            {show('auth.yht.publicKey') ? <Form.Item label="publicKey" name={['auth', 'yht', 'publicKey']} rules={[{ required: true, message: '启用一号通登录后请填写 publicKey' }]}><TextArea autoSize={{ minRows: 2, maxRows: 6 }} /></Form.Item> : null}
            {show('auth.yht.centerPublicKey') ? <Form.Item label="centerPublicKey" name={['auth', 'yht', 'centerPublicKey']} rules={[{ required: true, message: '启用一号通登录后请填写 centerPublicKey' }]}><TextArea autoSize={{ minRows: 2, maxRows: 6 }} /></Form.Item> : null}
            {show('auth.yht.centerUri') ? <Form.Item label="认证中心地址" name={['auth', 'yht', 'centerUri']} rules={[{ type: 'url', message: '请填写有效的认证中心 URL' }]}><Input /></Form.Item> : null}
            {show('auth.yht.authorizeUri') ? <Form.Item label="登录授权地址" name={['auth', 'yht', 'authorizeUri']} rules={[{ type: 'url', message: '请填写有效的登录授权 URL' }]}><Input /></Form.Item> : null}
            {show('auth.yht.redirectUri') ? <Form.Item label="后端回调地址" name={['auth', 'yht', 'redirectUri']} rules={[{ type: 'url', message: '请填写有效的回调 URL' }]}><Input /></Form.Item> : null}
            {show('auth.yht.endUrl') ? <Form.Item label="前端登出地址" name={['auth', 'yht', 'endUrl']} rules={[{ type: 'url', message: '请填写有效的登出 URL' }]}><Input /></Form.Item> : null}
            {show('auth.yht.logoutShareJs') ? <Form.Item label="登出同步脚本" name={['auth', 'yht', 'logoutShareJs']} rules={[{ type: 'url', message: '请填写有效的脚本 URL' }]}><Input /></Form.Item> : null}
          </>
        ) : null}
      </section>
      ) : null}
      {show('authorization.enabled') || show('authorization.initialAdministratorSubjects') ? (
      <section
        id={mode === 'settings' ? 'settings-authorization' : undefined}
        className={cx('application-form-section', 'application-form-section--full', 'application-form-section--toggle')}
      >
        <div className={cx('application-form-section-head')}>
          <div className={cx('application-form-section-title')}><span className={cx('application-form-section-icon')}><LockOutlined /></span><span className={cx('application-form-section-text')}>权限控制</span></div>
          {show('authorization.enabled') ? (
            <Form.Item className={cx('application-form-switch')} name={['authorization', 'enabled']} valuePropName="checked" noStyle>
              <Switch
                checkedChildren="启用"
                disabled={capabilityToggleDisabled(lockedCapabilities?.authorization)}
                unCheckedChildren="关闭"
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
          ) : null}
        </div>
        {lockedCapabilities?.authorization ? <p className={cx('application-form-section-description')}>已启用权限控制。关闭会删除已注入的工程代码，现阶段暂不支持。</p> : null}
        {authorizationEnabled && show('authorization.initialAdministratorSubjects') ? <Form.Item
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
          <Select mode="tags" placeholder="8位工号，输入后按回车" tokenSeparators={[',', '，', '\n']} />
        </Form.Item> : null}
        {authorizationEnabled && show('authorization.api.baseUrl') ? <Form.Item label="权限用户服务地址" name={['authorization', 'api', 'baseUrl']} rules={[{ type: 'url', message: '请填写有效的权限服务 URL' }]}><Input /></Form.Item> : null}
        {authorizationEnabled && show('authorization.api.viewId') ? <Form.Item label="用户视图 ID" name={['authorization', 'api', 'viewId']}><Input /></Form.Item> : null}
      </section>
      ) : null}
    </>
  )
}
