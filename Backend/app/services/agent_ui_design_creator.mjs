import { createHash } from 'node:crypto'
import { readFile, readdir, writeFile } from 'node:fs/promises'
import { createRequire } from 'node:module'
import { createInterface } from 'node:readline'
import path from 'node:path'
import { pathToFileURL } from 'node:url'

// SDK 凭据、模型回复、原始异常均不进入控制回执。
let client
let closing
let submitted = false
const lines = createInterface({ input: process.stdin, crlfDelay: Infinity })
process.stdout.on('error', () => {})

/** 只输出固定的任务状态，不将模型的完成声明当作文件修改证明。 */
function emit(value) {
  process.stdout.write(`${JSON.stringify(value)}\n`)
}

/** 将共享应用 CSS 隔离到唯一 Agent 容器，业务页样式不受影响。 */
async function scopeSharedStyles(root, postcss) {
  const scope = '.agent-design'
  const suffix = createHash('sha256').update('shared-agent-ui').digest('hex').slice(0, 12)
  /** 仅遍历共享应用层，不进入依赖或跟随链接。 */
  async function visit(directory) {
    for (const entry of await readdir(directory, { withFileTypes: true })) {
      const file = path.join(directory, entry.name)
      if (entry.isSymbolicLink()) throw new Error('linked stylesheet')
      if (entry.isDirectory()) { await visit(file); continue }
      if (!entry.name.endsWith('.css')) continue
      const original = await readFile(file, 'utf8')
      const css = postcss.parse(original)
      const animations = new Map()
      css.walkAtRules(rule => {
        if (/^(font-face|namespace|page|property|scope)$/i.test(rule.name)
            || (rule.name === 'import' && /https?:|url\s*\(/i.test(rule.params))) {
          throw new Error('global stylesheet resource')
        }
        if (/keyframes$/i.test(rule.name)) {
          const name = rule.params
          const scoped = name.endsWith(`-${suffix}`) ? name : `${name}-${suffix}`
          animations.set(name, scoped)
          rule.params = scoped
        }
      })
      css.walkRules(rule => {
        if (rule.parent?.type === 'atrule' && /keyframes$/i.test(rule.parent.name)) return
        rule.selectors = rule.selectors.map(selector => {
          if (selector.startsWith(scope)) return selector
          if (selector === ':root' || selector === 'html' || selector === 'body') return scope
          return `${scope} ${selector}`
        })
      })
      css.walkDecls(declaration => {
        if (/url\s*\(|expression\s*\(/i.test(declaration.value)) throw new Error('external stylesheet resource')
        if (/^animation(?:-name)?$/.test(declaration.prop)) {
          declaration.value = declaration.value.split(/(\s+|,)/).map(value => animations.get(value) ?? value).join('')
        }
      })
      const result = css.toString()
      if (result !== original) await writeFile(file, result)
    }
  }
  await visit(root)
}

/** 停止真实 SDK 及其子进程后回执，取消不等同于断开订阅。 */
async function close() {
  if (closing) return closing
  closing = (async () => {
    try {
      await client?.dispose()
      emit({ type: 'stopped' })
      process.exit(0)
    } catch {
      emit({ type: 'failed', code: 'stop_unconfirmed' })
      process.exit(1)
    }
  })()
  return closing
}

/** 从已核对的工具安装解析公开客户端，一次草稿任务只提交一次。 */
async function start(input) {
  try {
    const require = createRequire(pathToFileURL(path.join(process.cwd(), 'package.json')))
    const metadata = await readFile(require.resolve('@za38.03/agent-ui-creator/package.json'))
    if (createHash('sha256').update(metadata).digest('hex') !== input.packageJsonSha256
        || process.versions.node !== input.nodeVersion || process.versions.electron
        || !path.isAbsolute(input.projectRoot) || typeof input.request !== 'string' || !input.request.trim()) {
      throw new Error('invalid binding')
    }
    const { createCreatorClient } = await import('@za38.03/agent-ui-creator')
    if (closing) return
    client = createCreatorClient({ projectRoot: input.projectRoot, allowExternalEndpoint: false,
      environment: { ...process.env, CREATOR_HOT_RELOAD: 'false' } })
    emit({ type: 'progress', code: 'creator_running' })
    const response = await client.run(input.request)
    if (closing) return
    if (response.result?.completion !== 'success') {
      emit({ type: 'failed', code: response.result?.completion === 'needs_user_input' ? 'needs_user_input' : 'creator_blocked' })
      return
    }
    if (response.result?.executionPolicy === 'read-only') {
      emit({ type: 'failed', code: 'creator_read_only' })
      return
    }
    const receipt = response.result?.receipt
    if (!Array.isArray(receipt?.files) || !receipt.files.length) {
      emit({ type: 'failed', code: 'no_project_change' })
      return
    }
    if (input.sourceRoot) {
      emit({ type: 'progress', code: 'creator_verifying' })
      const source = path.resolve(input.projectRoot, input.sourceRoot)
      if (!source.startsWith(`${input.projectRoot}${path.sep}`)) throw new Error('source outside project')
      const requireFromCreator = createRequire(require.resolve('@za38.03/agent-ui-creator/package.json'))
      const requireFromTarget = createRequire(path.join(input.projectRoot, 'package.json'))
      const { validateCreatorSource } = await import(input.sourceValidator)
      try {
        for (const file of receipt.files) {
          if (file.status === 'deleted' || !/\.tsx?$/.test(file.path)) continue
          // 官方组合工具生成的 Registry 含必要的惰性导入，由下方公开验证器逐字校验新鲜度。
          if (file.path.endsWith('/plugins/registry.generated.ts')) continue
          const absolute = path.resolve(input.projectRoot, file.path.replace(/^\//, ''))
          if (!absolute.startsWith(`${input.projectRoot}${path.sep}`)) throw new Error('source outside project')
          validateCreatorSource(requireFromTarget('typescript'), absolute, await readFile(absolute, 'utf8'))
        }
      } catch (error) {
        emit({ type: 'failed', code: ['source_boundary_failed', 'source_syntax_failed'].includes(error?.code)
          ? error.code : 'source_validation_unavailable' })
        return
      }
      await scopeSharedStyles(source, requireFromCreator('postcss'))
      const { verifyUIProject } = await import('@za38.03/agent-ui-creator/project/dev')
      const verification = await verifyUIProject(input.projectRoot)
      if (verification.status !== 'passed') {
        emit({ type: 'failed', code: 'composition_invalid' })
        return
      }
    }
    // 仅保留公开回执中的验证摘要；实际文件、范围与构建由 Python 独立复核。
    const status = receipt?.verification?.status
    const allowed = new Set(['not-run', 'changed-and-statically-verified', 'changed-and-verified',
      'changed-unverified', 'no-project-change', 'decision-no-project-change', 'failed'])
    emit({ type: 'completed', ...(allowed.has(status) ? { verificationStatus: status } : {}) })
  } catch (error) {
    // 只分类公开错误，不回传可能含凭据的 SDK 原始异常或模型回复。
    const message = error instanceof Error ? error.message : ''
    const code = /timed?\s*out|timeout|aborted/i.test(message) ? 'creator_timeout'
      : /429|rate.?limit|too many requests/i.test(message) ? 'creator_rate_limited'
        : /401|unauthori[sz]ed|invalid.api.key/i.test(message) ? 'creator_auth_failed'
          : /connect|network|fetch failed|socket|econn|enotfound/i.test(message) ? 'creator_connection_failed' : 'creator_failed'
    if (!closing) emit({ type: 'failed', code })
  } finally {
    await close()
  }
}

/** 接收一次任务或停止请求；重复提交立即失败，不重新启动模型。 */
lines.on('line', line => {
  try {
    if (Buffer.byteLength(line) > 128 * 1024) throw new Error('input too large')
    const input = JSON.parse(line)
    if (input.action === 'stop') { void close(); return }
    if (submitted) throw new Error('duplicate submission')
    submitted = true
    void start(input)
  } catch {
    emit({ type: 'failed', code: 'invalid_request' })
    void close()
  }
})
lines.on('close', () => { void close() })
process.once('SIGTERM', () => { void close() })
process.once('SIGINT', () => { void close() })
