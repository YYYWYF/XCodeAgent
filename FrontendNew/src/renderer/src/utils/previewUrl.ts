const DEFAULT_PREVIEW_URL = 'https://example.com';

export function normalizePreviewUrl(value: string) {
  const trimmedValue = value.trim();
  if (!trimmedValue) return '';

  if (/^[a-z][a-z\d+.-]*:\/\//i.test(trimmedValue)) {
    return trimmedValue;
  }

  if (/^(localhost|127\.0\.0\.1|0\.0\.0\.0)(:\d+)?(\/.*)?$/i.test(trimmedValue)) {
    return `http://${trimmedValue}`;
  }

  return `https://${trimmedValue}`;
}

export function getStoredPreviewUrl(applicationId: string) {
  try {
    return window.localStorage.getItem(`xcode-agent-preview-url:${applicationId}`) || '';
  } catch {
    return '';
  }
}

export function storePreviewUrl(applicationId: string, url: string) {
  try {
    window.localStorage.setItem(`xcode-agent-preview-url:${applicationId}`, url);
  } catch {
    // localStorage may be unavailable in restricted browser contexts.
  }
}

export function getInitialPreviewUrl(applicationId: string) {
  return normalizePreviewUrl(getStoredPreviewUrl(applicationId)) || DEFAULT_PREVIEW_URL;
}

export async function openExternalPreviewUrl(url: string) {
  const targetUrl = normalizePreviewUrl(url);
  if (!targetUrl) return;

  if (window.xcodeAgent?.browser?.openExternal) {
    await window.xcodeAgent.browser.openExternal(targetUrl);
    return;
  }

  window.open(targetUrl, '_blank', 'noopener,noreferrer');
}

export async function openPreviewWindow(url: string) {
  const targetUrl = normalizePreviewUrl(url);
  if (!targetUrl) return;

  if (window.xcodeAgent?.browser?.openPreviewWindow) {
    await window.xcodeAgent.browser.openPreviewWindow(targetUrl);
    return;
  }

  const openedWindow = window.open(
    targetUrl,
    `xcode-agent-preview-${Date.now()}`,
    'popup,width=1280,height=860,left=80,top=60,noopener,noreferrer',
  );

  if (!openedWindow) {
    throw new Error('浏览器阻止了新预览窗口');
  }
}
