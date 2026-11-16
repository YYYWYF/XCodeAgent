import path from 'node:path'

/** 仅保留标准异常类型和系统错误码，避免自定义错误文字携带请求或凭据。 */
export function describeDiagnosticError(error: unknown): Record<string, string> {
  try {
    if (!(error instanceof Error)) return { errorType: 'NonError' }
    const type =
      /^(?:Error|TypeError|RangeError|SyntaxError|ReferenceError|URIError|AggregateError|EvalError)$/
    const code = (error as NodeJS.ErrnoException).code
    const result: Record<string, string> = {
      errorType: type.test(error.name) ? error.name : 'Error'
    }
    if (typeof code === 'string' && /^(?:E[A-Z]{2,12}|ERR_[A-Z0-9_]{1,60})$/.test(code)) {
      result.errorCode = code
    }

    // 排除原始异常消息、绝对路径、网页 URL 和未经约束的源码文本。
    const frames = (error.stack ?? '')
      .split('\n')
      .slice(1)
      .flatMap((line) => {
        const match =
          /^\s+at (?:[^()]* \()?((?:node:[\w/.-]+|(?:[a-z]:)?[\\/][^\r\n()]+)):(\d+):(\d+)\)?$/i.exec(
            line
          )
        if (!match) return []
        const source = match[1].startsWith('node:') ? match[1] : path.win32.basename(match[1])
        if (!/^(?:node:[\w/.-]+|[\w.-]+\.(?:js|cjs|mjs|ts|tsx))$/.test(source)) return []
        return [`${source}:${match[2]}:${match[3]}`]
      })
    if (frames.length) result.stack = frames.slice(0, 15).join('\n')
    return result
  } catch {
    return { errorType: 'Error' }
  }
}
