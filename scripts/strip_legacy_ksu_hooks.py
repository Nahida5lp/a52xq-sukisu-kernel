#!/usr/bin/env python3
"""
Remove the legacy KernelSU/ReSukiSU static hook blocks from the Samsung a52xq
kernel tree before integrating official SukiSU Ultra (v4.x "main" line).

Why: official SukiSU Ultra v4.x needs NO kernel source hooks - it installs all
of its hooks at runtime (kprobes + in-driver syscall-table patching:
hook/arm64/syscall_hook.c, hook/arm64/patch_memory.c, runtime/ksud_integration.c).
The a52xq tree still carries the old manual hook blocks ("ksu_handle_stat",
"ksu_handle_faccessat", "ksu_handle_execveat", "ksu_input_hook",
"ksu_vfs_read_hook", ...). Those symbols do not exist in the official driver
(or have a different signature), so with CONFIG_KSU=y the build would fail with
undefined symbol / type errors. This script deletes exactly those blocks.

Usage: strip_legacy_ksu_hooks.py <kernel_tree_root>
"""

import re
import sys
from pathlib import Path

# Files that carry legacy hook blocks (verified against OneUI8-dev).
FILES = [
    "fs/exec.c",
    "fs/open.c",
    "fs/read_write.c",
    "fs/stat.c",
    "drivers/input/input.c",
    # cleaned too, in case a future branch adds blocks there:
    "kernel/reboot.c",
    "kernel/sys.c",
    "security/selinux/selinuxfs.c",
]

IFDEF_KSU = re.compile(r"^\s*#\s*ifdef\s+CONFIG_KSU\b")
ANY_IF = re.compile(r"^\s*#\s*if(?:def|ndef)?\b")
ENDIF = re.compile(r"^\s*#\s*endif\b")
ELSE = re.compile(r"^\s*#\s*else\b")

# Any occurrence of these after stripping means the strip was incomplete.
LEGACY_SYMBOLS = [
    "ksu_handle_stat",
    "ksu_handle_faccessat",
    "ksu_handle_execveat",
    "ksu_handle_sys_read",
    "ksu_handle_sys_reboot",
    "ksu_handle_input_handle_event",
    "ksu_input_hook",
    "ksu_execveat_hook",
    "ksu_vfs_read_hook",
]


def strip_blocks(text: str):
    """Delete every top-level '#ifdef CONFIG_KSU ... #endif' block."""
    lines = text.splitlines(keepends=True)
    out, i, removed_blocks, removed_lines = [], 0, 0, 0
    while i < len(lines):
        if IFDEF_KSU.match(lines[i]):
            removed_blocks += 1
            depth = 0
            while i < len(lines):
                line = lines[i]
                if ANY_IF.match(line):
                    depth += 1
                elif ELSE.match(line):
                    # #else of the CONFIG_KSU block itself: keep depth
                    pass
                elif ENDIF.match(line):
                    depth -= 1
                    removed_lines += 1
                    i += 1
                    if depth == 0:
                        break
                    continue
                removed_lines += 1
                i += 1
            continue
        out.append(lines[i])
        i += 1
    return "".join(out), removed_blocks, removed_lines


def main() -> int:
    if len(sys.argv) < 2:
        print("usage: strip_legacy_ksu_hooks.py <kernel_tree_root>")
        return 2
    root = Path(sys.argv[1]).resolve()
    if not (root / "Makefile").is_file():
        print(f"[!] {root} does not look like a kernel tree")
        return 2

    total_blocks = 0
    problems = []
    for rel in FILES:
        path = root / rel
        if not path.is_file():
            print(f"[=] {rel}: missing, skipped")
            continue
        src = path.read_text(encoding="utf-8", errors="surrogateescape")
        if "CONFIG_KSU" not in src:
            print(f"[=] {rel}: no legacy hooks")
            continue
        new, blocks, lines = strip_blocks(src)
        path.write_text(new, encoding="utf-8", errors="surrogateescape")
        total_blocks += blocks
        print(f"[+] {rel}: removed {blocks} CONFIG_KSU block(s), {lines} line(s)")
        for sym in LEGACY_SYMBOLS:
            if sym in new:
                problems.append(f"{rel}: leftover reference to {sym}")

    print(f"[i] total blocks removed: {total_blocks}")
    if problems:
        print("[!] incomplete strip:")
        for p in problems:
            print("    " + p)
        return 1
    print("[OK] no legacy KSU hook references left in patched files")
    return 0


if __name__ == "__main__":
    sys.exit(main())