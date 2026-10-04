# Internal implementation contract

`src/learnmargin/models.py` is the shared Python data contract.

## Ingestion

`learnmargin.ingestion.extract_document(path: Path, output_dir: Path, document_id: str, original_name: str | None = None) -> Document`

Every unit has a one-based index, an honest display label, extracted text and image filenames relative to `output_dir`. No uploaded files execute code. Limits must reject oversized/decompression-bomb documents. PDF pages can be rendered via pypdfium2; plain text via pypdf. DOCX sections must not pretend to be rendered pages. PPTX units are slides. LibreOffice enables legacy Office conversion when installed; missing dependency produces a specific actionable error. Preserve warnings about extraction/visual limitations.

## PDF renderer

`async learnmargin.rendering.render_lesson(lesson: Lesson, output_dir: Path, *, layout: str = "a4") -> dict`

Writes `lesson.pdf`, standalone `lesson.html`, `lesson.json`, and `validation.json` (return metadata includes `page_count`). `layout="a4"` is 210 × 297 mm with the body and study sidebar both inside the page; `layout="wide"` is 286 × 297 mm. Unknown layouts are rejected. Overview first; substantive content + adjacent study actions; worked examples before practice; separate answer section; conditional rest prompts; sources/navigation. Static assets are in `src/learnmargin/templates/` and `src/learnmargin/assets/`.

`StudyPrompt.kind` is required and is either `question` or `action`. A question asks the learner to answer, explain or calculate and must have a nonblank `answer`; an action is an instruction that does not require a written response. Do not invent low-value questions to fill a sidebar: each section permits zero to two study prompts and zero to two exercises. Answers appear after the teaching content, rather than beside the question.

`LessonSection.source_notes` contains `{ref, explanation, relation}` records: faithfully paraphrase what a source explains and how it complements, qualifies or differs from other materials. These explanations remain in the PDF itself. `SourceCitation.role` is `primary|reference|topic`, preserving how a source participated in selection. Do not invent quotations or label a synthesized interpretation as text directly present in the source.

Generated navigation uses renderer-owned anchors: `overview`, `section-N`, `practice-N-M`, `answer-N-M`, `prompt-N-M`, `prompt-answer-N-M`, `answers`, `review`, and `source-N`. The overview links to sections, the answer area (when present), and review/sources. Exercise/answer and sidebar-question/answer links are bidirectional. Sidebar return links must resolve to the corresponding card's page and position, not merely the start of its section. Source citations link to the matching entry. Pagination must preserve unique anchors and content, keep substantive content on every page, and avoid navigation-only pages.

Treat generated Markdown as untrusted, escape HTML, neutralize authored links and never fetch remote resources. Mathematics uses local KaTeX. Rendering checks page dimensions, pagination, nonempty PDF text, overflow, formula errors, content preservation and internal-link targets; sidebar navigation additionally checks the PDF destination coordinates against measured card positions. `validation.json` records these results including `sidebar_navigation`. These checks do not establish subject correctness or learning effectiveness.

## HTTP API (same origin)

- `GET /api/health` → `{status, version}`.
- `GET /api/settings` → `{api:{base_url,model,protocol,vision,json_mode,has_api_key},limits:{max_upload_mb,max_documents},formats:[...],capabilities:{libreoffice,browser},demo_available:true}`. Never return API keys.
- `POST /api/documents` multipart `file` → Document summary `{id,name,kind,unit_label,total_units,warnings,units:[{index,label,preview,has_images}]}`.
- `GET /api/documents/{id}` → same summary.
- `GET /api/documents/{id}/units/{index}` → `{index,label,text,images:[url]}`.
- `GET /api/jobs/{job_id}/sources/{document_id}/{index}` → original unit fields plus `transcription: {text,uncertainties:[string]} | null`. Only references actually used by this job are accessible; other units are rejected. Keep the original images alongside recognized text and uncertainty notes.
- `DELETE /api/documents/{id}` removes this locally imported document when it has no running jobs.
- `POST /api/jobs` JSON GenerateRequest → job summary. `GenerateRequest.layout` is `a4|wide`, default `a4`; `reading_mode` is `auto|handwritten`, default `auto`. Automatic mode uses vision when scan/image material requires recognition; handwritten mode recognizes text and formulas page by page. Handwritten mode requires `api.vision=true`. Recognition makes additional API calls and retains uncertain readings for comparison against the original page. API key exists only in memory for this job.
- `GET /api/jobs/{id}` → `{id,status,stage,progress,created_at,demo:boolean,error:null|string,title:null|string,page_count:null|number,selection:[{document,label,ref,role}],warnings:[],artifacts:{pdf,html,json,source_zip}}`. Status: `queued|running|completed|failed|cancelled`; progress is 0–100. Paths are same-origin download URLs. Empty artifacts before completion.
- `POST /api/jobs/{id}/cancel` requests cancellation.
- `GET /api/jobs` → list of above summaries (newest first).
- `GET /api/jobs/{id}/artifacts/{name}` serves allowlisted artifact names.
- `POST /api/demo` → queued job; generates a clearly labeled bundled demonstration without API access or uploads. No fake provider generation.
- Error JSON: `{detail: string}` with meaningful Chinese message.

Scope page selection uses `ranges` keyed by primary document ID, e.g. `{"main-id":"1-3,5"}`. At least one selected document must have a valid explicit range. Selected documents omitted from `ranges` are reference materials: search them for content related to the primary selections, combine relevant knowledge across files and cite each source. The frontend defaults the first imported document to primary and later documents to reference, with an explicit role selector per selected document. It only sends primary keys in `ranges` and still includes every selected primary/reference ID in `document_ids`. Range numbers are file pages/slides/sections matching `unit_label`, not assumed printed book pages.

Topic mode searches all selected documents for the requested knowledge and integrates relevant content across files, rather than independently summarizing each file or stopping after the first match. The configured model selects relevant units; the resulting source references record what was actually used. Explicit primary ranges constrain the main learning scope while reference material supplements that scope.

## Frontend

Vite + React + TypeScript under `frontend/`, proxy `/api` to `http://127.0.0.1:8765` with `changeOrigin: false`. Preserve the original Host so the backend can enforce Origin/Host equality for writes; do not relax production origin checks to accommodate development. Build output is served by FastAPI; default local-only port 8765. Chinese UI with English LearnMargin brand. Never persist API keys in browser storage. Offer upload, per-document previews, primary/reference roles in page scope, cross-document topic scope, API settings, learner preferences, progress/history/cancel, web/PDF reading, PDF download, editable sources download, and clear demo labeling. Deduplicate nonblocking import notes behind an expandable summary; show operation failures clearly. Support keyboard and responsive layouts.

`lesson.html` and `lesson.pdf` are served inline. Web reading uses a same-origin HTML iframe. After `window.learnmarginReport` exists, the parent adds `embedded-reader` to the iframe's root element. The renderer's `button.web-source-button[data-source-ref="document-id:index"]` is hidden in standalone HTML and print; only embedded screen mode shows it. Enable only real references matching a 32-character hexadecimal document ID and a positive unit index; synthetic demo references do not offer an unavailable original-file action.

Source buttons load document metadata and the job-scoped source API into a modal containing document name, unit label, original text and images. When transcription exists, display the recognized text and “待核对处” while preserving access to the original images. Opening a source must not navigate, replace or reload the lesson iframe. Closing or pressing Escape restores the originating button's focus and iframe scroll position. Missing/deleted originals show a recoverable message with “返回讲义”. PDF source explanations remain self-contained; this Web-only interaction does not replace explanatory text inside the PDF.
