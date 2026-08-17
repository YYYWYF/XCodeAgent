import {
  AppstoreOutlined,
  BankOutlined,
  CloudOutlined,
  DashboardOutlined,
  DesktopOutlined,
  DownOutlined,
  FolderOpenOutlined,
  FundOutlined,
  LayoutOutlined,
  MessageOutlined,
  MobileOutlined,
  ProjectOutlined,
  RocketOutlined,
  ShopOutlined,
  ShoppingOutlined,
  TeamOutlined,
  ToolOutlined,
  UserOutlined
} from '@ant-design/icons'
import { Button, Dropdown, Input, message, Switch, Tooltip } from 'antd'
import type { ComponentType } from 'react'
import { useState } from 'react'
import { createApplicationLifecycle } from '../../service/applicationLifecycle'
import { createPagePlanningThreadId } from '../../service/applicationPagePlanning'
import { createInitialVersion } from '../../service/applicationVersions'
import type {
  ApplicationConfig,
  ApplicationDraft,
  ApplicationLifecycle,
  ApplicationTerminal
} from '../../typings'
import { cx } from '../../utils'
import { saveApplication } from './applicationService'
import { applicationIconOptions, initialApplicationDraft, terminalLabels } from './constants'
import { buildApplicationSchema, createApplicationId, formatError, pathBasename } from './utils'
import './CreateApplicationComposer.less'

const { TextArea } = Input

type Props = {
  onOpenWorkbenchAfterCreate: (
    application: ApplicationConfig,
    lifecycle: ApplicationLifecycle
  ) => void
  theme: 'dark' | 'light'
}

type LayoutType = ApplicationDraft['layout']['type']
type ComposerMenu = 'path' | 'terminal' | 'icon' | 'layout'

const iconComponents: Record<string, ComponentType> = {
  AppstoreOutlined,
  BankOutlined,
  CloudOutlined,
  DashboardOutlined,
  DesktopOutlined,
  FundOutlined,
  MessageOutlined,
  ProjectOutlined,
  ShopOutlined,
  ShoppingOutlined,
  TeamOutlined,
  ToolOutlined,
  UserOutlined
}

const layoutOptions: Array<{ value: LayoutType; label: string }> = [
  { value: 'side', label: '左侧导航' },
  { value: 'top', label: '顶部导航' },
  { value: 'mix', label: '混合导航' }
]

const demoScenario =
  '我想制作一个武汉分行需求回检应用，帮助业务人员完成回检任务的填报、审核与结果跟踪。'

/** 创建首页输入器使用的初始草稿；项目位置必须由用户本次主动选择。 */
function createComposerDraft(): ApplicationDraft {
  return {
    ...initialApplicationDraft,
    projectPath: '',
    senario: '',
    layout: { ...initialApplicationDraft.layout },
    theme: { ...initialApplicationDraft.theme },
    datasource: {
      ...initialApplicationDraft.datasource,
      db: {
        ...initialApplicationDraft.datasource.db,
        plantMode: { ...initialApplicationDraft.datasource.db.plantMode }
      }
    },
    menus: { ...initialApplicationDraft.menus }
  }
}

/** 使用轻量线框图说明不同导航布局，帮助用户在选择前理解页面结构。 */
function LayoutPreview({ type }: { type: LayoutType }): JSX.Element {
  return (
    <span className={cx('welcome-layout-preview', `is-${type}`)} aria-hidden="true">
      <i className={cx('preview-header')} />
      <i className={cx('preview-side')} />
      <i className={cx('preview-body')} />
    </span>
  )
}

/** 根据导航类型返回首页摘要文案。 */
function layoutLabel(layoutType: LayoutType): string {
  return layoutOptions.find((option) => option.value === layoutType)?.label || '选择导航'
}

/** 渲染首页中央的新建应用输入器，并沿用原创建、持久化和 v1.0 初始化流程。 */
export default function CreateApplicationAction({
  onOpenWorkbenchAfterCreate,
  theme
}: Props): JSX.Element {
  const [draft, setDraft] = useState<ApplicationDraft>(createComposerDraft)
  const [creating, setCreating] = useState(false)
  const [selectingParent, setSelectingParent] = useState(false)
  const [openMenu, setOpenMenu] = useState<ComposerMenu>()

  const SelectedIcon = iconComponents[draft.appIcon] || AppstoreOutlined
  const projectFolderName = draft.projectPath ? pathBasename(draft.projectPath) : ''
  const canCreate = Boolean(draft.projectPath.trim() && draft.senario.trim())

  /** 调用桌面端目录选择器，并把用户选择的路径写入创建草稿。 */
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
        setDraft((current) => ({
          ...current,
          projectPath: result.path || '',
          senario: current.senario.trim() ? current.senario : demoScenario
        }))
        setOpenMenu(undefined)
      }
    } catch (error) {
      message.error(formatError(error, '选择文件夹失败'))
    } finally {
      setSelectingParent(false)
    }
  }

  /** 创建项目目录和应用索引，然后直接进入工作台设计阶段。 */
  const handleCreateApplication = async (): Promise<void> => {
    if (!canCreate || creating) return
    setCreating(true)
    try {
      const workspaceApi = window.xcodeAgent?.workspace
      if (!workspaceApi?.createProjectDirectory) {
        throw new Error('当前环境不能创建本地项目目录，请在桌面客户端中使用。')
      }

      const projectPath = draft.projectPath.trim()
      const schema = buildApplicationSchema(draft)
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
      const initialVersion = createInitialVersion(application.id, lifecycle, Date.now())
      application.versions = [initialVersion]
      application.currentVersionId = initialVersion.id
      await saveApplication(application)
      onOpenWorkbenchAfterCreate(application, initialVersion.lifecycle)
    } catch (error) {
      message.error(formatError(error, '创建应用失败'))
    } finally {
      setCreating(false)
    }
  }

  const pathMenu = (
    <div className={cx('welcome-create-menu', 'is-path', `theme-${theme}`)}>
      <button type="button" onClick={() => void handleSelectProjectParent()}>
        <FolderOpenOutlined />
        <span>
          <strong>{draft.projectPath ? '重新选择项目位置' : '选择项目位置'}</strong>
          <small>打开系统文件夹选择器</small>
        </span>
      </button>
    </div>
  )

  const terminalMenu = (
    <div className={cx('welcome-create-menu', `theme-${theme}`)}>
      {(Object.entries(terminalLabels) as Array<[ApplicationTerminal, string]>).map(
        ([value, label]) => (
          <button
            className={cx(draft.terminal === value && 'is-selected')}
            key={value}
            type="button"
            onClick={() => {
              setDraft((current) => ({ ...current, terminal: value }))
              setOpenMenu(undefined)
            }}
          >
            {value === 'PC' ? <DesktopOutlined /> : <MobileOutlined />}
            <span>{label}</span>
          </button>
        )
      )}
    </div>
  )

  const iconMenu = (
    <div className={cx('welcome-create-menu', 'is-icons', `theme-${theme}`)}>
      {applicationIconOptions.map((option) => {
        const Icon = iconComponents[option.value] || AppstoreOutlined
        return (
          <Tooltip key={option.value} mouseEnterDelay={0.45} title={option.label}>
            <button
              aria-label={option.label}
              className={cx(draft.appIcon === option.value && 'is-selected')}
              type="button"
              onClick={() => {
                setDraft((current) => ({ ...current, appIcon: option.value }))
                setOpenMenu(undefined)
              }}
            >
              <Icon />
            </button>
          </Tooltip>
        )
      })}
    </div>
  )

  const layoutMenu = (
    <div
      className={cx('welcome-create-menu', 'is-layout', `theme-${theme}`)}
      onClick={(event) => event.stopPropagation()}
    >
      <div className={cx('welcome-layout-section')}>
        <span>导航布局</span>
        <div className={cx('welcome-layout-options')}>
          {layoutOptions.map((option) => (
            <button
              aria-pressed={draft.layout.type === option.value}
              className={cx(draft.layout.type === option.value && 'is-selected')}
              key={option.value}
              type="button"
              onClick={() =>
                setDraft((current) => ({
                  ...current,
                  layout: { ...current.layout, type: option.value }
                }))
              }
            >
              <LayoutPreview type={option.value} />
              <span>{option.label}</span>
            </button>
          ))}
        </div>
      </div>

      <div className={cx('welcome-layout-settings')}>
        <label>
          <span>
            <strong>页头</strong>
            <small>显示应用顶部区域</small>
          </span>
          <Switch
            checked={draft.layout.useHeader}
            checkedChildren="开"
            onChange={(checked) =>
              setDraft((current) => ({
                ...current,
                layout: { ...current.layout, useHeader: checked }
              }))
            }
            unCheckedChildren="关"
          />
        </label>
        <label>
          <span>
            <strong>页脚</strong>
            <small>显示应用底部区域</small>
          </span>
          <Switch
            checked={draft.layout.useFooter}
            checkedChildren="开"
            onChange={(checked) =>
              setDraft((current) => ({
                ...current,
                layout: { ...current.layout, useFooter: checked }
              }))
            }
            unCheckedChildren="关"
          />
        </label>
        <div className={cx('welcome-menu-setting')}>
          <label>
            <span>
              <strong>菜单</strong>
              <small>生成应用菜单与路由</small>
            </span>
            <Switch
              checked={draft.menus.enable}
              checkedChildren="开"
              onChange={(checked) =>
                setDraft((current) => ({
                  ...current,
                  menus: { ...current.menus, enable: checked }
                }))
              }
              unCheckedChildren="关"
            />
          </label>

          {draft.menus.enable ? (
            <label className={cx('welcome-menu-root')}>
              <span>菜单根路径</span>
              <Input
                onChange={(event) =>
                  setDraft((current) => ({
                    ...current,
                    menus: { ...current.menus, rootPath: event.target.value }
                  }))
                }
                placeholder="/page"
                size="small"
                value={draft.menus.rootPath}
              />
            </label>
          ) : null}
        </div>
      </div>
    </div>
  )

  return (
    <section className={cx('welcome-create-composer')} aria-label="新建应用">
      <div className={cx('welcome-create-intro')}>
        <strong>从一个真实场景开始</strong>
        <span>XCodeAgent 将据此完成需求设计</span>
      </div>
      <TextArea
        aria-label="应用场景"
        autoSize={{ minRows: 3, maxRows: 6 }}
        maxLength={1200}
        onChange={(event) => setDraft((current) => ({ ...current, senario: event.target.value }))}
        placeholder="描述你想解决的问题、关键流程，或希望这款应用为团队带来的改变……"
        value={draft.senario}
      />

      <div className={cx('welcome-create-toolbar')}>
        <div className={cx('welcome-create-options')}>
          <Dropdown
            onVisibleChange={(visible) => setOpenMenu(visible ? 'path' : undefined)}
            overlay={pathMenu}
            placement="bottomLeft"
            trigger={['click']}
            visible={openMenu === 'path'}
          >
            <button
              className={cx('welcome-create-option', !projectFolderName && 'is-required')}
              title={draft.projectPath || '项目位置（必填）'}
              type="button"
            >
              <FolderOpenOutlined spin={selectingParent} />
              <span>{projectFolderName || '项目位置'}</span>
              {!projectFolderName ? <em>必填</em> : null}
              <DownOutlined />
            </button>
          </Dropdown>

          <Dropdown
            onVisibleChange={(visible) => setOpenMenu(visible ? 'terminal' : undefined)}
            overlay={terminalMenu}
            placement="bottomLeft"
            trigger={['click']}
            visible={openMenu === 'terminal'}
          >
            <button className={cx('welcome-create-option')} type="button">
              {draft.terminal === 'PC' ? <DesktopOutlined /> : <MobileOutlined />}
              <span>{terminalLabels[draft.terminal]}</span>
              <DownOutlined />
            </button>
          </Dropdown>

          <Dropdown
            onVisibleChange={(visible) => setOpenMenu(visible ? 'icon' : undefined)}
            overlay={iconMenu}
            placement="bottomLeft"
            trigger={['click']}
            visible={openMenu === 'icon'}
          >
            <button
              aria-label="选择应用图标"
              className={cx('welcome-create-option')}
              title="选择应用图标"
              type="button"
            >
              <SelectedIcon />
              <span>选择图标</span>
              <DownOutlined />
            </button>
          </Dropdown>

          <Dropdown
            onVisibleChange={(visible) => setOpenMenu(visible ? 'layout' : undefined)}
            overlay={layoutMenu}
            placement="bottomLeft"
            trigger={['click']}
            visible={openMenu === 'layout'}
          >
            <button className={cx('welcome-create-option')} type="button">
              <LayoutOutlined />
              <span>{layoutLabel(draft.layout.type)}</span>
              <DownOutlined />
            </button>
          </Dropdown>
        </div>

        <Button
          className={cx('welcome-create-submit')}
          disabled={!canCreate}
          icon={<RocketOutlined />}
          loading={creating}
          onClick={() => void handleCreateApplication()}
          size="large"
          title={!draft.projectPath ? '请先选择项目位置' : undefined}
          type="primary"
        >
          开始应用设计
        </Button>
      </div>
    </section>
  )
}
