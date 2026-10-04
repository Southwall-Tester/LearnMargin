import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import ConnectionTest from './ConnectionTest';
import type { APIConfig } from './types';

const config: APIConfig = { base_url: 'https://model.example/v1', model: 'test-model', api_key: 'private-key', protocol: 'responses', vision: true, json_mode: true, timeout_seconds: 180 };
const response = (data: unknown, ok = true) => ({ ok, status: ok ? 200 : 400, json: async () => data } as Response);
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

describe('connection test', () => {
  it('only sends model settings after a click and shows success', async () => {
    const fetch = vi.fn().mockResolvedValue(response({ ok: true, model: 'test-model', message: '连接成功', latency_ms: 20 }));
    vi.stubGlobal('fetch', fetch);
    render(<ConnectionTest config={config} />);
    expect(fetch).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole('button', { name: '测试连接' }));
    expect(await screen.findByRole('status')).toHaveTextContent('连接成功 · test-model');
    expect(fetch).toHaveBeenCalledTimes(1);
    expect(fetch.mock.calls[0][0]).toBe('/api/connection-test');
    expect(JSON.parse(fetch.mock.calls[0][1].body)).toEqual(config);
    expect(screen.queryByText('private-key')).not.toBeInTheDocument();
  });

  it('shows authentication failure and allows another attempt', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(response({ detail: 'API 鉴权失败，请核对密钥。' }, false)));
    render(<ConnectionTest config={config} />);
    fireEvent.click(screen.getByRole('button', { name: '测试连接' }));
    expect(await screen.findByRole('status')).toHaveTextContent('API 鉴权失败');
    expect(screen.getByRole('button', { name: '测试连接' })).toBeEnabled();
  });

  it('ignores stale results after settings change and clears previous success', async () => {
    let finish!: (value: Response) => void;
    const fetch = vi.fn().mockImplementationOnce(() => new Promise<Response>(resolve => { finish = resolve; }))
      .mockResolvedValue(response({ ok: true, model: 'new-model', message: '连接成功', latency_ms: 20 }));
    vi.stubGlobal('fetch', fetch);
    const { rerender } = render(<ConnectionTest config={config} />);
    fireEvent.click(screen.getByRole('button', { name: '测试连接' }));
    expect(screen.getByRole('button', { name: '测试中…' })).toBeDisabled();
    const changed = { ...config, model: 'new-model' };
    rerender(<ConnectionTest config={changed} />);
    expect(fetch.mock.calls[0][1].signal.aborted).toBe(true);
    finish(response({ ok: true, model: 'test-model', message: '连接成功', latency_ms: 20 }));
    await waitFor(() => expect(screen.queryByRole('status')).not.toBeInTheDocument());
    fireEvent.click(screen.getByRole('button', { name: '测试连接' }));
    expect(await screen.findByRole('status')).toHaveTextContent('new-model');
    rerender(<ConnectionTest config={{ ...changed, api_key: 'replacement-key' }} />);
    expect(screen.queryByRole('status')).not.toBeInTheDocument();
  });

  it('does not request a test for an invalid configuration or accept an empty success response', async () => {
    const fetch = vi.fn().mockResolvedValue(response({}));
    vi.stubGlobal('fetch', fetch);
    const { rerender } = render(<ConnectionTest config={{ ...config, model: '' }} />);
    fireEvent.click(screen.getByRole('button', { name: '测试连接' }));
    expect(screen.getByRole('status')).toHaveTextContent('请填写模型名称');
    expect(fetch).not.toHaveBeenCalled();
    rerender(<ConnectionTest config={config} />);
    fireEvent.click(screen.getByRole('button', { name: '测试连接' }));
    expect(await screen.findByRole('status')).toHaveTextContent('服务未返回有效');
  });
});
