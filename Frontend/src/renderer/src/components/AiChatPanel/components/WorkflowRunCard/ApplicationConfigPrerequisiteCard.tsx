import { Button, Checkbox, Form, Typography } from 'antd'
import type { ReactElement } from 'react'
import { useEffect, useMemo, useState } from 'react'
import type { ApplicationConfig, ApplicationDraft } from '../../../../typings'
import { settingsConfigChanges } from '../../../SettingsPage/settingsConfigChanges'
import ApplicationConfigEditor from '../../../Welcome/ApplicationConfigEditor'
import type { ConfigChanges } from '../../../../service/applicationConfigOperation'
import { cx } from '../../../../utils'

const { Text } = Typography

export type ConfigPrerequisite = {
  path?: string
  desiredValue?: unknown
  source?: string
  reason?: string
}

type PendingConfig = {
  requestId?: string
  expectedConfigRevision?: number
  configIntents?: ConfigPrerequisite[]
  configPrerequisites?: ConfigPrerequisite[]
  suggestedChanges?: ConfigChanges
}

type Props = {
  application: ApplicationConfig
  disabled?: boolean
  pending: PendingConfig
  onCancel: () => void
  onSubmit: (changes: ConfigChanges) => void
}

/** 在对话中明确展示配置意图，并让用户确认推断出的能力依赖。 */
export default function ApplicationConfigPrerequisiteCard({
  application, disabled, pending, onCancel, onSubmit
}: Props): ReactElement {
  const [form] = Form.useForm<ApplicationDraft>()
  const [acceptInferred, setAcceptInferred] = useState(false)
  const suggested = pending.suggestedChanges || {}
  const inferred = pending.configPrerequisites || []
  const explicit = pending.configIntents || []
  const needsInferredAcceptance = inferred.length > 0
  const initialValues = useMemo(() => mergeSuggested(application, suggested), [application, suggested])

  useEffect(() => {
    // 流式快照补全建议后，保持用户已输入字段，只补充首次展示的初始值。
    form.setFieldsValue(initialValues)
  }, [form, initialValues])

  const submit = async (): Promise<void> => {
    if (needsInferredAcceptance && !acceptInferred) return
    const values = await form.validateFields()
    onSubmit(configPrerequisiteChanges(application, values as unknown as ApplicationConfig, inferred, acceptInferred))
  }

  return (
    <div className={cx('workflow-clarification', 'application-config-prerequisite-card')}>
      <div className={cx('workflow-clarification-header')}><Text strong>应用配置确认</Text></div>
      {explicit.map((item) => <Text key={`${item.path}:${String(item.desiredValue)}`} type="secondary">请求配置：{item.path}</Text>)}
      {inferred.map((item) => <Text key={`${item.path}:${item.reason || ''}`} type="secondary">依赖说明：{item.reason || item.path}</Text>)}
      <Form form={form} initialValues={initialValues} layout="vertical">
        <ApplicationConfigEditor form={form} mode="conversation" />
      </Form>
      {needsInferredAcceptance ? (
        <Checkbox checked={acceptInferred} disabled={disabled} onChange={(event) => {
          const accepted = event.target.checked
          setAcceptInferred(accepted)
          // 勾选前置能力后立即展示其依赖字段和校验，避免提交时才出现隐式失败。
          if (accepted) {
            const inferredValues = Object.fromEntries(
              inferred.filter((item) => item.path && item.desiredValue !== undefined)
                .map((item) => [item.path as string, item.desiredValue as string | boolean | string[]])
            )
            form.setFieldsValue(mergeSuggested(form.getFieldsValue(true) as ApplicationConfig, inferredValues))
          }
        }}>
          我确认启用上述推断出的配置依赖
        </Checkbox>
      ) : null}
      <div className={cx('workflow-clarification-actions')}>
        <Button disabled={disabled} onClick={onCancel}>取消本次请求</Button>
        <Button disabled={disabled || (needsInferredAcceptance && !acceptInferred)} onClick={() => void submit()} type="primary">
          确认配置并继续
        </Button>
      </div>
    </div>
  )
}

/** 根据用户填写与明确勾选状态生成卡片提交提案，推断依赖绝不自动写入。 */
export function configPrerequisiteChanges(
  application: ApplicationConfig,
  values: ApplicationConfig,
  inferred: ConfigPrerequisite[],
  acceptInferred: boolean
): ConfigChanges {
  const changes = settingsConfigChanges(application, values)
  if (!acceptInferred) return changes
  for (const item of inferred) {
    if (item.path && item.desiredValue !== undefined) changes[item.path] = item.desiredValue as never
  }
  return changes
}

/** 将后端建议覆盖到当前配置，避免在卡片中展示或回传未建议的字段变更。 */
function mergeSuggested(application: ApplicationConfig, suggested: ConfigChanges): ApplicationConfig {
  const result = structuredClone(application)
  for (const [path, value] of Object.entries(suggested)) {
    const parts = path.split('.')
    let cursor: Record<string, unknown> = result as unknown as Record<string, unknown>
    for (const part of parts.slice(0, -1)) cursor = cursor[part] as Record<string, unknown>
    cursor[parts.at(-1) || path] = value
  }
  return result
}
