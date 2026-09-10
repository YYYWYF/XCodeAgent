import type { CSSProperties } from 'react'

interface AgentUiTokenLike {
  colorBgLayout: string
  colorBgContainer: string
  colorBgElevated: string
  colorBorderSecondary: string
  colorText: string
  colorTextSecondary: string
  colorPrimary: string
  colorTextLightSolid: string
  colorError: string
  boxShadowSecondary: string
}

type AgentUiCssVariables = CSSProperties & {
  '--agent-ui-bg': string
  '--agent-ui-surface': string
  '--agent-ui-elevated': string
  '--agent-ui-border': string
  '--agent-ui-text': string
  '--agent-ui-text-secondary': string
  '--agent-ui-primary': string
  '--agent-ui-primary-text': string
  '--agent-ui-danger': string
  '--agent-ui-shadow': string
}

/** 把当前 Ant Design 主题 token 投影为 Agent UI 语义变量。 */
export function createAgentUiCssVariables(token: AgentUiTokenLike): AgentUiCssVariables {
  return {
    '--agent-ui-bg': token.colorBgLayout,
    '--agent-ui-surface': token.colorBgContainer,
    '--agent-ui-elevated': token.colorBgElevated,
    '--agent-ui-border': token.colorBorderSecondary,
    '--agent-ui-text': token.colorText,
    '--agent-ui-text-secondary': token.colorTextSecondary,
    '--agent-ui-primary': token.colorPrimary,
    '--agent-ui-primary-text': token.colorTextLightSolid,
    '--agent-ui-danger': token.colorError,
    '--agent-ui-shadow': token.boxShadowSecondary
  }
}

export const AGENT_CONVERSATION_STYLES = `
.x-agent-ui{box-sizing:border-box;color:var(--agent-ui-text)}
.x-agent-ui *{box-sizing:border-box}
.x-agent-chat{display:grid;grid-template-rows:minmax(0,1fr) auto auto;min-height:0;height:100%;background:var(--agent-ui-bg)}
.x-agent-chat__messages{min-height:0;overflow:auto;padding:24px max(20px,calc((100% - 820px)/2))}
.x-agent-chat__stack{display:flex;flex-direction:column;gap:20px}
.x-agent-message{display:grid;grid-template-columns:36px minmax(0,1fr);gap:10px;align-items:start;align-self:flex-start;max-width:min(82%,720px)}
.x-agent-message--user{align-self:flex-end;grid-template-columns:minmax(0,1fr) 36px}
.x-agent-message--user>.ant-avatar{grid-column:2;grid-row:1}
.x-agent-message--user>.x-agent-message__body{grid-column:1;grid-row:1;align-items:flex-end}
.x-agent-message__body{display:flex;min-width:0;flex-direction:column;align-items:flex-start;gap:4px}
.x-agent-message__author{padding-inline:4px;font-size:12px;color:var(--agent-ui-text-secondary)}
.x-agent-message__bubble{max-width:100%;padding:10px 14px;border:1px solid var(--agent-ui-border);border-radius:4px 16px 16px;background:var(--agent-ui-surface);box-shadow:var(--agent-ui-shadow);overflow-wrap:anywhere}
.x-agent-message__bubble>.ant-typography:last-child{margin-bottom:0}
.x-agent-message--user .x-agent-message__bubble{border-color:transparent;border-radius:16px 4px 16px 16px;background:var(--agent-ui-primary);color:var(--agent-ui-primary-text)}
.x-agent-message--user .x-agent-message__bubble .ant-typography{color:inherit}
.x-agent-chat__state{display:flex;min-height:240px;align-items:center;justify-content:center;padding:24px}
.x-agent-chat__empty{width:min(100%,620px);text-align:center}
.x-agent-chat__suggestions{display:flex;flex-wrap:wrap;justify-content:center;gap:8px;margin-top:18px}
.x-agent-chat__tool,.x-agent-chat__approval{margin-top:12px;border-color:var(--agent-ui-border)}
.x-agent-chat__status{display:flex;align-items:center;justify-content:space-between;gap:12px;padding:6px 20px;color:var(--agent-ui-text-secondary);background:var(--agent-ui-surface)}
.x-agent-composer{display:flex;gap:10px;align-items:flex-end;padding:14px 20px calc(14px + env(safe-area-inset-bottom));border-top:1px solid var(--agent-ui-border);background:var(--agent-ui-surface)}
.x-agent-composer .ant-input-textarea{flex:1}
.x-agent-context{display:flex;min-width:0;gap:6px;overflow:auto;white-space:nowrap}
.x-agent-conversation{height:100vh;display:grid;grid-template-columns:280px minmax(0,1fr);background:var(--agent-ui-bg)}
.x-agent-conversation__sidebar{display:flex;min-height:0;flex-direction:column;gap:12px;padding:16px;border-right:1px solid var(--agent-ui-border);background:var(--agent-ui-surface);overflow:auto}
.x-agent-conversation__thread{height:auto!important;min-height:40px;text-align:left;white-space:normal}
.x-agent-conversation__thread--active{background:color-mix(in srgb,var(--agent-ui-primary) 10%,transparent)}
.x-agent-conversation__main{min-width:0;display:grid;grid-template-rows:auto minmax(0,1fr)}
.x-agent-conversation__header{display:flex;min-width:0;align-items:center;justify-content:space-between;gap:12px;padding:14px 20px;border-bottom:1px solid var(--agent-ui-border);background:var(--agent-ui-surface)}
.x-agent-conversation__identity{min-width:0;max-width:100%}
.x-agent-conversation__identity>.ant-space-item:last-child,.x-agent-conversation__identity-copy{min-width:0}
.x-agent-conversation__title,.x-agent-conversation__description{display:block;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.x-agent-conversation__mobile-menu{display:none}
.x-agent-floating__launcher{position:fixed;z-index:1010;width:56px!important;height:56px!important;border-radius:50%!important;box-shadow:var(--agent-ui-shadow);touch-action:none}
.x-agent-floating__panel{position:fixed;right:24px;bottom:96px;z-index:1009;width:min(420px,calc(100vw - 32px));height:min(620px,calc(100vh - 128px));overflow:hidden;border-color:var(--agent-ui-border);box-shadow:var(--agent-ui-shadow)}
.x-agent-floating__panel .ant-card-body{height:calc(100% - 57px);min-height:0;padding:0;overflow:hidden}
.x-agent-floating__simulated{font-size:12px;color:var(--agent-ui-text-secondary)}
@media(max-width:767px){
  .x-agent-chat__messages{padding:16px 12px}.x-agent-message{max-width:94%}.x-agent-composer{padding-inline:12px}.x-agent-chat__approval .ant-space{display:flex;flex-direction:column;align-items:stretch}.x-agent-chat__status{padding-inline:12px}
  .x-agent-conversation{grid-template-columns:1fr}.x-agent-conversation>.x-agent-conversation__sidebar{display:none}.x-agent-conversation__mobile-menu{display:inline-flex}.x-agent-conversation__header{padding:10px 12px}
  .x-agent-floating__launcher{left:auto!important;right:16px;top:auto!important;bottom:calc(16px + env(safe-area-inset-bottom));touch-action:auto}
  .x-agent-floating__panel{right:16px;bottom:calc(88px + env(safe-area-inset-bottom));width:calc(100vw - 32px);height:min(620px,calc(100vh - 112px))}
}
`
