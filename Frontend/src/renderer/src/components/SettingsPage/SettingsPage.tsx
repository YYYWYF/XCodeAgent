import {
  AppstoreOutlined,
  LockOutlined,
  SaveOutlined,
  SettingOutlined,
  CloseOutlined
} from '@ant-design/icons'
import { Anchor, Button, Form, Modal, Typography, message } from 'antd'
import type { ReactElement } from 'react'
import { useMemo, useState } from 'react'
import { type ApplicationConfig, type ApplicationDraft } from '../../typings'
import { cx } from '../../utils'
import ApplicationForm from '../Welcome/ApplicationForm'
import { saveApplication } from '../Welcome/applicationService'
import { hasPendingConfigOperation, type ConfigChanges, type ConfigOperation } from '../../service/applicationConfigOperation'
import { loadWorkspaceApplicationConfig } from '../../service/applicationStorage'
import {
  applySettingsConfigChanges,
  configOperationFailureMessage,
  settingsConfigChanges,
  settingsSaveNoticeMessages
} from './settingsConfigChanges'
import { buildDatasourceConfig } from '../Welcome/utils'
import { createSettingsDraft } from './settingsDraft'
import { useSettingsConfigRecovery } from './useSettingsConfigRecovery'
import '../Welcome/ApplicationFormModal.less'
import './SettingsPage.less'

const { Title } = Typography

type Props = {
  application: ApplicationConfig
  /** 作为抽屉展示时的关闭入口；不传则不渲染关闭按钮。 */
  onClose?: () => void
  onSaved: (application: ApplicationConfig) => void
}

type SettingsFormValues = ApplicationDraft

/** 组织并保存应用级基础能力与环境配置。 */
export default function SettingsPage({ application, onClose, onSaved }: Props): ReactElement {
  const [form] = Form.useForm<SettingsFormValues>()
  const [saving, setSaving] = useState(false)
  const { canResume, refreshPending, handleResume } = useSettingsConfigRecovery(application, form, setSaving, onSaved)

  const settingsDraft = useMemo(() => createSettingsDraft(application), [application])
  // 校验设置并通过统一应用保存入口加密数据库凭据。
  const handleSave = async (): Promise<void> => {
    let submittedChanges: ConfigChanges = {}
    try {
      const values = await form.validateFields()
      setSaving(true)
      // environment 不在表单里：环境变量板块已下线，但已有数据要原样保留——
      // 老工作区可能存了用户的环境变量，保存设置不该把它们清掉。
      const updatedApplication: ApplicationConfig = {
        ...application,
        appName: values.appName,
        appIcon: values.appIcon,
        repoUrl: values.repoUrl.trim(),
        theme: values.theme,
        auth: values.auth,
        authorization: values.authorization,
        datasource: buildDatasourceConfig(values.datasource),
        menus: application.menus
      }
      submittedChanges = settingsConfigChanges(application, updatedApplication)
      if (hasPendingConfigOperation(application.workspaceRoot) && Object.keys(submittedChanges).length === 0) throw new Error('原配置操作尚未完成，请点击恢复原配置操作。')
      const applied: ConfigOperation | undefined = await applySettingsConfigChanges(application, updatedApplication)
      if (applied && applied.status !== 'APPLIED') throw new Error('应用配置尚未完成工程同步。')
      const disk = await loadWorkspaceApplicationConfig(application.workspaceRoot)
      const persistedApplication = await saveApplication({
        ...updatedApplication,
        appName: disk.appName,
        appIcon: disk.appIcon,
        repoUrl: disk.repoUrl,
        theme: disk.theme,
        auth: disk.auth,
        authorization: disk.authorization,
        configRevision: disk.configRevision
      })
      // 保存成功后以最新磁盘配置刷新表单，避免再次保存时复用提交前的开关或 revision。
      form.setFieldsValue(createSettingsDraft(persistedApplication))
      onSaved(persistedApplication)
      // 轻量成功反馈：固定 3 秒自动消失的 message，不再用 Modal 打断操作。
      message.success({ content: '保存成功', duration: 3 })
      const notices = settingsSaveNoticeMessages(applied)
      // 仅当有工程文件变更/能力注入等用户必须知晓的副作用时才弹窗展示明细；
      // 纯基础信息保存（只有「应用配置已保存」一条）不再打扰。
      const effectNotices = notices.filter((notice) => notice !== '应用配置已保存。')
      if (effectNotices.length > 0) {
        Modal.success({
          title: '应用设置已保存',
          content: (
            <ul>
              {effectNotices.map((notice) => <li key={notice}>{notice}</li>)}
            </ul>
          )
        })
      }
    } catch (error) {
      refreshPending()
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
          {canResume ? <Button disabled={saving} onClick={() => void handleResume()}>恢复原配置操作</Button> : null}
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
          </Anchor>
        </aside>
        <div className={cx('settings-page-scroll')}>
          <ApplicationForm
            form={form}
            initialValues={settingsDraft}
            lockedCapabilities={{
              login: application.auth.enable,
              authorization: application.authorization.enabled
            }}
            mode="settings"
          />
        </div>
      </div>
    </div>
  )
}
