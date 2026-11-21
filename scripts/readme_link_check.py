#!/usr/bin/env python3
"""Verify every relative link target in README.md exists on disk."""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
text = (ROOT / "README.md").read_text(encoding="utf-8")
links = re.findall(r"\]\(([^)#\s]+)(?:#[^)]*)?\)", text)
missing = []
checked = 0
for link in links:
    if link.startswith(("http://", "https://", "mailto:")):
        continue
    checked += 1
    if not (ROOT / link).exists():
        missing.append(link)
print(f"relative links checked: {checked}")
if missing:
    print("MISSING:")
    for m in missing:
        print(" ", m)
    sys.exit(1)
print("ALL LINKS OK")
