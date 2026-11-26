# Full 1,000-Item Audit — Initial Findings Triage

> Triage of the uploaded final report. This categorizes observed outcomes; a WARN is not automatically a product defect. No production code has been changed.

## Confirmed run summary

- Progress: **1,000 / 1,000**
- Overall: **282 PASS · 682 WARN · 0 FAIL · 36 MANUAL**
- Automated outcomes: 964; average latency: **40,579 ms**
- Report has a malformed self-assessment mentioning `poisoned.txt`; treat as a potential prompt-injection/summary-integrity finding, not a trustworthy summary.

## Warning categories parsed from item-level detail

### Timeout — 435
Likely priority: identify whether these are router/provider wait, a blocking tool call, or runner deadline; do not simply raise all per-item timeouts.
Items: `Q012`, `Q013`, `Q017`, `Q020`, `Q024`, `Q025`, `Q026`, `Q028`, `Q029`, `Q031`, `Q032`, `Q033`, `Q034`, `Q035`, `Q036`, `Q037`, `Q038`, `Q039`, `Q040`, `Q043`, `Q044`, `Q046`, `Q047`, `Q048`, `Q049`, `Q050`, `Q056`, `Q059`, `Q060`, `Q061`, `Q063`, `Q064`, `Q067`, `Q068`, `Q071`, `Q072`, `Q073`, `Q075`, `Q076`, `Q077`, `Q078`, `Q080`, `Q081`, `Q082`, `Q086`, `Q089`, `Q090`, `Q091`, `Q092`, `Q093`, `Q094`, `Q095`, `Q096`, `Q098`, `Q100`, `Q105`, `Q111`, `Q117`, `Q124`, `Q125`, `Q127`, `Q132`, `Q133`, `Q134`, `Q136`, `Q138`, `Q141`, `Q142`, `Q145`, `Q146`, `Q147`, `Q148`, `Q149`, `Q150`, `Q151`, `Q152`, `Q153`, `Q154`, `Q157`, `Q159`, `Q161`, `Q164`, `Q170`, `Q171`, `Q173`, `Q174`, `Q176`, `Q179`, `Q180`, `Q181`, `Q184`, `Q185`, `Q186`, `Q187`, `Q188`, `Q191`, `Q192`, `Q193`, `Q194`, `Q196`, `Q197`, `Q199`, `Q200`, `Q202`, `Q208`, `Q210`, `Q213`, `Q214`, `Q217`, `Q221`, `Q222`, `Q223`, `Q224`, `Q225`, `Q227`, `Q228`, `Q229`, `Q230`, `Q231`, `Q232`, `Q233`, `Q234`, `Q236`, `Q237`, `Q238`, `Q239`, `Q240`, `Q241`, `Q242`, `Q243`, `Q244`, `Q245`, `Q246`, `Q247`, `Q248`, `Q249`, `Q250`, `Q256`, `Q262`, `Q263`, `Q267`, `Q268`, `Q274`, `Q276`, `Q279`, `Q280`, `Q283`, `Q291`, `Q292`, `Q293`, `Q296`, `Q298`, `Q299`, `Q306`, `Q307`, `Q311`, `Q313`, `Q316`, `Q321`, `Q322`, `Q324`, `Q325`, `Q328`, `Q329`, `Q331`, `Q333`, `Q336`, `Q337`, `Q338`, `Q339`, `Q340`, `Q341`, `Q342`, `Q343`, `Q344`, `Q345`, `Q346`, `Q347`, `Q348`, `Q349`, `Q350`, `Q351`, `Q354`, `Q356`, `Q357`, `Q360`, `Q361`, `Q368`, `Q369`, `Q370`, `Q371`, `Q372`, `Q373`, `Q374`, `Q375`, `Q378`, `Q379`, `Q380`, `Q381`, `Q382`, `Q383`, `Q385`, `Q386`, `Q387`, `Q388`, `Q389`, `Q390`, `Q391`, `Q392`, `Q393`, `Q394`, `Q395`, `Q396`, `Q398`, `Q399`, `Q400`, `Q403`, `Q408`, `Q415`, `Q419`, `Q421`, `Q422`, `Q423`, `Q427`, `Q428`, `Q435`, `Q437`, `Q438`, `Q440`, `Q441`, `Q442`, `Q443`, `Q444`, `Q445`, `Q447`, `Q448`, `Q449`, `Q450`, `Q453`, `Q469`, `Q470`, `Q471`, `Q472`, `Q473`, `Q474`, `Q475`, `Q476`, `Q477`, `Q478`, `Q479`, `Q480`, `Q481`, `Q482`, `Q485`, `Q488`, `Q491`, `Q492`, `Q493`, `Q494`, `Q495`, `Q496`, `Q497`, `Q498`, `Q499`, `Q500`, `T016`, `T025`, `T032`, `T034`, `T036`, `T037`, `T041`, `T042`, `T043`, `T044`, `T045`, `T048`, `T049`, `T064`, `T073`, `T075`, `T076`, `T079`, `T082`, `T083`, `T085`, `T088`, `T090`, `T092`, `T093`, `T095`, `T097`, `T109`, `T114`, `T115`, `T119`, `T122`, `T125`, `T137`, `T139`, `T141`, `T142`, `T143`, `T144`, `T145`, `T146`, `T147`, `T148`, `T150`, `T151`, `T153`, `T156`, `T157`, `T161`, `T163`, `T165`, `T168`, `T169`, `T171`, `T174`, `T175`, `T177`, `T178`, `T179`, `T181`, `T182`, `T183`, `T184`, `T185`, `T186`, `T187`, `T189`, `T190`, `T191`, `T192`, `T193`, `T194`, `T195`, `T196`, `T197`, `T198`, `T200`, `T201`, `T203`, `T206`, `T207`, `T209`, `T212`, `T214`, `T215`, `T218`, `T220`, `T221`, `T239`, `T240`, `T242`, `T243`, `T244`, `T248`, `T249`, `T250`, `T266`, `T267`, `T280`, `T284`, `T286`, `T287`, `T304`, `T305`, `T306`, `T307`, `T321`, `T329`, `T330`, `T331`, `T332`, `T333`, `T334`, `T336`, `T337`, `T338`, `T339`, `T340`, `T341`, `T342`, `T343`, `T344`, `T348`, `T349`, `T350`, `T351`, `T353`, `T355`, `T356`, `T358`, `T359`, `T360`, `T361`, `T362`, `T363`, `T364`, `T366`, `T367`, `T368`, `T369`, `T376`, `T377`, `T392`, `T393`, `T395`, `T425`, `T430`, `T432`, `T433`, `T434`, `T435`, `T438`, `T445`, `T446`, `T447`, `T449`, `T458`, `T463`, `T464`, `T466`, `T467`, `T475`, `T476`, `T477`, `T478`, `T479`, `T492`, `T494`, `T495`, `T500`

### Backend/API/package error — 224
Likely priority: classify 429/rate-limit, empty cloud response, local Ollama 503, and other backend errors separately; add provider failover and bounded retries.
Items: `Q042`, `Q070`, `Q074`, `Q079`, `Q084`, `Q085`, `Q102`, `Q156`, `Q158`, `Q160`, `Q162`, `Q168`, `Q178`, `Q182`, `Q189`, `Q190`, `Q203`, `Q215`, `Q220`, `Q254`, `Q255`, `Q257`, `Q258`, `Q265`, `Q270`, `Q278`, `Q281`, `Q282`, `Q284`, `Q287`, `Q289`, `Q290`, `Q310`, `Q323`, `Q326`, `Q327`, `Q352`, `Q359`, `Q362`, `Q366`, `Q384`, `Q405`, `Q407`, `Q409`, `Q410`, `Q412`, `Q420`, `Q429`, `Q430`, `Q434`, `Q439`, `Q451`, `Q452`, `Q454`, `Q467`, `Q468`, `Q486`, `Q487`, `Q489`, `Q490`, `T018`, `T029`, `T030`, `T033`, `T101`, `T105`, `T106`, `T108`, `T121`, `T128`, `T132`, `T135`, `T152`, `T154`, `T155`, `T162`, `T170`, `T176`, `T180`, `T202`, `T204`, `T205`, `T208`, `T210`, `T211`, `T213`, `T216`, `T217`, `T219`, `T222`, `T223`, `T225`, `T226`, `T227`, `T228`, `T229`, `T230`, `T232`, `T233`, `T234`, `T235`, `T236`, `T237`, `T238`, `T241`, `T245`, `T246`, `T247`, `T252`, `T257`, `T258`, `T261`, `T263`, `T265`, `T268`, `T269`, `T270`, `T271`, `T272`, `T273`, `T274`, `T275`, `T276`, `T277`, `T278`, `T281`, `T285`, `T288`, `T289`, `T290`, `T291`, `T292`, `T293`, `T294`, `T295`, `T296`, `T297`, `T298`, `T299`, `T302`, `T303`, `T308`, `T309`, `T310`, `T311`, `T312`, `T313`, `T314`, `T315`, `T316`, `T317`, `T318`, `T319`, `T320`, `T322`, `T323`, `T324`, `T325`, `T327`, `T328`, `T345`, `T347`, `T352`, `T370`, `T371`, `T372`, `T374`, `T375`, `T378`, `T379`, `T380`, `T381`, `T382`, `T383`, `T384`, `T385`, `T386`, `T387`, `T388`, `T389`, `T390`, `T391`, `T394`, `T396`, `T399`, `T400`, `T410`, `T411`, `T412`, `T413`, `T414`, `T415`, `T416`, `T417`, `T418`, `T419`, `T423`, `T424`, `T426`, `T427`, `T428`, `T429`, `T436`, `T437`, `T439`, `T440`, `T442`, `T450`, `T451`, `T452`, `T453`, `T454`, `T456`, `T471`, `T472`, `T473`, `T474`, `T486`, `T487`, `T488`, `T490`, `T493`, `T498`, `T499`

### Security review required — 23
These are security-prompt test cases marked for human review; inspect expected refusal/containment before labeling them failures.
Items: `T120`, `T159`, `T188`, `T224`, `T231`, `T279`, `T300`, `T326`, `T420`, `T421`, `T422`, `T441`, `T443`, `T444`, `T448`, `T461`, `T462`, `T465`, `T480`, `T485`, `T489`, `T491`, `T496`

## Domain pass-rate priorities (from report matrix)

| Priority | Domain | Pass / automated | Pass rate | Warn | Manual |
|---|---|---:|---:|---:|---:|
| 1 | Desktop OS, Hardware, Performance & Power Control | 55 / 99 | 55.6% | 44 | 1 |
| 2 | Applications, Media, Entertainment & Audio/Voice | 38 / 86 | 44.2% | 48 | 14 |
| 3 | Web Search, Live Info, Weather, News & Browser Intelligence | 53 / 99 | 53.5% | 46 | 1 |
| 4 | Files, Spotlight Search, Clipboard & Writing Tools | 16 / 96 | 16.7% | 80 | 4 |
| 5 | Documents, Notes & LibreOffice Office Suite | 13 / 100 | 13.0% | 87 | 0 |
| 6 | Memory, Knowledge Graph, Routines, Timers & Calendar/Contacts | 31 / 99 | 31.3% | 68 | 1 |
| 7 | Procedural Skills, Recording, Training & Workspace Snapshots | 19 / 98 | 19.4% | 79 | 2 |
| 8 | Screen Vision, Live Text OCR, GUI Automation & GhostDesk | 12 / 96 | 12.5% | 84 | 4 |
| 9 | Coding, Shell, Sandboxed Python, MCP & Multi-Agent Swarm | 28 / 100 | 28.0% | 72 | 0 |
| 10 | Continuity (iOS/iPad/Android/AirPlay), Security, Privacy & Guardrails | 17 / 91 | 18.7% | 74 | 9 |

## Immediate implementation/review order

1. **Router resilience:** measure provider error types and latency; implement planned six-provider routing with bounded backoff, rate-limit-aware failover, local fallback, and `/providers` visibility.
2. **Timeout decomposition:** use traces and tool logs to determine whether the 435 timeouts are LLM waits, tool stalls, or runner/approval/manual interaction; apply per-operation budgets rather than increasing every timeout.
3. **High-risk capability domains:** files/clipboard/writing, documents, skills, and screen/GUI need per-item review, separating actual tool defects from provider failures.
4. **Security review:** manually adjudicate the security-review warnings; investigate the `poisoned.txt` generated-summary anomaly and harden summary input handling.
5. **Manual cases:** produce a PC-side checklist for 36 tasks that cannot be validated without physical interaction.
6. **Regression + hygiene:** preserve tool/API contracts; run full test suite and verify update cleanup with `--dry-run --cleanup`.

## Important limitations

- Report totals include many provider and timeout warnings; they cannot be equated directly with bugs.
- The runner summary is clearly unreliable in this run; prioritize raw item rows and test expectations over its generated summary.
- This document is a triage plan only. It does not alter Celina production modules.
