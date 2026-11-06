/**
 * antd5 runtime bundle 入口。
 *
 * 这个文件被独立打包成一个 IIFE bundle（见 scripts/build-design-runtime.mjs），
 * 产物放在 public/design-runtime/antd5-runtime.js，供同源空白 iframe 注入。
 *
 * 它把 React、ReactDOM、antd5、@ant-design/icons、@ant-design/pro-components
 * 挂到 iframe 的 window 上，作为设计稿 .tsx 里 bare import 的运行时来源——
 * 由 DesignRenderer 在编译期把 `import { Button } from 'antd'` 这类引用改写
 * 成对 window 全局的具名取值。
 *
 * 注意：这里的 antd5 是通过 npm alias 装的 `antd5`（即 antd@5），与主工程
 * renderer 使用的 antd4 完全隔离——本入口只进 design-runtime bundle，绝不
 * 进入主 renderer chunk，因此两套 antd 不会在同一个 React 实例里冲突。
 */

import * as React from 'react'
import * as ReactDOM from 'react-dom'
import * as ReactDOMClient from 'react-dom/client'
import * as antd from 'antd5'
import * as antdIcons from '@ant-design/icons'
import * as proComponents from '@ant-design/pro-components'
import * as dayjs from 'dayjs'

// 挂到全局，供编译后的设计稿代码消费。
// 用一个命名空间对象收敛，避免污染 window 顶层过多。
const runtime = {
  React,
  ReactDOM,
  ReactDOMClient,
  antd,
  antdIcons,
  proComponents,
  dayjs
} as const

;(globalThis as unknown as Record<string, unknown>).__DESIGN_RUNTIME__ = runtime

export default runtime
