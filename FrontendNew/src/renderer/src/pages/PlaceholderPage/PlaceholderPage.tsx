import { Result } from 'antd'
import './PlaceholderPage.less'

type PlaceholderPageProps = {
  title: string
}

function PlaceholderPage({ title }: PlaceholderPageProps): React.JSX.Element {
  return (
    <section className="placeholder-page">
      <Result subTitle="该页面路由已创建，后续可接入具体业务内容。" title={title} />
    </section>
  )
}

export default PlaceholderPage
