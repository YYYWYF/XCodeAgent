import type { ApplicationDraft, ApplicationTerminal, ApplicationTrackMethod } from '../../typings'

export const initialApplicationDraft: ApplicationDraft = {
  appName: '',
  appIcon: '',
  senario: '',
  projectParentPath: '',
  projectDirectoryName: '',
  terminal: 'PC',
  layout: { type: '', useHeader: true, useFooter: true },
  theme: { primaryColor: '' },
  datasource: {
    type: '',
    db: {
      plantMode: { domain: '', port: '', userName: '', pwd: '', schema: '' }
    }
  },
  envText: '',
  auth: { enable: true, authnSource: '', yht: { clientId: '' } },
  track: { enable: true, uploadId: '', apiHost: '', method: 'post' },
  apiTrack: { enable: true, businessId: '', traceBaggage: '', apiTrackHost: '' }
}

export const terminalLabels: Record<ApplicationTerminal, string> = {
  PC: 'PC 端',
  Mobile: '移动端'
}

export const trackMethodLabels: Record<ApplicationTrackMethod, string> = {
  post: '提交',
  get: '获取'
}
