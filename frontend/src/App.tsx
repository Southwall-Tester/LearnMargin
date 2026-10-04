import { useEffect, useRef, useState } from 'react';
import type { ChangeEvent, DragEvent } from 'react';
import {
  ArrowDownToLine, ArrowRight, BookOpen, BookOpenCheck, Check, ChevronDown,
  ChevronLeft, ChevronRight, CircleHelp, Clock3, FileText, Files, FolderOpen,
  Image, Layers3, LoaderCircle, Plus, RotateCcw, Settings2, Sparkles, Square,
  Trash2, TriangleAlert, UploadCloud, X,
} from 'lucide-react';
import { localArtifact, post, request } from './api';
import { configError, displayProgress, loadModelPreferences, saveModelPreferences, scopeError, statusLabels } from './domain';
import type { APIConfig, DocumentSummary, GenerateRequest, Job, Scope, Settings, UnitDetail } from './types';
import LessonReader from './LessonReader';

const difficulties = ['刚开始学，需要讲清基础', '看答案会，换题不会', '概念和公式容易混', '记得慢，学后容易忘', '经常拖延，很难开始', '近期有考试，需要自测'];
const initialConfig: APIConfig = {
  base_url: '', model: '', protocol: 'chat_completions', api_key: '',
  vision: true, json_mode: true, timeout_seconds: 180,
};

function serverModelConfig(settings: Settings): Omit<APIConfig, 'api_key' | 'timeout_seconds'> {
  const { base_url, model, protocol, vision, json_mode } = settings.api;
  return { base_url, model, protocol, vision, json_mode };
}

function errorText(error: unknown): string {
  return error instanceof Error ? error.message : '操作未完成，请重试。';
}

function shortDate(raw: string): string {
  const value = new Date(raw);
  return Number.isNaN(value.getTime()) ? '' : value.toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' });
}

function Warning({ children }: { children: React.ReactNode }) {
  return <div className="warning"><TriangleAlert size={16} aria-hidden="true" /><div>{children}</div></div>;
}

export default function App() {
  const [settings, setSettings] = useState<Settings | null>(null);
  const [config, setConfig] = useState<APIConfig>(initialConfig);
  const [showSettings, setShowSettings] = useState(false);
  const [settingsNotice, setSettingsNotice] = useState('');
  const [documents, setDocuments] = useState<DocumentSummary[]>([]);
  const [selected, setSelected] = useState<string[]>([]);
  const [scope, setScope] = useState<Scope>({ mode: 'all', ranges: {}, topics: '' });
  const [notes, setNotes] = useState('');
  const [selectedDifficulties, setSelectedDifficulties] = useState<string[]>([]);
  const [sectionCount, setSectionCount] = useState(4);
  const [layout, setLayout] = useState<'a4' | 'wide'>('a4');
  const [readingMode, setReadingMode] = useState<'auto' | 'handwritten'>('auto');
  const [uploading, setUploading] = useState(false);
  const [dragging, setDragging] = useState(false);
  const [uploadNote, setUploadNote] = useState('');
  const [error, setError] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [jobs, setJobs] = useState<Job[]>([]);
  const [activeJobId, setActiveJobId] = useState<string | null>(null);
  const [cancelling, setCancelling] = useState<string | null>(null);
  const [previewDocumentId, setPreviewDocumentId] = useState<string | null>(null);
  const [previewIndex, setPreviewIndex] = useState(1);
  const [unit, setUnit] = useState<UnitDetail | null>(null);
  const [unitLoading, setUnitLoading] = useState(false);
  const [unitError, setUnitError] = useState('');
  const [previewMode, setPreviewMode] = useState<'text' | 'image'>('text');
  const [previewTab, setPreviewTab] = useState<'material' | 'lesson'>('material');
  const [connectionError, setConnectionError] = useState('');
  const fileInput = useRef<HTMLInputElement>(null);
  const selectedDocuments = documents.filter(document => selected.includes(document.id));
  const importWarnings = [...new Set(selectedDocuments.flatMap(document => document.warnings))];
  const previewDocument = documents.find(document => document.id === previewDocumentId);
  const activeJob = jobs.find(job => job.id === activeJobId);
  const busy = submitting || uploading;

  useEffect(() => {
    const controller = new AbortController();
    request<Settings>('/api/settings', { signal: controller.signal }).then(data => {
      setSettings(data);
      setConfig({ ...initialConfig, ...serverModelConfig(data), ...loadModelPreferences(), api_key: '' });
    }).catch(error => { if (!controller.signal.aborted) setError(errorText(error)); });
    return () => controller.abort();
  }, []);

  useEffect(() => {
    let disposed = false;
    let timer: ReturnType<typeof setTimeout>;
    const controller = new AbortController();
    const refresh = async () => {
      try {
        const result = await request<Job[]>('/api/jobs', { signal: controller.signal });
        if (disposed) return;
        setJobs(result);
        setConnectionError('');
        setActiveJobId(current => current ?? result[0]?.id ?? null);
      } catch (error) { if (!disposed) setConnectionError(errorText(error)); }
      if (!disposed) timer = setTimeout(refresh, 2200);
    };
    void refresh();
    return () => { disposed = true; controller.abort(); clearTimeout(timer); };
  }, []);

  useEffect(() => {
    if (!previewDocumentId) { setUnit(null); return; }
    const controller = new AbortController();
    setUnitLoading(true);
    setUnitError('');
    setUnit(null);
    request<UnitDetail>(`/api/documents/${encodeURIComponent(previewDocumentId)}/units/${previewIndex}`, { signal: controller.signal })
      .then(result => { setUnit(result); setPreviewMode(result.text.trim() ? 'text' : 'image'); })
      .catch(error => { if (!controller.signal.aborted) setUnitError(errorText(error)); })
      .finally(() => { if (!controller.signal.aborted) setUnitLoading(false); });
    return () => controller.abort();
  }, [previewDocumentId, previewIndex]);

  async function uploadFiles(files: File[]) {
    if (!files.length || uploading) return;
    if (!settings) { setError('正在读取服务配置，请稍后再导入。'); return; }
    setError('');
    const maximum = settings.limits.max_documents;
    if (documents.length + files.length > maximum) {
      setError(`每次工作区最多导入 ${maximum} 份材料。请减少文件数量，或移除不再需要的材料。`); return;
    }
    setUploading(true);
    const failures: string[] = [];
    let imported = 0;
    const hasPrimary = documents.some(document => Object.hasOwn(scope.ranges, document.id));
    for (const file of files) {
      setUploadNote(`正在解析 ${file.name}`);
      if (file.size > settings.limits.max_upload_mb * 1024 * 1024) {
        failures.push(`${file.name}：超过 ${settings.limits.max_upload_mb} MB 上传限制。`); continue;
      }
      try {
        const form = new FormData(); form.append('file', file);
        const document = await request<DocumentSummary>('/api/documents', { method: 'POST', body: form });
        setDocuments(current => [...current, document]);
        setSelected(current => [...current, document.id]);
        if (!hasPrimary && imported === 0) {
          setScope(current => ({ ...current, ranges: { ...current.ranges, [document.id]: `1-${document.total_units}` } }));
        }
        setPreviewDocumentId(document.id); setPreviewIndex(1); setPreviewTab('material');
        imported += 1;
      } catch (error) { failures.push(`${file.name}：${errorText(error)}`); }
    }
    setUploading(false);
    setUploadNote(imported ? `已导入 ${imported} 份材料。点击文件可预览解析结果。` : '');
    if (failures.length) setError(failures.join('\n'));
    if (fileInput.current) fileInput.current.value = '';
  }

  async function removeDocument(document: DocumentSummary) {
    try {
      await request(`/api/documents/${encodeURIComponent(document.id)}`, { method: 'DELETE' });
      setDocuments(current => current.filter(item => item.id !== document.id));
      setSelected(current => current.filter(id => id !== document.id));
      if (previewDocumentId === document.id) { setPreviewDocumentId(null); setUnit(null); }
    } catch (error) { setError(errorText(error)); }
  }

  function selectDocument(document: DocumentSummary) {
    setPreviewDocumentId(document.id); setPreviewIndex(1); setPreviewTab('material');
  }

  function setMaterialRole(document: DocumentSummary, primary: boolean) {
    setScope(current => {
      const ranges = { ...current.ranges };
      if (primary) ranges[document.id] = `1-${document.total_units}`;
      else delete ranges[document.id];
      return { ...current, ranges };
    });
  }

  async function startGeneration(demo = false) {
    if (submitting) return;
    setError('');
    if (!demo) {
      const invalid = scopeError(selectedDocuments, scope) ?? configError(config);
      if (invalid) { setError(invalid); return; }
      if (readingMode === 'handwritten' && !config.vision) {
        setError('手写讲义识读需要支持图片理解的模型，请先开启“模型支持图片理解”。');
        setShowSettings(true); return;
      }
      if (!config.api_key.trim() && !settings?.api.has_api_key) {
        setError('请在模型设置中填写 API Key，或由本地服务配置密钥。');
        setShowSettings(true); return;
      }
    }
    setSubmitting(true);
    try {
      const body: GenerateRequest = {
        document_ids: selected, scope: { ...scope, ranges: Object.fromEntries(Object.entries(scope.ranges).filter(([key]) => selected.includes(key)).map(([key, value]) => [key, value.replace(/，/g, ',')])) },
        api: { ...config, base_url: config.base_url.trim(), model: config.model.trim(), api_key: config.api_key.trim() },
        learner_notes: [...selectedDifficulties, notes.trim()].filter(Boolean).join('；'),
        language: '简体中文', section_count: sectionCount, layout, reading_mode: readingMode,
      };
      const job = demo ? await post<Job>('/api/demo') : await post<Job>('/api/jobs', body);
      setJobs(current => [job, ...current.filter(item => item.id !== job.id)]);
      setActiveJobId(job.id); setPreviewTab('lesson');
      document.getElementById('result-panel')?.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
    } catch (error) { setError(errorText(error)); }
    finally { setSubmitting(false); }
  }

  async function cancelJob(job: Job) {
    setCancelling(job.id);
    try {
      await post(`/api/jobs/${encodeURIComponent(job.id)}/cancel`);
      const refreshed = await request<Job>(`/api/jobs/${encodeURIComponent(job.id)}`);
      setJobs(current => current.map(item => item.id === job.id ? refreshed : item));
    } catch (error) { setError(errorText(error)); }
    finally { setCancelling(null); }
  }

  function applyPreset(preset: string) {
    if (preset === 'server' && settings) {
      setConfig(current => ({ ...current, ...serverModelConfig(settings), api_key: current.api_key }));
    } else if (preset === 'deepseek') {
      setConfig(current => ({ ...current, base_url: 'https://api.deepseek.com', model: 'deepseek-flash', protocol: 'chat_completions', vision: true, json_mode: true }));
    } else if (preset === 'openai') {
      setConfig(current => ({ ...current, base_url: 'https://api.openai.com/v1', model: '', protocol: 'responses', vision: true, json_mode: true }));
    } else if (preset === 'compatible') {
      setConfig(current => ({ ...current, base_url: '', model: '', protocol: 'chat_completions', vision: false, json_mode: true }));
    }
    setSettingsNotice('');
  }

  function changeConfig<K extends keyof APIConfig>(key: K, value: APIConfig[K]) {
    setConfig(current => ({ ...current, [key]: value })); setSettingsNotice('');
  }

  function handleDrop(event: DragEvent<HTMLButtonElement>) {
    event.preventDefault(); setDragging(false); void uploadFiles(Array.from(event.dataTransfer.files));
  }

  return <div className="app-shell">
    <a className="skip-link" href="#main">跳到学习工作区</a>
    <aside className="sidebar" aria-label="工作区导航">
      <a className="brand" href="#main" aria-label="LearnMargin 首页"><span className="brand-icon"><BookOpen size={24} /></span><span>LearnMargin</span></a>
      <nav>
        <a href="#materials" className="nav-link"><FolderOpen size={18} /> 导入材料 <span>01</span></a>
        <a href="#scope" className="nav-link"><Layers3 size={18} /> 选择范围 <span>02</span></a>
        <a href="#learning" className="nav-link"><BookOpenCheck size={18} /> 安排学习 <span>03</span></a>
        <a href="#result-panel" className="nav-link"><FileText size={18} /> 我的讲义 <span>04</span></a>
      </nav>
      <div className="sidebar-footer"><span className={`connection-dot ${settings ? 'online' : ''}`} />{settings ? '本地工作区已连接' : '正在连接本地服务'}<span>v0.1</span></div>
    </aside>

    <main id="main" className="main-content">
      <header className="topbar"><h1 className="breadcrumb">新建学习讲义</h1><div className="topbar-actions"><button className="button secondary demo-button" disabled={submitting || !settings?.demo_available} onClick={() => void startGeneration(true)}><Sparkles size={17} /> 体验内置示例<small>无需密钥 · 不调用模型</small></button><button className="button ghost settings-toggle" onClick={() => setShowSettings(value => !value)} aria-expanded={showSettings} aria-controls="model-settings"><Settings2 size={16} /><span>{config.model || '模型设置'}</span><ChevronDown size={14} /></button></div></header>

      {error && <div className="error-banner" role="alert"><TriangleAlert size={18} /><p>{error}</p><button className="icon-button" aria-label="关闭错误提示" onClick={() => setError('')}><X size={17} /></button></div>}
      {connectionError && <div className="error-banner" role="status"><TriangleAlert size={18} /><p>{connectionError} 正在自动重连。</p></div>}

      {showSettings && <section className="card model-card" id="model-settings" aria-labelledby="settings-title">
        <div className="card-heading"><div className="heading-icon"><Settings2 size={19} /></div><div><h2 id="settings-title">连接你的模型</h2><p>支持 DeepSeek，以及兼容 Chat Completions / Responses 的服务。</p></div><button className="icon-button" aria-label="收起模型设置" onClick={() => setShowSettings(false)}><X size={18} /></button></div>
        <div className="preset-row"><span className="field-label">快速填写</span>{[['server', '服务默认'], ['deepseek', 'DeepSeek'], ['openai', 'OpenAI'], ['compatible', '兼容 API']].map(([value, label]) => <button key={value} className="button small secondary" onClick={() => applyPreset(value)}>{label}</button>)}</div>
        <div className="settings-grid">
          <label className="field">API 地址<input value={config.base_url} onChange={event => changeConfig('base_url', event.target.value)} placeholder="https://api.deepseek.com" autoComplete="url" spellCheck={false} /></label>
          <label className="field">模型名称<input value={config.model} onChange={event => changeConfig('model', event.target.value)} placeholder="填写服务商提供的模型 ID" spellCheck={false} /></label>
          <label className="field">API Key <span className="optional">仅保留在当前页面内存</span><input value={config.api_key} type="password" autoComplete="off" name="learnmargin-api-key" onChange={event => changeConfig('api_key', event.target.value)} placeholder={settings?.api.has_api_key ? '已配置服务端密钥，可在此覆盖' : '填写你的 API Key'} /></label>
          <label className="field">接口协议<select value={config.protocol} onChange={event => changeConfig('protocol', event.target.value as APIConfig['protocol'])}><option value="chat_completions">Chat Completions（通用兼容）</option><option value="responses">Responses</option></select></label>
        </div>
        <div className="capability-row"><label className="checkbox-label"><input type="checkbox" checked={config.vision} onChange={event => changeConfig('vision', event.target.checked)} />模型支持图片理解</label><label className="checkbox-label"><input type="checkbox" checked={config.json_mode} onChange={event => changeConfig('json_mode', event.target.checked)} />启用 JSON 输出模式</label><label className="timeout-label">请求超时<input aria-label="请求超时秒数" type="number" min={10} max={600} value={config.timeout_seconds} onChange={event => changeConfig('timeout_seconds', Math.max(10, Math.min(600, Number(event.target.value) || 180)))} />秒</label></div>
        <p className="helper"><CircleHelp size={14} />按所选模型实际能力配置。关闭图片理解后，扫描页、图片中的图表或公式可能无法读取；请使用视觉模型或提供可提取的文本。</p>
        <div className="settings-bottom"><span className="helper">生成时，所选材料会发送至你配置的模型服务。</span><button className="button primary small" onClick={() => { const invalid = configError(config); if (invalid) { setError(invalid); return; } saveModelPreferences(config); setSettingsNotice('模型偏好已保存，API Key 未写入浏览器存储。'); }}>保存模型偏好</button></div>
        {settingsNotice && <p className="success-text" role="status"><Check size={15} />{settingsNotice}</p>}
      </section>}

      <div className="workspace-grid"><div className="workspace-editor">
        <section className="card" id="materials" aria-labelledby="materials-title">
          <div className="card-heading"><span className="step-number">01</span><div><h2 id="materials-title">导入学习材料</h2></div>{documents.length > 0 && <span className="count-tag">{documents.length} 份材料</span>}</div>
          <input ref={fileInput} type="file" multiple className="visually-hidden" aria-label="选择学习材料文件" accept={settings?.formats.map(format => format.startsWith('.') ? format : `.${format}`).join(',')} onChange={(event: ChangeEvent<HTMLInputElement>) => void uploadFiles(Array.from(event.target.files ?? []))} />
          <button className={`drop-zone ${dragging ? 'dragging' : ''} ${documents.length ? 'compact' : ''}`} onClick={() => fileInput.current?.click()} disabled={uploading || !settings} onDragOver={event => { event.preventDefault(); setDragging(true); }} onDragLeave={() => setDragging(false)} onDrop={handleDrop}>
            <span className="upload-icon">{uploading ? <LoaderCircle className="spin" size={25} /> : <UploadCloud size={25} />}</span><strong>{uploading ? '正在解析学习材料…' : documents.length ? '继续添加材料' : '拖入文件，或点击选择'}</strong><span>{settings?.formats.length ? settings.formats.map(format => format.replace('.', '').toUpperCase()).join(' · ') : '正在读取支持的文件格式'}</span><small>{settings ? `单份不超过 ${settings.limits.max_upload_mb} MB，最多 ${settings.limits.max_documents} 份` : '连接后即可上传'}</small>
          </button>
          {uploadNote && <p className="upload-status" role="status">{uploadNote}</p>}
          {documents.length > 0 && <div className="document-list">{documents.map(document => <div className={`document-row ${previewDocumentId === document.id ? 'previewing' : ''}`} key={document.id}>
            <input type="checkbox" aria-label={`将 ${document.name} 纳入学习范围`} checked={selected.includes(document.id)} onChange={() => setSelected(current => current.includes(document.id) ? current.filter(id => id !== document.id) : [...current, document.id])} />
            <button className="document-open" onClick={() => selectDocument(document)}><span className="file-icon"><FileText size={20} /></span><span><strong>{document.name}</strong><small>{document.kind.toUpperCase()} <span>·</span> {document.total_units} {document.unit_label}</small></span></button>
            <button className="icon-button" aria-label={`移除 ${document.name}`} onClick={() => void removeDocument(document)}><Trash2 size={16} /></button>
          </div>)}</div>}
          {importWarnings.length > 0 && <details className="import-notes"><summary><TriangleAlert size={14} />导入提示（{importWarnings.length}）<ChevronDown size={13} /></summary><ul>{importWarnings.map(warning => <li key={warning}><p>{warning}</p><small>{selectedDocuments.filter(document => document.warnings.includes(warning)).map(document => document.name).join('、')}</small></li>)}</ul></details>}
          {!config.vision && selectedDocuments.some(document => document.units.some(item => item.has_images)) && <Warning>当前模型设置为仅处理文本。图片信息不会作为视觉输入发送；扫描页或图中知识请改用支持图片的模型。</Warning>}
          <p className="helper extraction-note"><Files size={14} />PDF 按文件页、PPT 按幻灯片、Word 按解析章节计数；以预览中的编号为准。</p>
        </section>

        <section className="card" id="scope" aria-labelledby="scope-title"><div className="card-heading"><span className="step-number">02</span><div><h2 id="scope-title">选择学习范围</h2></div></div>
          <div className="segmented" role="group" aria-label="学习范围方式">{([['all', '全部内容'], ['pages', '指定页码 / 章节'], ['topics', '指定知识点']] as const).map(([value, label]) => <button key={value} aria-pressed={scope.mode === value} className={scope.mode === value ? 'selected' : ''} onClick={() => setScope(current => ({ ...current, mode: value }))}>{label}</button>)}</div>
          {scope.mode === 'all' && selectedDocuments.length > 0 && <div className="scope-summary"><Layers3 size={20} /><p>已勾选 <strong>{selectedDocuments.length}</strong> 份材料，共 <strong>{selectedDocuments.reduce((sum, document) => sum + document.total_units, 0)}</strong> 个来源单元。</p></div>}
          {scope.mode === 'pages' && <div className="ranges">{selectedDocuments.length ? <><p className="helper">主材料按指定范围学习；参考资料自动检索相关内容，跨文件整合并标明出处。</p>{selectedDocuments.map(document => <div className="scope-document" key={document.id}><div className="scope-document-heading"><div><strong>{document.name}</strong><small>共 {document.total_units} {document.unit_label}</small></div><select aria-label={`${document.name} 的材料角色`} value={Object.hasOwn(scope.ranges, document.id) ? 'primary' : 'reference'} onChange={event => setMaterialRole(document, event.target.value === 'primary')}><option value="primary">主材料 · 指定范围</option><option value="reference">参考资料 · 自动检索</option></select></div>{Object.hasOwn(scope.ranges, document.id) && <label className="field scope-range-field">学习范围<input aria-label={`${document.name} 的范围`} value={scope.ranges[document.id]} onChange={event => setScope(current => ({ ...current, ranges: { ...current.ranges, [document.id]: event.target.value } }))} placeholder={`例如 1-${Math.min(3, document.total_units)}`} /><small>使用文件内编号，如 1-3,5；以预览为准，不是教材印刷页码。</small></label>}</div>)}</> : <p className="muted">先导入并勾选材料，再指定主材料的学习范围。</p>}</div>}
          {scope.mode === 'topics' && <label className="field topic-field">想弄明白的知识点<textarea rows={3} maxLength={2000} value={scope.topics} onChange={event => setScope(current => ({ ...current, topics: event.target.value }))} placeholder="例如：从前束范式开始，理解量词外移的条件，并能独立完成转换。" /><small>在所有已勾选材料中检索并整合相关知识，讲义标明各处来源。</small></label>}
        </section>

        <section className="card" id="learning" aria-labelledby="learning-title"><div className="card-heading"><span className="step-number">03</span><div><h2 id="learning-title">学习需求与版式</h2></div><span className="optional">可选</span></div>
          <div className="difficulty-chips" role="group" aria-label="学习困难">{difficulties.map(value => <button className={`choice-chip ${selectedDifficulties.includes(value) ? 'checked' : ''}`} key={value} aria-pressed={selectedDifficulties.includes(value)} onClick={() => setSelectedDifficulties(current => current.includes(value) ? current.filter(item => item !== value) : [...current, value])}>{selectedDifficulties.includes(value) ? <Check size={14} /> : <Plus size={14} />}{value}</button>)}</div>
          <label className="field learning-notes">补充你的基础或目标<textarea rows={2} maxLength={1500} value={notes} onChange={event => setNotes(event.target.value)} placeholder="例如：大一，第一次学谓词逻辑；希望每个公式都有直观解释。" /></label>
          <label className="field reading-mode-field">材料识读<select aria-label="材料识读" aria-describedby="reading-mode-help" value={readingMode} onChange={event => setReadingMode(event.target.value as 'auto' | 'handwritten')}><option value="auto">自动识读 · 扫描页自动使用视觉模型</option><option value="handwritten">手写讲义 · 逐页识读文字与公式</option></select><small id="reading-mode-help">识读会增加 API 调用；疑点保留供原页核对。</small></label>
          <div className="lesson-options"><label className="field">讲解章节数<select value={sectionCount} onChange={event => setSectionCount(Number(event.target.value))}>{[2, 3, 4, 5, 6, 7, 8].map(number => <option key={number} value={number}>{number} 个学习章节</option>)}</select></label><label className="field">PDF 版式<select value={layout} onChange={event => setLayout(event.target.value as 'a4' | 'wide')}><option value="a4">A4 标准版 · 打印 / 平板</option><option value="wide">电脑宽版 · 更宽阅读区域</option></select></label></div>
          <p className="helper">{layout === 'a4' ? '正文与学习侧栏都排在 A4 页面内。' : '更宽的页面为正文和侧栏提供额外空间。'} 章节数影响讲解深度，不等于最终页数。</p>
          <div className="generate-row"><div><strong>{selected.length ? `已选 ${selected.length} 份材料` : '尚未选择材料'}</strong><small>{config.model || '请连接模型'} · {layout === 'a4' ? 'A4 标准版' : '电脑宽版'}</small></div><button className="button primary generate-button" disabled={busy || !settings} onClick={() => void startGeneration()}>{submitting ? <LoaderCircle className="spin" size={18} /> : <Sparkles size={18} />} {submitting ? '正在创建任务' : '生成学习讲义'}<ArrowRight size={17} /></button></div>
        </section>
      </div>

      <aside className="preview-column" id="result-panel" aria-label="材料和讲义预览">
        <section className="card preview-card"><div className="preview-tabs" role="tablist" aria-label="预览内容"><button role="tab" id="material-tab" aria-controls="material-panel" aria-selected={previewTab === 'material'} className={previewTab === 'material' ? 'active' : ''} onClick={() => setPreviewTab('material')}><Files size={16} />材料预览</button><button role="tab" id="lesson-tab" aria-controls="lesson-panel" aria-selected={previewTab === 'lesson'} className={previewTab === 'lesson' ? 'active' : ''} onClick={() => setPreviewTab('lesson')}><BookOpen size={16} />生成的讲义{activeJob?.status === 'completed' && <span className="tab-dot" />}</button></div>
          {previewTab === 'material' ? <div role="tabpanel" id="material-panel" aria-labelledby="material-tab">
            {!previewDocument ? <div className="empty-preview"><div className="paper-illustration" aria-hidden="true"><span className="paper-heading" /><span /><span /><span className="short-line" /><div className="paper-block" /><span /><span /><div className="paper-margin"><i /><i /><i /></div></div><h3>导入后预览材料</h3></div> : <>
              <div className="preview-document-title" title={previewDocument.name}><FileText size={16} /><strong>{previewDocument.name}</strong></div>
              <div className="preview-controls"><button className="icon-button" aria-label="预览上一单元" disabled={previewIndex <= 1} onClick={() => setPreviewIndex(value => value - 1)}><ChevronLeft size={17} /></button><label className="unit-select"><span className="visually-hidden">预览单元</span><select value={previewIndex} onChange={event => setPreviewIndex(Number(event.target.value))}>{previewDocument.units.map(item => <option key={item.index} value={item.index}>{item.label}</option>)}</select></label><span className="unit-total">/ {previewDocument.total_units}</span><button className="icon-button" aria-label="预览下一单元" disabled={previewIndex >= previewDocument.total_units} onClick={() => setPreviewIndex(value => value + 1)}><ChevronRight size={17} /></button></div>
              <div className="view-switch" role="group" aria-label="材料预览方式"><button aria-pressed={previewMode === 'text'} onClick={() => setPreviewMode('text')}><FileText size={13} />解析文本</button><button aria-pressed={previewMode === 'image'} onClick={() => setPreviewMode('image')}><Image size={13} />页面 / 图片 {unit?.images.length ? `(${unit.images.length})` : ''}</button></div>
              <div className="source-preview" aria-busy={unitLoading}>{unitLoading ? <div className="preview-loading"><LoaderCircle className="spin" size={24} />正在读取此单元</div> : unitError ? <Warning>{unitError}</Warning> : previewMode === 'text' ? unit?.text.trim() ? <pre>{unit.text}</pre> : <div className="inline-empty"><Image size={28} /><p>此单元没有可提取的文本。</p><small>查看图片确认内容；生成时需要支持图片理解的模型。</small></div> : unit?.images.length ? unit.images.map((url, index) => localArtifact(url) ? <img key={url} src={url} alt={`${unit.label}，预览图片 ${index + 1}`} loading="lazy" /> : null) : <div className="inline-empty"><FileText size={28} /><p>此单元没有图片预览。</p><small>切换到解析文本查看内容。</small></div>}</div>
              <p className="preview-footnote">这里显示解析结果。文本排版与原文件可能不同，复杂公式请结合图片检查。</p>
            </>}
          </div> : <div role="tabpanel" id="lesson-panel" aria-labelledby="lesson-tab">
            {!activeJob ? <div className="empty-lesson"><BookOpenCheck size={40} strokeWidth={1.2} /><h3>学习讲义</h3><p>生成完成后可预览 PDF，并下载可编辑源文件。</p><button className="button secondary small" disabled={!settings?.demo_available || submitting} onClick={() => void startGeneration(true)}><Sparkles size={15} />体验示例 · 无需密钥</button></div> : <>
              <div className="job-heading"><span className={`status-tag ${activeJob.status}`}>{statusLabels[activeJob.status]}</span>{activeJob.demo && <span className="demo-tag">内置示例 · 未调用模型</span>}<h3>{activeJob.title || '正在准备你的学习讲义'}</h3><p>{shortDate(activeJob.created_at)}{activeJob.page_count ? ` · ${activeJob.page_count} 页` : ''}</p></div>
              {['queued', 'running'].includes(activeJob.status) && <div className="job-progress" role="status"><div className="progress-label"><span><LoaderCircle size={16} className="spin" />{activeJob.stage || '正在准备'}</span><strong>{displayProgress(activeJob.progress)}%</strong></div><progress max={100} value={displayProgress(activeJob.progress)} aria-label="讲义生成进度" /><button className="button small secondary" disabled={cancelling === activeJob.id} onClick={() => void cancelJob(activeJob)}><Square size={12} />{cancelling === activeJob.id ? '正在请求取消' : '取消任务'}</button></div>}
              {activeJob.status === 'failed' && <div className="job-failure"><Warning>{activeJob.error || '生成未完成，请检查模型配置后重试。'}</Warning><p>调整设置或范围后，可重新生成。</p></div>}
              {activeJob.status === 'cancelled' && <div className="inline-empty"><Square size={26} /><p>这次生成已取消。</p><small>材料与设置仍在，可以调整后重新开始。</small></div>}
              {activeJob.status === 'completed' && <>
                <div className="artifact-actions">{localArtifact(activeJob.artifacts?.pdf) && <a className="button primary small" href={activeJob.artifacts!.pdf} download><ArrowDownToLine size={15} />下载 PDF</a>}{localArtifact(activeJob.artifacts?.source_zip) && <a className="button secondary small" href={activeJob.artifacts!.source_zip} download><FolderOpen size={15} />可编辑源文件</a>}</div>
                <LessonReader key={activeJob.id} job={activeJob} />
                <div className="pdf-fallback">预览不显示？{localArtifact(activeJob.artifacts?.pdf) && <a href={activeJob.artifacts!.pdf} target="_blank" rel="noreferrer">在新窗口打开 PDF <ArrowRight size={12} /></a>}</div>
                {(activeJob.warnings?.length ?? 0) > 0 && <details className="result-details"><summary>生成提示（{activeJob.warnings!.length}）</summary>{activeJob.warnings!.map((warning, index) => <Warning key={index}>{warning}</Warning>)}</details>}
                {(activeJob.selection?.length ?? 0) > 0 && <details className="result-details"><summary>查看使用的材料（{activeJob.selection!.length} 个单元）</summary><ul>{activeJob.selection!.map((source, index) => <li key={`${source.ref}-${index}`}><strong>{source.document}</strong><span>{source.label} · {source.ref}</span></li>)}</ul></details>}
              </>}
            </>}
          </div>}
        </section>
        <section className="card history-card" aria-labelledby="history-title"><div className="history-heading"><h2 id="history-title"><Clock3 size={16} />最近的讲义</h2><span>{jobs.length}</span></div>{jobs.length ? <div className="job-list">{jobs.slice(0, 12).map(job => <button key={job.id} className={`history-item ${activeJobId === job.id && previewTab === 'lesson' ? 'current' : ''}`} onClick={() => { setActiveJobId(job.id); setPreviewTab('lesson'); }}><span className={`history-icon ${job.status}`}>{job.status === 'completed' ? <FileText size={18} /> : ['running', 'queued'].includes(job.status) ? <LoaderCircle size={18} className="spin" /> : <RotateCcw size={18} />}</span><span><strong>{job.title || (job.demo ? '内置示例讲义' : '学习讲义')}</strong><small>{statusLabels[job.status]} · {shortDate(job.created_at)}{job.demo ? ' · 示例' : ''}</small></span><ChevronRight size={15} /></button>)}</div> : <p className="history-empty">暂无讲义</p>}</section>
      </aside></div>
    </main>
  </div>;
}
