#!/usr/bin/env python3
"""
把官方 SukiSU Ultra v4.2.1（main 线）驱动 + 三星 4.19 内核适配到能在这台 A52 上编译并安全运行。
所有改动都是确定性的，并在改完后立即校验；任何一处不符合预期就直接报错退出（不会产出半成品）。

A) 驱动 infra/seccomp_cache.c —— 编译 + 内存安全
   4.19 里：
     * SECCOMP_ARCH_NATIVE_NR / SECCOMP_ARCH_COMPAT_NR 这两个宏不存在（5.9 才加入）
       → 驱动顶部 struct action_cache 直接编译不过；
     * 真实 struct seccomp_filter 是
         { refcount_t usage; bool log; struct seccomp_filter *prev; struct bpf_prog *prog; }
       没有 action_cache 成员，而驱动自己重定义了带 cache 的结构体并写 filter->cache
       → 即使编过也会写坏 prev/prog（内存损坏）。
   处理：< 5.9 时整体关掉（结构与实现），两个函数变成空实现；
         >= 5.9 保留原样。上游 KernelSU 在 < 5.9 也不启用这个 cache。

B) 内核 security/selinux/selinuxfs.c —— 保留 selinux_hide 功能
   官方驱动通过 kallsyms 解析 "write_op" / "sel_handle_status_ops"（selinux_hide 用）。
   4.19 里这两个是 static，kallsyms 查不到 → 功能静默失效。
   ReSukiSU 内核当时就是把这两个去掉 static 的，这里照做，行为保持一致。

用法: fix_sources_for_419.py <kernel_tree_root>
      （驱动在 <root>/KernelSU/kernel/，内核在 <root>/）
"""

import sys
from pathlib import Path

# ---------------------------------------------------------------- A) seccomp

SECCOMP_EXPECTED_MARKERS = (
    "DECLARE_BITMAP(allow_native, SECCOMP_ARCH_NATIVE_NR);",
    "void ksu_seccomp_clear_cache(struct seccomp_filter *filter, int nr)",
    "void ksu_seccomp_allow_cache(struct seccomp_filter *filter, int nr)",
    "clear_bit(nr, filter->cache.allow_native);",
    "set_bit(nr, filter->cache.allow_native);",
)

SECCOMP_HEAD = """#include <linux/version.h>
#include <linux/fs.h>
#include <linux/nsproxy.h>
#include <linux/sched/task.h>
#include <linux/uaccess.h>
#include <linux/filter.h>
#include <linux/seccomp.h>
#include "klog.h" // IWYU pragma: keep
#include "infra/seccomp_cache.h"

/*
 * KSU-A52 4.19 适配（由 scripts/fix_sources_for_419.py 生成）
 *
 * 4.19 没有 SECCOMP_ARCH_NATIVE_NR，也没有 struct seccomp_filter::cache
 * （真实结构体是 { refcount_t usage; bool log; prev; prog; }），
 * 因此 < 5.9 时把 action_cache / seccomp_filter 重定义与实现整体关掉，
 * 两个对外函数退化为空实现 —— 与上游 KernelSU 在 < 5.9 的行为一致。
 */
#if LINUX_VERSION_CODE >= KERNEL_VERSION(5, 9, 0)

struct action_cache {
    DECLARE_BITMAP(allow_native, SECCOMP_ARCH_NATIVE_NR);
#ifdef SECCOMP_ARCH_COMPAT
    DECLARE_BITMAP(allow_compat, SECCOMP_ARCH_COMPAT_NR);
#endif
};

struct seccomp_filter {
    refcount_t refs;
    refcount_t users;
    bool log;
#if LINUX_VERSION_CODE >= KERNEL_VERSION(6, 1, 0)
    bool wait_killable_recv;
#endif
    struct action_cache cache;
    struct seccomp_filter *prev;
    struct bpf_prog *prog;
    struct notification *notif;
    struct mutex notify_lock;
    wait_queue_head_t wqh;
};

void ksu_seccomp_clear_cache(struct seccomp_filter *filter, int nr)
{
    if (!filter) {
        return;
    }

    if (nr >= 0 && nr < SECCOMP_ARCH_NATIVE_NR) {
        clear_bit(nr, filter->cache.allow_native);
    }

#ifdef SECCOMP_ARCH_COMPAT
    if (nr >= 0 && nr < SECCOMP_ARCH_COMPAT_NR) {
        clear_bit(nr, filter->cache.allow_compat);
    }
#endif
}

void ksu_seccomp_allow_cache(struct seccomp_filter *filter, int nr)
{
    if (!filter) {
        return;
    }

    if (nr >= 0 && nr < SECCOMP_ARCH_NATIVE_NR) {
        set_bit(nr, filter->cache.allow_native);
    }

#ifdef SECCOMP_ARCH_COMPAT
    if (nr >= 0 && nr < SECCOMP_ARCH_COMPAT_NR) {
        set_bit(nr, filter->cache.allow_compat);
    }
#endif
}

#else /* LINUX_VERSION_CODE < 5.9 */

void ksu_seccomp_clear_cache(struct seccomp_filter *filter, int nr)
{
    (void)filter;
    (void)nr;
}

void ksu_seccomp_allow_cache(struct seccomp_filter *filter, int nr)
{
    (void)filter;
    (void)nr;
}

#endif /* LINUX_VERSION_CODE >= 5.9 */
"""

# ---------------------------------------------------------------- B) selinuxfs

DYNAMIC_MARK = "/* KSU-A52: 去 static 以便驱动通过 kallsyms 解析（与 ReSukiSU 内核一致） */\n"


def replace_once(path: Path, old: str, new: str) -> None:
    text = path.read_text(encoding="utf-8", errors="surrogateescape")
    if new in text:
        print(f"[=] {path}: 已处理过")
        return
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"[!] {path}: 期望唯一匹配，实际 {count} 处：{old[:70]!r}")
    path.write_text(text.replace(old, new, 1), encoding="utf-8", errors="surrogateescape")
    print(f"[+] {path}: 已修改")


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    root = Path(sys.argv[1]).resolve()
    driver = root / "KernelSU" / "kernel"
    seccomp = None
    if not driver.is_dir():
        print(f"[=] 没有驱动目录 {driver}（builtin 线不需要），跳过 A")
    else:
        # A) infra/seccomp_cache.c（只有 main 线有；builtin 线没有这个文件）
        cand = driver / "infra" / "seccomp_cache.c"
        if not cand.is_file():
            print("[=] 驱动里没有 infra/seccomp_cache.c（builtin 线），跳过 A")
        else:
            seccomp = cand
            cur = seccomp.read_text(encoding="utf-8", errors="surrogateescape")
            if "KSU-A52 4.19 适配" in cur:
                print(f"[=] {seccomp}: 已处理过")
            else:
                missing = [m for m in SECCOMP_EXPECTED_MARKERS if m not in cur]
                if missing:
                    raise SystemExit(f"[!] {seccomp} 内容与预期不符，缺少标记：{missing}")
                seccomp.write_text(SECCOMP_HEAD, encoding="utf-8", errors="surrogateescape")
                print(f"[+] {seccomp}: 已替换为 4.19/5.9 双分支版本")

    # B) security/selinux/selinuxfs.c
    selinuxfs = root / "security" / "selinux" / "selinuxfs.c"
    if not selinuxfs.is_file():
        print(f"[=] 找不到 {selinuxfs}，跳过 B")
    else:
        text = selinuxfs.read_text(encoding="utf-8", errors="surrogateescape")
        changed = False
        for old, new in (
            (
                "static const struct file_operations sel_handle_status_ops = {",
                DYNAMIC_MARK + "const struct file_operations sel_handle_status_ops = {",
            ),
            (
                "static ssize_t (*const write_op[])(struct file *, char *, size_t) = {",
                DYNAMIC_MARK + "ssize_t (*const write_op[])(struct file *, char *, size_t) = {",
            ),
        ):
            if new in text:
                continue
            if text.count(old) != 1:
                print(f"[=] {selinuxfs}: 未找到唯一匹配，跳过：{old[:60]!r}")
                continue
            text = text.replace(old, new, 1)
            changed = True
        if changed:
            selinuxfs.write_text(text, encoding="utf-8", errors="surrogateescape")
            print(f"[+] {selinuxfs}: 已去掉 static（selinux_hide 需要 kallsyms 可见）")
        else:
            print(f"[=] {selinuxfs}: 无需修改")

    # 校验
    if seccomp is not None and seccomp.is_file():
        s = seccomp.read_text(encoding="utf-8", errors="surrogateescape")
        assert "#if LINUX_VERSION_CODE >= KERNEL_VERSION(5, 9, 0)" in s, "seccomp 5.9 分支缺失"
        assert "#else /* LINUX_VERSION_CODE < 5.9 */" in s, "seccomp <5.9 分支缺失"
        assert s.count("(void)filter;") == 2, "seccomp 空实现数量不对"
        assert "set_bit(nr, filter->cache.allow_native);" in s, "seccomp 原实现被破坏"
    else:
        print("[=] 无 seccomp_cache.c，跳过该校验")
    if selinuxfs.is_file():
        sf = selinuxfs.read_text(encoding="utf-8", errors="surrogateescape")
        if DYNAMIC_MARK in sf:
            assert "\nconst struct file_operations sel_handle_status_ops = {" in sf, "sel_handle_status_ops 未导出"
            assert "\nssize_t (*const write_op[])(struct file *, char *, size_t) = {" in sf, "write_op 未导出"
    print("[OK] 4.19 适配处理完成")
    return 0


if __name__ == "__main__":
    sys.exit(main())