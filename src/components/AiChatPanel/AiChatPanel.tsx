import { SendOutlined } from '@ant-design/icons';
import { Button, Empty, Input, Typography } from 'antd';
import { useState } from 'react';
import { useWorkbench } from '../../context';
import type { EditorMode } from '../../typings';
import { cx } from '../../utils';
import './AiChatPanel.less';

const { Text, Title } = Typography;
const { TextArea } = Input;

const chatCopy: Record<
  EditorMode,
  { title: string; description: string; empty: string; placeholder: string; label: string }
> = {
  frontend: {
    title: '前端 AI 助手',
    description: '专注页面、组件、样式和交互，输出仅同步给前端编辑器。',
    empty: '暂无前端 AI 输出',
    placeholder: '输入前端页面或组件需求...',
    label: '前端 AI 输出',
  },
  backend: {
    title: '后端 AI 助手',
    description: '专注接口、数据模型和服务逻辑，输出仅同步给后端编辑器。',
    empty: '暂无后端 AI 输出',
    placeholder: '输入接口或服务逻辑需求...',
    label: '后端 AI 输出',
  },
};

export default function AiChatPanel({ editorMode }: { editorMode: EditorMode }) {
  // 草稿按前后端分别保存，来回切换不会串内容或丢失未发送文本。
  const [drafts, setDrafts] = useState<Record<EditorMode, string>>({
    frontend: '',
    backend: '',
  });
  const { aiMessages, publishAiMessage } = useWorkbench();
  const messages = aiMessages[editorMode];
  const copy = chatCopy[editorMode];
  const draft = drafts[editorMode];

  const handlePublish = () => {
    if (!draft.trim()) return;
    // Provider 会根据 editorMode 将消息投递给对应编辑器。
    publishAiMessage(editorMode, draft);
    setDrafts((currentDrafts) => ({ ...currentDrafts, [editorMode]: '' }));
  };

  return (
    <section className={cx('ai-chat-panel')}>
      <header className={cx('ai-chat-header')}>
        <Text className={cx('editor-scope-tag', editorMode)}>
          {editorMode === 'frontend' ? 'FRONTEND' : 'BACKEND'}
        </Text>
        <Title level={4}>{copy.title}</Title>
        <Text type="secondary">{copy.description}</Text>
      </header>

      <div className={cx('ai-message-list')} aria-live="polite">
        {messages.length === 0 ? (
          <Empty description={copy.empty} image={Empty.PRESENTED_IMAGE_SIMPLE} />
        ) : (
          messages.map((message) => (
            <article className={cx('ai-message')} key={message.id}>
              <Text className={cx('ai-message-label')}>{copy.label}</Text>
              <Text>{message.content}</Text>
            </article>
          ))
        )}
      </div>

      <div className={cx('ai-chat-composer')}>
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
              handlePublish();
            }
          }}
        />
        <Button
          disabled={!draft.trim()}
          icon={<SendOutlined />}
          onClick={handlePublish}
          type="primary"
        >
          输出到{editorMode === 'frontend' ? '前端' : '后端'}编辑器
        </Button>
      </div>
    </section>
  );
}
