/**
 * 设计稿 .tsx 运行时编译器。
 *
 * 把 LLM 生成的 .tsx 源码字符串编译成可在同源 iframe 里执行的 JS：
 * 1. 用 sucrase 做 TS/JSX/ESM→CJS 转译（不查类型，与后端 validate_tsx 的
 *    esbuild transform 定位一致——设计稿代码落盘前已在后端校验过语法）。
 * 2. sucrase 的 imports transform 会把 `import` 转成 `require()`、
 *    `export default` 转成 `exports.default =`。再把 `require('antd')` 等
 *    改写成对 iframe window 上 __DESIGN_RUNTIME__ 全局的取值，让编译产物
 *    不依赖任何模块系统即可运行。
 *
 * 这是方案 B 的"运行时编译"环节——替代原先由独立 Vite dev server 做的编译。
 */

import { transform } from 'sucrase'

/** 设计稿允许的 require 来源 → 运行时全局上的命名空间键名。 */
const RUNTIME_MODULE_MAP: Record<string, string> = {
  react: 'React',
  'react-dom': 'ReactDOM',
  'react-dom/client': 'ReactDOMClient',
  antd: 'antd',
  antd5: 'antd',
  '@ant-design/icons': 'antdIcons',
  '@ant-design/pro-components': 'proComponents'
}

/**
 * 把 require('xxx') 改写成对运行时全局的取值。
 *
 * sucrase 产物形如：
 *   var _react = require('react');
 *   var _react2 = _interopRequireDefault(_react);
 *   var _antd = require('antd');
 * 我们把 require('react') → window.__DESIGN_RUNTIME__.React，并让
 * _interopRequireDefault 对运行时对象正常工作（运行时对象带 __esModule
 * 标记的 default 取值）。
 */
function rewriteRequire(code: string): string {
  // 匹配 require('xxx') 或 require("xxx")
  return code.replace(
    /\brequire\(\s*['"]([^'"]+)['"]\s*\)/g,
    (full, spec: string) => {
      const nsKey = RUNTIME_MODULE_MAP[spec]
      if (!nsKey) {
        // 未知来源：保留原样，运行时 require 未定义会报错（设计稿规范禁用未登记的 import）。
        return full
      }
      return `window.__DESIGN_RUNTIME__.${nsKey}`
    }
  )
}

/**
 * 编译 .tsx 源码为可在 iframe 内执行的 JS 字符串。
 *
 * 返回的字符串里：所有 import 已转为对 window.__DESIGN_RUNTIME__ 的取值，
 * JSX/TS 已转译为普通 JS，export default 已转为给 exports 赋值。
 * 调用方包一层 exports 对象执行，即可拿到默认导出组件。
 */
export function compileTsx(source: string): string {
  // sucrase 转 TS/JSX/ESM→CJS。production 去除 dev-only 检查。
  // jsxRuntime: automatic 需要引入 jsx-runtime，但运行时对象里没有，
  // 这里改用 classic 运行时（React.createElement），与设计稿代码里
  // 显式 import React 的写法兼容。
  const transformed = transform(source, {
    transforms: ['jsx', 'typescript', 'imports'],
    production: true,
    jsxRuntime: 'classic'
  }).code

  // 把 require('xxx') 改写为对运行时全局的取值。
  const rewritten = rewriteRequire(transformed)

  // 包一层 exports 对象执行，把默认导出挂到 window.__DESIGN_COMPONENT__。
  // 注意：产物会被注入 <script> 标签在顶层执行，不能有 return 语句，
  // 所以用全局赋值而非返回值传递组件。
  // sucrase 的 imports transform 会产出 exports.default = ...，
  // 以及 _interopRequireDefault 包装默认导入。运行时对象是命名空间
  // （带 __esModule），_interopRequireDefault 会正确取 .default。
  const wrapped = `
    var __design_exports__ = {};
    (function(exports){
      "use strict";
      ${rewritten}
    })(__design_exports__);
    window.__DESIGN_COMPONENT__ = __design_exports__.default;
  `
  return wrapped
}

