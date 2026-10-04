export class ApiError extends Error {
  constructor(public readonly status: number, message: string) {
    super(message);
    this.name = 'ApiError';
  }
}

export async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  let response: Response;
  try {
    response = await fetch(path, init);
  } catch (error) {
    if (error instanceof DOMException && error.name === 'AbortError') throw error;
    throw new Error('无法连接 LearnMargin 服务。请确认本地服务仍在运行，然后重试。');
  }
  if (!response.ok) {
    let message = `请求失败（${response.status}），请稍后重试。`;
    try {
      const body: { detail?: unknown } = await response.json();
      if (typeof body.detail === 'string') message = body.detail;
      else if (Array.isArray(body.detail)) message = '请求内容不符合要求，请检查学习范围和模型设置。';
    } catch { /* The server may return a non-JSON error page. */ }
    throw new ApiError(response.status, message);
  }
  if (response.status === 204) return undefined as T;
  return response.json() as Promise<T>;
}

export function post<T>(path: string, body?: unknown): Promise<T> {
  return request<T>(path, { method: 'POST', ...(body === undefined ? {} : {
    headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
  }) });
}

/** Only locally served artifacts should appear in frames, images or download links. */
export function localArtifact(url: string | undefined): string | undefined {
  if (!url || !url.startsWith('/api/') || url.startsWith('//') || /[\\\r\n]/.test(url)) return undefined;
  return url;
}
