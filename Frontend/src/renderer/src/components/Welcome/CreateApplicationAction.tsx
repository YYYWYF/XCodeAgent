import { PlusOutlined } from '@ant-design/icons'
import { Form, message, Modal } from 'antd'
import { useState } from 'react'
import {
  createPagePlanningThreadId,
  requestPagePlanningQuestions
} from '../../service/applicationPagePlanning'
import { isAuthenticationFailure } from '../../service/authentication'
import type {
  ApplicationConfig,
  ApplicationDraft,
  ApplicationPagePlan,
  ConfirmedPagePlan,
  PagePlanningProgress,
  PagePlanningQuestion
} from '../../typings'
import { cx } from '../../utils'
import ApplicationForm from './ApplicationForm'
import ApplicationPagePlanningModal from './ApplicationPagePlanningModal'
import { appendPagePlanningProgress } from './ApplicationPlanningProgress'
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
  const [planningQuestions, setPlanningQuestions] = useState<PagePlanningQuestion[]>([])
  const [planningQuestionsError, setPlanningQuestionsError] = useState('')
  const [planningQuestionsLoading, setPlanningQuestionsLoading] = useState(false)
  const [planningQuestionsProgressEvents, setPlanningQuestionsProgressEvents] = useState<PagePlanningProgress[]>([])
  const [planningQuestionsStream, setPlanningQuestionsStream] = useState('')
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
      await loadPagePlanningQuestions(application, createPagePlanningThreadId())
    } catch (error) {
      message.error(formatError(error, '创建应用失败'))
    } finally {
      setCreating(false)
    }
  }

  // 创建页面规划线程并实时加载业务澄清问题。
  const loadPagePlanningQuestions = async (
    application: ApplicationConfig,
    threadId: string
  ): Promise<void> => {
    setPlanningApplication(application)
    setPlanningThreadId(threadId)
    setPlanningQuestions([])
    setPlanningQuestionsError('')
    setPlanningQuestionsLoading(true)
    setPlanningQuestionsProgressEvents([])
    setPlanningQuestionsStream('')
    try {
      const questions = await requestPagePlanningQuestions(
        {
          name: application.appName,
          scenario: application.senario,
          terminal: application.terminal
        },
        threadId,
        (progress) => setPlanningQuestionsProgressEvents(
          (history) => appendPagePlanningProgress(history, progress)
        ),
        setPlanningQuestionsStream
      )
      setPlanningQuestions(questions)
    } catch (error) {
      setPlanningQuestionsError(
        isAuthenticationFailure(error)
          ? '请重新登录后重试页面规划。'
          : formatError(error, '生成细节问题失败')
      )
    } finally {
      setPlanningQuestionsLoading(false)
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

  // 同步已确认的 menus 与 apis 到本地应用索引后打开应用。
  const handlePagePlanConfirmed = async (
    plan: ApplicationPagePlan,
    confirmation: ConfirmedPagePlan
  ): Promise<void> => {
    if (!planningApplication) return
    const pageNames = plan.pages.map((page) => page.name)
    const schema = {
      ...planningApplication.schema,
      menus: confirmation.menus,
      apis: confirmation.apis
    }
    const application = {
      ...planningApplication,
      menus: confirmation.menus,
      apis: confirmation.apis,
      schema,
      pages: pageNames,
      defaultPage: pageNames[0] || planningApplication.defaultPage
    }
    await saveAndOpenApplication(application, onOpenApplication)
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
          onRetryQuestions={() => loadPagePlanningQuestions(planningApplication, planningThreadId)}
          onSettingsSave={handleSettingsSave}
          questions={planningQuestions}
          questionsError={planningQuestionsError}
          questionsLoading={planningQuestionsLoading}
          questionsProgressEvents={planningQuestionsProgressEvents}
          questionsStream={planningQuestionsStream}
          theme={theme}
          threadId={planningThreadId}
        />
      ) : null}
    </>
  )
}
