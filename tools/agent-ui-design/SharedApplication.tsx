import { AgentUIProvider, type AgentUIUser } from '@za38.03/agent-ui-runtime'
import { createContext, useContext, useState, type ReactElement } from 'react'
import { SharedAgentHost, type SharedAgentHostProps } from './SharedHost'
import { PageSlotContext, usePageBounds, type PageSlot } from './SharedPage'
import { SharedRunContextProvider } from './runtimeContext'
export { SharedAgentPage } from './SharedPage'
export { useSharedAgentContext } from './runtimeContext'

interface PanelControls {
  panelOpen: boolean
  openPanel: () => void
  closePanel: () => void
}

const PanelContext = createContext<PanelControls | null>(null)

/** 未登录时保留页面 hook 的只读边界，不创建或恢复任何会话。 */
function ignoreUnauthenticatedAction(): void {}

const inactivePanel: PanelControls = {
  panelOpen: false,
  openPanel: ignoreUnauthenticatedAction,
  closePanel: ignoreUnauthenticatedAction,
}

export interface SharedAgentApplicationProps extends Omit<SharedAgentHostProps, 'panelOpen' | 'onPanelOpenChange'> {
  contextDescription?: string
  /** 由宿主身份生命周期提供；退出为 null，重新登录或切用户必须改变。不是授权凭据。 */
  sessionKey: string | null
  user?: AgentUIUser | null
  panelOpen?: boolean
  onPanelOpenChange?: (open: boolean) => void
}

/** 业务页面只操作同一 Host 的显隐，不创建第二个 Agent 或消息发送器。 */
export function useSharedAgentPanel(): PanelControls {
  const value = useContext(PanelContext)
  if (!value) throw new Error('SharedAgentApplication is required')
  return value
}

/** 路由外的会话边界仅在身份变化时重建；展示资料不参与服务端授权。 */
export function SharedAgentApplication(props: SharedAgentApplicationProps): ReactElement {
  if (!props.sessionKey?.trim()) return <PanelContext.Provider value={inactivePanel}>
    <SharedRunContextProvider pageId={props.pageId}>
      <PageSlotContext.Provider value={ignoreUnauthenticatedAction}>
        {props.children}
      </PageSlotContext.Provider>
    </SharedRunContextProvider>
  </PanelContext.Provider>
  return <AgentUIProvider key={props.sessionKey} user={props.user}>
    <SharedRunContextProvider pageId={props.pageId} description={props.contextDescription}>
      <ApplicationSession {...props} />
    </SharedRunContextProvider>
  </AgentUIProvider>
}

/** 将全页/浮窗及业务页面放在同一稳定树中，草稿由官方 Composer 持有。 */
function ApplicationSession({ panelOpen: controlledOpen, onPanelOpenChange, ...props }:
  SharedAgentApplicationProps): ReactElement {
  const [localOpen, setLocalOpen] = useState(false)
  const [slot, setSlot] = useState<PageSlot | null>(null)
  const bounds = usePageBounds(slot, props.pageId)
  const panelOpen = controlledOpen ?? localOpen
  /** 宿主可受控接入；默认显隐仍只保存在本会话边界内。 */
  const setOpen = (open: boolean): void => {
    setLocalOpen(open)
    onPanelOpenChange?.(open)
  }
  /** 页面入口打开同一个常驻助手。 */
  const openPanel = (): void => setOpen(true)
  /** 关闭浮窗保留会话和未发送草稿。 */
  const closePanel = (): void => setOpen(false)
  return <PanelContext.Provider value={{ panelOpen, openPanel, closePanel }}>
    <PageSlotContext.Provider value={setSlot}>
      <SharedAgentHost {...props} standaloneBounds={bounds} preserveLayout
        panelOpen={panelOpen} onPanelOpenChange={setOpen} />
    </PageSlotContext.Provider>
  </PanelContext.Provider>
}
