import {
  AppstoreOutlined,
  CodeOutlined,
  DeleteOutlined,
  LockOutlined,
  PlusOutlined,
  SaveOutlined,
  SettingOutlined,
  CloseOutlined
} from '@ant-design/icons'
import { Anchor, Button, Form, Input, Modal, Typography, message } from 'antd'
import type { ReactElement } from 'react'
import { useMemo, useState } from 'react'
import { type ApplicationConfig, type ApplicationDraft } from '../../typings'
import { cx } from '../../utils'
import ApplicationForm from '../Welcome/ApplicationForm'
import { saveApplication } from '../Welcome/applicationService'
import type { ConfigChanges, ConfigOperation } from '../../service/applicationConfigOperation'
import { loadWorkspaceApplicationConfig } from '../../service/applicationStorage'
import {
  applySettingsConfigChanges,
  configOperationFailureMessage,
  settingsConfigChanges,
  settingsSaveNoticeMessages
} from './settingsConfigChanges'
import { buildDatasourceConfig } from '../Welcome/utils'
import { createSettingsDraft } from './settingsDraft'
import '../Welcome/ApplicationFormModal.less'
import './SettingsPage.less'

const { Title, Text } = Typography

type EnvVariable = {
  key: string
  devValue: string
  prodValue: string
  encrypted: boolean
}

type Props = {
  application: ApplicationConfig
  /** 作为抽屉展示时的关闭入口；不传则不渲染关闭按钮。 */
  onClose?: () => void
  onSaved: (application: ApplicationConfig) => void
}

type SettingsFormValues = ApplicationDraft & {
  envVariables: EnvVariable[]
}

/** 组织并保存应用级基础能力与环境配置。 */
export default function SettingsPage({ application, onClose, onSaved }: Props): ReactElement {
  const [form] = Form.useForm<SettingsFormValues>()
  const [saving, setSaving] = useState(false)

  const settingsDraft = useMemo(() => createSettingsDraft(application), [application])
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
  // 校验设置并通过统一应用保存入口加密数据库凭据。
  const handleSave = async (): Promise<void> => {
    let submittedChanges: ConfigChanges = {}
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
        appName: rest.appName,
        appIcon: rest.appIcon,
        senario: rest.senario,
        theme: rest.theme,
        auth: rest.auth,
        authorization: rest.authorization,
        datasource: buildDatasourceConfig(rest.datasource),
        menus: application.menus,
        environment
      }
      submittedChanges = settingsConfigChanges(application, updatedApplication)
      const applied: ConfigOperation | undefined = await applySettingsConfigChanges(application, updatedApplication)
      if (applied && applied.status !== 'APPLIED') throw new Error('应用配置尚未完成工程同步。')
      const disk = await loadWorkspaceApplicationConfig(application.workspaceRoot)
      const persistedApplication = await saveApplication({
        ...updatedApplication,
        appName: disk.appName,
        appIcon: disk.appIcon,
        theme: disk.theme,
        auth: disk.auth,
        authorization: disk.authorization,
        configRevision: disk.configRevision
      })
      onSaved(persistedApplication)
      const notices = settingsSaveNoticeMessages(applied)
      Modal.success({
        title: '应用设置已保存',
        content: (
          <ul>
            {notices.map((notice) => <li key={notice}>{notice}</li>)}
          </ul>
        )
      })
    } catch (error) {
      // antd validateFields 校验失败时返回 { errorFields, values }，非 Error 实例
      // errorFields 按字段注册顺序排列（静态字段先于 Form.List 动态字段），
      // 需要按 DOM 视觉位置（从上到下）找出第一个可见的错误
      if (
        typeof error === 'object' &&
        error !== null &&
        'errorFields' in error &&
        Array.isArray(error.errorFields) &&
        error.errorFields.length > 0
      ) {
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
        message.error(configOperationFailureMessage(error, submittedChanges))
      }
    } finally {
      setSaving(false)
    }
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
          {onClose ? (
            <Button
              aria-label="关闭应用设置"
              icon={<CloseOutlined />}
              onClick={onClose}
              title="关闭应用设置"
              type="text"
            />
          ) : null}
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
              href="#settings-auth"
              title={
                <span className={cx('settings-anchor-item')}>
                  <LockOutlined />
                  <span>认证</span>
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
          <ApplicationForm
            form={form}
            initialValues={{
              ...settingsDraft,
              envVariables: safeEnvVariables
            }}
            lockedCapabilities={{
              login: application.auth.enable,
              authorization: application.authorization.enabled
            }}
            mode="settings"
          >
            <section
              id="settings-environment"
              className={cx('application-form-section', 'application-form-section--full')}
            >
              <div className={cx('application-form-section-title', 'datasource-config-title')}>
                <span className={cx('application-form-section-icon')}>
                  <CodeOutlined />
                </span>
                <span className={cx('application-form-section-text')}>环境变量</span>
              </div>
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
            </section>
          </ApplicationForm>
        </div>
      </div>
    </div>
  )
}
