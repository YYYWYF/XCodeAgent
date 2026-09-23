import { PlusOutlined } from '@ant-design/icons'
import { Form, message, Modal } from 'antd'
import { useState } from 'react'
import { createApplicationLifecycle } from '../../service/applicationLifecycle'
import { createPagePlanningThreadId } from '../../service/applicationPagePlanning'
import { checkRemoteBranch } from '../../service/repositoryBranch'
import { encryptSensitiveDatasourceFields } from '../../service/databaseCredentialCrypto'
import type { ApplicationConfig, ApplicationDraft, ApplicationLifecycle } from '../../typings'
import { cx } from '../../utils'
import ApplicationForm from './ApplicationForm'
import WelcomeActionCard from './WelcomeActionCard'
import WelcomeModalTitle from './WelcomeModalTitle'
import './ApplicationFormModal.less'
import './WelcomeModal.less'
import { saveApplication } from './applicationService'
import { buildApplicationSchema, createApplicationId, formatError, pathBasename } from './utils'

type Props = {
  onStartPlanning: (
    application: ApplicationConfig,
    threadId: string,
    lifecycle: ApplicationLifecycle
  ) => void
  theme: 'dark' | 'light'
}

/**
 * 提交前处理"远端已有同名分支"：检查存在性，需要时弹窗让用户确认覆盖。
 *
 * 返回 proceed=false 表示用户选择返回修改，调用方应中止创建。
 * **检查本身失败（断网、令牌失效、仓库不可达）不阻断创建** —— 按"未知"处理继续，
 * 真正的推送发生在模板基线阶段，那里失败会以提示的形式反馈，不影响应用创建与规划。
 */
async function resolveBranchOverwrite(
  repoUrl: string,
  branchName: string,
  theme: 'dark' | 'light'
): Promise<{ proceed: boolean; overwriteConfirmed: boolean }> {
  if (!repoUrl || !branchName) return { proceed: true, overwriteConfirmed: false }

  let exists = false
  try {
    exists = (await checkRemoteBranch({ repoUrl, branchName })).exists
  } catch (error) {
    console.warn('[远端分支检查失败，按未知处理并继续创建]', error)
    return { proceed: true, overwriteConfirmed: false }
  }
  if (!exists) return { proceed: true, overwriteConfirmed: false }

  return new Promise((resolve) => {
    Modal.confirm({
      cancelText: '返回修改',
      centered: true,
      content: (
        <div className={cx('welcome-branch-overwrite-confirmation')}>
          <p>
            远端仓库里已经有一个叫 <strong>{branchName}</strong> 的分支。
          </p>
          <p>继续创建会把它的代码覆盖掉，原来的内容找不回来。</p>
        </div>
      ),
      okButtonProps: { danger: true },
      okText: '继续并覆盖',
      onCancel: () => resolve({ proceed: false, overwriteConfirmed: false }),
      onOk: () => resolve({ proceed: true, overwriteConfirmed: true }),
      title: `分支 ${branchName} 已存在，确定要覆盖吗？`,
      wrapClassName: cx('welcome-modal', `theme-${theme}`)
    })
  })
}

// 创建应用基础配置，并把新应用交给独立的全屏规划页。
export default function CreateApplicationAction({ onStartPlanning, theme }: Props): JSX.Element {
  const [form] = Form.useForm<ApplicationDraft>()
  const [modalOpen, setModalOpen] = useState(false)
  const [creating, setCreating] = useState(false)
  const [selectingParent, setSelectingParent] = useState(false)

  // 打开应用基础配置弹窗。
  const openModal = (): void => {
    setModalOpen(true)
  }

  // 调用桌面端目录选择器填写项目创建位置。
  const handleSelectProjectParent = async (): Promise<void> => {
    setSelectingParent(true)
    try {
      const workspaceApi = window.devAgentStudio?.workspace
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
      const workspaceApi = window.devAgentStudio?.workspace
      if (!workspaceApi?.createProjectDirectory) {
        throw new Error('当前环境不能创建本地项目目录，请在桌面客户端中使用。')
      }

      const projectPath = values.projectPath.trim()
      // 远端已存在同名分支时先让用户确认覆盖，确认结果随应用配置一起落盘。
      const branchDecision = await resolveBranchOverwrite(
        values.repoUrl.trim(),
        values.branchName.trim(),
        theme
      )
      if (!branchDecision.proceed) return

      const schema = buildApplicationSchema(values, {
        branchOverwriteConfirmed: branchDecision.overwriteConfirmed
      })
      const persistedSchema = await encryptSensitiveDatasourceFields(schema)
      const planningThreadId = createPagePlanningThreadId()
      const projectDirectory = await workspaceApi.createProjectDirectory({
        workspacePath: projectPath,
        applicationConfig: persistedSchema
      })
      const application: ApplicationConfig = {
        ...persistedSchema,
        id: createApplicationId(),
        name: persistedSchema.appName,
        workspaceRoot: projectDirectory.path,
        projectParentPath: '',
        projectDirectoryName: pathBasename(projectPath),
        source: 'new',
        legacyTheme: 'custom',
        legacyLayout: 'side-nav',
        enableTabs: false,
        pages: ['默认页面'],
        defaultPage: '默认页面',
        hasDynamicRoutes: false,
        lastOpenedAt: Date.now()
      }
      const persistedApplication = await saveApplication(application)
      const lifecycle = await createApplicationLifecycle(persistedApplication, planningThreadId)

      setModalOpen(false)
      onStartPlanning(persistedApplication, planningThreadId, lifecycle)
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
          form.resetFields()
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
    </>
  )
}
