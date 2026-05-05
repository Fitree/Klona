#!/usr/bin/env python3
"""KLONA agent installer router."""

from __future__ import annotations

import argparse


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Install or uninstall KLONA agent integrations."
    )
    parser.add_argument(
        "--platform",
        required=True,
        choices=["opencode"],
        help="Target agent platform. Phase 1 supports only 'opencode'.",
    )
    parser.add_argument(
        "--uninstall",
        action="store_true",
        help="Remove KLONA-owned files and config for the selected platform.",
    )
    parser.add_argument(
        "--klona-memory-server-url",
        dest="mcp_url",
        help="Klona high-level memory MCP URL for non-interactive install.",
    )
    parser.add_argument(
        "--klona-memory-server-token",
        dest="mcp_token",
        help="Klona high-level memory bearer token for non-interactive install.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    if args.platform == "opencode":
        from klona_agent.opencode import install as opencode_installer

        if args.uninstall:
            opencode_installer.uninstall()
        else:
            opencode_installer.install(mcp_url=args.mcp_url, mcp_token=args.mcp_token)
        return 0

    raise AssertionError(f"unsupported platform reached argparse dispatch: {args.platform}")


if __name__ == "__main__":
    raise SystemExit(main())
