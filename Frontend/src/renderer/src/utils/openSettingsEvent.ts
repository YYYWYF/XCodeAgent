/**
 * 「打开设置」全局事件。
 *
 * 工作台顶部按钮、发布弹窗等深层组件需要跳到 AiChatPanel 的设置抽屉时，
 * 通过该 CustomEvent 通知，无需把 setActiveView 一路透传下去。
 * AiChatPanel 内部监听本事件并切换 activeView。
 */

export const OPEN_SETTINGS_EVENT = 'devagentstudio:open-settings'

/** 触发打开设置面板。 */
export function dispatchOpenSettings(): void {
  window.dispatchEvent(new CustomEvent(OPEN_SETTINGS_EVENT))
}
