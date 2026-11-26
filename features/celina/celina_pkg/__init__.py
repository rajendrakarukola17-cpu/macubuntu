"""Celina AI (Celly) — Hardened Built-In Assistant Subsystem for macubuntu."""

import os

# Enforce private file creation permissions (owner-only) on startup
try:
    os.umask(0o077)
except OSError:
    pass
