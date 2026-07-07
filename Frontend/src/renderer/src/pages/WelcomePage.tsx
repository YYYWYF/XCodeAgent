import {
  ApiOutlined,
  CodeOutlined,
  FolderAddOutlined,
  FolderOpenOutlined,
  PlusOutlined,
  RocketOutlined,
} from '@ant-design/icons';
import {
  Button,
  Empty,
  Form,
  Input,
  List,
  message,
  Modal,
  Radio,
  Select,
  Space,
  Switch,
  Tag,
  Typography,
} from 'antd';
import { useMemo, useState } from 'react';
import type {
  ApplicationAudience,
  ApplicationConfig,
  ApplicationDraft,
  ApplicationLayout,
  ApplicationTerminal,
  ApplicationTheme,
} from '../typings';
import {
  loadStoredApplications,
  saveStoredApplications,
} from '../service/applicationStorage';
import {
  canListSessionWorkspaces,
  listSessionWorkspaces,
  type SessionWorkspaceSummary,
} from '../service/chatSessions';
import { cx } from '../utils';
import './WelcomePage.less';

const { Paragraph, Text, Title } = Typography;
const { TextArea } = Input;

const audienceLabels: Record<ApplicationAudience, string> = {
  operator: '内部运营人员',
  admin: '管理员',
  user: '普通用户',
  customer: '客户',
  developer: '开发或配置人员',
  other: '其他',
};

const terminalLabels: Record<ApplicationTerminal, string> = {
  pc: 'PC Web',
  mobile: '移动端 Web',
  responsive: 'PC + 移动端响应式',
};

const themeLabels: Record<ApplicationTheme, string> = {
  light: '默认浅色',
  dark: '深色',
  'enterprise-blue': '企业蓝',
  custom: '自定义主题',
};

const layoutLabels: Record<ApplicationLayout, string> = {
  'top-nav': '顶部导航',
  'side-nav': '侧边导航',
  'top-side-nav': '顶部 + 侧边导航',
  immersive: '单页沉浸式',
  'login-admin': '登录页 + 后台框架',
};

const initialDraft: ApplicationDraft = {
  name: '',
  projectParentPath: '',
  projectDirectoryName: '',
  audience: 'operator',
  terminal: 'pc',
  enableAuth: true,
  enableTracking: false,
  theme: 'light',
  layout: 'login-admin',
  enableTabs: false,
  pagesText: '首页\n用户管理\n系统设置',
  hasDynamicRoutes: false,
};

type Props = {
  onOpenApplication: (application: ApplicationConfig) => void;
};

function parsePages(value?: string) {
  return Array.from(
    new Set(
      (value ?? '')
        .split(/[\n,，、]/)
        .map((page) => page.trim())
        .filter(Boolean),
    ),
  );
}

function createApplicationId() {
  return `${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
}

function pathBasename(value: string) {
  const normalizedValue = value.replace(/[\\/]+$/, '');
  return normalizedValue.split(/[\\/]/).pop() || normalizedValue || '未命名项目';
}

function pathDirname(value: string) {
  const normalizedValue = value.replace(/[\\/]+$/, '');
  const index = Math.max(normalizedValue.lastIndexOf('/'), normalizedValue.lastIndexOf('\\'));
  return index > 0 ? normalizedValue.slice(0, index) : '';
}

function toProjectDirectoryName(value: string) {
  return (
    value
      .trim()
      .replace(/[<>:"/\\|?*\x00-\x1F]/g, '')
      .replace(/\s+/g, '-')
      .slice(0, 80) || ''
  );
}

function joinLocalPath(parentPath: string, directoryName: string) {
  const parent = parentPath.trim().replace(/[\\/]+$/, '');
  const directory = directoryName.trim();
  if (!parent || !directory) return '';
  const separator = parent.includes('\\') ? '\\' : '/';
  return `${parent}${separator}${directory}`;
}

function formatError(error: unknown, fallback: string) {
  return error instanceof Error ? error.message : fallback;
}

function formatHistoryTime(value: number): string {
  if (!value) return '未知时间';
  return new Intl.DateTimeFormat('zh-CN', {
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  }).format(value);
}

function validateProjectDirectoryName(_: unknown, value?: string) {
  if (!value?.trim()) return Promise.reject(new Error('请输入项目文件夹名'));
  if (/[<>:"/\\|?*\x00-\x1F]/.test(value.trim())) {
    return Promise.reject(new Error('项目文件夹名不能包含路径分隔符或特殊字符'));
  }
  return Promise.resolve();
}

export default function WelcomePage({ onOpenApplication }: Props) {
  const [form] = Form.useForm<ApplicationDraft>();
  const [modalOpen, setModalOpen] = useState(false);
  const [pagesText, setPagesText] = useState(initialDraft.pagesText);
  const [creating, setCreating] = useState(false);
  const [openingWorkspace, setOpeningWorkspace] = useState(false);
  const [workspaceHistoryOpen, setWorkspaceHistoryOpen] = useState(false);
  const [workspaceHistory, setWorkspaceHistory] = useState<SessionWorkspaceSummary[]>([]);
  const [openingWorkspaceRoot, setOpeningWorkspaceRoot] = useState<string>();
  const [selectingParent, setSelectingParent] = useState(false);
  const pageOptions = useMemo(
    () => parsePages(pagesText).map((page) => ({ label: page, value: page })),
    [pagesText],
  );

  const openCreateModal = () => {
    setPagesText(initialDraft.pagesText);
    form.setFieldsValue({
      ...initialDraft,
      defaultPage: parsePages(initialDraft.pagesText)[0],
    });
    setModalOpen(true);
  };

  const closeCreateModal = () => {
    setModalOpen(false);
  };

  const selectDirectory = async (title: string) => {
    const workspaceApi = window.xcodeAgent?.workspace;
    if (!workspaceApi?.selectDirectory) {
      message.warning('当前环境不能打开系统目录选择器，请在桌面客户端中使用。');
      return null;
    }

    const result = await workspaceApi.selectDirectory({ title });
    if (result.canceled || !result.path) return null;
    return result.path;
  };

  const saveAndOpenApplication = async (application: ApplicationConfig) => {
    const storedApplications = await loadStoredApplications();
    const nextApplications = [
      application,
      ...storedApplications.filter(
        (storedApplication) =>
          storedApplication.id !== application.id &&
          (!application.workspaceRoot || storedApplication.workspaceRoot !== application.workspaceRoot),
      ),
    ];
    await saveStoredApplications(nextApplications);
    onOpenApplication(application);
  };

  const handleSelectProjectParent = async () => {
    setSelectingParent(true);
    try {
      const selectedPath = await selectDirectory('选择新应用的创建位置');
      if (selectedPath) form.setFieldsValue({ projectParentPath: selectedPath });
    } catch (error) {
      message.error(formatError(error, '选择文件夹失败'));
    } finally {
      setSelectingParent(false);
    }
  };

  const handleOpenExistingWorkspace = async (): Promise<void> => {
    setOpeningWorkspace(true);
    try {
      if (!canListSessionWorkspaces()) {
        message.warning('当前环境不能读取本地历史工作目录，请在桌面客户端中使用。');
        return;
      }

      setWorkspaceHistory(await listSessionWorkspaces());
      setWorkspaceHistoryOpen(true);
    } catch (error) {
      message.error(formatError(error, '读取历史工作目录失败'));
    } finally {
      setOpeningWorkspace(false);
    }
  };

  const closeWorkspaceHistory = (): void => {
    setWorkspaceHistoryOpen(false);
  };

  const openHistoryWorkspace = async (workspace: SessionWorkspaceSummary): Promise<void> => {
    setOpeningWorkspaceRoot(workspace.workspaceRoot);
    try {
      const workspaceName = workspace.name || pathBasename(workspace.workspaceRoot);
      const application: ApplicationConfig = {
        id: createApplicationId(),
        name: workspaceName,
        workspaceRoot: workspace.workspaceRoot,
        projectParentPath: pathDirname(workspace.workspaceRoot),
        projectDirectoryName: workspaceName,
        source: 'existing-workspace',
        audience: 'developer',
        terminal: 'pc',
        enableAuth: false,
        enableTracking: false,
        theme: 'light',
        layout: 'login-admin',
        enableTabs: false,
        pages: ['工作台'],
        defaultPage: '工作台',
        hasDynamicRoutes: false,
        createdAt: Date.now(),
      };
      await saveAndOpenApplication(application);
      setWorkspaceHistoryOpen(false);
    } catch (error) {
      message.error(formatError(error, '打开历史工作目录失败'));
    } finally {
      setOpeningWorkspaceRoot(undefined);
    }
  };

  const handleCreateApplication = async () => {
    setCreating(true);
    try {
      const values = await form.validateFields();
      const workspaceApi = window.xcodeAgent?.workspace;
      if (!workspaceApi?.createProjectDirectory) {
        throw new Error('当前环境不能创建本地项目目录，请在桌面客户端中使用。');
      }

      const projectParentPath = values.projectParentPath.trim();
      const projectDirectoryName = values.projectDirectoryName.trim();
      const projectDirectory = await workspaceApi.createProjectDirectory({
        parentPath: projectParentPath,
        projectName: projectDirectoryName,
      });
      const pages = parsePages(values.pagesText);
      const application: ApplicationConfig = {
        id: createApplicationId(),
        name: values.name.trim(),
        workspaceRoot: projectDirectory.path,
        projectParentPath,
        projectDirectoryName,
        source: 'new',
        audience: values.audience,
        terminal: values.terminal,
        enableAuth: values.enableAuth,
        enableTracking: values.enableTracking,
        theme: values.theme,
        layout: values.layout,
        enableTabs: values.enableTabs,
        pages,
        defaultPage: values.defaultPage || pages[0],
        hasDynamicRoutes: values.hasDynamicRoutes,
        dynamicRouteDescription: values.dynamicRouteDescription?.trim(),
        createdAt: Date.now(),
      };
      await saveAndOpenApplication(application);
      setModalOpen(false);
    } catch (error) {
      message.error(formatError(error, '创建应用失败'));
    } finally {
      setCreating(false);
    }
  };

  return (
    <main className={cx('welcome-page')}>
      <section className={cx('welcome-shell')}>
        <header className={cx('welcome-hero')}>
          <div>
            <Text className={cx('welcome-kicker')}>LOCAL APP BUILDER</Text>
            <Title>XCodeAgent</Title>
            <Paragraph>
              面向本地项目的写代码助手，从需求规划到前端页面、后端接口和验证命令，围绕同一个工作目录推进。
            </Paragraph>
          </div>
          <Space className={cx('welcome-tags')} wrap>
            <Tag icon={<CodeOutlined />}>写代码</Tag>
            <Tag icon={<ApiOutlined />}>前后端协作</Tag>
            <Tag icon={<RocketOutlined />}>本地验证</Tag>
          </Space>
        </header>

        <section className={cx('welcome-actions')} aria-label="开始使用 XCodeAgent">
          <article className={cx('welcome-action-card', 'primary')}>
            <div className={cx('welcome-action-icon')}>
              <PlusOutlined />
            </div>
            <div className={cx('welcome-action-copy')}>
              <Title level={3}>新建应用</Title>
              <Paragraph type="secondary">
                配置应用骨架、页面、主题和内置模块，并指定本地项目创建位置。
              </Paragraph>
            </div>
            <Button icon={<PlusOutlined />} onClick={openCreateModal} size="large" type="primary">
              新建应用
            </Button>
          </article>

          <article className={cx('welcome-action-card')}>
            <div className={cx('welcome-action-icon', 'folder')}>
              <FolderOpenOutlined />
            </div>
            <div className={cx('welcome-action-copy')}>
              <Title level={3}>打开工作目录</Title>
              <Paragraph type="secondary">
                从历史会话中选择工作目录，直接进入对话和受保护工具工作台。
              </Paragraph>
            </div>
            <Button
              icon={<FolderOpenOutlined />}
              loading={openingWorkspace}
              onClick={handleOpenExistingWorkspace}
              size="large"
            >
              选择工作目录
            </Button>
          </article>
        </section>
      </section>

      <Modal
        destroyOnClose
        footer={null}
        onCancel={closeWorkspaceHistory}
        open={workspaceHistoryOpen}
        title="选择历史工作目录"
        width={820}
      >
        {workspaceHistory.length === 0 ? (
          <Empty description="暂无历史工作目录" image={Empty.PRESENTED_IMAGE_SIMPLE} />
        ) : (
          <List
            className={cx('workspace-history-list')}
            dataSource={workspaceHistory}
            renderItem={(workspace) => (
              <List.Item
                actions={[
                  <Button
                    key="open"
                    loading={openingWorkspaceRoot === workspace.workspaceRoot}
                    onClick={() => openHistoryWorkspace(workspace)}
                    type="primary"
                  >
                    进入
                  </Button>,
                ]}
                className={cx('workspace-history-item')}
              >
                <List.Item.Meta
                  avatar={<FolderOpenOutlined className={cx('workspace-history-icon')} />}
                  description={
                    <div className={cx('workspace-history-description')}>
                      <Text className={cx('workspace-history-path')} title={workspace.workspaceRoot}>
                        {workspace.workspaceRoot}
                      </Text>
                      <Space className={cx('workspace-history-meta')} size={[8, 6]} wrap>
                        <Tag>共 {workspace.sessionCount} 条</Tag>
                        <Tag>前端 {workspace.frontendCount}</Tag>
                        {workspace.backendCount > 0 && <Tag>后端 {workspace.backendCount}</Tag>}
                        <Text type="secondary">最近 {formatHistoryTime(workspace.latestUpdatedAt)}</Text>
                      </Space>
                      <Text className={cx('workspace-history-latest')} type="secondary">
                        最近会话：{workspace.latestTitle}
                      </Text>
                    </div>
                  }
                  title={<Text strong>{workspace.name}</Text>}
                />
              </List.Item>
            )}
          />
        )}
      </Modal>

      <Modal
        bodyStyle={{ maxHeight: 'calc(100vh - 260px)', overflow: 'auto' }}
        confirmLoading={creating}
        destroyOnClose
        maskClosable={false}
        okText="创建并进入工作台"
        onCancel={closeCreateModal}
        onOk={handleCreateApplication}
        open={modalOpen}
        style={{ top: 24 }}
        title="新建应用"
        width={780}
      >
        <Form
          form={form}
          initialValues={{
            ...initialDraft,
            defaultPage: parsePages(initialDraft.pagesText)[0],
          }}
          layout="vertical"
          onValuesChange={(changedValues: Partial<ApplicationDraft>, allValues) => {
            if ('name' in changedValues && !allValues.projectDirectoryName) {
              form.setFieldsValue({
                projectDirectoryName: toProjectDirectoryName(changedValues.name ?? ''),
              });
            }
            if ('pagesText' in changedValues) {
              const nextPages = parsePages(changedValues.pagesText);
              setPagesText(changedValues.pagesText ?? '');
              if (!nextPages.includes(allValues.defaultPage ?? '')) {
                form.setFieldsValue({ defaultPage: nextPages[0] });
              }
            }
          }}
        >
          <section className={cx('application-form-section')}>
            <Title level={5}>基础信息</Title>
            <Form.Item
              label="你的应用叫什么名字？"
              name="name"
              rules={[{ required: true, message: '请输入应用名称' }]}
            >
              <Input placeholder="例如：客户管理后台" />
            </Form.Item>
            <Form.Item label="这个应用主要给谁使用？" name="audience">
              <Radio.Group>
                {Object.entries(audienceLabels).map(([value, label]) => (
                  <Radio.Button key={value} value={value}>
                    {label}
                  </Radio.Button>
                ))}
              </Radio.Group>
            </Form.Item>
            <Form.Item label="这个应用主要运行在哪种终端？" name="terminal">
              <Radio.Group>
                {Object.entries(terminalLabels).map(([value, label]) => (
                  <Radio.Button key={value} value={value}>
                    {label}
                  </Radio.Button>
                ))}
              </Radio.Group>
            </Form.Item>
          </section>

          <section className={cx('application-form-section')}>
            <Title level={5}>项目位置</Title>
            <Form.Item label="项目创建在哪个文件夹下？" required>
              <Input.Group compact>
                <Form.Item
                  name="projectParentPath"
                  noStyle
                  rules={[{ required: true, message: '请选择项目创建位置' }]}
                >
                  <Input
                    placeholder="选择一个父文件夹"
                    style={{ width: 'calc(100% - 132px)' }}
                  />
                </Form.Item>
                <Button
                  icon={<FolderOpenOutlined />}
                  loading={selectingParent}
                  onClick={handleSelectProjectParent}
                  style={{ width: 132 }}
                >
                  选择文件夹
                </Button>
              </Input.Group>
            </Form.Item>
            <Form.Item
              label="项目文件夹叫什么名字？"
              name="projectDirectoryName"
              rules={[{ validator: validateProjectDirectoryName }]}
            >
              <Input prefix={<FolderAddOutlined />} placeholder="customer-admin" />
            </Form.Item>
            <Form.Item noStyle shouldUpdate>
              {({ getFieldValue }) => {
                const finalPath = joinLocalPath(
                  String(getFieldValue('projectParentPath') || ''),
                  String(getFieldValue('projectDirectoryName') || ''),
                );
                return finalPath ? (
                  <Text className={cx('project-path-preview')} type="secondary">
                    将创建在：{finalPath}
                  </Text>
                ) : null;
              }}
            </Form.Item>
          </section>

          <section className={cx('application-form-section')}>
            <Title level={5}>内置模块</Title>
            <Space size={24}>
              <Form.Item label="是否集成登录认证模块？" name="enableAuth" valuePropName="checked">
                <Switch checkedChildren="集成" unCheckedChildren="不集成" />
              </Form.Item>
              <Form.Item
                label="是否集成埋点或日志上报模块？"
                name="enableTracking"
                valuePropName="checked"
              >
                <Switch checkedChildren="集成" unCheckedChildren="不集成" />
              </Form.Item>
            </Space>
          </section>

          <section className={cx('application-form-section')}>
            <Title level={5}>主题与布局</Title>
            <Form.Item label="选择应用主题。" name="theme">
              <Radio.Group>
                {Object.entries(themeLabels).map(([value, label]) => (
                  <Radio.Button key={value} value={value}>
                    {label}
                  </Radio.Button>
                ))}
              </Radio.Group>
            </Form.Item>
            <Form.Item label="选择整体布局。" name="layout">
              <Radio.Group>
                {Object.entries(layoutLabels).map(([value, label]) => (
                  <Radio.Button key={value} value={value}>
                    {label}
                  </Radio.Button>
                ))}
              </Radio.Group>
            </Form.Item>
            <Form.Item
              label="是否需要页签式导航？"
              name="enableTabs"
              tooltip="开启后，用户打开多个页面时会像浏览器标签一样保留页面入口。"
              valuePropName="checked"
            >
              <Switch checkedChildren="需要" unCheckedChildren="不需要" />
            </Form.Item>
          </section>

          <section className={cx('application-form-section')}>
            <Title level={5}>页面与路由</Title>
            <Form.Item
              label="这个应用包含哪些页面？"
              name="pagesText"
              rules={[
                {
                  validator: (_, value: string) =>
                    parsePages(value).length > 0
                      ? Promise.resolve()
                      : Promise.reject(new Error('请至少填写一个页面')),
                },
              ]}
            >
              <TextArea
                autoSize={{ minRows: 3, maxRows: 5 }}
                placeholder="每行一个页面，例如：首页、用户管理、系统设置"
              />
            </Form.Item>
            <Form.Item
              label="默认首页是哪一个页面？"
              name="defaultPage"
              rules={[{ required: true, message: '请选择默认首页' }]}
            >
              <Select options={pageOptions} placeholder="先填写页面清单，再选择默认首页" />
            </Form.Item>
            <Form.Item label="是否存在动态参数页面？" name="hasDynamicRoutes">
              <Radio.Group>
                <Radio.Button value={false}>没有</Radio.Button>
                <Radio.Button value>有</Radio.Button>
              </Radio.Group>
            </Form.Item>
            <Form.Item noStyle shouldUpdate={(previous, current) => previous.hasDynamicRoutes !== current.hasDynamicRoutes}>
              {({ getFieldValue }) =>
                getFieldValue('hasDynamicRoutes') ? (
                  <Form.Item
                    label="有哪些动态页面？"
                    name="dynamicRouteDescription"
                    rules={[{ required: true, message: '请描述动态页面和参数' }]}
                  >
                    <TextArea
                      autoSize={{ minRows: 2, maxRows: 4 }}
                      placeholder="例如：用户详情页需要 userId，订单详情页需要 orderId"
                    />
                  </Form.Item>
                ) : null
              }
            </Form.Item>
          </section>
        </Form>
      </Modal>
    </main>
  );
}
