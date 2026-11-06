import tseslint from 'typescript-eslint'

export default tseslint.config(
  { ignores: ['src/agent-ui/**', 'src/design/agent-pages/**', 'dist/**', 'node_modules/**'] },
  ...tseslint.configs.recommended,
)
