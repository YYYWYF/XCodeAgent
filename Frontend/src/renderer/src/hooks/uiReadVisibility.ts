import { createContext } from 'react'

/** 工作台隐藏时暂停界面读取并收起所属提示，后台执行与 AG-UI 连接不消费此开关。 */
export const UiReadVisibility = createContext(true)
