# Production-Safe iMessage Path on `macubuntu` (BlueBubbles Bridge vs. Tier B Research)

`macubuntu` intentionally does **not** ship reverse-engineered native Linux iMessage registration clients in Tier A (`install.sh`). Apple's Identity Services (IDS) and GrandSlam (GSA) enforce cryptographic hardware attestation and **ECID-bound device quotas** tied to genuine Apple Secure Enclave (SEP) silicon; direct Linux spoofing frequently triggers Apple ID challenges or lockouts.

---

## Recommended Production Option: BlueBubbles Bridge

If you need reliable day-to-day iMessage on Ubuntu 26.04 without risking your primary Apple ID:

| Option | Reliability & Account Safety | Notes |
|---|---|---|
| **1. BlueBubbles Server on Real Apple Hardware (Best)** | **High** — Uses a genuine Mac (e.g., used Mac mini M1/2014–2018 or spare MacBook) with real Apple hardware ECID/SEP attestation. | Install **BlueBubbles Server** on the Mac and the native **BlueBubbles Linux Desktop / Flatpak** client on `macubuntu`. Supports attachments, Tapbacks, typing indicators, and Private API features when configured on the host Mac. |
| **2. BlueBubbles Server in an Isolated macOS VM (e.g., Docker-OSX / QEMU-KVM)** | **Medium** — Much safer than raw Linux GSA spoofing, **still subject to Apple hardware-validation checks & Apple macOS EULA** (Apple's macOS license permits running macOS only on Apple-branded hardware). | If used strictly in a personal lab on Apple-licensed hardware or secondary test accounts: isolate VM networking, keep a stable SMBIOS/UUID, never cycle serials rapidly, and expose BlueBubbles only over Tailscale/WireGuard or LAN TLS — never unauthenticated on the public internet. |
| **3. Tier B Research Sandbox (`tools/experimental-deep-stack.sh`)** | **Research Only — Expect Flagging** | Strictly for studying AWDL (`owl`), AirDrop (`opendrop`), and Find My BLE (`FindMy.py`). Enforces a pre-flight GNOME keyring check to block accidental execution under a desktop profile containing a primary Apple ID. **Never use for daily iMessage.** |

---

## Security Checklist When Running BlueBubbles on `macubuntu`

1. **Zero Public Port Forwarding:** Connect Ubuntu ↔ BlueBubbles Server over **Tailscale / WireGuard** or encrypted LAN rather than opening raw TCP ports on your router.
2. **Keep Primary Apple ID Off Tier B:** Do not run `tools/experimental-deep-stack.sh` under the same Linux user account or Apple ID used for BlueBubbles.
3. **Notification Integration:** BlueBubbles desktop notifications flow natively into GNOME Shell and Celina's Focus Mode / DND rules without requiring any elevated privileges.
