# LearnMargin maintenance

This repository contains two independent products: the API-powered local application and the conversation-driven `skills/learnmargin` skill. The skill must work without an API key or app installation, using its host AI's file/document capabilities. Keep shared pedagogical references in the skill library; the software packages and reads that content without requiring a skill-enabled client.

- Preserve user-selected scope, output language, provider, model and layout. Output language is independent of source language; use it for teaching, answers, guidance and PDF navigation.
- Default layout: A4 portrait with the sidebar inside A4. Keep the wider option available.
- Give a substantive overview before detailed teaching. Prompts should be concise, useful and optional rather than filling margins with questions.
- Let the model choose chapter count, order and boundaries from the material and learning dependencies. Do not require an operator-selected count, a fixed default count or a chapter quota; keep coherent explanations together.
- Expand unfamiliar subject abbreviations on first use with their original full name and an explanation in the selected output language, using the source's terminology. Do not invent expansions from letters alone.
- Keep application copy functional: control labels, necessary input guidance, actual state and errors. No slogans, decorative English captions, introductory feature summaries or repeated footer branding. Remove their layout space as well; check the actual desktop and mobile interface before delivery.
- Express provider preference through preset order: DeepSeek first, then the other options. Keep UI and documentation wording neutral; omit the redundant server-default preset and provider-default promotional copy.
- Answer-bearing sidebar prompts use `kind=question` and need a separated reference answer with a return link. Pure actions use `kind=action`. Links target the actual answer/card coordinates; verify PDF destination zoom as well as page, and highlight Web destinations without affecting print.
- Never represent Word sections as rendered pages. Never silently truncate selected source material.
- Topic selection searches every selected document. Page selection keeps explicit ranges as primary material and retrieves related content from the remaining documents; explain each source and preserve its location in the lesson.
- PDFs must explain references without requiring other files. Web reading may open source units and must return to the original reading position.
- No raw model HTML, network-loaded PDF assets, stored API keys, or uploaded user files in commits.
- Check actual PDF output after renderer changes, not just HTML or page-count assertions.
- This is a solo-developed project. For routine changes, run the README checks, review the diff, then commit and push directly to `main`; feature branches, issues and PRs are not required. Use a temporary branch only when a large or experimental change, parallel work, or an explicit user request warrants isolation. Reuse the existing branch for related work instead of creating a branch per small change. Report actual validation results and limits, and check CI after pushing.
- Avoid touching another developer's files or committing unrelated local changes. No force push, automatic release tag, or fabricated verification record.
