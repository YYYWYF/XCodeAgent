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
  PagePlanningQuestion
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
import { buildApplicationSchema, createApplicationId, formatError } from './utils'

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
  const [planningThreadId, setPlanningThreadId] = useState('')

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
        form.setFieldsValue({ projectParentPath: result.path })
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

      const projectParentPath = values.projectParentPath.trim()
      const projectDirectoryName = values.projectDirectoryName.trim()
      const schema = buildApplicationSchema(values)
      const projectDirectory = await workspaceApi.createProjectDirectory({
        parentPath: projectParentPath,
        projectName: projectDirectoryName,
        applicationConfig: schema
      })
      const application: ApplicationConfig = {
        ...schema,
        id: createApplicationId(),
        name: schema.appName,
        workspaceRoot: projectDirectory.path,
        projectParentPath,
        projectDirectoryName,
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
      setModalOpen(false)
      await loadPagePlanningQuestions(application, createPagePlanningThreadId())
    } catch (error) {
      message.error(formatError(error, '创建应用失败'))
    } finally {
      setCreating(false)
    }
  }

  const loadPagePlanningQuestions = async (
    application: ApplicationConfig,
    threadId: string
  ): Promise<void> => {
    setPlanningApplication(application)
    setPlanningThreadId(threadId)
    setPlanningQuestions([])
    setPlanningQuestionsError('')
    setPlanningQuestionsLoading(true)
    try {
      const questions = await requestPagePlanningQuestions(
        {
          name: application.appName,
          scenario: application.senario,
          terminal: application.terminal
        },
        threadId
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

  const handlePagePlanConfirmed = async (
    plan: ApplicationPagePlan,
    confirmation: ConfirmedPagePlan
  ): Promise<void> => {
    if (!planningApplication) return
    const pageNames = plan.pages.map((page) => page.name)
    const schema = { ...planningApplication.schema, menus: confirmation.menus }
    const application = {
      ...planningApplication,
      menus: confirmation.menus,
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
        description="配置应用骨架、页面、主题和内置模块，并指定本地项目创建位置。"
        icon={<PlusOutlined />}
        onClick={openModal}
        primary
        title="新建应用"
      />

      <Modal
        afterClose={() => form.setFieldsValue(initialApplicationDraft)}
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
          onConfirmed={handlePagePlanConfirmed}
          onRetryQuestions={() => loadPagePlanningQuestions(planningApplication, planningThreadId)}
          questions={planningQuestions}
          questionsError={planningQuestionsError}
          questionsLoading={planningQuestionsLoading}
          threadId={planningThreadId}
        />
      ) : null}
    </>
  )
}
