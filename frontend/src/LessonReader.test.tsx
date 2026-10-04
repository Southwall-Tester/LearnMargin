import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import LessonReader from './LessonReader';
import type { Job } from './types';

const documentId = 'a123456789abcdef0123456789abcdef';
const job: Job = {
  id: 'job1', title: '测试讲义', status: 'completed', stage: '完成', progress: 100,
  created_at: '2026-10-04T00:00:00Z', artifacts: {
    html: '/api/jobs/job1/artifacts/lesson.html', pdf: '/api/jobs/job1/artifacts/lesson.pdf',
  },
};

beforeEach(() => {
  HTMLDialogElement.prototype.showModal = function () { this.setAttribute('open', ''); };
  HTMLDialogElement.prototype.close = function () { this.removeAttribute('open'); };
  vi.stubGlobal('requestAnimationFrame', (callback: FrameRequestCallback) => { callback(0); return 1; });
  vi.stubGlobal('fetch', vi.fn(async (path: string) => ({
    ok: true, status: 200, json: async () => path.includes('/sources/')
      ? { index: 3, label: '文件第 3 页', text: '这是原材料中的关键解释。', images: [`/api/documents/${documentId}/images/3.png`] }
      : { id: documentId, name: '概率教材.pdf', kind: 'pdf', unit_label: '文件页', total_units: 5, warnings: [], units: [] },
  }) as Response));
});
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

async function activateFrame(ref = `${documentId}:3`) {
  const frame = screen.getByTitle('测试讲义 网页阅读') as HTMLIFrameElement;
  const content = frame.contentDocument!;
  content.open();
  content.write('<!doctype html><html><head></head><body></body></html>');
  content.close();
  content.body.innerHTML = `<main><p>当前位置</p><button class="web-source-button" data-source-ref="${ref}">查看原材料</button></main>`;
  Object.defineProperty(frame.contentWindow, 'learnmarginReport', { configurable: true, value: {} });
  Object.defineProperty(frame.contentWindow, 'scrollY', { configurable: true, value: 431 });
  const scrollTo = vi.fn();
  Object.defineProperty(frame.contentWindow, 'scrollTo', { configurable: true, value: scrollTo });
  fireEvent.load(frame);
  await waitFor(() => expect(content.documentElement).toHaveClass('embedded-reader'));
  return { frame, content, button: content.querySelector('button')!, scrollTo };
}

describe('web lesson source navigation', () => {
  it('opens original text and images, then returns to the same DOM, scroll position and button', async () => {
    render(<LessonReader job={job} />);
    const { frame, content, button, scrollTo } = await activateFrame();
    fireEvent.click(button);
    expect(await screen.findByRole('heading', { name: '概率教材.pdf' })).toBeInTheDocument();
    expect(screen.getByText('这是原材料中的关键解释。')).toBeInTheDocument();
    expect(screen.getByAltText('文件第 3 页，原材料图片 1')).toHaveAttribute('src', `/api/documents/${documentId}/images/3.png`);
    fireEvent.click(screen.getAllByRole('button', { name: '返回讲义' }).at(-1)!);
    expect(screen.getByTitle('测试讲义 网页阅读')).toBe(frame);
    expect(frame.contentDocument).toBe(content);
    expect(content.activeElement).toBe(button);
    expect(scrollTo).toHaveBeenCalledWith(0, 431);
  });

  it('keeps the lesson usable when the original source was deleted', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => ({ ok: false, status: 404, json: async () => ({ detail: '材料不存在或已被移除。' }) }) as Response));
    render(<LessonReader job={job} />);
    const { frame, button, scrollTo } = await activateFrame();
    fireEvent.click(button);
    expect(await screen.findByText(/原材料暂时无法打开，可能已被移除/)).toBeInTheDocument();
    fireEvent(screen.getByRole('dialog'), new Event('cancel', { bubbles: false, cancelable: true }));
    expect(screen.getByTitle('测试讲义 网页阅读')).toBe(frame);
    expect(scrollTo).toHaveBeenCalledWith(0, 431);
  });

  it('hides synthetic demo sources and preserves both frames when switching views', async () => {
    render(<LessonReader job={{ ...job, demo: true }} />);
    const { frame, button } = await activateFrame('demo:1');
    expect(button).toHaveStyle({ display: 'none' });
    expect(fetch).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole('button', { name: 'PDF 预览' }));
    expect(frame).toHaveAttribute('hidden');
    fireEvent.click(screen.getByRole('button', { name: '网页阅读' }));
    expect(screen.getByTitle('测试讲义 网页阅读')).toBe(frame);
    expect(frame).not.toHaveAttribute('hidden');
  });

  it('shows task-scoped transcription and uncertainty beside the original image', async () => {
    vi.stubGlobal('fetch', vi.fn(async (path: string) => ({
      ok: true, status: 200, json: async () => path.includes('/sources/')
        ? { index: 3, label: '文件第 3 页', text: '', images: [`/api/documents/${documentId}/images/3.png`], transcription: { text: 'P(A|B) = P(A∩B) / P(B)', uncertainties: ['分母中的字母 B 需核对原页。'] } }
        : { id: documentId, name: '手写讲义.pdf', kind: 'pdf', unit_label: '文件页', total_units: 5, warnings: [], units: [] },
    }) as Response));
    render(<LessonReader job={job} />);
    const { button } = await activateFrame();
    fireEvent.click(button);
    expect(await screen.findByRole('heading', { name: '识读结果' })).toBeInTheDocument();
    expect(screen.getByText('P(A|B) = P(A∩B) / P(B)')).toBeInTheDocument();
    expect(screen.getByText('分母中的字母 B 需核对原页。')).toBeInTheDocument();
    expect(screen.getByAltText('文件第 3 页，原材料图片 1')).toBeInTheDocument();
    expect(fetch).toHaveBeenCalledWith(`/api/jobs/job1/sources/${documentId}/3`, expect.anything());
  });
});
