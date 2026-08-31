// 演示应用的项目目录（对齐真实工程：每个应用一个独立项目目录，文档与代码同树）。
// 目录名与 mock 应用 workspaceRoot 末级一致；用户可见的正式文档统一收在 docs/
// （与 frontend/backend 平级），不放进 .xcodeagent 内部工件目录。

export type WorkspaceSourceFile = {
  path: string
  content: string
}

/** 演示应用的项目根目录名。 */
export const APPLICATION_ROOT = 'wh-branch-pms-new'

/** 应用初始化时允许预设的空目录；目录存在不代表其中已经生成正式产物。 */
export const workspaceScaffoldDirectories = [appPath('docs')]

/** 应用内正式文档路径（统一放在 docs/，与前后端工程平级）。 */
export const WORKSPACE_DOC_PATHS = {
  requirementSpec: 'docs/requirement-spec.md',
  projectPlan: 'docs/project-plan.md',
  codeReview: 'docs/code-review.md'
} as const

/** 返回前端页面源码在原型文件树中的浅层路径，保留 pages 分组但移除 src 与 index 单文件层。 */
export function frontendPagePath(pageId: string): string {
  return `frontend/pages/${pageId}.tsx`
}

/** 返回后端接口源码在原型文件树中的浅层路径，移除 Java 包目录链，只保留可识别的控制器文件。 */
export function backendControllerPath(resource: string): string {
  return `backend/${resource.toLowerCase()}-controller.java`
}

/** 把应用内相对路径包装为项目树内的完整路径。 */
export function appPath(relativePath: string): string {
  return `${APPLICATION_ROOT}/${relativePath.replace(/^\//, '')}`
}

const frontendPackageJson = `{
  "name": "wh-branch-pms-web",
  "private": true,
  "dependencies": {
    "antd": "4.24.16",
    "axios": "1.7.2",
    "dayjs": "1.11.11",
    "react": "18.2.0",
    "react-dom": "18.2.0",
    "react-router-dom": "6.24.0"
  }
}
`

const mainTsx = `import React from 'react'
import ReactDOM from 'react-dom/client'
import { ConfigProvider } from 'antd'
import zhCN from 'antd/es/locale/zh_CN'
import App from './router'

// 前端入口：挂载路由应用并启用中文与主题色配置。
ReactDOM.createRoot(document.getElementById('root') as HTMLElement).render(
  <ConfigProvider locale={zhCN}>
    <App />
  </ConfigProvider>
)
`

const routerTsx = `import { lazy, Suspense } from 'react'
import { Navigate, Route, Routes } from 'react-router-dom'

// 路由表：页面由 XCodeAgent 按产物生成，新增页面自动追加到此处。
const RecheckIntroduction = lazy(() => import('./pages/recheck-introduction'))
const MyRechecks = lazy(() => import('./pages/my-rechecks'))

export default function App() {
  return (
    <Suspense fallback={null}>
      <Routes>
        <Route path="/" element={<Navigate to="/recheck-introduction" replace />} />
        <Route path="/recheck-introduction" element={<RecheckIntroduction />} />
        <Route path="/my-rechecks" element={<MyRechecks />} />
      </Routes>
    </Suspense>
  )
}
`

const apiClient = `import axios from 'axios'

const client = axios.create({ baseURL: '/api', timeout: 10000 })

// 我的回检单分页查询：页面表格的数据来源。
export async function fetchMyRechecks(params: { status?: string; page: number; size: number }) {
  const { data } = await client.get('/rechecks/my', { params })
  return data
}
`

const backendPom = `<?xml version="1.0" encoding="UTF-8"?>
<project xmlns="http://maven.apache.org/POM/4.0.0">
  <modelVersion>4.0.0</modelVersion>
  <groupId>com.xcodeagent</groupId>
  <artifactId>wh-branch-pms</artifactId>
  <version>1.0.0</version>
  <parent>
    <groupId>org.springframework.boot</groupId>
    <artifactId>spring-boot-starter-parent</artifactId>
    <version>3.2.5</version>
  </parent>
</project>
`

const applicationYml = `server:
  port: 8080

spring:
  datasource:
    url: jdbc:mysql://10.16.18.21:3306/pms_wuhan
    username: pms_admin
    driver-class-name: com.mysql.cj.jdbc.Driver
`

// 代码骨架文件（路由属于初始化脚手架；动态页面/接口源码由对话产物按需补充）。
const scaffoldFiles: WorkspaceSourceFile[] = [
  { path: appPath('frontend/package.json'), content: frontendPackageJson },
  { path: appPath('frontend/main.tsx'), content: mainTsx },
  { path: appPath('frontend/router.tsx'), content: routerTsx },
  { path: appPath('frontend/api/rechecks.ts'), content: apiClient },
  { path: appPath('backend/pom.xml'), content: backendPom },
  { path: appPath('backend/application.yml'), content: applicationYml }
]

/** 返回应用初始化时就存在的路由表；页面开发只补充页面源码，不重复改动路由。 */
export const workspaceRouterFile: WorkspaceSourceFile = {
  path: appPath('frontend/router.tsx'),
  content: routerTsx
}

/**
 * 项目目录中的静态工程骨架。
 *
 * 正式文档和页面/接口源码都必须由工作流写出 Diff 并经用户确认后才加入文件树；
 * 路由表属于初始化脚手架，因此随基础工程一起存在。
 */
export const workspaceScaffoldFiles: WorkspaceSourceFile[] = scaffoldFiles
