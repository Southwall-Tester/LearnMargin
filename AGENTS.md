# LearnMargin maintenance

This repository contains two independent products: the API-powered local application and the conversation-driven `skills/learnmargin` skill. The skill must work without an API key or app installation, using its host AI's file/document capabilities. Keep shared pedagogical references in the skill library; the software packages and reads that content without requiring a skill-enabled client.

- Preserve user-selected scope, provider, model and layout.
- Default layout: A4 portrait with the sidebar inside A4. Keep the wider option available.
- Give a substantive overview before detailed teaching. Prompts should be concise, useful and optional rather than filling margins with questions.
- Keep application copy functional: control labels, necessary input guidance, actual state and errors. No slogans, decorative English captions, introductory feature summaries or repeated footer branding. Remove their layout space as well; check the actual desktop and mobile interface before delivery.
- Answer-bearing sidebar prompts use `kind=question` and need a separated reference answer with a return link. Pure actions use `kind=action`.
- Never represent Word sections as rendered pages. Never silently truncate selected source material.
- Topic selection searches every selected document. Page selection keeps explicit ranges as primary material and retrieves related content from the remaining documents; explain each source and preserve its location in the lesson.
- PDFs must explain references without requiring other files. Web reading may open source units and must return to the original reading position.
- No raw model HTML, network-loaded PDF assets, stored API keys, or uploaded user files in commits.
- Check actual PDF output after renderer changes, not just HTML or page-count assertions.
- Use feature branches and PRs. Run the README checks, review the diff, and report exact validation limits before merging.
- Avoid touching another developer's files or committing unrelated local changes. No force push, automatic release tag, or fabricated verification record.
