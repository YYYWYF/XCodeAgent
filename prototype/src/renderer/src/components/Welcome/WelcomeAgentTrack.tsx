import {
  CheckSquareOutlined,
  CodeOutlined,
  FileOutlined,
  FolderOpenOutlined,
  ProjectOutlined,
  ProfileOutlined,
  SafetyCertificateOutlined
} from '@ant-design/icons'
import { cx } from '../../utils'
import './WelcomeAgentTrack.less'

// 首页执行轨道的阶段文案与工作台阶段旅程保持一致（前两个阶段直接使用“需求分析/项目规划”名称），
// 避免两套旅程术语。
const stages = [
  { icon: <ProfileOutlined />, label: '需求分析' },
  { icon: <ProjectOutlined />, label: '项目规划' },
  { icon: <CodeOutlined />, label: '开发阶段' },
  { icon: <CheckSquareOutlined />, label: '测试阶段' },
  { icon: <SafetyCertificateOutlined />, label: '审查阶段' }
]

const files = [
  { icon: <FolderOpenOutlined />, label: 'apps', level: 0, tone: 'folder' },
  { icon: <FolderOpenOutlined />, label: 'web', level: 1, tone: 'blue' },
  { icon: <FolderOpenOutlined />, label: 'api', level: 1, tone: 'blue' },
  { icon: <FolderOpenOutlined />, label: 'packages', level: 0, tone: 'green' },
  { icon: <FileOutlined />, label: 'README.md', level: 0, tone: 'file' },
  { icon: <FileOutlined />, label: 'package.json', level: 0, tone: 'code' },
  { icon: <FileOutlined />, label: 'tsconfig.json', level: 0, tone: 'blue' }
]

export default function WelcomeAgentTrack(): JSX.Element {
  return (
    <section className={cx('agent-preview')} aria-label="Agent 执行轨道预览">
      <div className={cx('agent-track-title')}>
        <span />
        <h2>Agent 执行轨道</h2>
        <span />
      </div>

      <div className={cx('agent-stages')}>
        {stages.map((stage, index) => (
          <div className={cx('agent-stage')} key={stage.label}>
            <div className={cx('agent-stage-icon')}>{stage.icon}</div>
            <span>{stage.label}</span>
            {index < stages.length - 1 ? <i aria-hidden="true">→</i> : null}
          </div>
        ))}
      </div>

      <div className={cx('agent-editor')}>
        <aside className={cx('agent-file-tree')}>
          <div className={cx('agent-tree-title')}>
            <FileOutlined />
            <span>资源管理器</span>
          </div>
          <strong>⌄ data-ops-platform</strong>
          {files.map((file) => (
            <span
              className={cx('agent-file', file.tone)}
              key={file.label}
              style={{ paddingLeft: 12 + file.level * 16 }}
            >
              {file.icon}
              {file.label}
            </span>
          ))}
        </aside>

        <div className={cx('agent-code-area')}>
          <div className={cx('agent-tabs')}>
            <span className={cx('active')}>TS&nbsp;&nbsp; user.service.ts</span>
            <span>TS&nbsp;&nbsp; router.ts</span>
          </div>
          <pre className={cx('agent-code')} aria-label="TypeScript 示例代码">
            <code>
              <em>1</em> <b>import</b> {'{ Injectable }'} <b>from</b> <q>@nestjs/common</q>;
            </code>
            <code>
              <em>2</em> <b>import</b> {'{ PrismaService }'} <b>from</b> <q>../prisma.service</q>;
            </code>
            <code>
              <em>3</em> <b>import</b> {'{ CreateUserDto }'} <b>from</b>{' '}
              <q>./dto/create-user.dto</q>;
            </code>
            <code>
              <em>4</em>
            </code>
            <code>
              <em>5</em> @Injectable()
            </code>
            <code>
              <em>6</em> <b>export class</b> <strong>UserService</strong> {'{'}
            </code>
            <code>
              <em>7</em>&nbsp;&nbsp; constructor(<b>private</b> prisma: PrismaService) {'{}'}
            </code>
            <code>
              <em>8</em>
            </code>
            <code>
              <em>9</em>&nbsp;&nbsp; <b>async</b> create(dto: CreateUserDto) {'{'}
            </code>
            <code>
              <em>10</em>&nbsp;&nbsp;&nbsp;&nbsp; <b>return</b> this.prisma.user.create({'{'} data:
              dto {'}'});
            </code>
            <code>
              <em>11</em>&nbsp;&nbsp; {'}'}
            </code>
            <code>
              <em>12</em> {'}'}
            </code>
          </pre>
          <div className={cx('agent-terminal')}>
            <div>
              <span>终端</span>
              <small>1: node&nbsp;&nbsp; ＋</small>
            </div>
            <code>$ pnpm dev</code>
            <code>
              › concurrently &quot;pnpm --filter api start:dev&quot; &quot;pnpm --filter web
              dev&quot;
            </code>
            <code className={cx('success')}>
              <CheckSquareOutlined /> [api]&nbsp; Server running at http://localhost:3001
            </code>
            <code className={cx('success')}>
              <CheckSquareOutlined /> [web]&nbsp; Vite ready in 421 ms
            </code>
          </div>
        </div>
      </div>
    </section>
  )
}
