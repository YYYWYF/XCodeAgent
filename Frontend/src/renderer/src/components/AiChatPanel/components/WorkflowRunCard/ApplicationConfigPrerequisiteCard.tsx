import { Alert, Button, Checkbox, Form, Typography } from 'antd'
import type { ReactElement } from 'react'
import { useEffect, useMemo, useState } from 'react'
import type { ApplicationConfig, ApplicationDraft } from '../../../../typings'
import { configOperationFailureMessage, settingsConfigChanges } from '../../../SettingsPage/settingsConfigChanges'
import ApplicationConfigEditor, { type ApplicationConfigFieldPath } from '../../../Welcome/ApplicationConfigEditor'
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
  onSubmit: (changes: ConfigChanges) => Promise<void>
}

/** 在对话中明确展示配置意图，并让用户确认推断出的能力依赖。 */
export default function ApplicationConfigPrerequisiteCard({
  application, disabled, pending, onCancel, onSubmit
}: Props): ReactElement {
  const [form] = Form.useForm<ApplicationDraft>()
  const [acceptInferred, setAcceptInferred] = useState(false)
  const [submitting, setSubmitting] = useState(false)
  const [submitError, setSubmitError] = useState<string>()
  const suggested = pending.suggestedChanges || {}
  const inferred = pending.configPrerequisites || []
  const explicit = pending.configIntents || []
  const editablePaths = [...explicit, ...inferred]
    .map((item) => item.path)
    .filter((path): path is ApplicationConfigFieldPath => isApplicationConfigFieldPath(path))
  const needsInferredAcceptance = inferred.length > 0
  const initialValues = useMemo(() => mergeSuggested(application, suggested), [application, suggested])

  useEffect(() => {
    // 流式快照补全建议后，保持用户已输入字段，只补充首次展示的初始值。
    form.setFieldsValue(initialValues)
  }, [form, initialValues])

  const submit = async (): Promise<void> => {
    if (needsInferredAcceptance && !acceptInferred) return
    // 表单错误由 Form.Item 就地展示，避免以通用错误覆盖具体的缺失字段提示。
    let values: ApplicationDraft
    try {
      values = await form.validateFields()
    } catch {
      return
    }
    let submittedChanges: ConfigChanges = {}
    setSubmitting(true)
    setSubmitError(undefined)
    try {
      submittedChanges = configPrerequisiteChanges(
        application,
        mergeConversationConfigValues(application, values),
        inferred,
        acceptInferred
      )
      if (Object.keys(submittedChanges).length === 0) {
        setSubmitError('当前配置已满足，未提交空配置提案。')
        return
      }
      await onSubmit(submittedChanges)
    } catch (error) {
      // 配置预检、CAS 或模板收敛失败必须留在当前卡片，用户才能修正后重试。
      setSubmitError(configOperationFailureMessage(error, submittedChanges))
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <div className={cx('workflow-clarification', 'application-config-prerequisite-card')}>
      <div className={cx('workflow-clarification-header')}><Text strong>应用配置确认</Text></div>
      {explicit.map((item) => <Text key={`${item.path}:${String(item.desiredValue)}`} type="secondary">请求配置：{item.path}</Text>)}
      {inferred.map((item) => <Text key={`${item.path}:${item.reason || ''}`} type="secondary">依赖说明：{item.reason || item.path}</Text>)}
      <Form form={form} initialValues={initialValues} layout="vertical">
        <ApplicationConfigEditor form={form} mode="conversation" visiblePaths={editablePaths} />
      </Form>
      {submitError ? <Alert message={submitError} showIcon type="error" /> : null}
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
        <Button disabled={disabled || submitting} onClick={onCancel}>取消本次请求</Button>
        <Button disabled={disabled || submitting || (needsInferredAcceptance && !acceptInferred)} loading={submitting} onClick={() => void submit()} type="primary">
          确认配置并继续
        </Button>
      </div>
    </div>
  )
}

/** 过滤后端意图为当前受管表单允许的字段，避免未知路径被客户端注册或提交。 */
function isApplicationConfigFieldPath(path: string | undefined): path is ApplicationConfigFieldPath {
  return [
    'appName', 'appIcon', 'theme.primaryColor', 'auth.enable',
    'auth.yht.clientId', 'auth.yht.privateKey', 'auth.yht.publicKey', 'auth.yht.centerPublicKey',
    'authorization.enabled', 'authorization.initialAdministratorSubjects'
  ].includes(path as ApplicationConfigFieldPath)
}

/** 根据用户填写与明确勾选状态生成卡片提交提案，推断依赖绝不自动写入。 */
export function configPrerequisiteChanges(
  application: ApplicationConfig,
  values: ApplicationConfig,
  inferred: ConfigPrerequisite[],
  acceptInferred: boolean
): ConfigChanges {
  if (!acceptInferred) return settingsConfigChanges(application, values)
  // 先把已确认依赖合并进完整草稿，再统一做白名单差异，避免启用登录时漏掉用户填写的凭据字段。
  const proposed = structuredClone(values)
  for (const item of inferred) {
    if (!item.path || item.desiredValue === undefined) continue
    const parts = item.path.split('.')
    let cursor = proposed as unknown as Record<string, unknown>
    for (const part of parts.slice(0, -1)) cursor = cursor[part] as Record<string, unknown>
    cursor[parts.at(-1) || item.path] = item.desiredValue
  }
  return settingsConfigChanges(application, proposed)
}

/** 将对话卡已注册的局部表单值合并回完整配置，避免通用差异计算读取缺失字段。 */
export function mergeConversationConfigValues(
  application: ApplicationConfig,
  values: ApplicationDraft
): ApplicationConfig {
  const partial = values as Partial<ApplicationDraft>
  const currentYht = application.auth.yht ?? {
    clientId: '', privateKey: '', publicKey: '', centerPublicKey: ''
  }
  return {
    ...structuredClone(application),
    appName: partial.appName ?? application.appName,
    appIcon: partial.appIcon ?? application.appIcon,
    theme: { ...application.theme, ...partial.theme },
    auth: {
      ...application.auth,
      ...partial.auth,
      yht: { ...currentYht, ...partial.auth?.yht }
    },
    authorization: { ...application.authorization, ...partial.authorization }
  }
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
