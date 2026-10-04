import { useEffect, useRef, useState } from 'react';
import { Check, LoaderCircle, PlugZap, TriangleAlert } from 'lucide-react';
import { request } from './api';
import { configError } from './domain';
import type { APIConfig } from './types';

type Result = { ok: boolean; message: string; model: string; latency_ms: number };
type Status = { kind: 'idle' | 'testing' | 'success' | 'error'; message: string };

export default function ConnectionTest({ config }: { config: APIConfig }) {
  const [status, setStatus] = useState<Status>({ kind: 'idle', message: '' });
  const pending = useRef<AbortController | null>(null);

  useEffect(() => {
    pending.current?.abort();
    pending.current = null;
    setStatus({ kind: 'idle', message: '' });
    return () => { pending.current?.abort(); pending.current = null; };
  }, [config]);

  async function testConnection() {
    if (pending.current) return;
    const invalid = configError(config);
    if (invalid) { setStatus({ kind: 'error', message: invalid }); return; }
    const controller = new AbortController();
    pending.current = controller;
    setStatus({ kind: 'testing', message: '' });
    const timer = setTimeout(() => controller.abort(), 35000);
    try {
      const result = await request<Result>('/api/connection-test', {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, signal: controller.signal,
        body: JSON.stringify({ ...config, base_url: config.base_url.trim(), model: config.model.trim(), api_key: config.api_key.trim() }),
      });
      if (pending.current !== controller) return;
      if (result.ok !== true || typeof result.model !== 'string' || !result.model.trim()) {
        throw new Error('服务未返回有效的连接测试结果。');
      }
      setStatus({ kind: 'success', message: `连接成功 · ${result.model}` });
    } catch (error) {
      if (pending.current !== controller) return;
      let message = controller.signal.aborted ? '连接测试超时，请检查服务地址和网络后重试。'
        : error instanceof Error ? error.message : '连接测试失败，请重试。';
      if (message === 'Not Found') message = '请重新启动 LearnMargin，以载入连接测试功能。';
      setStatus({ kind: 'error', message });
    } finally {
      clearTimeout(timer);
      if (pending.current === controller) pending.current = null;
    }
  }

  return <div className="connection-test">
    <div className="connection-test-controls">
      <button className="button secondary small" disabled={status.kind === 'testing'} onClick={() => void testConnection()} aria-describedby="connection-test-note">
        {status.kind === 'testing' ? <LoaderCircle className="spin" size={15} /> : <PlugZap size={15} />}
        {status.kind === 'testing' ? '测试中…' : '测试连接'}
      </button>
      <span id="connection-test-note" className="helper">发送少量测试文本，可能产生 API 费用。</span>
    </div>
    {status.message && <p className={`connection-test-result ${status.kind}`} role="status">
      {status.kind === 'success' ? <Check size={15} /> : <TriangleAlert size={15} />}{status.message}
    </p>}
  </div>;
}
