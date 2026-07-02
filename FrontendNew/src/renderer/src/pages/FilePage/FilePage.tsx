import { FileAddOutlined } from '@ant-design/icons'
import { Button, message, Typography } from 'antd'
import { useState } from 'react'
import { useApi } from '../../context/ApiContext'
import './FilePage.less'

const getDirectoryPath = (filePath: string): string => {
  const normalizedPath = filePath.replace(/\\/g, '/')
  const lastSeparatorIndex = normalizedPath.lastIndexOf('/')

  return lastSeparatorIndex >= 0 ? filePath.slice(0, lastSeparatorIndex) : filePath
}

function FilePage(): React.JSX.Element {
  const api = useApi()
  const [loading, setLoading] = useState(false)
  const [directoryPath, setDirectoryPath] = useState('')

  const handleWriteTestData = async (): Promise<void> => {
    setLoading(true)

    try {
      const result = await api.writeTestData()
      const nextDirectoryPath = getDirectoryPath(result.path)
      console.log(result)
      setDirectoryPath(nextDirectoryPath)
      message.success(`写入成功：${result.path}`)
    } catch (error) {
      console.error(error)
      message.error('写入失败，请查看控制台错误信息')
    } finally {
      setLoading(false)
    }
  }

  return (
    <section className="file-page">
      <div className="file-page__content">
        <Button
          icon={<FileAddOutlined />}
          loading={loading}
          type="primary"
          onClick={() => void handleWriteTestData()}
        >
          node api测试
        </Button>
        {directoryPath ? (
          <Typography.Text className="file-page__result">
            在 {directoryPath} 目录下新增了一个data.json文件
          </Typography.Text>
        ) : null}
      </div>
    </section>
  )
}

export default FilePage
