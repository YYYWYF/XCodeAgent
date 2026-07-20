import type { ApplicationSchemaConfig } from '../typings'

export interface TemplateInitRequest {
  appName: string
  appIcon: string
  senario: string
  terminal: string
  layout: ApplicationSchemaConfig['layout']
  theme: ApplicationSchemaConfig['theme']
  datasource: ApplicationSchemaConfig['datasource']
  env: string[]
  menus: ApplicationSchemaConfig['menus']
  auth: ApplicationSchemaConfig['auth']
  track: ApplicationSchemaConfig['track']
  apiTrack: ApplicationSchemaConfig['apiTrack']
}

export interface TemplateInitResponse {
  code: number
  message: string
  data: {
    projectZipUrl?: string
    templateVersion: string
    generatedAt: number
    fileCount?: number
  }
}

/** Mock 一个模板拉取接口，模拟 800ms 网络延迟后返回模板成功。 */
export async function fetchTemplateCode(schema: ApplicationSchemaConfig): Promise<TemplateInitResponse> {
  const body: TemplateInitRequest = {
    appName: schema.appName,
    appIcon: schema.appIcon,
    senario: schema.senario,
    terminal: schema.terminal,
    layout: schema.layout,
    theme: schema.theme,
    datasource: {
      type: 'DataBase',
      db: {
        plantMode: {
          domain: 'string',
          port: 3000,
          userName: 'string',
          pwd: 'string',
          schema: 'string'
        }
      }
    },
    env: [],
    menus: {
      homeMenuKey: 'Default',
      items: [
        {
          key: 'firstLevel',
          path: 'firstLevel',
          label: '一级目录',
          type: 'menu',
          children: [
            {
              key: 'secondLevel',
              path: 'secondLevel',
              label: '默认页面',
              type: 'page',
              pageKey: 'Default'
            }
          ]
        }
      ]
    },
    auth: {
      enable: true,
      authnSource: 'YHT',
      yht: { clientId: '123456789' }
    },
    track: {
      enable: true,
      uploadId: 'TESTUPLOADID@ST',
      apiHost: 'https://localhost:8080/track',
      method: 'post'
    },
    apiTrack: {
      enable: true,
      businessId: 'testBizId',
      traceBaggage: 'sysId=sys001',
      apiTrackHost: 'https://localhost:8080/apitrack'
    }
  }

  // 模拟网络延迟
  await new Promise((resolve) => setTimeout(resolve, 800))

  console.log('[Mock] POST /api/high-code/generate/init', JSON.stringify(body, null, 2))

  return {
    code: 0,
    message: 'success',
    data: {
      templateVersion: '1.0.0',
      generatedAt: Date.now(),
      fileCount: 42
    }
  }
}
