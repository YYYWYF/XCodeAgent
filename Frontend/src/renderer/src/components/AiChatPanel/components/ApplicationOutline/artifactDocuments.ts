import { CodeOutlined, FileTextOutlined, PictureOutlined, ProfileOutlined } from '@ant-design/icons'

// 文档目录的稳定身份、名称和图标共用一份定义，供导航与详情标题读取。
export const ARTIFACT_DOCUMENTS = [
  { key: 'requirement-spec', label: '需求文档', icon: FileTextOutlined },
  { key: 'product-plan', label: '产品规划', icon: ProfileOutlined },
  { key: 'ui-design', label: 'UI 设计稿', icon: PictureOutlined },
  { key: 'technical-plan', label: '技术规划', icon: CodeOutlined }
] as const

export type ArtifactDocumentKey = (typeof ARTIFACT_DOCUMENTS)[number]['key']
