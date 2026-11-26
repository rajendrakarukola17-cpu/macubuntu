---
name: document-polisher
description: Executive proofreading, restructuring, and LibreOffice formatting for .docx, .odt, .xlsx, .pptx, and resumes.
triggers: [polish document, proofread, resume, format docx, improve presentation, clean up report, libreoffice]
---
# Document Polisher Playbook (Open-Source Agent Skill)
1. **Inspect First:** Read the actual document via `read_document` (never guess its contents).
2. **Respect Privacy:** If personal info/salary/contacts are present, remind the user they can click `Keep Local` or `Send Redacted`.
3. **High-Impact Edits Only:** Tighten wordiness, fix grammar/clarity, enforce consistent headings/bullets, and preserve existing formatting via `edit_document` (automatic timestamped backup created before saving).
4. **Offer Final Step:** Ask whether to convert to PDF (`convert_document`) or open directly in LibreOffice (`open_document`).
