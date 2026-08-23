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
  planner: {
    title: '需求规划',
    description: '通过选择题逐步澄清需求，并生成可执行的开发计划。',
  },
  tools: {
    title: '受保护工具',
    description: '执行需要确认的本地工具操作。',
  },
  config: {
    title: '可视化配置界面',
    description: '这里预留低代码可视化配置区，后续可放置页面结构、组件属性和流程配置。',
  },
};

export const editorPanels: Record<EditorMode, PlaceholderProps> = {
  frontend: {
    title: '网页预览',
    description: '这里展示网页预览窗口，并可跳转到系统浏览器打开真实页面。',
  },
  backend: {
    title: '后端编辑器',
    description: '这里预留后端逻辑编辑区，后续可配置接口、数据实体和服务函数。',
  },
};
