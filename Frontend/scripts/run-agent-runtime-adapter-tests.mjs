import { mkdtemp, rm } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { resolve, dirname } from 'node:path'
import { fileURLToPath } from 'node:url'
import { execFileSync } from 'node:child_process'
import { createRequire } from 'node:module'
import { build } from 'esbuild'
import ts from 'typescript'

const require = createRequire(import.meta.url)
const root = resolve(dirname(fileURLToPath(import.meta.url)), '..')
const source = resolve(root, '../Backend/app/builtin_templates/agent-ui-frontend/src')
const modules = resolve(root, 'node_modules')
const files = ts.sys.readDirectory(source, ['.ts', '.tsx'])
const options = { noEmit: true, strict: true, skipLibCheck: true, esModuleInterop: true,
  target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext, moduleResolution: ts.ModuleResolutionKind.Bundler,
  jsx: ts.JsxEmit.ReactJSX, baseUrl: root, paths: { '@/*': [`${source}/*`],
    antd: [`${modules}/antd5`],
    react: [`${modules}/@types/react/index.d.ts`], 'react/*': [`${modules}/@types/react/*`],
    '*': [`${modules}/*`, `${modules}/@types/*`] } }
const program = ts.createProgram(files, options)
const diagnostics = ts.getPreEmitDiagnostics(program)
if (diagnostics.length) {
  console.error(ts.formatDiagnosticsWithColorAndContext(diagnostics, {
    getCanonicalFileName: (name) => name, getCurrentDirectory: () => root, getNewLine: () => '\n'
  }))
  process.exit(1)
}
const temporary = await mkdtemp(resolve(tmpdir(), 'agent-runtime-adapter-'))
try {
  const output = resolve(temporary, 'test.cjs')
  await build({ entryPoints: [resolve(root, 'tests/agentRuntimeAdapter.test.ts')], outfile: output,
    bundle: true, platform: 'node', format: 'cjs', nodePaths: [modules], logLevel: 'silent' })
  execFileSync(process.execPath, [output], { cwd: root, stdio: 'inherit', timeout: 30000 })
  // 原生 React/renderer 共用同一份运行时，避免 act 使用被打包的 MessagePort。
  const uiOutput = resolve(temporary, 'floating.cjs')
  await build({ entryPoints: [resolve(root, 'tests/agentFloatingPanel.test.tsx')], outfile: uiOutput,
    bundle: true, platform: 'node', format: 'cjs', nodePaths: [modules], logLevel: 'silent',
    plugins: [{ name: 'floating-ui-boundary', setup(api) {
      api.onResolve({ filter: /^(react|react-test-renderer)$/ }, ({ path }) => ({ path: require.resolve(path), external: true }))
      api.onResolve({ filter: /^(antd|@ant-design\/icons)$|^\.\/AgentChatCore$/ }, ({ path }) => ({ path, namespace: 'boundary' }))
      api.onLoad({ filter: /.*/, namespace: 'boundary' }, ({ path }) => ({ loader: 'jsx', contents:
        path === 'antd' ? `import React from 'react';
          const Box = ({children,...props}) => <div {...props}>{children}</div>;
          export const Button=Box, Space=Box, Tag=Box;
          export const Card=({children,extra})=><div>{extra}{children}</div>;
          export const Grid={useBreakpoint:()=>({md:true})}, theme={useToken:()=>({token:{}})};`
        : path === '@ant-design/icons' ? `export const CloseOutlined=()=>null, FullscreenOutlined=()=>null, RobotOutlined=()=>null;`
        : `import React from 'react';
          export const createAgentConversationAdapter=()=>{throw new Error('Expected provided adapter')};
          export function AgentChatCore(props) { return <div data-core-thread={props.threadId}>
            <button data-core-create={true} onClick={()=>props.onThreadCreated({id:'kept-thread'})}>创建会话</button></div>; }`
      }))
    } }] })
  execFileSync(process.execPath, [uiOutput], { cwd: root, stdio: 'inherit', timeout: 30000 })
} finally { await rm(temporary, { recursive: true, force: true }) }
