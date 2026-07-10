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
import { useState } from 'react';
import type {
  ApplicationConfig,
  ApplicationDraft,
  ApplicationSchemaConfig,
  ApplicationTerminal,
  ApplicationTrackMethod,
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

const terminalLabels: Record<ApplicationTerminal, string> = {
  PC: 'PC 端',
  Mobile: '移动端',
};

const trackMethodLabels: Record<ApplicationTrackMethod, string> = {
  post: '提交',
  get: '获取',
};

const initialDraft: ApplicationDraft = {
  appName: '',
  appIcon: '',
  senario: '',
  projectParentPath: '',
  projectDirectoryName: '',
  terminal: 'PC',
  layout: {
    type: '',
    useHeader: true,
    useFooter: true,
  },
  theme: {
    primaryColor: '',
  },
  datasource: {
    type: '',
    db: {
      plantMode: {
        domain: '',
        port: '',
        userName: '',
        pwd: '',
        schema: '',
      },
    },
  },
  envText: '',
  auth: {
    enable: true,
    authnSource: '',
    yht: {
      clientId: '',
    },
  },
  track: {
    enable: true,
    uploadId: '',
    apiHost: '',
    method: 'post',
  },
  apiTrack: {
    enable: true,
    businessId: '',
    traceBaggage: '',
    apiTrackHost: '',
  },
};

type Props = {
  onOpenApplication: (application: ApplicationConfig) => void;
};

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

function parseEnv(value?: string) {
  return (value ?? '')
    .split(/[\n,，、]/)
    .map((item) => item.trim())
    .filter(Boolean);
}

function buildApplicationSchema(values: ApplicationDraft): ApplicationSchemaConfig {
  return {
    appName: values.appName.trim(),
    appIcon: values.appIcon.trim(),
    senario: values.senario.trim(),
    terminal: values.terminal,
    layout: values.layout,
    theme: values.theme,
    datasource: values.datasource,
    env: parseEnv(values.envText),
    menus: {
      homeMenuKey: '',
      items: [],
    },
    auth: values.auth,
    track: values.track,
    apiTrack: values.apiTrack,
  };
}

export default function WelcomePage({ onOpenApplication }: Props) {
  const [form] = Form.useForm<ApplicationDraft>();
  const [modalOpen, setModalOpen] = useState(false);
  const [creating, setCreating] = useState(false);
  const [openingWorkspace, setOpeningWorkspace] = useState(false);
  const [workspaceHistoryOpen, setWorkspaceHistoryOpen] = useState(false);
  const [workspaceHistory, setWorkspaceHistory] = useState<SessionWorkspaceSummary[]>([]);
  const [openingWorkspaceRoot, setOpeningWorkspaceRoot] = useState<string>();
  const [selectingParent, setSelectingParent] = useState(false);

  const openCreateModal = () => {
    form.setFieldsValue(initialDraft);
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
      const schema = buildApplicationSchema({
        ...initialDraft,
        appName: workspaceName,
        projectParentPath: pathDirname(workspace.workspaceRoot),
        projectDirectoryName: workspaceName,
      });
      const application: ApplicationConfig = {
        ...schema,
        id: createApplicationId(),
        name: workspaceName,
        workspaceRoot: workspace.workspaceRoot,
        projectParentPath: pathDirname(workspace.workspaceRoot),
        projectDirectoryName: workspaceName,
        source: 'existing-workspace',
        audience: 'developer',
        enableAuth: schema.auth.enable,
        enableTracking: schema.track.enable || schema.apiTrack.enable,
        legacyTheme: 'light',
        legacyLayout: 'login-admin',
        enableTabs: false,
        pages: ['工作台'],
        defaultPage: '工作台',
        hasDynamicRoutes: false,
        schema,
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
      const schema = buildApplicationSchema(values);
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
          initialValues={initialDraft}
          layout="vertical"
          onValuesChange={(changedValues: Partial<ApplicationDraft>, allValues) => {
            if ('appName' in changedValues && !allValues.projectDirectoryName) {
              form.setFieldsValue({
                projectDirectoryName: toProjectDirectoryName(changedValues.appName ?? ''),
              });
            }
          }}
        >
          <section className={cx('application-form-section')}>
            <Title level={5}>基础信息</Title>
            <Form.Item
              label="应用名称"
              name="appName"
              rules={[{ required: true, message: '请输入应用名称' }]}
            >
              <Input />
            </Form.Item>
            <Form.Item
              label="应用图标"
              name="appIcon"
            >
              <Input />
            </Form.Item>
            <Form.Item label="应用场景" name="senario">
              <TextArea autoSize={{ minRows: 2, maxRows: 4 }} />
            </Form.Item>
            <Form.Item label="终端类型" name="terminal">
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
                  <Input style={{ width: 'calc(100% - 132px)' }} />
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
              label="项目文件夹名"
              name="projectDirectoryName"
              rules={[{ validator: validateProjectDirectoryName }]}
            >
              <Input prefix={<FolderAddOutlined />} />
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
            <Title level={5}>认证</Title>
            <Form.Item label="启用认证" name={['auth', 'enable']} valuePropName="checked">
              <Switch checkedChildren="启用" unCheckedChildren="关闭" />
            </Form.Item>
            <Form.Item label="认证来源" name={['auth', 'authnSource']}>
              <Input />
            </Form.Item>
            <Form.Item label="一号通clientId" name={['auth', 'yht', 'clientId']}>
              <Input />
            </Form.Item>
          </section>

          <section className={cx('application-form-section')}>
            <Title level={5}>页面埋点</Title>
            <Form.Item label="启用页面埋点" name={['track', 'enable']} valuePropName="checked">
              <Switch checkedChildren="启用" unCheckedChildren="关闭" />
            </Form.Item>
            <Form.Item label="上传标识" name={['track', 'uploadId']}>
              <Input />
            </Form.Item>
            <Form.Item label="上报地址" name={['track', 'apiHost']}>
              <Input />
            </Form.Item>
            <Form.Item label="请求方式" name={['track', 'method']}>
              <Select
                options={Object.entries(trackMethodLabels).map(([value, label]) => ({ label, value }))}
              />
            </Form.Item>
          </section>

          <section className={cx('application-form-section')}>
            <Title level={5}>接口埋点</Title>
            <Form.Item label="启用接口埋点" name={['apiTrack', 'enable']} valuePropName="checked">
              <Switch checkedChildren="启用" unCheckedChildren="关闭" />
            </Form.Item>
            <Form.Item label="业务标识" name={['apiTrack', 'businessId']}>
              <Input />
            </Form.Item>
            <Form.Item label="链路透传信息" name={['apiTrack', 'traceBaggage']}>
              <Input />
            </Form.Item>
            <Form.Item label="接口埋点地址" name={['apiTrack', 'apiTrackHost']}>
              <Input />
            </Form.Item>
          </section>
        </Form>
      </Modal>
    </main>
  );
}
