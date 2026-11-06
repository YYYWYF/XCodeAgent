import { AgentApplication, type AgentProps } from '@za38.03/agent-ui-runtime/ui'
import { resolveAgentUILocaleMessages } from '@za38.03/agent-ui-plugins'
import { useCallback, useState, type ReactElement } from 'react'
import { agentUIProject } from '../agent-ui/application/project-config'

/** 所有页面复用同一官方应用和会话，页面差异仅通过公开展示 props 传入。 */
export function Agent({ pageId, ...props }: AgentProps & { pageId: string }): ReactElement {
  const project = agentUIProject
  const [pageLocales, setPageLocales] = useState<Record<string, NonNullable<AgentProps['locale']>>>({})
  /** 页面默认语言跟随实际应用层；用户在官方控件中切换的选择保留在当前预览。 */
  const changeLocale = useCallback((value: NonNullable<AgentProps['locale']>): void => {
    setPageLocales((previous) => ({ ...previous, [pageId]: value }))
    props.onLocaleChange?.(value)
  }, [pageId, props.onLocaleChange])
  /** 未定制建议时沿用初始化应用层的官方语言资源。 */
  const suggestions: AgentProps['suggestions'] = (locale) => {
    const configured = project.getConversationStarterSuggestions(locale)
    if (configured.length) return configured
    const messages = resolveAgentUILocaleMessages(locale, props.locales?.[locale]).starterSuggestions
    return [
      { title: messages.architectureTitle, label: messages.architectureLabel, prompt: messages.architecturePrompt },
      { title: messages.debugTitle, label: messages.debugLabel, prompt: messages.debugPrompt },
      { title: messages.nextTitle, label: messages.nextLabel, prompt: messages.nextPrompt },
    ]
  }
  return <AgentApplication project={project} {...props}
    locale={props.locale ?? pageLocales[pageId] ?? project.defaultLocale}
    onLocaleChange={changeLocale} suggestions={props.suggestions ?? suggestions} />
}
