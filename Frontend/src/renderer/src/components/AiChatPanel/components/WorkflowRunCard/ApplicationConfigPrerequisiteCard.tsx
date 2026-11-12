import { Alert, Button, Checkbox, Form, Typography, type FormInstance } from 'antd'
import type { ReactElement } from 'react'
import { useEffect, useMemo, useRef, useState, useSyncExternalStore } from 'react'
import type { ApplicationConfig, ApplicationDraft } from '../../../../typings'
import { configOperationFailureMessage, settingsConfigChanges } from '../../../SettingsPage/settingsConfigChanges'
import ApplicationConfigEditor, { type ApplicationConfigFieldPath } from '../../../Welcome/ApplicationConfigEditor'
import { LOGIN_AUTHORIZATION_DEFAULTS } from '../../../Welcome/constants'
import type { ConfigChanges } from '../../../../service/applicationConfigOperation'
import {
  configActionPhase, resolveConfigPrerequisiteMode, subscribeConfigAction,
  type ConfigPrerequisiteAction, type ConfigPrerequisiteMode
} from '../../../../service/applicationConfigContinuation'
import { cx } from '../../../../utils'

import ConfigCardBoundary, { configurationViewError, InvalidConfiguration } from './ConfigCardBoundary'

const { Text } = Typography
const EMPTY_CONFIG_CHANGES: ConfigChanges = {}
const EMPTY_PREREQUISITES: ConfigPrerequisite[] = []

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
  historical?: boolean
  result?: 'confirmed' | 'cancelled' | 'submitted'
  onAction?: (input: ConfigPrerequisiteAction) => Promise<void>
}

/** 校验完整配置并隔离卡片渲染错误。 */
export default function ApplicationConfigPrerequisiteCard(props: Props): ReactElement {
  const reason = configurationViewError(props.application)
  if (reason) return <InvalidConfiguration reason={reason} />
  const identity = JSON.stringify([props.application.workspaceRoot, props.pending?.requestId])
  return <ConfigCardBoundary key={identity}><ValidatedConfigCard key={identity} {...props} /></ConfigCardBoundary>
}

/** 按原请求归属展示唯一配置动作，并区分配置同步与会话续跑。 */
function ValidatedConfigCard({
  application, disabled, historical, result, pending, onAction
}: Props): ReactElement {
  const [form] = Form.useForm<ApplicationDraft>()
  const [acceptInferred, setAcceptInferred] = useState(false)
  const [submitError, setSubmitError] = useState<string>()
  const [accepted, setAccepted] = useState<'confirmed' | 'cancelled'>()
  const [check, setCheck] = useState<{ mode: ConfigPrerequisiteMode } | { error: string }>()
  const [checkAttempt, setCheckAttempt] = useState(0)
  const [submitting, setSubmitting] = useState(false)
  const clickLock = useRef(false)
  const workspaceRoot = application.workspaceRoot
  const requestId = typeof pending?.requestId === 'string' ? pending.requestId : ''
  const phase = useSyncExternalStore(subscribeConfigAction, () => configActionPhase(workspaceRoot, requestId), () => undefined)
  const outcome = result ?? accepted
  const inactive = Boolean(historical || outcome || !onAction)
  const busy = submitting || Boolean(phase)
  const mode = check && 'mode' in check ? check.mode : undefined
  const suggested = pending?.suggestedChanges ?? EMPTY_CONFIG_CHANGES
  const inferred = pending?.configPrerequisites ?? EMPTY_PREREQUISITES
  const explicit = pending?.configIntents ?? EMPTY_PREREQUISITES
  const editablePaths = resolveConversationEditablePaths(explicit, inferred, suggested)
  const needsInferredAcceptance = inferred.length > 0
  const initialChanges = useMemo(
    () => resolveConversationInitialChanges(explicit, inferred, suggested),
    [explicit, inferred, suggested]
  )
  const initialValues = useMemo(() => mergeSuggested(application, initialChanges), [application, initialChanges])
  const initializedPaths = useRef(new Set<string>())
  useEffect(() => {
    if (mode === 'apply') initializeConversationFields(form, initialValues, editablePaths, initializedPaths.current)
  }, [form, initialValues, editablePaths, mode])

  useEffect(() => {
    if (inactive || phase || clickLock.current) return
    let current = true
    setCheck(undefined)
    void resolveConfigPrerequisiteMode(workspaceRoot, requestId).then(
      (next) => { if (current) setCheck({ mode: next }) },
      (error) => { if (current) setCheck({ error: configOperationFailureMessage(error, {}) }) }
    )
    return () => { current = false }
  }, [workspaceRoot, requestId, inactive, phase, checkAttempt])

  /** 恢复和续跑直接提交原操作身份；新提案才读取表单。 */
  const submit = async (action: ConfigPrerequisiteMode | 'cancelled'): Promise<void> => {
    if (!onAction || !requestId || disabled || inactive || busy || clickLock.current) return
    clickLock.current = true
    setSubmitting(true)
    setSubmitError(undefined)
    let changes: ConfigChanges = {}
    try {
      if (action !== 'apply') {
        await onAction({ action })
        setAccepted(action === 'cancelled' ? 'cancelled' : 'confirmed')
        return
      }
      if (needsInferredAcceptance && !acceptInferred) return
      // 表单字段错误由 Form.Item 就地展示。
      const values = await form.validateFields().catch(() => undefined)
      if (!values) return
      changes = configPrerequisiteChanges(application, mergeConversationConfigValues(application, values), inferred, acceptInferred)
      if (!Object.keys(changes).length) {
        setSubmitError('当前表单没有新的配置修改。')
        return
      }
      await onAction({ action, changes })
      setAccepted('confirmed')
    } catch (error) {
      setSubmitError(configOperationFailureMessage(error, changes))
      setCheck(undefined)
      setCheckAttempt((value) => value + 1)
    } finally {
      clickLock.current = false
      setSubmitting(false)
    }
  }

  const showForm = !inactive && mode === 'apply' && phase !== 'continuing'
  const primaryLabel = mode === 'resume' ? '继续完成配置并执行' : mode === 'continue' ? '继续原请求' : '确认配置并继续'
  const checkError = check && 'error' in check ? check.error : undefined
  return (
    <div className={cx('workflow-clarification', 'application-config-prerequisite-card')}>
      <div className={cx('workflow-clarification-header')}><Text strong>
        {mode === 'resume' && !inactive && !busy ? '配置同步尚未完成' : '应用配置确认'}
      </Text></div>
      {explicit.map((item) => <Text key={`${item.path}:${String(item.desiredValue)}`} type="secondary">请求配置：{item.path}</Text>)}
      {inferred.map((item) => <Text key={`${item.path}:${item.reason || ''}`} type="secondary">依赖说明：{item.reason || item.path}</Text>)}
      {outcome === 'confirmed' ? <Text>配置已完成，已继续处理原请求。</Text> :
        outcome === 'cancelled' ? <Text>已取消本次请求。</Text> :
        phase === 'continuing' ? <Text>配置已完成，正在继续原请求…</Text> :
        busy ? <Text>正在同步工程配置…</Text> :
        !inactive && !check ? <Text>正在确认上次配置状态…</Text> :
        !inactive && mode === 'resume' ? <Text>应用配置已保存，工程能力安装或参数同步尚未完成。继续执行上次中断的步骤会保留已有工程。</Text> :
        !inactive && mode === 'continue' ? <Text>配置已完成，原请求待继续。继续原请求只会提交会话续跑。</Text> : null}
      {showForm ? <Form form={form} initialValues={initialValues} layout="vertical">
        <fieldset disabled={disabled || busy} style={{ border: 0, padding: 0, margin: 0, minWidth: 0 }}>
          <ApplicationConfigEditor form={form} mode="conversation" visiblePaths={editablePaths}
            administratorBaseline={application.authorization.enabled ? application.authorization.initialAdministratorSubjects : []} />
        </fieldset>
      </Form> : null}
      {!inactive && (submitError || checkError) ? <Alert message={submitError || checkError} showIcon type="error" /> : null}
      {showForm && needsInferredAcceptance ? (
        <Checkbox checked={acceptInferred} disabled={disabled || busy} onChange={(event) => {
          const accepted = event.target.checked
          setAcceptInferred(accepted)
          // 勾选前置能力后立即展示其依赖字段和校验。
          if (accepted) {
            const inferredValues = Object.fromEntries(
              inferred.filter((item) => item.path && item.desiredValue !== undefined)
                .map((item) => [item.path as string, item.desiredValue as string | boolean | string[]])
            )
            form.setFieldsValue(mergeSuggested(mergeConversationConfigValues(application, form.getFieldsValue(true)), inferredValues))
          }
        }}>
          我确认启用上述推断出的配置依赖
        </Checkbox>
      ) : null}
      {!inactive ? <div className={cx('workflow-clarification-actions')}>
        <Button disabled={disabled || busy || !requestId} title="取消后保留已经保存的应用配置"
          onClick={() => void submit('cancelled')}>取消本次请求</Button>
        {checkError && requestId ? <Button disabled={disabled || busy} onClick={() => {
          setSubmitError(undefined)
          setCheck(undefined)
          setCheckAttempt((value) => value + 1)
        }}>重新检查</Button> : null}
        {mode && phase !== 'continuing' ? <Button
          disabled={disabled || busy || (mode === 'apply' && needsInferredAcceptance && !acceptInferred)}
          loading={busy} onClick={() => void submit(mode)} type="primary">{primaryLabel}</Button> : null}
      </div> : null}
    </div>
  )
}

/** 过滤后端意图为当前受管表单允许的字段，避免未知路径被客户端注册或提交。 */
function isApplicationConfigFieldPath(path: string | undefined): path is ApplicationConfigFieldPath {
  return [
    'appName', 'appIcon', 'theme.primaryColor', 'auth.enable',
    'auth.yht.clientId', 'auth.yht.privateKey', 'auth.yht.publicKey', 'auth.yht.centerPublicKey',
    'auth.yht.centerUri', 'auth.yht.authorizeUri', 'auth.yht.redirectUri', 'auth.yht.endUrl', 'auth.yht.logoutShareJs',
    'authorization.enabled', 'authorization.initialAdministratorSubjects', 'authorization.api.baseUrl', 'authorization.api.viewId'
  ].includes(path as ApplicationConfigFieldPath)
}

const LOGIN_CONFIGURATION_PATHS: readonly ApplicationConfigFieldPath[] = [
  'auth.enable', 'auth.yht.clientId', 'auth.yht.privateKey', 'auth.yht.publicKey', 'auth.yht.centerPublicKey',
  'auth.yht.centerUri', 'auth.yht.authorizeUri', 'auth.yht.redirectUri', 'auth.yht.endUrl', 'auth.yht.logoutShareJs'
]
const AUTHORIZATION_CONFIGURATION_PATHS: readonly ApplicationConfigFieldPath[] = [
  'authorization.enabled', 'authorization.initialAdministratorSubjects', 'authorization.api.baseUrl', 'authorization.api.viewId'
]

/** 将启动能力的开关意图扩展为完整字段组，使对话卡与新建、设置页使用同一配置表单。 */
export function resolveConversationEditablePaths(
  explicit: ConfigPrerequisite[],
  inferred: ConfigPrerequisite[],
  suggested: ConfigChanges
): ApplicationConfigFieldPath[] {
  const paths = new Set<ApplicationConfigFieldPath>([
    ...[...explicit, ...inferred].map((item) => item.path),
    ...Object.keys(suggested)
  ].filter(isApplicationConfigFieldPath))
  const hasLoginConfiguration = [...paths].some((path) => path === 'auth.enable' || path.startsWith('auth.yht.'))
  const hasAuthorizationConfiguration = [...paths].some((path) => path === 'authorization.enabled' || path.startsWith('authorization.'))
  // 权限能力依赖登录；任一能力的启动必须让用户能填写其全部必填字段。
  if (hasLoginConfiguration || hasAuthorizationConfiguration) {
    for (const path of LOGIN_CONFIGURATION_PATHS) paths.add(path)
  }
  if (hasAuthorizationConfiguration) {
    for (const path of AUTHORIZATION_CONFIGURATION_PATHS) paths.add(path)
  }
  return [...paths]
}

/** 将权限启动意图同步为登录初始值，确保依赖凭据字段在确认前即可见且可校验。 */
export function resolveConversationInitialChanges(
  explicit: ConfigPrerequisite[],
  inferred: ConfigPrerequisite[],
  suggested: ConfigChanges
): ConfigChanges {
  const capabilityIntents = [...explicit, ...inferred]
  const enables = (path: 'auth.enable' | 'authorization.enabled'): boolean =>
    suggested[path] === true || capabilityIntents.some((item) => item.path === path && item.desiredValue === true)
  return enables('auth.enable') || enables('authorization.enabled')
    ? { ...suggested, 'auth.enable': true }
    : suggested
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
    applyConfigValue(proposed, item.path, item.desiredValue)
  }
  return settingsConfigChanges(application, proposed)
}

/** 将对话卡已注册的局部表单值合并回完整配置，避免通用差异计算读取缺失字段。 */
export function mergeConversationConfigValues(
  application: ApplicationConfig,
  values: ApplicationDraft
): ApplicationConfig {
  const partial = values as Partial<ApplicationDraft>
  // 对话卡也可能收到未启用登录时落盘的当前配置，合并局部字段前先补齐当前契约的基线。
  const currentYht = application.auth.yht ?? {
    clientId: '',
    privateKey: '',
    publicKey: '',
    centerPublicKey: '',
    ...LOGIN_AUTHORIZATION_DEFAULTS
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
    authorization: {
      ...application.authorization,
      ...partial.authorization,
      ...(application.authorization.api || partial.authorization?.api ? {
        api: {
          baseUrl: partial.authorization?.api?.baseUrl ?? application.authorization.api?.baseUrl ?? '',
          viewId: partial.authorization?.api?.viewId ?? application.authorization.api?.viewId ?? ''
        }
      } : {})
    }
  }
}

/** 将后端建议覆盖到当前配置，避免在卡片中展示或回传未建议的字段变更。 */
export function mergeSuggested(application: ApplicationConfig, suggested: ConfigChanges): ApplicationConfig {
  const result = structuredClone(application)
  // 对话卡直接消费application.json，先补齐与新建/设置页一致的默认表单值。
  const { clientId = '', privateKey = '', publicKey = '', centerPublicKey = '', ...existingYht } = result.auth.yht ?? {}
  result.auth.yht = {
    clientId,
    privateKey,
    publicKey,
    centerPublicKey,
    ...LOGIN_AUTHORIZATION_DEFAULTS,
    ...existingYht
  }
  result.authorization.api = {
    baseUrl: LOGIN_AUTHORIZATION_DEFAULTS.baseUrl,
    viewId: LOGIN_AUTHORIZATION_DEFAULTS.viewId,
    ...result.authorization.api
  }
  for (const [path, value] of Object.entries(suggested)) {
    applyConfigValue(result, path, value)
  }
  return result
}

/** 按白名单字段路径写入配置，并为当前配置中不存在的对象层级创建容器。 */
function applyConfigValue(target: ApplicationConfig, path: string, value: unknown): void {
  const parts = path.split('.')
  let cursor = target as unknown as Record<string, unknown>
  for (const part of parts.slice(0, -1)) {
    const nested = cursor[part]
    if (nested === undefined) {
      cursor[part] = {}
    } else if (typeof nested !== 'object' || nested === null || Array.isArray(nested)) {
      throw new Error(`${path} 的父级配置结构无效。`)
    }
    cursor = cursor[part] as Record<string, unknown>
  }
  cursor[parts.at(-1) || path] = value
}


/** 只初始化同一请求中新展示且未编辑的字段，防止流式建议覆盖用户草稿。 */
export function initializeConversationFields(
  form: Pick<FormInstance, 'isFieldTouched' | 'setFieldValue'>,
  initialValues: ApplicationConfig,
  paths: ApplicationConfigFieldPath[],
  initialized: Set<string>
): void {
  for (const path of paths) {
    if (initialized.has(path)) continue
    initialized.add(path)
    const parts = path.split('.')
    if (form.isFieldTouched(parts)) continue
    let value: unknown = initialValues
    for (const part of parts) value = (value as Record<string, unknown>)?.[part]
    form.setFieldValue(parts, value)
  }
}
