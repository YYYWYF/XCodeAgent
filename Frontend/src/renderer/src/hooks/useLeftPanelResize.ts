import { useCallback, useEffect, useRef, useState } from 'react';
import { COLLAPSE_THRESHOLD, DEFAULT_WIDTH_RATIO, MIN_WIDTH } from '../constants';
import { clampWidth } from '../utils';

export function useLeftPanelResize(wrapperRef: React.RefObject<HTMLDivElement | null>) {
  const [leftWidth, setLeftWidth] = useState(() => window.innerWidth * DEFAULT_WIDTH_RATIO);
  const [leftCollapsed, setLeftCollapsed] = useState(false);
  const [dragging, setDragging] = useState(false);

  const dragRef = useRef<{ startX: number; startWidth: number } | null>(null);
  const leftWidthRef = useRef(leftWidth);
  const leftCollapsedRef = useRef(leftCollapsed);
  const savedWidthRef = useRef(leftWidth);

  // 拖拽事件绑定在 document 上，通过 ref 读取最新值可避免闭包中的旧状态。
  leftWidthRef.current = leftWidth;
  leftCollapsedRef.current = leftCollapsed;

  // 拖动过程中直接更新 DOM，减少高频 setState 带来的整棵组件树重渲染。
  const applyWrapperWidth = useCallback((w: number, collapsed: boolean) => {
    const el = wrapperRef.current;
    if (!el) return;
    if (collapsed) {
      el.style.flex = '0 0 0px';
      el.style.maxWidth = '0px';
    } else {
      el.style.flex = `0 0 ${w}px`;
      el.style.maxWidth = `${w}px`;
    }
  }, [wrapperRef]);

  const handleDragStart = useCallback(
    (e: React.MouseEvent) => {
      e.preventDefault();
      setDragging(true);
      dragRef.current = { startX: e.clientX, startWidth: leftWidthRef.current };
      document.body.style.cursor = 'col-resize';
      document.body.style.userSelect = 'none';
    },
    [],
  );

  useEffect(() => {
    if (!dragging) return;

    const onMouseMove = (e: MouseEvent) => {
      if (!dragRef.current) return;
      const delta = e.clientX - dragRef.current.startX;
      const newWidth = clampWidth(dragRef.current.startWidth + delta);
      const shouldCollapse = newWidth <= COLLAPSE_THRESHOLD;
      applyWrapperWidth(newWidth, shouldCollapse);
      leftWidthRef.current = newWidth;
      leftCollapsedRef.current = shouldCollapse;
    };

    const onMouseUp = () => {
      if (!dragRef.current) return;
      dragRef.current = null;
      document.body.style.cursor = '';
      document.body.style.userSelect = '';
      setDragging(false);
      // 松手后再把最终尺寸同步回 React 状态。
      if (leftCollapsedRef.current) {
        setLeftWidth(MIN_WIDTH);
      } else {
        savedWidthRef.current = leftWidthRef.current;
        setLeftWidth(leftWidthRef.current);
      }
      setLeftCollapsed(leftCollapsedRef.current);
    };

    document.addEventListener('mousemove', onMouseMove);
    document.addEventListener('mouseup', onMouseUp);
    return () => {
      document.removeEventListener('mousemove', onMouseMove);
      document.removeEventListener('mouseup', onMouseUp);
    };
  }, [dragging, applyWrapperWidth]);

  const toggleCollapse = useCallback(() => {
    setLeftCollapsed((prev) => {
      if (prev) {
        // 展开时恢复上一次有效宽度。
        const w = clampWidth(savedWidthRef.current);
        setLeftWidth(w);
        applyWrapperWidth(w, false);
      } else {
        // 收起前保存当前宽度，供下次展开使用。
        savedWidthRef.current = leftWidthRef.current;
        applyWrapperWidth(0, true);
      }
      return !prev;
    });
  }, [applyWrapperWidth]);

  // 非拖拽场景下由 React 状态负责同步面板宽度。
  useEffect(() => {
    if (!dragging) {
      applyWrapperWidth(leftWidth, leftCollapsed);
    }
  }, [leftWidth, leftCollapsed, dragging, applyWrapperWidth]);

  return { leftWidth, leftCollapsed, dragging, handleDragStart, toggleCollapse };
}
