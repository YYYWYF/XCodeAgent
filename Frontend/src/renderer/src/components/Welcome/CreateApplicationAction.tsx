import { PlusOutlined } from '@ant-design/icons'
import { Form, message, Modal } from 'antd'
import { useState } from 'react'
import {
  createApplicationLifecycle,
  createPagePlanningThreadId
} from '../../service/applicationPagePlanning'
import type { ApplicationConfig, ApplicationDraft, ApplicationLifecycle } from '../../typings'
import { cx } from '../../utils'
import ApplicationForm from './ApplicationForm'
import WelcomeActionCard from './WelcomeActionCard'
import WelcomeModalTitle from './WelcomeModalTitle'
import './ApplicationFormModal.less'
import './WelcomeModal.less'
import { saveApplication } from './applicationService'
import { initialApplicationDraft } from './constants'
import { buildApplicationSchema, createApplicationId, formatError, pathBasename } from './utils'
import { fetchTemplateCode, type TemplateInitResponse } from '../../service/templateApi'

type Props = {
  disabled?: boolean
  onStartPlanning: (
    application: ApplicationConfig,
    threadId: string,
    lifecycle: ApplicationLifecycle
  ) => void
  theme: 'dark' | 'light'
}

// 创建应用基础配置，并把新应用交给独立的全屏规划页。
export default function CreateApplicationAction({
  disabled,
  onStartPlanning,
  theme
}: Props): JSX.Element {
  const [form] = Form.useForm<ApplicationDraft>()
  const [modalOpen, setModalOpen] = useState(false)
  const [creating, setCreating] = useState(false)
  const [fetchingTemplate, setFetchingTemplate] = useState(false)
  const [selectingParent, setSelectingParent] = useState(false)

  // 打开应用基础配置弹窗。
  const openModal = (): void => {
    setModalOpen(true)
  }

  // 调用桌面端目录选择器填写项目创建位置。
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

  // 创建项目目录和应用索引，然后进入全屏页面规划。
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
      const planningThreadId = createPagePlanningThreadId()
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
      const lifecycle = await createApplicationLifecycle(application, planningThreadId)

      // 调用模板工程拉取接口
      setFetchingTemplate(true)
      let templateResult: TemplateInitResponse | undefined
      try {
        templateResult = await fetchTemplateCode(schema, projectDirectory.path)
        console.log('[模板拉取成功]', templateResult)
      } catch (templateError) {
        console.error('[模板拉取失败]', templateError)
        // 模板拉取失败不阻塞流程，继续打开规划页面
        message.warning('模板拉取失败，请稍后在规划页面重试')
      } finally {
        setFetchingTemplate(false)
      }

      setModalOpen(false)
      onStartPlanning(application, planningThreadId, lifecycle)
    } catch (error) {
      message.error(formatError(error, '创建应用失败'))
    } finally {
      setCreating(false)
    }
  }

  return (
    <>
      <WelcomeActionCard
        buttonIcon={<PlusOutlined />}
        buttonLabel="新建应用"
        disabled={disabled}
        description={
          disabled
            ? '请先完成当前页面规划，再创建新的应用。'
            : '配置应用骨架、页面、主题和内置模块，并指定项目创建位置。'
        }
        icon={<PlusOutlined />}
        onClick={openModal}
        primary
        title="新建应用"
      />

      <Modal
        afterClose={() => {
          form.setFieldsValue(initialApplicationDraft)
        }}
        cancelText="取消"
        confirmLoading={creating || fetchingTemplate}
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
    </>
  )
}
