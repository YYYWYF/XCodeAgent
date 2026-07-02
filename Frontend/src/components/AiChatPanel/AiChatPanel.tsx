import {
  CloseOutlined,
  DesktopOutlined,
  DownOutlined,
  ExportOutlined,
  FolderOpenOutlined,
  MessageOutlined,
  RobotOutlined,
  SendOutlined,
  UserOutlined,
} from '@ant-design/icons';
import { Alert, Button, Dropdown, Empty, Input, Spin, Typography } from 'antd';
import type { MenuProps } from 'antd';
import type { KeyboardEvent } from 'react';
import { useEffect, useRef, useState } from 'react';
import { useWorkbench } from '../../context';
import { AgUiChatSession } from '../../service/agUiAgent';
import {
  createChatSessionId,
  createChatSessionTitle,
  listChatSessions,
  readChatSession,
  saveChatSession,
  type ChatSessionMessage,
  type ChatSessionRecord,
  type ChatSessionSummary,
} from '../../service/chatSessions';
import type { ApplicationConfig, EditorMode } from '../../typings';
import { cx, getInitialPreviewUrl, openPreviewWindow } from '../../utils';
import BrowserPreviewPanel from '../BrowserPreviewPanel/BrowserPreviewPanel';
import MarkdownContent from '../MarkdownContent/MarkdownContent';
import './AiChatPanel.less';

const { Text, Title } = Typography;
const { TextArea } = Input;

type AgentChatMessage = {
  id: number;
  role: 'user' | 'assistant';
  content: string;
  createdAt: number;
};

type Props = {
  application: ApplicationConfig;
  editorMode: EditorMode;
};

const chatCopy: Record<
  EditorMode,
  { title: string; description: string; empty: string; placeholder: string; label: string }
> = {
  frontend: {
    title: '应用开发助手',
    description: '围绕当前应用的代码实现、页面体验、接口协作和验证步骤推进开发。',
    empty: '暂无应用开发助手输出',
    placeholder: '输入你想开发或修改的应用需求...',
    label: '应用开发助手输出',
  },
  backend: {
    title: '应用开发助手',
    description: '围绕当前应用的接口、数据模型、服务逻辑和验证步骤推进开发。',
    empty: '暂无应用开发助手输出',
    placeholder: '输入接口、服务或应用开发需求...',
    label: '应用开发助手输出',
  },
};

export default function AiChatPanel({ application, editorMode }: Props) {
  // 草稿按前后端分别保存，来回切换不会串内容或丢失未发送文本。
  const [drafts, setDrafts] = useState<Record<EditorMode, string>>({
    frontend: '',
    backend: '',
  });
  const [agentMessages, setAgentMessages] = useState<Record<EditorMode, AgentChatMessage[]>>({
    frontend: [],
    backend: [],
  });
  const [sessionSummaries, setSessionSummaries] = useState<Record<EditorMode, ChatSessionSummary[]>>({
    frontend: [],
    backend: [],
  });
  const [activeSessionIds, setActiveSessionIds] = useState<Partial<Record<EditorMode, string>>>({});
  const [sessionLoadingModes, setSessionLoadingModes] = useState<Partial<Record<EditorMode, boolean>>>({});
  const [sessionErrors, setSessionErrors] = useState<Partial<Record<EditorMode, string>>>({});
  const agUiSessionsRef = useRef<Partial<Record<EditorMode, AgUiChatSession>>>({});
  const [loadingModes, setLoadingModes] = useState<Partial<Record<EditorMode, boolean>>>({});
  const [errors, setErrors] = useState<Partial<Record<EditorMode, string>>>({});
  const [previewError, setPreviewError] = useState('');
  const [embeddedPreviewOpen, setEmbeddedPreviewOpen] = useState(false);
  const { publishAiMessage } = useWorkbench();
  const messages = agentMessages[editorMode];
  const sessions = sessionSummaries[editorMode];
  const activeSessionId = activeSessionIds[editorMode];
  const copy = chatCopy[editorMode];
  const draft = drafts[editorMode];
  const loading = Boolean(loadingModes[editorMode]);
  const loadingSessions = Boolean(sessionLoadingModes[editorMode]);
  const error = errors[editorMode];
  const sessionError = sessionErrors[editorMode];
  const showPreviewActions = editorMode === 'frontend';
  const workspaceRoot = application.workspaceRoot || '未选择工作目录';

  useEffect(() => {
    loadSessionsForMode(editorMode);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [application.workspaceRoot, editorMode]);

  const previewMenuItems: MenuProps['items'] = [
    {
      key: 'external',
      icon: <ExportOutlined />,
      label: '打开网页预览',
    },
    {
      key: 'embedded',
      icon: <DesktopOutlined />,
      label: '打开内嵌页面预览',
    },
  ];

  const handlePreviewAction: MenuProps['onClick'] = async ({ key }) => {
    setPreviewError('');

    if (key === 'embedded') {
      setEmbeddedPreviewOpen(true);
      return;
    }

    try {
      await openPreviewWindow(getInitialPreviewUrl(application.id));
    } catch (caughtError) {
      setPreviewError(caughtError instanceof Error ? caughtError.message : '无法打开网页预览');
    }
  };

  const replaceSessionSummary = (mode: EditorMode, summary: ChatSessionSummary) => {
    setSessionSummaries((currentSummaries) => ({
      ...currentSummaries,
      [mode]: [
        summary,
        ...currentSummaries[mode].filter((item) => item.id !== summary.id),
      ].sort((a, b) => b.updatedAt - a.updatedAt),
    }));
  };

  const loadSessionsForMode = async (mode: EditorMode) => {
    if (!application.workspaceRoot) {
      setSessionSummaries((currentSummaries) => ({ ...currentSummaries, [mode]: [] }));
      setAgentMessages((currentMessages) => ({ ...currentMessages, [mode]: [] }));
      setActiveSessionIds((currentSessionIds) => ({ ...currentSessionIds, [mode]: undefined }));
      return;
    }

    setSessionLoadingModes((currentLoadingModes) => ({ ...currentLoadingModes, [mode]: true }));
    setSessionErrors((currentErrors) => ({ ...currentErrors, [mode]: undefined }));
    try {
      const nextSessions = await listChatSessions(application.workspaceRoot, mode);
      setSessionSummaries((currentSummaries) => ({ ...currentSummaries, [mode]: nextSessions }));
      if (nextSessions.length === 0) {
        setAgentMessages((currentMessages) => ({ ...currentMessages, [mode]: [] }));
        setActiveSessionIds((currentSessionIds) => ({ ...currentSessionIds, [mode]: undefined }));
        agUiSessionsRef.current[mode] = undefined;
        return;
      }
      await openChatSession(mode, nextSessions[0].id);
    } catch (caughtError) {
      setSessionErrors((currentErrors) => ({
        ...currentErrors,
        [mode]: caughtError instanceof Error ? caughtError.message : '读取本地会话失败。',
      }));
    } finally {
      setSessionLoadingModes((currentLoadingModes) => ({ ...currentLoadingModes, [mode]: false }));
    }
  };

  const openChatSession = async (mode: EditorMode, sessionId: string) => {
    if (!application.workspaceRoot) return;

    const session = await readChatSession(application.workspaceRoot, mode, sessionId);
    setActiveSessionIds((currentSessionIds) => ({ ...currentSessionIds, [mode]: session.id }));
    setAgentMessages((currentMessages) => ({ ...currentMessages, [mode]: session.messages }));
    setDrafts((currentDrafts) => ({ ...currentDrafts, [mode]: '' }));
    agUiSessionsRef.current[mode] = new AgUiChatSession(session.threadId);
  };

  const handleOpenSession = async (sessionId: string) => {
    if (sessionId === activeSessionId || loadingSessions) return;
    setSessionLoadingModes((currentLoadingModes) => ({ ...currentLoadingModes, [editorMode]: true }));
    setSessionErrors((currentErrors) => ({ ...currentErrors, [editorMode]: undefined }));
    try {
      await openChatSession(editorMode, sessionId);
    } catch (caughtError) {
      setSessionErrors((currentErrors) => ({
        ...currentErrors,
        [editorMode]: caughtError instanceof Error ? caughtError.message : '打开本地会话失败。',
      }));
    } finally {
      setSessionLoadingModes((currentLoadingModes) => ({ ...currentLoadingModes, [editorMode]: false }));
    }
  };

  const handleOpenSessionKeyDown = (
    event: KeyboardEvent<HTMLDivElement>,
    sessionId: string,
  ) => {
    if (event.key !== 'Enter' && event.key !== ' ') return;
    event.preventDefault();
    handleOpenSession(sessionId);
  };

  const createNewSession = async () => {
    const agUiSession = new AgUiChatSession();
    agUiSessionsRef.current[editorMode] = agUiSession;
    setAgentMessages((currentMessages) => ({ ...currentMessages, [editorMode]: [] }));
    setDrafts((currentDrafts) => ({ ...currentDrafts, [editorMode]: '' }));

    if (!application.workspaceRoot) {
      setActiveSessionIds((currentSessionIds) => ({ ...currentSessionIds, [editorMode]: undefined }));
      return;
    }

    const now = Date.now();
    const session: ChatSessionRecord = {
      id: createChatSessionId(),
      title: '新对话',
      editorMode,
      threadId: agUiSession.threadId,
      workspaceRoot: application.workspaceRoot,
      messages: [],
      createdAt: now,
      updatedAt: now,
    };
    setActiveSessionIds((currentSessionIds) => ({ ...currentSessionIds, [editorMode]: session.id }));
    try {
      const summary = await saveChatSession(session);
      replaceSessionSummary(editorMode, summary);
    } catch (caughtError) {
      setSessionErrors((currentErrors) => ({
        ...currentErrors,
        [editorMode]: caughtError instanceof Error ? caughtError.message : '创建本地会话失败。',
      }));
    }
  };

  const handleCreateSessionFromList = () => {
    if (!application.workspaceRoot) return;
    createNewSession();
  };

  const handleCreateSessionKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    if (!application.workspaceRoot || (event.key !== 'Enter' && event.key !== ' ')) return;
    event.preventDefault();
    createNewSession();
  };

  const persistSession = async (
    mode: EditorMode,
    nextMessages: ChatSessionMessage[],
    options?: { titleFrom?: string; sessionId?: string; threadId?: string },
  ) => {
    if (!application.workspaceRoot) return;
    const existingSummary = sessionSummaries[mode].find(
      (summary) => summary.id === (options?.sessionId || activeSessionIds[mode]),
    );
    const now = Date.now();
    const session: ChatSessionRecord = {
      id: options?.sessionId || existingSummary?.id || createChatSessionId(),
      title:
        options?.titleFrom && (!existingSummary || existingSummary.title === '新对话')
          ? createChatSessionTitle(options.titleFrom)
          : existingSummary?.title || '新对话',
      editorMode: mode,
      threadId: options?.threadId || existingSummary?.threadId || agUiSessionsRef.current[mode]?.threadId || createChatSessionId(),
      workspaceRoot: application.workspaceRoot,
      messages: nextMessages,
      createdAt: existingSummary?.createdAt || now,
      updatedAt: now,
    };
    setActiveSessionIds((currentSessionIds) => ({ ...currentSessionIds, [mode]: session.id }));
    const summary = await saveChatSession(session);
    replaceSessionSummary(mode, summary);
  };

  const handleSend = async () => {
    const message = draft.trim();
    if (!message || loading) return;

    const userMessage: AgentChatMessage = {
      id: Date.now(),
      role: 'user',
      content: message,
      createdAt: Date.now(),
    };
    const agUiSession =
      agUiSessionsRef.current[editorMode] ??
      (agUiSessionsRef.current[editorMode] = new AgUiChatSession());
    const sessionId = activeSessionId || createChatSessionId();
    const nextMessages = [...messages, userMessage];

    setAgentMessages((currentMessages) => ({
      ...currentMessages,
      [editorMode]: nextMessages,
    }));
    setDrafts((currentDrafts) => ({ ...currentDrafts, [editorMode]: '' }));
    setErrors((currentErrors) => ({ ...currentErrors, [editorMode]: undefined }));
    setLoadingModes((currentLoadingModes) => ({ ...currentLoadingModes, [editorMode]: true }));

    try {
      await persistSession(editorMode, nextMessages, {
        sessionId,
        threadId: agUiSession.threadId,
        titleFrom: message,
      });
      const { answer: rawAnswer } = await agUiSession.sendMessage(message, {
        systemPrompt: buildScopedSystemPrompt(application, editorMode),
        workspaceRoot: application.workspaceRoot,
      });
      const answer = rawAnswer.trim();
      const assistantMessage: AgentChatMessage = {
        id: Date.now() + 1,
        role: 'assistant',
        content: answer || '后端已返回，但内容为空。',
        createdAt: Date.now(),
      };
      const completedMessages = [...nextMessages, assistantMessage];

      setAgentMessages((currentMessages) => ({
        ...currentMessages,
        [editorMode]: completedMessages,
      }));
      await persistSession(editorMode, completedMessages, {
        sessionId,
        threadId: agUiSession.threadId,
        titleFrom: message,
      });
      // AG-UI run 完成后，将模型返回投递给对应编辑器。
      publishAiMessage(editorMode, answer);
    } catch (caughtError) {
      const message =
        caughtError instanceof Error ? caughtError.message : '调用应用开发助手失败。';
      setErrors((currentErrors) => ({ ...currentErrors, [editorMode]: message }));
    } finally {
      setLoadingModes((currentLoadingModes) => ({
        ...currentLoadingModes,
        [editorMode]: false,
      }));
    }
  };

  return (
    <section className={cx('ai-chat-panel', embeddedPreviewOpen && 'embedded-preview-open')}>
      <div className={cx('ai-chat-assistant')}>
        <aside className={cx('session-sidebar')} aria-label="历史会话">
          <div className={cx('session-sidebar-header')}>
            <Text strong>历史会话</Text>
          </div>
          <Text className={cx('session-workspace-name')} title={workspaceRoot}>
            <FolderOpenOutlined /> {application.workspaceRoot ? application.name : '未选择工作目录'}
          </Text>
          <div className={cx('session-list')} aria-live="polite">
            <div
              aria-disabled={!application.workspaceRoot}
              className={cx('session-new-entry', !application.workspaceRoot && 'disabled')}
              onClick={handleCreateSessionFromList}
              onKeyDown={handleCreateSessionKeyDown}
              tabIndex={application.workspaceRoot ? 0 : -1}
            >
              <span className={cx('session-item-title')}>
                <MessageOutlined /> 新对话
              </span>
              <span className={cx('session-item-meta')}>创建空白会话</span>
            </div>
            {loadingSessions ? (
              <div className={cx('session-loading')}>
                <Spin size="small" />
                <Text type="secondary">读取会话...</Text>
              </div>
            ) : sessions.length === 0 ? (
              <Empty
                description={application.workspaceRoot ? '暂无本地会话' : '选择工作目录后保存会话'}
                image={Empty.PRESENTED_IMAGE_SIMPLE}
              />
            ) : (
              sessions.map((session) => (
                <div
                  className={cx('session-item', activeSessionId === session.id && 'active')}
                  key={session.id}
                  onClick={() => handleOpenSession(session.id)}
                  onKeyDown={(event) => handleOpenSessionKeyDown(event, session.id)}
                  tabIndex={0}
                >
                  <span className={cx('session-item-title')}>
                    <MessageOutlined /> {session.title}
                  </span>
                  <span className={cx('session-item-meta')}>
                    {formatSessionTime(session.updatedAt)} · {session.messageCount} 条
                  </span>
                </div>
              ))
            )}
          </div>
          {sessionError && <Alert message={sessionError} showIcon type="error" />}
        </aside>

        <div className={cx('ai-chat-main')}>
          {showPreviewActions && (
            <div className={cx('preview-actions')}>
              {embeddedPreviewOpen && (
                <Button
                  aria-label="关闭内嵌预览"
                  icon={<CloseOutlined />}
                  onClick={() => setEmbeddedPreviewOpen(false)}
                  type="text"
                />
              )}
              <Dropdown menu={{ items: previewMenuItems, onClick: handlePreviewAction }} trigger={['click']}>
                <Button icon={<DesktopOutlined />} type="primary">
                  预览应用 <DownOutlined />
                </Button>
              </Dropdown>
            </div>
          )}
          <header className={cx('ai-chat-header')}>
            <div className={cx('ai-chat-title')}>
              <Text className={cx('editor-scope-tag', editorMode)}>
                APP DEV
              </Text>
              <Title level={4}>{copy.title}</Title>
              <Text type="secondary">{copy.description}</Text>
            </div>
          </header>

          {previewError && (
            <Alert
              className={cx('preview-action-error')}
              message={previewError}
              showIcon
              type="error"
            />
          )}

          <div className={cx('ai-message-list')} aria-live="polite">
            {messages.length === 0 ? (
              <Empty description={copy.empty} image={Empty.PRESENTED_IMAGE_SIMPLE} />
            ) : (
              messages.map((message) => (
                <article className={cx('ai-message', message.role)} key={message.id}>
                  <Text className={cx('ai-message-label')}>
                    {message.role === 'user' ? (
                      <>
                        <UserOutlined /> 用户输入
                      </>
                    ) : (
                      <>
                        <RobotOutlined /> {copy.label}
                      </>
                    )}
                  </Text>
                  {message.role === 'assistant' ? (
                    <MarkdownContent content={message.content} />
                  ) : (
                    <Text className={cx('ai-message-text')}>{message.content}</Text>
                  )}
                </article>
              ))
            )}
            {loading && (
              <div className={cx('ai-message', 'assistant', 'loading')}>
                <Spin size="small" />
                <Text type="secondary">正在调用应用开发助手...</Text>
              </div>
            )}
          </div>

          <div className={cx('ai-chat-composer')}>
            {error && <Alert message={error} showIcon type="error" />}
            <TextArea
              aria-label={`${copy.title}输出内容`}
              autoSize={{ minRows: 3, maxRows: 6 }}
              placeholder={copy.placeholder}
              value={draft}
              onChange={(event) =>
                setDrafts((currentDrafts) => ({
                  ...currentDrafts,
                  [editorMode]: event.target.value,
                }))
              }
              onPressEnter={(event) => {
                if (!event.shiftKey) {
                  event.preventDefault();
                  handleSend();
                }
              }}
            />
            <div className={cx('ai-chat-composer-footer')}>
              <Text className={cx('workspace-root-label')} title={workspaceRoot}>
                <FolderOpenOutlined /> 工作目录：{workspaceRoot}
              </Text>
              <Button
                disabled={!draft.trim() || loading}
                icon={<SendOutlined />}
                loading={loading}
                onClick={handleSend}
                type="primary"
              >
                发送给应用开发助手
              </Button>
            </div>
          </div>
        </div>
      </div>

      {embeddedPreviewOpen && (
        <div className={cx('embedded-preview-pane')}>
          <BrowserPreviewPanel application={application} />
        </div>
      )}
    </section>
  );
}

function formatSessionTime(value: number) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return '未知时间';
  return date.toLocaleString('zh-CN', {
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  });
}

function buildScopedSystemPrompt(application: ApplicationConfig, editorMode: EditorMode) {
  const scopePrompt =
    editorMode === 'frontend'
      ? '你是 XCodeAgent，一个应用开发助手。回答要围绕当前应用的代码实现、页面体验、接口协作和验证步骤。'
      : '你是 XCodeAgent，一个应用开发助手。回答要围绕当前应用的接口、数据模型、服务逻辑和验证步骤。';
  const workspacePrompt = application.workspaceRoot
    ? `当前应用工作目录：${application.workspaceRoot}。涉及本地工具或命令时优先使用这个 workspace_root。`
    : '当前应用没有绑定工作目录，涉及本地工具或命令时先说明需要选择工作目录。';

  return `${scopePrompt}\n应用名称：${application.name}。\n${workspacePrompt}`;
}
