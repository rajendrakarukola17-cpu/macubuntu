# `macubuntu` v5 Architectural Roadmap — Cross-Device Hub-and-Spoke Actuation

> **Status:** Planned for **v5.0** (intentionally NOT shipped in v4.4).
> In **v4.4**, cross-device workflows are human-operated via Tier A open protocols (`LocalSend`, `GSConnect`, `Deskflow`, `Syncthing`, `UxPlay`, `Shairport-Sync`).

---

## 1. Why Cross-Device Actuation Is Scoped to v5

Celina v4.4 is an **OS-grounded single-node agent**: every Tier-2 action is bound to local SHA-256 argument hashes, local OS capabilities (`system/capabilities/celina-capabilities.json`), local pre-execution undo backups (`celina undo`), and a local hash-chained evidence ledger (`celina evidence`).

Extending single-intent orchestration across devices (for example, *"move this PDF to my phone and open it there"*) without weakening those guarantees requires solving three security primitives first:

1. **Mutual-TLS (mTLS) Device Pairing & Certificate Pinning:** No plaintext LAN RPCs or bearer tokens. Each paired node (laptop, secondary desktop, Android companion) holds an Ed25519 client/server certificate pinned out-of-band via QR/SAS verification (matching `GSConnect` / `Syncthing` trust ergonomics).
2. **Delegated Capability Attenuation:** A remote orchestrator node may NEVER exercise broader capabilities on a spoke node than the spoke's local `celina-capabilities.json` permits—and remote Tier-2 actions MUST still display a fresh confirmation prompt on the target device AND append to **both** nodes' hash-chained `evidence/ledger.jsonl`.
3. **Atomic OS Tasking via gRPC:** Where native REST/D-Bus APIs are unavailable on a spoke device, fine-grained actuation (file handoff, URI/document open, clipboard sync, semantic-blueprint UI action) runs over a strict protobuf schema with short-lived signed task leases (TTL <= 30s).

---

## 2. Proposed v5 Hub-and-Spoke gRPC Service Contract

```protobuf
syntax = "proto3";
package macubuntu.actuation.v1;

service NodeActuator {
  // Read-only capability & semantic blueprint discovery
  rpc GetNodeCapabilities(Empty) returns (NodeCapabilityReport);
  rpc GetActiveBlueprint(BlueprintRequest) returns (SemanticBlueprint);

  // Scoped, lease-bound cross-device actions (always require spoke-side policy check)
  rpc ReceiveAndOpenArtifact(ArtifactHandoffRequest) returns (ActuationReceipt);
  rpc InvokeBlueprintAction(BlueprintActionRequest) returns (ActuationReceipt);
}
```

- **Any node can act as Hub** for a given user turn; spoke nodes enforce their own local capability manifest, undo journal, and evidence chain independently.
- **Zero Apple ID risk:** All v5 cross-device actuation stays strictly on open mTLS + LAN/WireGuard transport—never touching proprietary Apple IDS/Push servers.
