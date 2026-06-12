import type { EditorMode, LeftMode, PlaceholderProps } from '../typings';

export const MIN_WIDTH = 280;
export const MAX_WIDTH_RATIO = 0.6;
export const DEFAULT_WIDTH_RATIO = 0.36;
export const COLLAPSE_THRESHOLD = 120;

export const leftPanels: Record<LeftMode, PlaceholderProps> = {
  chat: {
    title: 'AI 对话框',
    description: '这里预留 AI 对话区，后续可接入会话列表、输入框和上下文消息。',
  },
  config: {
    title: '可视化配置界面',
    description: '这里预留低代码可视化配置区，后续可放置页面结构、组件属性和流程配置。',
  },
};

export const editorPanels: Record<EditorMode, PlaceholderProps> = {
  frontend: {
    title: '前端编辑器',
    description: '这里预留前端代码编辑区，后续可接入 Monaco Editor、文件树和预览面板。',
  },
  backend: {
    title: '后端编辑器',
    description: '这里预留后端逻辑编辑区，后续可配置接口、数据模型和服务函数。',
  },
};
