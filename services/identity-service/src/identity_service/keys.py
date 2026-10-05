"""Lối vào CLI: `python -m identity_service.keys list|rotate` (logic ở tokens/keys.py)."""

from identity_service.tokens.keys import main

if __name__ == "__main__":
    import sys
    sys.exit(main())
