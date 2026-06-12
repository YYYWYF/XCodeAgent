import { Layout } from 'antd';
import { useRef, useState } from 'react';
import {
  ActivityBar,
  EditorPanel,
  GlobalConfigPanel,
  LeftPanel,
  ResizeHandle,
} from '../components';
import { useLeftPanelResize } from '../hooks';
import type { CanvasMode, EditorMode, LeftMode, PageKey, WorkbenchMode } from '../typings';
import { cx } from '../utils';

function WorkbenchPage() {
  // 顶层页面模式决定展示编辑工作台还是应用全局配置页。
  const [workbenchMode, setWorkbenchMode] = useState<WorkbenchMode>('editor');
  // 前后端各自记住左侧当前打开的子面板。
  const [leftModes, setLeftModes] = useState<Record<EditorMode, LeftMode>>({
    frontend: 'chat',
    backend: 'chat',
  });
  const [editorMode, setEditorMode] = useState<EditorMode>('frontend');
  const [canvasMode, setCanvasMode] = useState<CanvasMode>('edit');
  const [pageKey, setPageKey] = useState<PageKey>('home');

  const wrapperRef = useRef<HTMLDivElement>(null);
  const { leftCollapsed, dragging, handleDragStart, toggleCollapse } = useLeftPanelResize(wrapperRef);
  const leftMode = leftModes[editorMode];

  const handleLeftModeChange = (mode: LeftMode) => {
    // 只修改当前编辑器的左侧模式，不覆盖另一侧的选择。
    setLeftModes((modes) => ({ ...modes, [editorMode]: mode }));
  };

  return (
    <Layout className={cx('workbench-shell')}>
      <ActivityBar
        editorMode={editorMode}
        workbenchMode={workbenchMode}
        onEditorChange={setEditorMode}
        onWorkbenchModeChange={setWorkbenchMode}
      />

      {workbenchMode === 'global-config' ? (
        <GlobalConfigPanel />
      ) : (
        <>
          <LeftPanel
            ref={wrapperRef}
            editorMode={editorMode}
            leftMode={leftMode}
            onLeftModeChange={handleLeftModeChange}
            collapsed={leftCollapsed}
            dragging={dragging}
          />
          <ResizeHandle
            collapsed={leftCollapsed}
            dragging={dragging}
            onDragStart={handleDragStart}
            onToggleCollapse={toggleCollapse}
          />
          <EditorPanel
            editorMode={editorMode}
            canvasMode={canvasMode}
            onCanvasModeChange={setCanvasMode}
            pageKey={pageKey}
            onPageKeyChange={setPageKey}
          />
        </>
      )}
    </Layout>
  );
}

export default WorkbenchPage;
