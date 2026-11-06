import type { ReactElement } from 'react'
import { useSharedAgentPanel } from '@/正式助手/design/SharedApplication'

/** 普通页面只打开唯一常驻助手，保留业务内容。 */
export default function Home(): ReactElement {
  const { openPanel } = useSharedAgentPanel()
  return <main><h1>业务首页</h1><p>当前为独立安装的交接验收工程，回复来自官方模拟服务。</p>
    <button onClick={openPanel}>打开助手</button></main>
}
