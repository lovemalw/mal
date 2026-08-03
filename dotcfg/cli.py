"""Command-line interface for dotcfg.

Provides ``dotcfg check`` and ``dotcfg diff`` commands.

Usage::

    $ dotcfg check .env --schema myapp.schema
    $ dotcfg diff .env .env.production
    $ dotcfg template --output .env.example
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import Dict, Optional, TextIO

from .core import _parse_content
from .vault import SecretVault


def main(argv: Optional[list] = None) -> int:
    """Entry point for the dotcfg CLI."""
    parser = argparse.ArgumentParser(
        prog="dotcfg",
        description="Environment file utilities — validate, diff, and manage .env files.",
    )
    sub = parser.add_subparsers(dest="command")

    check_p = sub.add_parser("check", help="Validate a .env file")
    check_p.add_argument("file", nargs="?", default=".env", help="Path to .env file")
    check_p.add_argument("--strict", action="store_true", help="Fail on warnings too")

    diff_p = sub.add_parser("diff", help="Compare two .env files")
    diff_p.add_argument("file_a", help="First .env file")
    diff_p.add_argument("file_b", help="Second .env file")
    diff_p.add_argument("--mask", action="store_true", help="Mask sensitive values")

    keys_p = sub.add_parser("keys", help="List all keys in a .env file")
    keys_p.add_argument("file", nargs="?", default=".env", help="Path to .env file")
    keys_p.add_argument("--sort", action="store_true", help="Sort alphabetically")

    args = parser.parse_args(argv)

    if args.command == "check":
        return _cmd_check(args)
    elif args.command == "diff":
        return _cmd_diff(args)
    elif args.command == "keys":
        return _cmd_keys(args)
    else:
        parser.print_help()
        return 0


def _cmd_check(args: argparse.Namespace) -> int:
    """Validate a .env file for common issues."""
    path = Path(args.file)

    if not path.exists():
        print(f"Error: {path} not found", file=sys.stderr)
        return 1

    content = path.read_text(encoding="utf-8")
    parsed = _parse_content(content)

    issues = []
    for line_num, line in enumerate(content.splitlines(), 1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if "=" not in stripped and not stripped.startswith("export"):
            issues.append((line_num, "WARN", f"Line has no '=': {stripped[:50]}"))

    for key, value in parsed.items():
        if not value:
            issues.append((0, "WARN", f"{key} is empty"))
        if " " in key:
            issues.append((0, "ERROR", f"Key contains spaces: {key!r}"))

    if not issues:
        print(f"OK: {path} ({len(parsed)} variables)")
        return 0

    errors = [i for i in issues if i[1] == "ERROR"]
    warnings = [i for i in issues if i[1] == "WARN"]

    for line_num, level, msg in issues:
        loc = f":{line_num}" if line_num else ""
        print(f"  [{level}] {path}{loc}: {msg}")

    print(f"\n{len(errors)} errors, {len(warnings)} warnings")
    if errors or (args.strict and warnings):
        return 1
    return 0


def _cmd_diff(args: argparse.Namespace) -> int:
    """Compare two .env files."""
    path_a, path_b = Path(args.file_a), Path(args.file_b)

    for p in (path_a, path_b):
        if not p.exists():
            print(f"Error: {p} not found", file=sys.stderr)
            return 1

    env_a = _parse_content(path_a.read_text(encoding="utf-8"))
    env_b = _parse_content(path_b.read_text(encoding="utf-8"))

    vault = SecretVault() if args.mask else None

    keys_a = set(env_a.keys())
    keys_b = set(env_b.keys())

    only_a = sorted(keys_a - keys_b)
    only_b = sorted(keys_b - keys_a)
    common = sorted(keys_a & keys_b)
    changed = [(k, env_a[k], env_b[k]) for k in common if env_a[k] != env_b[k]]

    if not only_a and not only_b and not changed:
        print("Files are identical.")
        return 0

    if only_a:
        print(f"Only in {path_a.name}:")
        for k in only_a:
            v = vault.mask(k, env_a[k]) if vault else env_a[k]
            print(f"  - {k}={v}")
        print()

    if only_b:
        print(f"Only in {path_b.name}:")
        for k in only_b:
            v = vault.mask(k, env_b[k]) if vault else env_b[k]
            print(f"  + {k}={v}")
        print()

    if changed:
        print("Changed:")
        for k, va, vb in changed:
            if vault:
                va, vb = vault.mask(k, va), vault.mask(k, vb)
            print(f"  ~ {k}: {va!r} -> {vb!r}")

    return 0


def _cmd_keys(args: argparse.Namespace) -> int:
    """List all keys in a .env file."""
    path = Path(args.file)

    if not path.exists():
        print(f"Error: {path} not found", file=sys.stderr)
        return 1

    content = path.read_text(encoding="utf-8")
    parsed = _parse_content(content)
    keys = sorted(parsed.keys()) if args.sort else list(parsed.keys())

    for k in keys:
        print(k)

    return 0


if __name__ == "__main__":
    sys.exit(main())
