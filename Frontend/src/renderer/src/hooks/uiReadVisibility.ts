import { createContext } from 'react'

/** 仅控制可暂停的界面磁盘读取，后台执行与 AG-UI 连接不消费此开关。 */
export const UiReadVisibility = createContext(true)
