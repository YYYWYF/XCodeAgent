import { PlusOutlined } from '@ant-design/icons'
import { Button } from 'antd'
import './SkillPage.less'

function SkillPage(): React.JSX.Element {
  return (
    <section className="skill-page">
      <header className="skill-page__header">
        <h1 className="skill-page__title">Skill管理</h1>
        <div className="skill-page__actions">
          <Button>导入Skill</Button>
          <Button>订阅Skill</Button>
          <Button icon={<PlusOutlined />} type="primary">
            新增Skill
          </Button>
        </div>
      </header>
      <div className="skill-page__empty">暂无Skill数据，点击“新增Skill”创建</div>
    </section>
  )
}

export default SkillPage
