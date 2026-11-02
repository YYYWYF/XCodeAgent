import { useEffect, useState } from 'react'
import { loadAppVersion } from '../service/appVersion'
import { cx } from '../utils'
import './AppVersionBadge.less'

/** 在品牌名称旁展示只读客户端版本，忽略组件卸载后的异步回执。 */
export default function AppVersionBadge(): JSX.Element | null {
  const [version, setVersion] = useState('')

  useEffect(() => {
    let active = true
    void loadAppVersion().then((value) => {
      if (active) setVersion(value)
    })
    return () => {
      active = false
    }
  }, [])

  if (!version) return null
  return (
    <span className={cx('app-version-badge')} aria-label={`客户端版本 ${version}`}>
      {version}
    </span>
  )
}
