import { CheckCircleOutlined, SyncOutlined } from '@ant-design/icons';
import { Button, Layout, Select, Typography } from 'antd';
import { useEffect, useRef } from 'react';
import { editorPanels } from '../../constants';
import { useWorkbench } from '../../context';
import type { CanvasMode, EditorMode, PageKey } from '../../typings';
import { cx } from '../../utils';
import PlaceholderPanel from '../PlaceholderPanel/PlaceholderPanel';
import './EditorPanel.less';

const { Content } = Layout;
const { Text } = Typography;

type Props = {
  editorMode: EditorMode;
  canvasMode: CanvasMode;
  onCanvasModeChange: (mode: CanvasMode) => void;
  pageKey: PageKey;
  onPageKeyChange: (key: PageKey) => void;
};

export default function EditorPanel({
  editorMode,
  canvasMode,
  onCanvasModeChange,
  pageKey,
  onPageKeyChange,
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
        content: `${editorMode === 'frontend' ? '前端' : '后端'}编辑器已捕获对应 AI 输出：“${latestAiMessage.content}”`,
      },
    }));
  }, [editorMode, latestAiMessage, setEditorResponses]);

  const frontendPanel =
    canvasMode === 'edit'
      ? {
          title: '前端编辑器 - 编辑态',
          description: '这里预留前端可编辑画布，后续可接入拖拽编排、组件选中和属性联动。',
        }
      : {
          title: '前端编辑器 - 预览态',
          description: '这里预留前端预览画布，后续可展示当前应用页面的运行效果。',
        };

  return (
    <Content className={cx('workbench-pane', 'workbench-right')}>
      <div className={cx('pane-content', 'editor-content')}>
        {editorMode === 'frontend' ? (
          <>
            <div className={cx('canvas-toolbar')}>
              <Select<PageKey>
                bordered={false}
                className={cx('page-select')}
                value={pageKey}
                onChange={onPageKeyChange}
                options={[
                  { label: '首页', value: 'home' },
                  { label: '详情页', value: 'detail' },
                  { label: '设置页', value: 'settings' },
                ]}
              />
              <nav className={cx('canvas-mode-switcher')} aria-label="前端画布模式切换">
                <Button
                  aria-label="编辑态"
                  className={cx('mode-button', canvasMode === 'edit' && 'active')}
                  onClick={() => onCanvasModeChange('edit')}
                  type="text"
                >
                  编辑
                </Button>
                <Button
                  aria-label="预览态"
                  className={cx('mode-button', canvasMode === 'preview' && 'active')}
                  onClick={() => onCanvasModeChange('preview')}
                  type="text"
                >
                  预览
                </Button>
              </nav>
            </div>
            <PlaceholderPanel {...frontendPanel} />
          </>
        ) : (
          <PlaceholderPanel {...editorPanels.backend} />
        )}

        <aside className={cx('editor-message-bridge')} aria-live="polite">
          <div className={cx('editor-message-bridge-title')}>
            {editorResponse ? <CheckCircleOutlined /> : <SyncOutlined />}
            <Text strong>{editorMode === 'frontend' ? '前端面板通信' : '后端面板通信'}</Text>
          </div>
          <Text type={editorResponse ? undefined : 'secondary'}>
            {editorResponse?.content ??
              `等待捕获${editorMode === 'frontend' ? '前端' : '后端'} AI 助手的输出...`}
          </Text>
        </aside>
      </div>
    </Content>
  );
}
