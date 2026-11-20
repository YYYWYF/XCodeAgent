import { type ReactElement, type ComponentProps, type ComponentType } from 'react'
import { ConversationAdapter, createAgentUIProject, createPluginCapabilityCatalog, createRuntimeCompositionStore,
  type AgentUIProjectOptions } from '@za38.03/agent-ui-runtime/ui'
import type { UIPluginComponentProps } from '@za38.03/agent-ui-plugins'
import { RuntimeBusinessCards } from './RuntimeBusinessPanel'

/** 在官方消息锚点追加业务卡片，保留当前设计的所有其他插槽。 */
function BusinessConversationAdapter(props: ComponentProps<typeof ConversationAdapter> = {}): ReactElement {
  return <ConversationAdapter {...props} rendererSlots={{ ...props.rendererSlots, transientMessages: true }}
    renderScopedSlot={(name, scope, fallback) => <>
      {name !== 'transientMessages' && (props.renderScopedSlot?.(name, scope, fallback) ?? fallback)}
      {name === 'transientMessages' && scope.kind === 'conversation.transient-messages'
        && <RuntimeBusinessCards afterMessageId={(scope.value as { afterMessageId: string | null }).afterMessageId} />}
    </>} />
}

/** 只替换项目公开 Adapter；不改包源码、AppUIModel 或原会话服务。 */
export function withBusinessPresentation(project: AgentUIProjectOptions): AgentUIProjectOptions {
  const entries = project.pluginCapabilityCatalog.list().map((entry) => entry.manifest.id !== 'conversation-surface'
    ? entry : { ...entry, loadDefinition: async () => {
      const definition = await entry.loadDefinition()
      const Surface: ComponentType<UIPluginComponentProps & { Adapter?: typeof ConversationAdapter }> = definition.Component
      return { ...definition,
        /** 既有应用层仍负责会话列表、欢迎、Composer 与 renderer 配置。 */
        Component: (props: UIPluginComponentProps): ReactElement => <Surface {...props} Adapter={BusinessConversationAdapter} />,
      }
    } })
  return createAgentUIProject({ ...project, pluginCapabilityCatalog: createPluginCapabilityCatalog(entries),
    compositionStore: createRuntimeCompositionStore() })
}
