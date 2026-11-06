import type { ReactElement } from 'react'
import { SharedAgentPage } from '@/正式助手/design/SharedApplication'

/** 独立页面只声明内容区，Shell 和共享会话由原 Provider 持有。 */
export default function Chat(): ReactElement {
  return <main><h1>独立助手</h1><SharedAgentPage pageId="chat" /></main>
}
