import type { APIConfig, DocumentSummary, ReasoningProfile, Scope } from './types';

export function reasoningProfile(config: APIConfig, profiles: ReasoningProfile[] = []): ReasoningProfile | undefined {
  if (apiUrlError(config.base_url)) return undefined;
  const url = new URL(config.base_url.trim());
  let path = url.pathname.replace(/\/+$/, '');
  for (const suffix of ['/chat/completions', '/responses']) {
    if (path.endsWith(suffix)) path = path.slice(0, -suffix.length);
  }
  const endpoint = `${url.origin}${path}`.replace(/\/+$/, '');
  return profiles.find(profile => profile.endpoints.includes(endpoint)
    && profile.models.includes(config.model.trim()) && profile.protocols.includes(config.protocol));
}

export function supportsReasoning(config: APIConfig, profiles: ReasoningProfile[] = []): boolean {
  return config.reasoning_effort == null || Boolean(reasoningProfile(config, profiles)?.options.some(option => option.value === config.reasoning_effort));
}

export function rangeError(value: string, total: number): string | null {
  if (!value.trim()) return '请填写学习范围。';
  const segments = value.trim().split(/[,，]/);
  for (const segment of segments) {
    const match = segment.trim().match(/^(\d+)(?:\s*-\s*(\d+))?$/);
    if (!match) return '请使用 1-3,5 这样的格式。';
    const first = Number(match[1]);
    const last = Number(match[2] ?? match[1]);
    if (first < 1 || last > total || first > last) return `范围应在 1–${total} 内，并且起点不能大于终点。`;
  }
  return null;
}

export function scopeError(documents: DocumentSummary[], scope: Scope): string | null {
  if (!documents.length) return '请先导入并勾选至少一份学习材料。';
  if (scope.mode === 'topics' && !scope.topics.trim()) return '请写下你希望学习的知识点。';
  if (scope.mode === 'pages') {
    const primary = documents.filter(document => Object.hasOwn(scope.ranges, document.id));
    if (!primary.length) return '请至少选择一份主材料并填写学习范围。';
    for (const document of primary) {
      const error = rangeError(scope.ranges[document.id] ?? '', document.total_units);
      if (error) return `${document.name}：${error}`;
    }
  }
  return null;
}

export function configError(config: APIConfig): string | null {
  if (!config.model.trim()) return '请填写模型名称。';
  return apiUrlError(config.base_url);
}

function apiUrlError(value: string): string | null {
  const candidate = value.replace(/^ +| +$/g, '');
  // URL() silently removes tabs/newlines and normalizes backslashes. Validate
  // the original spelling first, matching the server's endpoint boundary.
  if (candidate.length > 4096 || /[\s\x00-\x1f\x7f]/u.test(candidate)) {
    return 'API 地址过长或含有空白、控制字符，请检查地址。';
  }
  const authority = /^https?:\/\/([^/?#]*)/i.exec(candidate)?.[1];
  if (!authority) return 'API 地址需要完整的 http:// 或 https:// 地址。';
  if (candidate.includes('\\') || authority.includes('%')) return 'API 地址的主机名或路径格式不正确。';
  try {
    const url = new URL(candidate);
    if (url.port === '0') return 'API 地址的端口不正确。';
    if (authority.includes('@') || url.username || url.password || url.search || url.hash) {
      return 'API 地址不能包含账号、密钥、查询参数或片段；请把密钥填入独立字段。';
    }
    const hostname = authority.startsWith('[') ? authority.slice(0, authority.indexOf(']') + 1) : authority.split(':')[0];
    if (url.protocol === 'http:' && !['localhost', '127.0.0.1', '[::1]'].includes(hostname.toLowerCase())) {
      return '远程 API 请使用 HTTPS；HTTP 仅用于本机模型服务。';
    }
  } catch { return '请填写有效的 HTTP 或 HTTPS API 地址，不要在地址中放入密钥。'; }
  return null;
}

const preferenceKey = 'learnmargin.model-preferences.v1';

export function saveModelPreferences(config: APIConfig): void {
  const { base_url, model, protocol, vision, json_mode, timeout_seconds, reasoning_effort } = config;
  // Guard the storage boundary too, including callers other than the form.
  const safeUrl = apiUrlError(base_url) ? {} : { base_url };
  try { localStorage.setItem(preferenceKey, JSON.stringify({ ...safeUrl, model, protocol, vision, json_mode, timeout_seconds, reasoning_effort })); }
  catch { /* Private browsing or storage limits should not block generation. */ }
}

export function loadModelPreferences(): Partial<Omit<APIConfig, 'api_key'>> {
  try {
    const raw: unknown = JSON.parse(localStorage.getItem(preferenceKey) ?? 'null');
    if (!raw || typeof raw !== 'object') return {};
    const source = raw as Record<string, unknown>;
    const result: Partial<Omit<APIConfig, 'api_key'>> = {};
    if (typeof source.base_url === 'string' && !apiUrlError(source.base_url)) result.base_url = source.base_url;
    if (typeof source.model === 'string') result.model = source.model;
    if (source.protocol === 'chat_completions' || source.protocol === 'responses') result.protocol = source.protocol;
    if (typeof source.vision === 'boolean') result.vision = source.vision;
    if (typeof source.json_mode === 'boolean') result.json_mode = source.json_mode;
    if (source.reasoning_effort === null || ['none', 'minimal', 'low', 'medium', 'high', 'xhigh', 'max', 'enabled', 'disabled'].includes(source.reasoning_effort as string)) result.reasoning_effort = source.reasoning_effort as APIConfig['reasoning_effort'];
    if (typeof source.timeout_seconds === 'number' && source.timeout_seconds >= 10 && source.timeout_seconds <= 600) result.timeout_seconds = source.timeout_seconds;
    // Remove legacy URL credentials and secret/unknown fields instead of merely
    // ignoring them while leaving their values in browser storage.
    try { localStorage.setItem(preferenceKey, JSON.stringify(result)); } catch { /* Storage may be read-only. */ }
    return result;
  } catch { return {}; }
}

export const statusLabels = {
  queued: '等待开始', running: '生成中', completed: '已完成', failed: '生成失败', cancelled: '已取消',
} as const;

export function displayProgress(progress: number): number {
  return Math.max(0, Math.min(100, Math.round(Number.isFinite(progress) ? progress : 0)));
}
