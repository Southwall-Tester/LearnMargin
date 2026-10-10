import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import App from './App';
import type { GenerateRequest } from './types';

const settings = {
  reasoning_profiles: [{ endpoints: ['https://api.deepseek.com', 'https://api.deepseek.com/v1'], models: ['deepseek-flash'], protocols: ['chat_completions', 'responses'], options: ['none', 'low', 'high', 'max'].map(value => ({ value, label: value })), note: '' }],
  api: { base_url: 'https://api.deepseek.com', model: 'deepseek-flash', protocol: 'chat_completions', vision: true, json_mode: true, has_api_key: false },
  limits: { max_upload_mb: 30, max_documents: 8 }, formats: ['pdf', 'pptx', 'docx', 'txt'],
  capabilities: { libreoffice: false, browser: true }, demo_available: true,
};
const document = {
  id: 'doc1', name: '教材.pdf', kind: 'pdf', unit_label: '文件页', total_units: 5, warnings: [],
  units: Array.from({ length: 5 }, (_, index) => ({ index: index + 1, label: `文件第 ${index + 1} 页`, preview: '内容', has_images: true })),
};

let requests: { path: string; init?: RequestInit }[];
beforeEach(() => {
  requests = [];
  localStorage.clear();
  Element.prototype.scrollIntoView = vi.fn();
  vi.stubGlobal('fetch', vi.fn(async (path: string, init?: RequestInit) => {
    requests.push({ path, init });
    let data: unknown;
    if (path === '/api/settings') data = settings;
    else if (path === '/api/jobs' && init?.method !== 'POST') data = [];
    else if (path === '/api/documents' && init?.method === 'POST') {
      const file = (init.body as FormData).get('file') as File;
      data = file.name === '参考.md' ? { ...document, id: 'doc2', name: file.name, kind: 'md', unit_label: '内容段' } : document;
    }
    else if (path.includes('/units/')) data = { index: 1, label: '文件第 1 页', text: '条件概率的定义：已知 B 发生，求 A 发生的概率。', images: ['/api/documents/doc1/images/page-1.png'] };
    else if (path === '/api/demo') data = { id: 'demo1', status: 'queued', stage: '准备内置示例', progress: 0, created_at: new Date().toISOString(), demo: true };
    else if (path === '/api/jobs' && init?.method === 'POST') data = { id: 'job1', status: 'queued', stage: '准备', progress: 0, created_at: new Date().toISOString(), demo: false };
    else throw new Error(`Unhandled request: ${path}`);
    return { ok: true, status: 200, json: async () => data } as Response;
  }));
});
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

describe('workspace generation', () => {
  it('shows only native options and clears effort when the connection changes', async () => {
    render(<App />);
    fireEvent.click(await screen.findByRole('button', { name: /deepseek-flash/ }));
    const selector = screen.getByLabelText('思考设置');
    expect(Array.from((selector as HTMLSelectElement).options).map(option => option.value)).toEqual(['', 'none', 'low', 'high', 'max']);
    fireEvent.change(selector, { target: { value: 'high' } });
    fireEvent.change(screen.getByLabelText('接口协议'), { target: { value: 'responses' } });
    expect(selector).toHaveValue('');
    expect(screen.getByText(/连接配置已更改/)).toBeInTheDocument();
    fireEvent.change(selector, { target: { value: 'low' } });
    fireEvent.change(screen.getByLabelText('模型名称'), { target: { value: 'unknown-model' } });
    expect(selector).toHaveValue('');
    expect(selector).toBeDisabled();
    fireEvent.click(screen.getByRole('button', { name: 'DeepSeek' }));
    fireEvent.change(selector, { target: { value: 'max' } });
    fireEvent.click(screen.getByRole('button', { name: 'OpenAI' }));
    expect(selector).toHaveValue('');
    expect(selector).toBeDisabled();
  });

  it('migrates an incompatible saved effort with a visible notice', async () => {
    localStorage.setItem('learnmargin.model-preferences.v1', JSON.stringify({ ...settings.api, reasoning_effort: 'medium' }));
    render(<App />);
    fireEvent.click(await screen.findByRole('button', { name: /deepseek-flash/ }));
    expect(screen.getByLabelText('思考设置')).toHaveValue('');
    expect(screen.getByText(/已保存的思考选项不适用于当前配置/)).toBeInTheDocument();
    expect(JSON.parse(localStorage.getItem('learnmargin.model-preferences.v1')!).reasoning_effort).toBeNull();
  });

  it.each(['a4', 'wide'] as const)('uploads material, validates ranges, and submits %s without a chapter count', async layout => {
    render(<App />);
    await screen.findByRole('button', { name: /deepseek-flash/ });
    fireEvent.change(screen.getByLabelText('选择学习材料文件'), { target: { files: [new File(['test'], '教材.pdf', { type: 'application/pdf' })] } });
    await screen.findByLabelText('将 教材.pdf 纳入学习范围');
    expect(await screen.findByText(/条件概率的定义/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '指定页码 / 章节' }));
    const range = screen.getByLabelText('教材.pdf 的范围');
    fireEvent.change(range, { target: { value: '6' } });
    fireEvent.click(screen.getByRole('button', { name: '生成学习讲义' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('范围应在 1–5 内');
    expect(requests.filter(item => item.path === '/api/jobs' && item.init?.method === 'POST')).toHaveLength(0);
    fireEvent.change(range, { target: { value: '1-2，4' } });
    fireEvent.click(screen.getByRole('button', { name: /deepseek-flash/ }));
    fireEvent.change(screen.getByLabelText(/API Key/), { target: { value: 'private-key' } });
    fireEvent.change(screen.getByLabelText('思考设置'), { target: { value: 'low' } });
    fireEvent.click(screen.getByRole('button', { name: '看答案会，换题不会' }));
    expect(screen.queryByLabelText('讲解章节数')).not.toBeInTheDocument();
    expect(screen.getByLabelText('PDF 版式')).toHaveValue('a4');
    fireEvent.change(screen.getByLabelText('PDF 版式'), { target: { value: layout } });
    fireEvent.click(screen.getByRole('button', { name: '生成学习讲义' }));
    await waitFor(() => expect(requests.some(item => item.path === '/api/jobs' && item.init?.method === 'POST')).toBe(true));
    const payload = JSON.parse(requests.find(item => item.path === '/api/jobs' && item.init?.method === 'POST')!.init!.body as string) as GenerateRequest;
    expect(payload.layout).toBe(layout);
    expect(payload).not.toHaveProperty('section_count');
    expect(payload.language).toBe('简体中文');
    expect(payload.reading_mode).toBe('auto');
    expect(payload.scope.ranges.doc1).toBe('1-2,4');
    expect(payload.scope.include_prerequisites).toBe(true);
    expect(payload.api.model).toBe('deepseek-flash');
    expect(payload.api.api_key).toBe('private-key');
    expect(payload.api.reasoning_effort).toBe('low');
    expect(payload.api).not.toHaveProperty('has_api_key');
    expect(payload.learner_notes).toContain('看答案会，换题不会');
    expect(JSON.stringify(localStorage)).not.toContain('private-key');
  });

  it.each([
    { choice: 'English', custom: '', expected: 'English' },
    { choice: 'custom', custom: '  Deutsch  ', expected: 'Deutsch' },
  ])('submits the chosen output language: $expected', async ({ choice, custom, expected }) => {
    render(<App />);
    await screen.findByRole('button', { name: /deepseek-flash/ });
    fireEvent.change(screen.getByLabelText('选择学习材料文件'), { target: { files: [new File(['test'], '教材.pdf')] } });
    await screen.findByLabelText('将 教材.pdf 纳入学习范围');
    fireEvent.click(screen.getByRole('button', { name: /deepseek-flash/ }));
    fireEvent.change(screen.getByLabelText(/API Key/), { target: { value: 'private-key' } });
    fireEvent.change(screen.getByLabelText('输出语言'), { target: { value: choice } });
    if (choice === 'custom') fireEvent.change(screen.getByLabelText('自定义语言'), { target: { value: custom } });
    fireEvent.click(screen.getByRole('button', { name: '生成学习讲义' }));
    await waitFor(() => expect(requests.some(item => item.path === '/api/jobs' && item.init?.method === 'POST')).toBe(true));
    const payload = JSON.parse(requests.find(item => item.path === '/api/jobs' && item.init?.method === 'POST')!.init!.body as string) as GenerateRequest;
    expect(payload.language).toBe(expected);
  });

  it.each(['', '   '])('rejects an empty custom language before creating a job: %j', async custom => {
    render(<App />);
    await screen.findByRole('button', { name: /deepseek-flash/ });
    fireEvent.change(screen.getByLabelText('选择学习材料文件'), { target: { files: [new File(['test'], '教材.pdf')] } });
    await screen.findByLabelText('将 教材.pdf 纳入学习范围');
    fireEvent.click(screen.getByRole('button', { name: /deepseek-flash/ }));
    fireEvent.change(screen.getByLabelText(/API Key/), { target: { value: 'private-key' } });
    fireEvent.change(screen.getByLabelText('输出语言'), { target: { value: 'custom' } });
    fireEvent.change(screen.getByLabelText('自定义语言'), { target: { value: custom } });
    fireEvent.click(screen.getByRole('button', { name: '生成学习讲义' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('请填写输出语言。');
    expect(requests.filter(item => item.path === '/api/jobs' && item.init?.method === 'POST')).toHaveLength(0);
  });

  it('runs a clearly identified demo without requiring files or credentials', async () => {
    render(<App />);
    await screen.findByRole('button', { name: /deepseek-flash/ });
    fireEvent.click(screen.getByRole('button', { name: /体验内置示例/ }));
    expect(await screen.findByText('内置示例 · 未调用模型')).toBeInTheDocument();
    expect(requests.filter(item => item.path === '/api/demo')).toHaveLength(1);
    expect(requests.filter(item => item.path === '/api/jobs' && item.init?.method === 'POST')).toHaveLength(0);
  });

  it('uses one primary range and all selected references, requiring at least one primary', async () => {
    render(<App />);
    await screen.findByRole('button', { name: /deepseek-flash/ });
    fireEvent.change(screen.getByLabelText('选择学习材料文件'), { target: { files: [new File(['main'], '教材.pdf'), new File(['reference'], '参考.md')] } });
    await screen.findByLabelText('将 参考.md 纳入学习范围');
    fireEvent.click(screen.getByRole('button', { name: '指定页码 / 章节' }));
    expect(screen.getByLabelText('教材.pdf 的材料角色')).toHaveValue('primary');
    expect(screen.getByLabelText('参考.md 的材料角色')).toHaveValue('reference');
    expect(screen.queryByLabelText('参考.md 的范围')).not.toBeInTheDocument();
    fireEvent.change(screen.getByLabelText('教材.pdf 的材料角色'), { target: { value: 'reference' } });
    fireEvent.click(screen.getByRole('button', { name: '生成学习讲义' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('至少选择一份主材料');
    fireEvent.change(screen.getByLabelText('教材.pdf 的材料角色'), { target: { value: 'primary' } });
    fireEvent.change(screen.getByLabelText('教材.pdf 的范围'), { target: { value: '2-4' } });
    const prerequisites = screen.getByRole('checkbox', { name: '回查主材料其他页的必要定义与前提' });
    expect(prerequisites).toBeChecked();
    fireEvent.click(prerequisites);
    fireEvent.click(screen.getByRole('button', { name: /deepseek-flash/ }));
    fireEvent.change(screen.getByLabelText(/API Key/), { target: { value: 'private-key' } });
    fireEvent.click(screen.getByRole('button', { name: '生成学习讲义' }));
    await waitFor(() => expect(requests.some(item => item.path === '/api/jobs' && item.init?.method === 'POST')).toBe(true));
    const payload = JSON.parse(requests.find(item => item.path === '/api/jobs' && item.init?.method === 'POST')!.init!.body as string) as GenerateRequest;
    expect(payload.document_ids).toEqual(['doc1', 'doc2']);
    expect(payload.scope.ranges).toEqual({ doc1: '2-4' });
    expect(payload.scope.include_prerequisites).toBe(false);
  });

  it('requires image understanding for handwritten materials and submits the selected reading mode', async () => {
    render(<App />);
    await screen.findByRole('button', { name: /deepseek-flash/ });
    fireEvent.change(screen.getByLabelText('选择学习材料文件'), { target: { files: [new File(['test'], '教材.pdf')] } });
    await screen.findByLabelText('将 教材.pdf 纳入学习范围');
    fireEvent.change(screen.getByLabelText('材料识读'), { target: { value: 'handwritten' } });
    fireEvent.click(screen.getByRole('button', { name: /deepseek-flash/ }));
    fireEvent.click(screen.getByLabelText('模型支持图片理解'));
    fireEvent.click(screen.getByRole('button', { name: '生成学习讲义' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('手写讲义识读需要支持图片理解的模型');
    expect(requests.filter(item => item.path === '/api/jobs' && item.init?.method === 'POST')).toHaveLength(0);
    fireEvent.click(screen.getByLabelText('模型支持图片理解'));
    fireEvent.change(screen.getByLabelText(/API Key/), { target: { value: 'private-key' } });
    fireEvent.click(screen.getByRole('button', { name: '生成学习讲义' }));
    await waitFor(() => expect(requests.some(item => item.path === '/api/jobs' && item.init?.method === 'POST')).toBe(true));
    const payload = JSON.parse(requests.find(item => item.path === '/api/jobs' && item.init?.method === 'POST')!.init!.body as string) as GenerateRequest;
    expect(payload.reading_mode).toBe('handwritten');
  });
});
