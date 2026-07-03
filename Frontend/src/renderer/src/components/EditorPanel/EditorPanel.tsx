import { CheckCircleOutlined, SyncOutlined } from '@ant-design/icons';
import { Layout, Typography } from 'antd';
import { useEffect, useRef } from 'react';
import { editorPanels } from '../../constants';
import { useWorkbench } from '../../context';
import type { ApplicationConfig, EditorMode } from '../../typings';
import { cx } from '../../utils';
import BrowserPreviewPanel from '../BrowserPreviewPanel/BrowserPreviewPanel';
import PlaceholderPanel from '../PlaceholderPanel/PlaceholderPanel';
import './EditorPanel.less';

const { Content } = Layout;
const { Text } = Typography;

type Props = {
  application: ApplicationConfig;
  editorMode: EditorMode;
};

export default function EditorPanel({
  application,
  editorMode,
}: Props) {
  const { aiMessages, editorResponses, setEditorResponses } = useWorkbench();
  const messages = aiMessages[editorMode];
  const latestAiMessage = messages[messages.length - 1];
  const editorResponse = editorResponses[editorMode];
  // 记录每个编辑器已处理的消息，避免 effect 因重复渲染重复响应。
  const handledMessageIdsRef = useRef<Partial<Record<EditorMode, number>>>({});

  useEffect(() => {
    if (!latestAiMessage || handledMessageIdsRef.current[editorMode] === latestAiMessage.id) return;

    // 这里是面板通信的消费入口，后续可替换为代码生成、画布更新等真实动作。
    handledMessageIdsRef.current[editorMode] = latestAiMessage.id;
    setEditorResponses((responses) => ({
      ...responses,
      [editorMode]: {
        messageId: latestAiMessage.id,
        content: `${editorMode === 'frontend' ? '预览面板' : '后端编辑器'}已捕获应用开发助手输出：“${latestAiMessage.content}”`,
      },
    }));
  }, [editorMode, latestAiMessage, setEditorResponses]);

  return (
    <Content className={cx('workbench-pane', 'workbench-right')}>
      <div className={cx('pane-content', 'editor-content')}>
        {editorMode === 'frontend' ? (
          <BrowserPreviewPanel application={application} />
        ) : (
          <PlaceholderPanel {...editorPanels.backend} />
        )}

        {(editorMode === 'backend' || editorResponse) && (
          <aside className={cx('editor-message-bridge')} aria-live="polite">
            <div className={cx('editor-message-bridge-title')}>
              {editorResponse ? <CheckCircleOutlined /> : <SyncOutlined />}
              <Text strong>{editorMode === 'frontend' ? '预览面板通信' : '后端面板通信'}</Text>
            </div>
            <Text type={editorResponse ? undefined : 'secondary'}>
              {editorResponse?.content ??
                `等待捕获应用开发助手的输出...`}
            </Text>
          </aside>
        )}
      </div>
    </Content>
  );
}
