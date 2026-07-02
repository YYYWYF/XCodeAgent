import type { TaskDetail, TaskMenuItem } from '../types/task'

export const defaultProjectTasks: TaskMenuItem[] = [
  {
    id: '45',
    title: '45',
    statusTone: 'success'
  },
  {
    id: '343',
    title: '343',
    statusTone: 'warning'
  },
  {
    id: 'generate-page-add-button',
    title: '生成页面并添加按钮',
    statusTone: 'warning'
  },
  {
    id: 'implement-page-button',
    title: '实现页面按钮',
    statusTone: 'warning'
  },
  {
    id: 'node-template-project',
    title: '模板node工程搭建及页面',
    statusTone: 'warning'
  },
  {
    id: 'frontend-project-page',
    title: '实现前端工程展示页面',
    statusTone: 'success'
  },
  {
    id: 'button-dialog',
    title: '实现页面按钮弹框',
    statusTone: 'success'
  },
  {
    id: 'mock-scroll-01',
    title: '测试滚动条任务01',
    statusTone: 'warning'
  },
  {
    id: 'mock-scroll-02',
    title: '测试滚动条任务02',
    statusTone: 'success'
  },
  {
    id: 'mock-scroll-03',
    title: '测试滚动条任务03',
    statusTone: 'warning'
  },
  {
    id: 'mock-scroll-04',
    title: '测试滚动条任务04',
    statusTone: 'success'
  },
  {
    id: 'mock-scroll-05',
    title: '测试滚动条任务05',
    statusTone: 'warning'
  },
  {
    id: 'mock-scroll-06',
    title: '测试滚动条任务06',
    statusTone: 'success'
  },
  {
    id: 'mock-scroll-07',
    title: '测试滚动条任务07',
    statusTone: 'warning'
  },
  {
    id: 'mock-scroll-08',
    title: '测试滚动条任务08',
    statusTone: 'success'
  },
  {
    id: 'mock-scroll-09',
    title: '测试滚动条任务09',
    statusTone: 'warning'
  },
  {
    id: 'mock-scroll-10',
    title: '测试滚动条任务10',
    statusTone: 'success'
  }
]

export const taskDetails: Record<string, TaskDetail> = {
  '45': {
    id: '45',
    title: '45',
    status: '已完成',
    statusTone: 'success',
    description: '默认项目中的示例任务，用于验证任务路由和详情页数据读取。',
    owner: 'DevAgent',
    createdAt: '2026-06-20 10:00',
    updatedAt: '2026-06-26 09:30',
    checklist: ['匹配 /task/45 路由', '展示任务基础信息', '保持页面布局一致']
  },
  '343': {
    id: '343',
    title: '343',
    status: '进行中',
    statusTone: 'warning',
    description: '默认项目中的进行中任务，用于展示不同状态下的任务详情数据。',
    owner: 'DevAgent',
    createdAt: '2026-06-21 14:20',
    updatedAt: '2026-06-26 10:12',
    checklist: ['同步菜单状态点', '读取本地 mock 数据', '保留后续接口替换位置']
  },
  'generate-page-add-button': {
    id: 'generate-page-add-button',
    title: '生成页面并添加按钮',
    status: '进行中',
    statusTone: 'warning',
    description: '生成业务页面并添加基础操作按钮，验证页面结构与按钮布局。',
    owner: 'DevAgent',
    createdAt: '2026-06-22 11:15',
    updatedAt: '2026-06-26 11:05',
    checklist: ['创建页面容器', '添加主要操作按钮', '校验按钮状态']
  },
  'implement-page-button': {
    id: 'implement-page-button',
    title: '实现页面按钮',
    status: '进行中',
    statusTone: 'warning',
    description: '完善页面按钮的交互入口，后续可接入真实业务处理逻辑。',
    owner: 'DevAgent',
    createdAt: '2026-06-22 15:45',
    updatedAt: '2026-06-26 11:18',
    checklist: ['定义按钮文案', '绑定点击入口', '处理禁用与加载状态']
  },
  'node-template-project': {
    id: 'node-template-project',
    title: '模板node工程搭建及页面',
    status: '进行中',
    statusTone: 'warning',
    description: '搭建 Node 模板工程并补齐展示页面，便于后续扩展更多任务能力。',
    owner: 'DevAgent',
    createdAt: '2026-06-23 09:10',
    updatedAt: '2026-06-26 12:30',
    checklist: ['整理工程目录', '补充页面入口', '验证基础构建']
  },
  'frontend-project-page': {
    id: 'frontend-project-page',
    title: '实现前端工程展示页面',
    status: '已完成',
    statusTone: 'success',
    description: '实现前端工程展示页面，用于呈现项目状态、任务结果和关键信息。',
    owner: 'DevAgent',
    createdAt: '2026-06-24 10:30',
    updatedAt: '2026-06-26 13:40',
    checklist: ['完成页面布局', '接入展示数据', '验证响应式效果']
  },
  'button-dialog': {
    id: 'button-dialog',
    title: '实现页面按钮弹框',
    status: '已完成',
    statusTone: 'success',
    description: '为页面按钮添加弹框能力，覆盖基础确认、取消和关闭交互。',
    owner: 'DevAgent',
    createdAt: '2026-06-24 16:20',
    updatedAt: '2026-06-26 14:08',
    checklist: ['新增弹框触发按钮', '实现弹框内容', '校验关闭流程']
  },
  'mock-scroll-01': {
    id: 'mock-scroll-01',
    title: '测试滚动条任务01',
    status: '进行中',
    statusTone: 'warning',
    description: '默认项目中的滚动条测试任务，用于验证任务列表过多时的独立滚动效果。',
    owner: 'DevAgent',
    createdAt: '2026-06-26 15:01',
    updatedAt: '2026-06-26 15:11',
    checklist: ['展示在默认项目列表中', '验证列表内部滚动', '保持路由详情可访问']
  },
  'mock-scroll-02': {
    id: 'mock-scroll-02',
    title: '测试滚动条任务02',
    status: '已完成',
    statusTone: 'success',
    description: '默认项目中的滚动条测试任务，用于验证任务列表过多时的独立滚动效果。',
    owner: 'DevAgent',
    createdAt: '2026-06-26 15:02',
    updatedAt: '2026-06-26 15:12',
    checklist: ['展示在默认项目列表中', '验证列表内部滚动', '保持路由详情可访问']
  },
  'mock-scroll-03': {
    id: 'mock-scroll-03',
    title: '测试滚动条任务03',
    status: '进行中',
    statusTone: 'warning',
    description: '默认项目中的滚动条测试任务，用于验证任务列表过多时的独立滚动效果。',
    owner: 'DevAgent',
    createdAt: '2026-06-26 15:03',
    updatedAt: '2026-06-26 15:13',
    checklist: ['展示在默认项目列表中', '验证列表内部滚动', '保持路由详情可访问']
  },
  'mock-scroll-04': {
    id: 'mock-scroll-04',
    title: '测试滚动条任务04',
    status: '已完成',
    statusTone: 'success',
    description: '默认项目中的滚动条测试任务，用于验证任务列表过多时的独立滚动效果。',
    owner: 'DevAgent',
    createdAt: '2026-06-26 15:04',
    updatedAt: '2026-06-26 15:14',
    checklist: ['展示在默认项目列表中', '验证列表内部滚动', '保持路由详情可访问']
  },
  'mock-scroll-05': {
    id: 'mock-scroll-05',
    title: '测试滚动条任务05',
    status: '进行中',
    statusTone: 'warning',
    description: '默认项目中的滚动条测试任务，用于验证任务列表过多时的独立滚动效果。',
    owner: 'DevAgent',
    createdAt: '2026-06-26 15:05',
    updatedAt: '2026-06-26 15:15',
    checklist: ['展示在默认项目列表中', '验证列表内部滚动', '保持路由详情可访问']
  },
  'mock-scroll-06': {
    id: 'mock-scroll-06',
    title: '测试滚动条任务06',
    status: '已完成',
    statusTone: 'success',
    description: '默认项目中的滚动条测试任务，用于验证任务列表过多时的独立滚动效果。',
    owner: 'DevAgent',
    createdAt: '2026-06-26 15:06',
    updatedAt: '2026-06-26 15:16',
    checklist: ['展示在默认项目列表中', '验证列表内部滚动', '保持路由详情可访问']
  },
  'mock-scroll-07': {
    id: 'mock-scroll-07',
    title: '测试滚动条任务07',
    status: '进行中',
    statusTone: 'warning',
    description: '默认项目中的滚动条测试任务，用于验证任务列表过多时的独立滚动效果。',
    owner: 'DevAgent',
    createdAt: '2026-06-26 15:07',
    updatedAt: '2026-06-26 15:17',
    checklist: ['展示在默认项目列表中', '验证列表内部滚动', '保持路由详情可访问']
  },
  'mock-scroll-08': {
    id: 'mock-scroll-08',
    title: '测试滚动条任务08',
    status: '已完成',
    statusTone: 'success',
    description: '默认项目中的滚动条测试任务，用于验证任务列表过多时的独立滚动效果。',
    owner: 'DevAgent',
    createdAt: '2026-06-26 15:08',
    updatedAt: '2026-06-26 15:18',
    checklist: ['展示在默认项目列表中', '验证列表内部滚动', '保持路由详情可访问']
  },
  'mock-scroll-09': {
    id: 'mock-scroll-09',
    title: '测试滚动条任务09',
    status: '进行中',
    statusTone: 'warning',
    description: '默认项目中的滚动条测试任务，用于验证任务列表过多时的独立滚动效果。',
    owner: 'DevAgent',
    createdAt: '2026-06-26 15:09',
    updatedAt: '2026-06-26 15:19',
    checklist: ['展示在默认项目列表中', '验证列表内部滚动', '保持路由详情可访问']
  },
  'mock-scroll-10': {
    id: 'mock-scroll-10',
    title: '测试滚动条任务10',
    status: '已完成',
    statusTone: 'success',
    description: '默认项目中的滚动条测试任务，用于验证任务列表过多时的独立滚动效果。',
    owner: 'DevAgent',
    createdAt: '2026-06-26 15:10',
    updatedAt: '2026-06-26 15:20',
    checklist: ['展示在默认项目列表中', '验证列表内部滚动', '保持路由详情可访问']
  }
}
