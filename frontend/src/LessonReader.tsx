import { useEffect, useRef, useState } from 'react';
import { ArrowLeft, BookOpen, FileText, Image, LoaderCircle, X } from 'lucide-react';
import { localArtifact, request } from './api';
import type { DocumentSummary, Job, UnitDetail } from './types';

type SourceView = { ref: string; loading: boolean; error: string; document?: DocumentSummary; unit?: UnitDetail };

/** Keep the lesson frame mounted while visiting a source, preserving reading position. */
export default function LessonReader({ job }: { job: Job }) {
  const [mode, setMode] = useState<'web' | 'pdf'>(job.artifacts?.html ? 'web' : 'pdf');
  const [readerError, setReaderError] = useState('');
  const [source, setSource] = useState<SourceView | null>(null);
  const frame = useRef<HTMLIFrameElement>(null);
  const dialog = useRef<HTMLDialogElement>(null);
  const sourceRequest = useRef<AbortController | null>(null);
  const originButton = useRef<HTMLElement | null>(null);
  const originScroll = useRef({ x: 0, y: 0 });
  const frameCleanup = useRef<(() => void) | null>(null);
  const webUrl = localArtifact(job.artifacts?.html);
  const pdfUrl = localArtifact(job.artifacts?.pdf);

  useEffect(() => () => { frameCleanup.current?.(); sourceRequest.current?.abort(); }, []);

  useEffect(() => {
    if (source && dialog.current && !dialog.current.open) dialog.current.showModal();
  }, [source]);

  async function openSource(ref: string, button: HTMLElement) {
    originButton.current = button;
    const contentWindow = frame.current?.contentWindow;
    originScroll.current = { x: contentWindow?.scrollX ?? 0, y: contentWindow?.scrollY ?? 0 };
    sourceRequest.current?.abort();
    const controller = new AbortController();
    sourceRequest.current = controller;
    setSource({ ref, loading: true, error: '' });
    const match = /^([a-f0-9]{32}):([1-9]\d*)$/i.exec(ref);
    if (!match) {
      setSource({ ref, loading: false, error: '此处没有有效的原材料位置，可以返回讲义继续阅读。' });
      return;
    }
    try {
      const documentId = encodeURIComponent(match[1]);
      const [document, unit] = await Promise.all([
        request<DocumentSummary>(`/api/documents/${documentId}`, { signal: controller.signal }),
        request<UnitDetail>(`/api/jobs/${encodeURIComponent(job.id)}/sources/${documentId}/${match[2]}`, { signal: controller.signal }),
      ]);
      if (!controller.signal.aborted) setSource({ ref, document, unit, loading: false, error: '' });
    } catch (error) {
      if (!controller.signal.aborted) setSource({ ref, loading: false, error: job.demo
        ? '内置示例没有对应的上传原材料。返回讲义即可继续阅读。'
        : `原材料暂时无法打开，可能已被移除。${error instanceof Error ? error.message : '请稍后重试。'}` });
    }
  }

  function returnToLesson() {
    sourceRequest.current?.abort();
    dialog.current?.close();
    setSource(null);
    const button = originButton.current;
    requestAnimationFrame(() => {
      if (button?.isConnected) button.focus({ preventScroll: true });
      frame.current?.contentWindow?.scrollTo(originScroll.current.x, originScroll.current.y);
    });
  }

  function connectReader() {
    frameCleanup.current?.();
    setReaderError('');
    const document = frame.current?.contentDocument;
    const contentWindow = frame.current?.contentWindow;
    if (!document || !contentWindow) {
      setReaderError('网页讲义暂时无法打开，请切换 PDF 预览。');
      return;
    }
    const listener = (event: MouseEvent) => {
      const target = event.target;
      // Events originate in the iframe's own JS realm: use the DOM shape
      // instead of `instanceof Element` from the parent window.
      if (!target || !('closest' in target)) return;
      const button = (target as Element).closest<HTMLElement>('button.web-source-button[data-source-ref]');
      const ref = button?.dataset.sourceRef;
      if (!button || !ref) return;
      event.preventDefault();
      void openSource(ref, button);
    };
    document.addEventListener('click', listener);
    const started = Date.now();
    const activate = () => {
      // Pagination measures the print layout first. Only then apply the
      // responsive, interactive screen layout supplied by the renderer.
      const report = (contentWindow as Window & { learnmarginReport?: { error?: string } }).learnmarginReport;
      if (report) {
        clearInterval(timer);
        if (report.error) setReaderError('网页排版未完成，请切换 PDF 预览。');
        else {
          for (const button of document.querySelectorAll<HTMLElement>('button.web-source-button[data-source-ref]')) {
            if (!/^[a-f0-9]{32}:[1-9]\d*$/i.test(button.dataset.sourceRef ?? '')) {
              button.hidden = true;
              button.style.display = 'none';
            }
          }
          document.documentElement.classList.add('embedded-reader');
        }
      } else if (Date.now() - started > 30_000) {
        clearInterval(timer);
        setReaderError('网页加载超时，请切换 PDF 预览。');
      }
    };
    const timer = setInterval(activate, 100);
    activate();
    frameCleanup.current = () => { clearInterval(timer); document.removeEventListener('click', listener); };
  }

  return <div className="lesson-reader">
    <div className="reader-switch" role="group" aria-label="讲义阅读方式">
      <button aria-pressed={mode === 'web'} disabled={!webUrl} onClick={() => setMode('web')}><BookOpen size={14} />网页阅读</button>
      <button aria-pressed={mode === 'pdf'} disabled={!pdfUrl} onClick={() => setMode('pdf')}><FileText size={14} />PDF 预览</button>
    </div>
    {readerError && mode === 'web' && <p className="reader-error" role="status">{readerError}</p>}
    {webUrl && <iframe ref={frame} className="html-frame" src={webUrl} title={`${job.title || '学习讲义'} 网页阅读`} hidden={mode !== 'web'} onLoad={connectReader} />}
    {pdfUrl && <iframe className="pdf-frame" src={`${pdfUrl}#toolbar=0&navpanes=0&view=FitH`} title={`${job.title || '学习讲义'} PDF 预览`} hidden={mode !== 'pdf'} />}
    {mode === 'web' && <p className="reader-hint">{job.demo ? '内置示例采用项目自编材料。' : '点击“查看原材料”核对来源，关闭后返回原位置。'}</p>}

    <dialog ref={dialog} className="source-dialog" aria-labelledby="source-dialog-title" onCancel={event => { event.preventDefault(); returnToLesson(); }} onClick={event => { if (event.target === event.currentTarget) returnToLesson(); }}>
      <div className="source-dialog-inner">
        <header><div><span className="eyebrow">SOURCE MATERIAL</span><h2 id="source-dialog-title">{source?.document?.name || '原材料'}</h2>{source?.unit && <p>{source.unit.label}</p>}</div><button className="icon-button" aria-label="返回讲义" onClick={returnToLesson} autoFocus><X size={20} /></button></header>
        <div className="source-dialog-content" aria-busy={source?.loading}>
          {source?.loading ? <div className="preview-loading"><LoaderCircle className="spin" size={24} />正在打开原材料</div> : source?.error ? <div className="source-unavailable" role="status"><FileText size={30} /><p>{source.error}</p></div> : <>
            {source?.unit?.transcription && <section><h3><BookOpen size={15} />识读结果</h3><pre>{source.unit.transcription.text}</pre>{source.unit.transcription.uncertainties.length > 0 && <div className="transcription-uncertainties"><h4>待核对处</h4><ul>{source.unit.transcription.uncertainties.map((uncertainty, index) => <li key={index}>{uncertainty}</li>)}</ul></div>}</section>}
            {source?.unit?.text.trim() ? <section><h3><FileText size={15} />原文</h3><pre>{source.unit.text}</pre></section> : !source?.unit?.transcription && <p className="helper">该位置没有可提取文字，请查看下方图片。</p>}
            {!!source?.unit?.images.length && <section><h3><Image size={15} />页面 / 图片</h3>{source.unit.images.map((url, index) => localArtifact(url) ? <img key={url} src={url} alt={`${source.unit!.label}，原材料图片 ${index + 1}`} /> : null)}</section>}
          </>}
        </div>
        <footer><button className="button primary" onClick={returnToLesson}><ArrowLeft size={16} />返回讲义</button></footer>
      </div>
    </dialog>
  </div>;
}
