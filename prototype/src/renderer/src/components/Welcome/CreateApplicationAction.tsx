import { PlusOutlined } from '@ant-design/icons'
import { Form, message, Modal } from 'antd'
import { useState } from 'react'
import { randomUUID } from '@ag-ui/client'
import { createApplicationLifecycle } from '../../service/applicationLifecycle'
import type { ApplicationConfig, ApplicationDraft, ApplicationLifecycle } from '../../typings'
import { cx } from '../../utils'
import ApplicationForm from './ApplicationForm'
import WelcomeActionCard from './WelcomeActionCard'
import WelcomeModalTitle from './WelcomeModalTitle'
import './ApplicationFormModal.less'
import './WelcomeModal.less'
import { saveApplication } from './applicationService'
import { createInitialVersion } from '../../service/applicationVersions'
import { initialApplicationDraft } from './constants'
import { buildApplicationSchema, createApplicationId, formatError, pathBasename } from './utils'

/** 为新建应用生成稳定的规划线程标识，随 lifecycle create 一起提交。 */
function createPagePlanningThreadId(): string {
  return randomUUID()
}

type Props = {
  onOpenWorkbenchAfterCreate: (
    application: ApplicationConfig,
    lifecycle: ApplicationLifecycle
  ) => void
}

// 创建应用基础配置，创建后直接进工作台需求分析阶段。
export default function CreateApplicationAction({
  onOpenWorkbenchAfterCreate
}: Props): JSX.Element {
  const [form] = Form.useForm<ApplicationDraft>()
  const [modalOpen, setModalOpen] = useState(false)
  const [creating, setCreating] = useState(false)
  const [selectingParent, setSelectingParent] = useState(false)

  // 打开应用基础配置弹窗，并异步预填一个全新的合法项目目录：
  // 演示旅程要求“新建应用”开箱即走，用户不应手输目录，更不应撞上已有应用目录
  // 而被“已有 XCodeAgent 应用目录不能复用”拦截。
  const openModal = (): void => {
    setModalOpen(true)
    void prefillProjectDirectory()
  }

  /** 预填新项目目录；仅在用户尚未填写时写入，不覆盖手动输入，失败时仍可手动选择。 */
  const prefillProjectDirectory = async (): Promise<void> => {
    try {
      const workspaceApi = window.xcodeAgent?.workspace
      if (!workspaceApi?.selectDirectory) return
      const result = await workspaceApi.selectDirectory({ title: '选择新应用的创建位置' })
      const currentPath = String(form.getFieldValue('projectPath') || '').trim()
      if (!result.canceled && result.path && !currentPath) {
        form.setFieldsValue({ projectPath: result.path })
      }
    } catch {
      // 预填失败不打断弹窗；目录仍可手动输入或点击选择文件夹。
    }
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

  // 创建项目目录和应用索引，然后直接进入工作台需求分析阶段。
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
      // 注入初始版本 v1.0(迭代中) —— 版本概念从新建即建立,lifecycle 挂到 v1。
      const initialVersion = createInitialVersion(application.id, lifecycle, Date.now())
      application.versions = [initialVersion]
      application.currentVersionId = initialVersion.id
      await saveApplication(application)

      setModalOpen(false)
      onOpenWorkbenchAfterCreate(application, initialVersion.lifecycle)
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
        description="配置应用骨架、页面、主题和内置模块，并指定项目创建位置。"
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
        confirmLoading={creating}
        destroyOnClose
        forceRender
        maskClosable={false}
        maskTransitionName=""
        okText="开始需求分析"
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
        wrapClassName={cx('welcome-modal', 'create-application-modal', 'theme-light')}
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
