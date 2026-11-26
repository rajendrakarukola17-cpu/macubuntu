---
name: daily-planner
description: Personal chief-of-staff daily prioritization, time-blocking, and focus timer scheduling.
triggers: [plan my day, daily plan, prioritize, schedule today, overwhelm, too many tasks, time block]
---
# Daily Planner Playbook (Open-Source Agent Skill)
1. **Check Context:** Call `prepare_meeting_context` (zero-token compound brief across calendar, contacts, files & memory) or `get_upcoming_events` + `search_memory` along with the current local time.
2. **Force Priority Clarity:** Pick the **Top 1 Must-Win Task** (highest leverage) and at most **2 Secondary Tasks**; explicitly defer everything else.
3. **Actionable Schedule:** Output a realistic time-blocked plan with short breaks (suited to the user's current local hour).
4. **Make It Real:** Offer ONE next step — e.g., starting a 25-minute Pomodoro timer (`set_timer`) or creating today's checklist note.
