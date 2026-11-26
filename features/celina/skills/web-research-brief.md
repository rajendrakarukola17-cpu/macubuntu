---
name: web-research-brief
description: Multi-source live web research yielding a crisp executive verdict, key facts, and source URLs.
triggers: [research, latest news, compare options, market overview, fact check, deep dive]
---
# Web Research Brief Playbook (Open-Source Agent Skill)
1. **Verify Live:** Call `web_search` (and `fetch_webpage` on the top 1–2 authoritative URLs if details matter).
2. **Lead With the Verdict:** Give a clear 1–2 sentence bottom-line conclusion upfront.
3. **Structured Evidence:** Present 3–5 concise bullet points with concrete dates, numbers, and source URLs.
4. **Flag Uncertainty:** Clearly call out conflicting sources or outdated specs instead of guessing.
