"""Celina AI CLI entry point (zipapp-compatible).

Invokes CelinaRouter.main() from router.py.
Usage: python3 -m celina_pkg or ./celina.pyz
"""
from __future__ import annotations

from .router import main

if __name__ == "__main__":
    main()
