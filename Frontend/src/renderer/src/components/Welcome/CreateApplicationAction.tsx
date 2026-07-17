import { PlusOutlined } from '@ant-design/icons'
import { Form, message, Modal } from 'antd'
import { useState } from 'react'
import { createPagePlanningThreadId } from '../../service/applicationPagePlanning'
import type {
  ApplicationConfig,
  ApplicationDraft,
  ApplicationPlanningConfirmation
} from '../../typings'
import { cx } from '../../utils'
import ApplicationForm from './ApplicationForm'
import ApplicationPagePlanningModal from './ApplicationPagePlanningModal'
import WelcomeActionCard from './WelcomeActionCard'
import WelcomeModalTitle from './WelcomeModalTitle'
import './ApplicationFormModal.less'
import './WelcomeModal.less'
import { saveAndOpenApplication, saveApplication } from './applicationService'
import { initialApplicationDraft } from './constants'
import { buildApplicationSchema, createApplicationId, formatError, pathBasename } from './utils'
import type { SettingsValues } from './ApplicationPagePlanningModal'

type Props = {
  onOpenApplication: (application: ApplicationConfig) => void
  theme: 'dark' | 'light'
}

export default function CreateApplicationAction({ onOpenApplication, theme }: Props): JSX.Element {
  const [form] = Form.useForm<ApplicationDraft>()
  const [modalOpen, setModalOpen] = useState(false)
  const [creating, setCreating] = useState(false)
  const [selectingParent, setSelectingParent] = useState(false)
  const [planningApplication, setPlanningApplication] = useState<ApplicationConfig>()
  const [planningThreadId, setPlanningThreadId] = useState('')
  const [savedFormValues, setSavedFormValues] = useState<ApplicationDraft>()

  const openModal = (): void => {
    setModalOpen(true)
  }

  const handleSelectProjectParent = async (): Promise<void> => {
    setSelectingParent(true)
    try {
      const workspaceApi = window.xcodeAgent?.workspace
      if (!workspaceApi?.selectDirectory) {
        message.warning('当前环境不能打开系统目录选择器，请在桌面客户端中使用。')
        return
      }

      const result = await workspaceApi.selectDirectory({ title: '选择新应用的创建位置' })
      if (!result.canceled && result.path) {
        form.setFieldsValue({ projectPath: result.path })
      }
    } catch (error) {
      message.error(formatError(error, '选择文件夹失败'))
    } finally {
      setSelectingParent(false)
    }
  }

  const handleCreateApplication = async (): Promise<void> => {
    setCreating(true)
    try {
      const values = await form.validateFields()
      const workspaceApi = window.xcodeAgent?.workspace
      if (!workspaceApi?.createProjectDirectory) {
        throw new Error('当前环境不能创建本地项目目录，请在桌面客户端中使用。')
      }

      const projectPath = values.projectPath.trim()
      const schema = buildApplicationSchema(values)
      const projectDirectory = await workspaceApi.createProjectDirectory({
        workspacePath: projectPath,
        applicationConfig: schema
      })
      const application: ApplicationConfig = {
        ...schema,
        id: createApplicationId(),
        name: schema.appName,
        workspaceRoot: projectDirectory.path,
        projectParentPath: '',
        projectDirectoryName: pathBasename(projectPath),
        source: 'new',
        enableAuth: schema.auth.enable,
        enableTracking: schema.track.enable || schema.apiTrack.enable,
        legacyTheme: 'custom',
        legacyLayout: 'side-nav',
        enableTabs: false,
        pages: ['默认页面'],
        defaultPage: '默认页面',
        hasDynamicRoutes: false,
        schema,
        createdAt: Date.now()
      }
      await saveApplication(application)
      setSavedFormValues(values)
      setModalOpen(false)
      setPlanningThreadId(createPagePlanningThreadId())
      setPlanningApplication(application)
    } catch (error) {
      message.error(formatError(error, '创建应用失败'))
    } finally {
      setCreating(false)
    }
  }

  const handleCancelPlanning = (): void => {
    setPlanningApplication(undefined)
    setModalOpen(true)
    if (savedFormValues) {
      form.setFieldsValue(savedFormValues)
    }
  }

  // 在页面规划阶段同步应用设置到本地索引与完整 schema。
  const handleSettingsSave = async (values: SettingsValues): Promise<void> => {
    if (!planningApplication) return
    const updatedSchema = {
      ...planningApplication.schema,
      appName: values.appName,
      appIcon: values.appIcon,
      layout: values.layout,
      auth: values.auth,
      track: values.track,
      apiTrack: values.apiTrack
    }
    const updatedApp: ApplicationConfig = {
      ...planningApplication,
      appName: values.appName,
      appIcon: values.appIcon,
      name: values.appName,
      layout: values.layout,
      auth: values.auth,
      track: values.track,
      apiTrack: values.apiTrack,
      enableAuth: values.auth.enable,
      enableTracking: values.track.enable || values.apiTrack.enable,
      schema: updatedSchema
    }
    setPlanningApplication(updatedApp)
    await saveApplication(updatedApp)
  }

  // specs/plans 产物确认完整后直接打开工作台，不改写应用配置。
  const handlePagePlanConfirmed = async (_confirmation: ApplicationPlanningConfirmation): Promise<void> => {
    if (!planningApplication) return
    await saveAndOpenApplication(planningApplication, onOpenApplication)
    setPlanningApplication(undefined)
  }

  return (
    <>
      <WelcomeActionCard
        buttonIcon={<PlusOutlined />}
        buttonLabel="新建应用"
        description="配置应用骨架、页面、主题和内置模块，并指定项目创建位置。"
        icon={<PlusOutlined />}
        onClick={openModal}
        primary
        title="新建应用"
      />

      <Modal
        afterClose={() => {
          if (!planningApplication) {
            form.setFieldsValue(initialApplicationDraft)
          }
        }}
        cancelText="取消"
        confirmLoading={creating}
        destroyOnClose
        forceRender
        maskClosable={false}
        maskTransitionName=""
        okText="创建并规划页面"
        onCancel={() => setModalOpen(false)}
        onOk={handleCreateApplication}
        open={modalOpen}
        style={{ top: 24 }}
        title={
          <WelcomeModalTitle
            description="定义应用骨架、创建位置和基础能力"
            icon={<PlusOutlined />}
            title="新建应用"
          />
        }
        transitionName=""
        width={860}
        wrapClassName={cx('welcome-modal', 'create-application-modal', `theme-${theme}`)}
      >
        <ApplicationForm
          form={form}
          onSelectProjectParent={handleSelectProjectParent}
          selectingParent={selectingParent}
        />
      </Modal>

      {planningApplication ? (
        <ApplicationPagePlanningModal
          application={planningApplication}
          key={planningApplication.id}
          onCancel={handleCancelPlanning}
          onConfirmed={handlePagePlanConfirmed}
          onSettingsSave={handleSettingsSave}
          theme={theme}
          threadId={planningThreadId}
        />
      ) : null}
    </>
  )
}
