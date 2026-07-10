import { PlusOutlined } from '@ant-design/icons'
import { Form, message, Modal } from 'antd'
import { useState } from 'react'
import type { ApplicationConfig, ApplicationDraft } from '../../typings'
import ApplicationForm from './ApplicationForm'
import WelcomeActionCard from './WelcomeActionCard'
import { saveAndOpenApplication } from './applicationService'
import { initialApplicationDraft } from './constants'
import { buildApplicationSchema, createApplicationId, formatError } from './utils'

type Props = {
  onOpenApplication: (application: ApplicationConfig) => void
}

export default function CreateApplicationAction({ onOpenApplication }: Props) {
  const [form] = Form.useForm<ApplicationDraft>()
  const [modalOpen, setModalOpen] = useState(false)
  const [creating, setCreating] = useState(false)
  const [selectingParent, setSelectingParent] = useState(false)

  const openModal = () => {
    form.setFieldsValue(initialApplicationDraft)
    setModalOpen(true)
  }

  const handleSelectProjectParent = async () => {
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

  const handleCreateApplication = async () => {
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
      await saveAndOpenApplication(application, onOpenApplication)
      setModalOpen(false)
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
        description="配置应用骨架、页面、主题和内置模块，并指定本地项目创建位置。"
        icon={<PlusOutlined />}
        onClick={openModal}
        primary
        title="新建应用"
      />

      <Modal
        bodyStyle={{ maxHeight: 'calc(100vh - 260px)', overflow: 'auto' }}
        confirmLoading={creating}
        destroyOnClose
        maskClosable={false}
        okText="创建并进入工作台"
        onCancel={() => setModalOpen(false)}
        onOk={handleCreateApplication}
        open={modalOpen}
        style={{ top: 24 }}
        title="新建应用"
        width={780}
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
