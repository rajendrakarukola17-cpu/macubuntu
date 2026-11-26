# Celina Update 21 — Latency Baseline

**N:** 2000  **Warmup:** 200  **Generated:** 3.13.18

## Environment

- **python:** 3.13.18
- **platform:** Linux-6.1.189+-x86_64-with-glibc2.41
- **processor:** 
- **session_type:** unknown
- **container:** False
- **cpu_count:** 2
- **celina_root:** /home/user/macubuntu/features/celina
- **khal_installed:** False
- **calcurse_installed:** False
- **pactl_installed:** False
- **psutil_installed:** True
- **pulsectl_installed:** False
- **icalendar_installed:** False
- **recurring_ical_events_installed:** False

## Fixtures

- **ics_events:** 500
- **habit_rows:** 10000
- **csv_rows:** 100000
- **self_heal_forced_failure:** True

## Results (ms)

| Case | Cold p50 | Cold p99 | Warm p50 | Warm p99 | Max | Stdev |
|------|----------|----------|----------|----------|-----|-------|
| dispatch_hit::gear0_utils | 0.0949 | 0.265 | 0.0907 | 0.2705 | 3.967 | 0.2229 |
| dispatch_hit::gear0_intent | 0.062 | 0.6895 | 0.0588 | 0.5592 | 3.505 | 0.2103 |
| dispatch_hit::gear0_graph | 0.4566 | 0.6919 | 0.4726 | 0.6957 | 1.0883 | 0.0633 |
| dispatch_hit::gear0_contacts | 0.4527 | 0.8852 | 0.4023 | 1.0149 | 1.7114 | 0.1572 |
| dispatch_hit::gear0_calendar | 0.1626 | 0.2612 | 0.1686 | 0.3474 | 0.5595 | 0.0392 |
| dispatch_hit::gear0_network | 0.3778 | 0.6798 | 0.3456 | 1.1166 | 2.4404 | 0.1548 |
| dispatch_hit::gear0_hardware | 0.1424 | 0.5077 | 0.1648 | 0.6045 | 1.755 | 0.1099 |
| dispatch_hit::gear0_ledger | 0.4804 | 1.5349 | 0.3472 | 1.1625 | 2.9 | 0.1661 |
| dispatch_hit::gear0_macro | 0.1162 | 0.1857 | 0.1177 | 0.1873 | 0.4382 | 0.0181 |
| dispatch_hit::gear0_lists | 0.5298 | 1.3953 | 0.4774 | 0.994 | 2.4448 | 0.1608 |
| dispatch_hit::gear0_routines | 11.4583 | 16.2685 | 11.6535 | 17.6256 | 24.6966 | 1.6254 |
| dispatch_hit::gear0_media | 0.127 | 1.0151 | 0.1251 | 1.0108 | 3.6714 | 0.251 |
| dispatch_hit::gear0_focus | 0.1271 | 0.2324 | 0.1291 | 0.1853 | 0.407 | 0.0148 |
| dispatch_hit::gear0_clipboard | 0.1017 | 0.151 | 0.1026 | 0.1518 | 0.3319 | 0.0125 |
| dispatch_hit::gear0_audit | 0.1229 | 0.1922 | 0.1205 | 0.1924 | 0.7188 | 0.0216 |
| dispatch_hit::gear0_dual | 0.1739 | 0.2725 | 0.1771 | 0.2886 | 0.5768 | 0.0362 |
| dispatch_hit::gear0_status | 0.271 | 0.4417 | 0.2914 | 0.4832 | 0.8729 | 0.0448 |
| dispatch_hit::gear0_iot | 0.522 | 0.8691 | 0.5295 | 0.8114 | 1.0885 | 0.0684 |
| dispatch_hit::gear0_context | 0.118 | 0.9723 | 0.1173 | 0.324 | 0.6238 | 0.0504 |
| dispatch_hit::gear0_5_micro | 0.0603 | 0.6384 | 0.0611 | 0.6921 | 3.2734 | 0.2133 |
| dispatch_hit::gear0_voice | 0.4178 | 0.6359 | 0.3611 | 0.5325 | 0.7067 | 0.0544 |
| dispatch_hit::gear0_memory | 0.3779 | 0.5382 | 0.381 | 0.6328 | 0.8017 | 0.0601 |
| dispatch_hit::gear0_ml_nlu | 0.3698 | 0.6008 | 0.4251 | 0.6506 | 1.0292 | 0.0811 |
| dispatch_hit::gear0_brain | 0.3899 | 0.8819 | 0.3561 | 0.8015 | 1.5408 | 0.1374 |
| dispatch_hit::gear0_slots | 0.3843 | 0.6568 | 0.3837 | 0.6866 | 1.717 | 0.0819 |
| dispatch_hit::plugin1_sniffer | 0.584 | 1.0049 | 0.6321 | 0.7923 | 1.0541 | 0.0658 |
| dispatch_hit::plugin2_habit | 0.4662 | 0.6328 | 0.4587 | 0.6546 | 1.2422 | 0.0674 |
| dispatch_hit::plugin3_data_oracle | 0.5279 | 0.7209 | 0.4816 | 0.794 | 1.0573 | 0.0897 |
| dispatch_hit::plugin4_os_shim | 0.4383 | 0.6177 | 0.4247 | 0.6642 | 1.5576 | 0.0763 |
| dispatch_hit::plugin5_self_heal | 0.3867 | 0.5155 | 0.3986 | 0.5672 | 0.9901 | 0.0483 |
| dispatch_miss | 0.4114 | 0.5478 | 0.4124 | 0.5615 | 0.7624 | 0.0433 |

## Per-block probe cost (ms)

| Module | p50 | p99 |
|--------|-----|-----|
| gear0_utils | 0.0028 | 0.0067 |
| gear0_intent | 0.0008 | 0.0012 |
| gear0_graph | 0.0017 | 0.0022 |
| gear0_contacts | 0.0007 | 0.0011 |
| gear0_calendar | 0.0005 | 0.0008 |
| gear0_network | 0.0005 | 0.0008 |
| gear0_hardware | 0.0007 | 0.001 |
| gear0_ledger | 0.0006 | 0.0008 |
| gear0_macro | 0.0004 | 0.0006 |
| gear0_lists | 0.0006 | 0.0008 |
| gear0_routines | 0.1675 | 0.3486 |
| gear0_media | 0.0004 | 0.0006 |
| gear0_focus | 0.0006 | 0.0008 |
| gear0_clipboard | 0.0005 | 0.0007 |
| gear0_audit | 0.0004 | 0.0007 |
| gear0_dual | 0.0005 | 0.0007 |
| gear0_status | 0.0004 | 0.0006 |
| gear0_iot | 0.0009 | 0.0095 |
| gear0_context | 0.0007 | 0.0013 |
| gear0_5_micro | 0.0001 | 0.0002 |
| gear0_ml_nlu | 0.009 | 0.0251 |
| plugin1_sniffer | 0.0005 | 0.0008 |
| plugin2_habit | 0.0005 | 0.0007 |
| plugin3_data_oracle | 0.0009 | 0.0013 |
| plugin4_os_shim | 0.0015 | 0.0024 |
| plugin5_self_heal | 0.0005 | 0.0007 |