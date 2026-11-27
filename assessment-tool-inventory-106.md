# Celina 106-Tool Inventory (assessment 2026-11-27)

Source: features/celina/config/neural-schema.json + approval.py TOOL_RISK_MAP + tests/ mentions + code inspection. Gate = tools.py:5460 execute_tool (T1 ∩ READ_ONLY ∩ non-approval-map required). "tested-working (CI mention)" means the tool name appears in this repo's test suite and CI is green on 6d4d9ae — it does NOT mean live end-to-end execution was observed in this environment (tests importing celina_pkg.tools cannot run on the sandbox's Python 3.11).

| # | domain | tool | tier | risk | gate | classification | notes |
|---:|---|---|---|---|---|---|---|
| 1 | apple_pim_writing | `create_calendar_event` | T2 | FILE_WRITE | DENIED | disabled-fail-closed (denied tests asserted) |  |
| 2 | apple_pim_writing | `get_upcoming_events` | T1 | READ_ONLY | PASS | tested-working (CI mention) |  |
| 3 | apple_pim_writing | `live_text_ocr` | T1 | LOCAL_MUTATION | DENIED | disabled-via-gate; DIRECT-PATH-EXISTS | bypass: island_ui.py:789 via writing_tools.live_text_ocr_region direct |
| 4 | apple_pim_writing | `prepare_meeting_context` | T1 | READ_ONLY | PASS | tested-working (CI mention) |  |
| 5 | apple_pim_writing | `search_active_app_menus` | T1 | READ_ONLY | PASS | tested-working (CI mention) |  |
| 6 | apple_pim_writing | `search_browser_bookmarks` | T1 | READ_ONLY | PASS | tested-working (CI mention) |  |
| 7 | apple_pim_writing | `search_contacts` | T1 | READ_ONLY | PASS | tested-working (CI mention) |  |
| 8 | apple_pim_writing | `spotlight_query` | T1 | READ_ONLY | PASS | tested-working (CI mention) |  |
| 9 | apple_pim_writing | `writing_tool_transform` | T1 | LOCAL_MUTATION | DENIED | disabled-fail-closed (denied tests asserted) |  |
| 10 | code_exec | `code_sandbox` | T2 | SHELL_HOST | DENIED | disabled-fail-closed (denied tests asserted) |  |
| 11 | code_exec | `grep_code` | T1 | READ_ONLY | PASS | tested-working (CI mention) |  |
| 12 | code_exec | `list_files` | T1 | READ_ONLY | PASS | tested-working (CI mention) |  |
| 13 | code_exec | `read_function_signatures` | T1 | READ_ONLY | PASS | tested-working (CI mention) |  |
| 14 | code_exec | `read_lines` | T1 | READ_ONLY | PASS | tested-working (CI mention) |  |
| 15 | continuity_audio_privacy | `audit_app_permissions` | T1 | READ_ONLY | PASS | tested-working (CI mention) |  |
| 16 | continuity_audio_privacy | `control_airplay_mirror` | T2 | LOCAL_MUTATION | DENIED | disabled-fail-closed (denied tests asserted) |  |
| 17 | continuity_audio_privacy | `get_continuity_status` | T1 | READ_ONLY | PASS | tested-working (CI mention) |  |
| 18 | continuity_audio_privacy | `get_hardware_capabilities` | T1 | READ_ONLY | PASS | tested-working (CI mention) |  |
| 19 | continuity_audio_privacy | `get_secret_vault_status` | T1 | READ_ONLY | PASS | tested-working (CI mention) |  |
| 20 | continuity_audio_privacy | `launch_android_mirror` | T2 | LOCAL_MUTATION | DENIED | disabled-fail-closed (denied tests asserted) |  |
| 21 | continuity_audio_privacy | `mount_ios_device` | T2 | LOCAL_MUTATION | DENIED | disabled-fail-closed (denied tests asserted) |  |
| 22 | continuity_audio_privacy | `respond_ios_notification` | T2 | LOCAL_MUTATION | DENIED | disabled-fail-closed (denied tests asserted) |  |
| 23 | continuity_audio_privacy | `route_app_audio` | T1 | LOCAL_MUTATION | DENIED | disabled-fail-closed (denied tests asserted) |  |
| 24 | continuity_audio_privacy | `set_focus_mode` | T1 | LOCAL_MUTATION | DENIED | disabled-fail-closed (denied tests asserted) |  |
| 25 | continuity_audio_privacy | `start_ios_continuity_camera` | T2 | LOCAL_MUTATION | DENIED | disabled-fail-closed (denied tests asserted) | hardware-dependent? |
| 26 | continuity_audio_privacy | `start_ipad_sidecar_weylus` | T2 | LOCAL_MUTATION | DENIED | disabled-fail-closed (denied tests asserted) |  |
| 27 | continuity_audio_privacy | `transcribe_system_audio` | T2 | SECURITY_SENSITIVE | DENIED | disabled-fail-closed (denied tests asserted) |  |
| 28 | files_office | `convert_document` | T2 | FILE_WRITE | DENIED | disabled-fail-closed (denied tests asserted) |  |
| 29 | files_office | `create_document` | T2 | FILE_WRITE | DENIED | disabled-fail-closed (denied tests asserted) |  |
| 30 | files_office | `edit_document` | T2 | FILE_WRITE | DENIED | disabled-fail-closed (denied tests asserted) |  |
| 31 | files_office | `edit_file` | T2 | FILE_WRITE | DENIED | disabled-fail-closed (denied tests asserted) |  |
| 32 | files_office | `libreoffice_edit` | T2 | FILE_WRITE | DENIED | disabled-fail-closed (denied tests asserted) |  |
| 33 | files_office | `move_file` | T2 | FILE_WRITE | DENIED | disabled-fail-closed (denied tests asserted) |  |
| 34 | files_office | `open_document` | T1 | LOCAL_MUTATION | DENIED | disabled-fail-closed (denied tests asserted) |  |
| 35 | files_office | `read_document` | T1 | READ_ONLY | PASS | tested-working (CI mention) |  |
| 36 | files_office | `read_file` | T1 | READ_ONLY | PASS | tested-working (CI mention) |  |
| 37 | files_office | `search_files` | T1 | READ_ONLY | PASS | tested-working (CI mention) |  |
| 38 | files_office | `search_in_files` | T1 | READ_ONLY | PASS | tested-working (CI mention) |  |
| 39 | files_office | `trash_file` | T2 | FILE_WRITE | DENIED | disabled-fail-closed (denied tests asserted) |  |
| 40 | files_office | `write_file` | T2 | FILE_WRITE | DENIED | disabled-fail-closed (denied tests asserted) |  |
| 41 | mcp_perception_actuation | `computer_click` | T2 | DESKTOP_INPUT | DENIED | disabled-fail-closed (denied tests asserted) | ghostdesk/MCP + vision models |
| 42 | mcp_perception_actuation | `computer_type` | T2 | DESKTOP_INPUT | DENIED | disabled-fail-closed (denied tests asserted) | ghostdesk/MCP + vision models |
| 43 | mcp_perception_actuation | `ghostdesk_click` | T2 | DESKTOP_INPUT | DENIED | disabled-fail-closed (denied tests asserted) | ghostdesk/MCP + vision models |
| 44 | mcp_perception_actuation | `vision_analyze` | T2 | SECURITY_SENSITIVE | DENIED | disabled-fail-closed (denied tests asserted) | ghostdesk/MCP + vision models |
| 45 | mcp_perception_actuation | `vision_ocr` | T2 | SECURITY_SENSITIVE | DENIED | disabled-fail-closed (denied tests asserted) | ghostdesk/MCP + vision models |
| 46 | mcp_system | `cache_status` | T1 | READ_ONLY | PASS | tested-working (CI mention) | external MCP servers |
| 47 | mcp_system | `clear_cache` | T1 | LOCAL_MUTATION | DENIED | disabled-fail-closed (denied tests asserted) | external MCP servers |
| 48 | mcp_system | `mcp_call_tool` | T2 | NETWORK_REQUEST | DENIED | disabled-fail-closed (denied tests asserted) | external MCP servers |
| 49 | mcp_system | `mcp_list_servers` | T1 | READ_ONLY | PASS | tested-working (CI mention) | external MCP servers |
| 50 | mcp_system | `mcp_list_tools` | T1 | READ_ONLY | PASS | tested-working (CI mention) | external MCP servers |
| 51 | mcp_system | `run_python_sandbox` | T2 | SHELL_HOST | DENIED | disabled-fail-closed (denied tests asserted) | external MCP servers; bwrap kernel req |
| 52 | mcp_system | `run_shell_command` | T2 | SHELL_HOST | DENIED | disabled-fail-closed (denied tests asserted) | external MCP servers; bwrap kernel req |
| 53 | mcp_system | `submit_goal` | T2 | LOCAL_MUTATION | DENIED | disabled-fail-closed (denied tests asserted) | external MCP servers |
| 54 | mcp_system | `trigger_iot_webhook` | T2 | NETWORK_REQUEST | DENIED | disabled-fail-closed (denied tests asserted) | external MCP servers |
| 55 | os_hardware | `close_application` | T2 | LOCAL_MUTATION | DENIED | disabled-fail-closed (denied tests asserted) |  |
| 56 | os_hardware | `control_connectivity` | T1 | LOCAL_MUTATION | DENIED | disabled-fail-closed (denied tests asserted) |  |
| 57 | os_hardware | `control_media` | T1 | LOCAL_MUTATION | DENIED | disabled-via-gate; DIRECT-PATH-EXISTS | bypass: island_ui.py:1296 direct call |
| 58 | os_hardware | `diagnose_systemd_service` | T1 | READ_ONLY | PASS | tested-working (CI mention) |  |
| 59 | os_hardware | `get_clipboard` | T1 | READ_ONLY | PASS | tested-working (CI mention) |  |
| 60 | os_hardware | `get_highlighted_text` | T1 | READ_ONLY | PASS | tested-working (CI mention) |  |
| 61 | os_hardware | `get_system_stats` | T1 | READ_ONLY | PASS | tested-working (CI mention) |  |
| 62 | os_hardware | `list_running_apps` | T1 | READ_ONLY | PASS | implemented-unverified |  |
| 63 | os_hardware | `manage_default_apps` | T2 | LOCAL_MUTATION | DENIED | disabled-fail-closed (denied tests asserted) |  |
| 64 | os_hardware | `open_application` | T1 | LOCAL_MUTATION | DENIED | disabled-fail-closed (denied tests asserted) |  |
| 65 | os_hardware | `set_brightness` | T1 | LOCAL_MUTATION | DENIED | disabled-fail-closed (denied tests asserted) | hardware-dependent? |
| 66 | os_hardware | `set_clipboard` | T1 | LOCAL_MUTATION | DENIED | disabled-fail-closed (denied tests asserted) |  |
| 67 | os_hardware | `set_timer` | T1 | LOCAL_MUTATION | DENIED | disabled-fail-closed (denied tests asserted) |  |
| 68 | os_hardware | `set_volume` | T1 | LOCAL_MUTATION | DENIED | disabled-fail-closed (denied tests asserted) | hardware-dependent? |
| 69 | os_hardware | `toggle_app_nap` | T1 | LOCAL_MUTATION | DENIED | disabled-fail-closed (denied tests asserted) |  |
| 70 | skill_security | `ponytail_evaluate` | T2 | LOCAL_MUTATION | DENIED | disabled-fail-closed (denied tests asserted) |  |
| 71 | skill_security | `skilspector_scan` | T2 | SECURITY_SENSITIVE | DENIED | disabled-fail-closed (denied tests asserted) |  |
| 72 | skills_memory | `approve_quarantined_skill` | T2 | SECURITY_SENSITIVE | DENIED | disabled-fail-closed (denied tests asserted) |  |
| 73 | skills_memory | `forget_skill` | T1 | LOCAL_MUTATION | DENIED | disabled-via-gate; DIRECT-PATH-EXISTS | bypass: router heal path (heal_skill_from_last_trace) |
| 74 | skills_memory | `install_community_skill` | T2 | NETWORK_REQUEST | DENIED | disabled-fail-closed (denied tests asserted) |  |
| 75 | skills_memory | `learn_skill` | T1 | LOCAL_MUTATION | DENIED | disabled-via-gate; DIRECT-PATH-EXISTS | bypass: tools.py:2604 inside save_workspace_snapshot (itself gate-denied); router heal shortcut |
| 76 | skills_memory | `link_knowledge` | T1 | LOCAL_MUTATION | DENIED | disabled-fail-closed (denied tests asserted) |  |
| 77 | skills_memory | `list_quarantined_skills` | T1 | READ_ONLY | PASS | tested-working (CI mention) |  |
| 78 | skills_memory | `list_skills` | T1 | READ_ONLY | PASS | implemented-unverified |  |
| 79 | skills_memory | `manage_goal` | T1 | LOCAL_MUTATION | DENIED | disabled-fail-closed (denied tests asserted) |  |
| 80 | skills_memory | `reject_quarantined_skill` | T1 | LOCAL_MUTATION | DENIED | disabled-fail-closed; untested |  |
| 81 | skills_memory | `remember_fact` | T1 | LOCAL_MUTATION | DENIED | disabled-fail-closed (denied tests asserted) |  |
| 82 | skills_memory | `run_skill` | T2 | DESKTOP_INPUT | DENIED | disabled-fail-closed (denied tests asserted) |  |
| 83 | skills_memory | `save_last_actions_as_skill` | T1 | LOCAL_MUTATION | DENIED | disabled-via-gate; DIRECT-PATH-EXISTS | bypass: router.py:3026 shortcut, no approval_cb |
| 84 | skills_memory | `save_workspace_snapshot` | T1 | LOCAL_MUTATION | DENIED | disabled-fail-closed (denied tests asserted) |  |
| 85 | skills_memory | `search_memory` | T1 | READ_ONLY | PASS | tested-working (CI mention) |  |
| 86 | skills_memory | `set_assistant_identity` | T1 | LOCAL_MUTATION | DENIED | disabled-fail-closed (denied tests asserted) |  |
| 87 | skills_memory | `start_recording` | T1 | LOCAL_MUTATION | DENIED | disabled-fail-closed; untested |  |
| 88 | skills_memory | `stop_recording` | T1 | LOCAL_MUTATION | DENIED | disabled-fail-closed; untested |  |
| 89 | skills_memory | `update_skill` | T1 | LOCAL_MUTATION | DENIED | disabled-via-gate; DIRECT-PATH-EXISTS | bypass: router heal path |
| 90 | wayland_gui | `autonomous_gui_task` | T2 | DESKTOP_INPUT | DENIED | disabled-fail-closed (denied tests asserted) |  |
| 91 | wayland_gui | `click_on_text` | T2 | DESKTOP_INPUT | DENIED | disabled-fail-closed (denied tests asserted) |  |
| 92 | wayland_gui | `click_position` | T2 | DESKTOP_INPUT | DENIED | disabled-fail-closed (denied tests asserted) |  |
| 93 | wayland_gui | `drag_and_drop` | T2 | DESKTOP_INPUT | DENIED | disabled-fail-closed (denied tests asserted) |  |
| 94 | wayland_gui | `find_text_on_screen` | T1 | READ_ONLY | PASS | implemented-unverified |  |
| 95 | wayland_gui | `inspect_ui_elements` | T1 | READ_ONLY | PASS | tested-working (CI mention) |  |
| 96 | wayland_gui | `press_hotkey` | T2 | DESKTOP_INPUT | DENIED | disabled-fail-closed; untested |  |
| 97 | wayland_gui | `read_screen` | T1 | READ_ONLY | PASS | tested-working (CI mention) |  |
| 98 | wayland_gui | `scroll_screen` | T2 | DESKTOP_INPUT | DENIED | disabled-fail-closed; untested |  |
| 99 | wayland_gui | `type_text` | T2 | DESKTOP_INPUT | DENIED | disabled-fail-closed (denied tests asserted) |  |
| 100 | web_comms | `compose_message` | T2 | MESSAGE_SEND | DENIED | disabled-fail-closed (denied tests asserted) | network + OPT_IN (5 tools) |
| 101 | web_comms | `fetch_url_text` | T1 | NETWORK_REQUEST | DENIED | disabled-fail-closed (denied tests asserted) | network + OPT_IN (5 tools); OPT_IN_TOOLS |
| 102 | web_comms | `fetch_webpage` | T1 | NETWORK_REQUEST | DENIED | disabled-fail-closed (denied tests asserted) | network + OPT_IN (5 tools); OPT_IN_TOOLS |
| 103 | web_comms | `get_current_time_zone` | T1 | NETWORK_REQUEST | DENIED | disabled-fail-closed (denied tests asserted) | network + OPT_IN (5 tools); OPT_IN_TOOLS |
| 104 | web_comms | `get_news_headlines` | T1 | NETWORK_REQUEST | DENIED | disabled-fail-closed (denied tests asserted) | network + OPT_IN (5 tools); OPT_IN_TOOLS |
| 105 | web_comms | `get_weather` | T1 | NETWORK_REQUEST | DENIED | disabled-fail-closed (denied tests asserted) | network + OPT_IN (5 tools); OPT_IN_TOOLS |
| 106 | web_comms | `web_search` | T1 | NETWORK_REQUEST | DENIED | disabled-fail-closed (denied tests asserted) | network + OPT_IN (5 tools) |

## Summary

- disabled-fail-closed: 63
- tested-working: 29
- disabled-via-gate; DIRECT-PATH-EXISTS: 6
- disabled-fail-closed; untested: 5
- implemented-unverified: 3
- TOTAL: 106
