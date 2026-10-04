# Internal implementation contract

`src/learnmargin/models.py` is the shared Python data contract.

## Ingestion

`learnmargin.ingestion.extract_document(path: Path, output_dir: Path, document_id: str, original_name: str | None = None) -> Document`

Every unit has a one-based index, an honest display label, extracted text and image filenames relative to `output_dir`. No uploaded files execute code. Limits must reject oversized/decompression-bomb documents. PDF pages can be rendered via pypdfium2; plain text via pypdf. DOCX sections must not pretend to be rendered pages. PPTX units are slides. LibreOffice enables legacy Office conversion when installed; missing dependency produces a specific actionable error. Preserve warnings about extraction/visual limitations.

## PDF renderer

`async learnmargin.rendering.render_lesson(lesson: Lesson, output_dir: Path) -> dict`

Writes `lesson.pdf`, standalone `lesson.html`, `lesson.json`, and `validation.json` (return metadata includes `page_count`). Overview first; substantive content + adjacent study actions; worked examples before practice; separate answer section; conditional rest prompts; sources/navigation. Treat generated Markdown as untrusted, escape HTML and never fetch remote URLs. Local math rendering, overflow checks, actual PDF text/page checks. Static assets in `src/learnmargin/templates/` and `src/learnmargin/assets/`.

## HTTP API (same origin)

- `GET /api/health` → `{status, version}`.
- `GET /api/settings` → `{api:{base_url,model,protocol,vision,json_mode,has_api_key},limits:{max_upload_mb,max_documents},formats:[...],capabilities:{libreoffice,browser},demo_available:true}`. Never return API keys.
- `POST /api/documents` multipart `file` → Document summary `{id,name,kind,unit_label,total_units,warnings,units:[{index,label,preview,has_images}]}`.
- `GET /api/documents/{id}` → same summary.
- `GET /api/documents/{id}/units/{index}` → `{index,label,text,images:[url]}`.
- `DELETE /api/documents/{id}` removes this locally imported document when it has no running jobs.
- `POST /api/jobs` JSON GenerateRequest → `{id,status,stage,progress,created_at}`. API key exists only in memory for this job.
- `GET /api/jobs/{id}` → `{id,status,stage,progress,created_at,error:null|string,title:null|string,page_count:null|number,selection:[{document,label,ref}],warnings:[],artifacts:{pdf,html,json,source_zip}}`. Status: `queued|running|completed|failed|cancelled`. Paths are same-origin download URLs. Empty artifacts before completion.
- `POST /api/jobs/{id}/cancel` requests cancellation.
- `GET /api/jobs` → list of above summaries (newest first).
- `GET /api/jobs/{id}/artifacts/{name}` serves allowlisted artifact names.
- `POST /api/demo` → queued job; generates a clearly labeled bundled demonstration without API access or uploads. No fake provider generation.
- Error JSON: `{detail: string}` with meaningful Chinese message.

Scope pages use `ranges` keyed by document ID, e.g. `{"id":"1-3,5"}`; all selected documents need an explicit range in pages mode. These are file pages/slides/sections, matching `unit_label`. Topic mode is semantically selected by the configured model and recorded in resulting source references.

## Frontend

Vite + React + TypeScript under `frontend/`, proxy `/api` to `http://127.0.0.1:8765`. Build output served by FastAPI; default local-only port 8765. Chinese UI with English LearnMargin brand. Never persist API keys in browser storage. Offer upload, per-document previews, range/topic scope, API settings, learner preferences, progress/history/cancel, PDF preview/download, editable sources download, clear demo labeling. Support keyboard and responsive layouts.
