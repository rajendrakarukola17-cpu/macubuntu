# Gate 4.7 release scope

## Release decisions

- **Target:** Ubuntu/Linux desktop. The features proposed by `docs/v4.5-roadmap.md` and `docs/v5-roadmap-cross-device-actuation.md` are deferred from this release; the roadmap documents are retained.
- **Mirage:** `gear0_5_1_mirage.py` must be disabled and fail closed for this release unless it is brought under the same authenticated signed-manifest policy as SmolLM. This note does not itself change or disable the backend.
- **Approval security:** Design work is authorized. A subsequent modification of protected `features/celina/celina_pkg/approval.py` is authorized only for approval-card binding and token-forgery fixes, and only after the operator explicitly approves the Prompt 2 design. Other protected files remain unchanged until separately authorized. Same-process code is not an independent security boundary.
- **Workflow:** Work on `gate47-hardening` in `/home/user/celina-gate47`. Do not modify `/home/user/macubuntu`, commit to `master`, or move `gate46-baseline` or `selfimprove-v1-complete`. Complete and commit each phase before moving to the next; stop for operator approval after the approval design, for real-host execution, and for integration-plan approval.

## Preserved original checkout

Before this scope commit, the original `master` checkout was at `1ba19961221dbc5801dc77b841fb2fc9b9476a2a`. Its uncommitted architecture content and 23 shell-file mode changes were inventoried without modifying the checkout. Read-only copies of the full `ARCHITECTURE.md`, its binary-compatible Git patch, mode-change manifest, status, original HEAD, and SHA-256 checksums were written to `/tmp/macubuntu-master-backup/` as requested. A second verified copy was saved to `/home/user/macubuntu-master-backup/` because `/tmp` is not part of the persisted workspace snapshot. Both directories are owner-read-only, not immutable: the owner can change their permissions. The 23 modes changed from `100755` to `100644`; their cause and intended resolution remain undecided.

Neither copy is an instruction to restore or overwrite files. Integration requires a separately approved plan preserving both the original edits and Gate 4.6 evidence. `selfimprove/protected_hashes.json` currently records exactly six core files; the manifest itself is separately protected. Any future change to this scheme needs explicit design and authorization, not an undocumented self-referential hash.
