import { PlusOutlined } from '@ant-design/icons'
import { Form, message, Modal } from 'antd'
import { useEffect, useRef, useState } from 'react'
import { useBranchNameValidation } from '../../hooks/useBranchNameValidation'
import { initializeApplication } from '../../service/applicationLifecycle'
import { createPagePlanningThreadId } from '../../service/applicationPagePlanning'
import { emitTelemetryEvent } from '../../service/telemetry'
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

// 创建应用基础配置，并把新应用交给独立的全屏规划页。
export default function CreateApplicationAction({ onStartPlanning, theme }: Props): JSX.Element {
  const [form] = Form.useForm<ApplicationDraft>()
  const [modalOpen, setModalOpen] = useState(false)
  const [creating, setCreating] = useState(false)
  const creatingRef = useRef(false)
  const [selectingParent, setSelectingParent] = useState(false)
  const creationIdentity = useRef({ id: createApplicationId(), threadId: createPagePlanningThreadId() })
  const branchEdited = useRef(false)
  const branchName = Form.useWatch('branchName', form) ?? 'v1_0'
  const repoUrl = Form.useWatch('repoUrl', form) ?? ''
  const branchValidation = useBranchNameValidation({
    enabled: modalOpen, repoUrl, branchName, localNames: [], suggestionBase: 'v1_0', includeBase: true
  })
  useEffect(() => {
    // 只调整未手动修改的默认值；用户输入后只展示推荐，不再自动覆盖。
    if (modalOpen && !branchEdited.current && !creating && branchValidation.suggestion) {
      form.setFieldsValue({ branchName: branchValidation.suggestion })
    }
  }, [modalOpen, creating, branchValidation.suggestion, form])

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

  // 完整创建成功后才保存首页索引，并进入规划页。
  const handleCreateApplication = async (): Promise<void> => {
    if (creatingRef.current) return
    creatingRef.current = true
    setCreating(true)
    try {
      await form.validateFields()
      // 关闭能力的字段未挂载，校验结果只含已注册字段；提交仍须保留完整初始化草稿。
      const values = form.getFieldsValue(true) as ApplicationDraft
      if (!window.devAgentStudio?.isElectron) {
        throw new Error('当前环境不能创建本地项目目录，请在桌面客户端中使用。')
      }

      const projectPath = values.projectPath.trim()
      // 点击确认时复检，冲突或查询失败保留弹窗与输入，不创建目录。
      if (!(await branchValidation.validate())) return
      const schema = buildApplicationSchema(values)
      const persistedSchema = await encryptSensitiveDatasourceFields(schema)
      const planningThreadId = creationIdentity.current.threadId
      const application: ApplicationConfig = {
        ...persistedSchema,
        id: creationIdentity.current.id,
        name: persistedSchema.appName,
        workspaceRoot: projectPath,
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
      const created = await initializeApplication(application, persistedSchema, planningThreadId)
      const persistedApplication = await saveApplication(created.application)
      // 业务功能埋点：新建应用完成
      emitTelemetryEvent({
        record_id: `application_created:${persistedApplication.id}`,
        identity: { project_id: persistedApplication.id },
        data: { event_name: 'application_created', payload: { creation_source: 'new_app' } }
      })

      setModalOpen(false)
      onStartPlanning(persistedApplication, created.lifecycle.initialization.threadId || planningThreadId, created.lifecycle)
      creationIdentity.current = { id: createApplicationId(), threadId: createPagePlanningThreadId() }
    } catch (error) {
      message.error(formatError(error, '创建应用失败'))
    } finally {
      creatingRef.current = false
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
          branchEdited.current = false
        }}
        cancelText="取消"
        confirmLoading={creating}
        destroyOnClose
        maskClosable={false}
        maskTransitionName=""
        okText="创建并规划页面"
        okButtonProps={{ disabled: branchValidation.checking || Boolean(branchValidation.error) }}
        cancelButtonProps={{ disabled: creating }}
        closable={!creating}
        onCancel={() => { if (!creatingRef.current) setModalOpen(false) }}
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
          branchValidation={branchValidation}
          onBranchNameEdited={() => { branchEdited.current = true }}
          disabled={creating}
          onSelectProjectParent={handleSelectProjectParent}
          selectingParent={selectingParent}
        />
      </Modal>
    </>
  )
}
