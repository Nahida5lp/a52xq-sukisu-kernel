#!/usr/bin/env python3
"""
apply_susfs_419.py — 把 susfs4ksu 的 4.19 内核补丁 + v2 API 兼容层
                     打到三星 a52xq (SM-A5260) 4.19 内核树上。

用法:
    py apply_susfs_419.py <kernel_src_root> <susfs4ksu_dir> [compat_c_dir]

设计要点:
  1) 先拷入 5 个 susfs 源文件 + 兼容层 susfs_compat_419.c
  2) 用 patch -F0 打主补丁(不用 fuzz，避免落错位置)；失败的 hunk 由下面手工补
  3) 只手工补「本项目实际启用」的特性所缺的 hunk；SUS_MOUNT / SUS_MAP 整组跳过
  4) 每处手工改动都带 "SUSFS-HANDFIX:<tag>" 标记，天然幂等；锚点找不到直接报错退出
  5) 最后统一把驱动 dispatch.c 的 susfs 调用改成 *arg（v1.5.5 的入参约定）

退出码 0 = 全部就位；非 0 = 有锚点未命中，需要人工检查
"""
import os
import re
import shutil
import subprocess
import sys
import pathlib

HAND = "/* SUSFS-HANDFIX:%s */"

def die(msg):
    print("ERROR: " + msg)
    sys.exit(2)

def read(p):
    return p.read_text(encoding="utf-8", errors="surrogateescape")

def write(p, t):
    p.write_text(t, encoding="utf-8", errors="surrogateescape")

def insert_after(ks, rel, anchor, block, tag):
    p = ks / rel
    if not p.exists():
        die(f"缺文件 {rel}")
    t = read(p)
    if (HAND % tag) in t:
        print(f"  = {rel} [{tag}] 已存在，跳过")
        return
    n = t.count(anchor)
    if n != 1:
        die(f"{rel}: 锚点命中 {n} 次（要求 1 次）: {anchor!r}")
    t = t.replace(anchor, anchor + block, 1)
    write(p, t)
    print(f"  + {rel} [{tag}] 已插入")

def insert_before(ks, rel, anchor, block, tag):
    p = ks / rel
    if not p.exists():
        die(f"缺文件 {rel}")
    t = read(p)
    if (HAND % tag) in t:
        print(f"  = {rel} [{tag}] 已存在，跳过")
        return
    n = t.count(anchor)
    if n != 1:
        die(f"{rel}: 锚点命中 {n} 次（要求 1 次）: {anchor!r}")
    t = t.replace(anchor, block + anchor, 1)
    write(p, t)
    print(f"  + {rel} [{tag}] 已插入")

def insert_before_in_func(ks, rel, func_marker, anchor, block, tag):
    """在 func_marker 之后第一次出现的 anchor 之前插入"""
    p = ks / rel
    if not p.exists():
        die(f"缺文件 {rel}")
    t = read(p)
    if (HAND % tag) in t:
        print(f"  = {rel} [{tag}] 已存在，跳过")
        return
    fi = t.find(func_marker)
    if fi < 0:
        die(f"{rel}: 找不到函数标记 {func_marker!r}")
    ai = t.find(anchor, fi)
    if ai < 0:
        die(f"{rel}: 在 {func_marker!r} 之后找不到锚点 {anchor!r}")
    t = t[:ai] + block + t[ai:]
    write(p, t)
    print(f"  + {rel} [{tag}] 已插入")


def insert_after_in_func(ks, rel, func_marker, anchor, block, tag):
    p = ks / rel
    if not p.exists():
        die(f"缺文件 {rel}")
    t = read(p)
    if (HAND % tag) in t:
        print(f"  = {rel} [{tag}] 已存在，跳过")
        return
    fi = t.find(func_marker)
    if fi < 0:
        die(f"{rel}: 找不到函数标记 {func_marker!r}")
    ai = t.find(anchor, fi)
    if ai < 0:
        die(f"{rel}: 在 {func_marker!r} 之后找不到锚点 {anchor!r}")
    ai += len(anchor)
    t = t[:ai] + block + t[ai:]
    write(p, t)
    print(f"  + {rel} [{tag}] 已插入")


SUSFS_H_TAIL = r"""
/* ==================== SUSFS v2 API 兼容层（A52 / 4.19 / SukiSU） ====================
 * SukiSU Ultra 4.2.1 按 SUSFS v2.x 的接口工作，而 4.19 分支只到 v1.5.5。
 * 下面这些符号由 fs/susfs_compat_419.c 实现，或在此以 static inline 等价映射。
 * 调用约定：dispatch.c 里统一传 *arg（void __user *），与本处签名一致。
 */
#include <linux/workqueue.h>
#include <linux/jump_label.h>

#ifdef CONFIG_KSU_SUSFS

void susfs_set_avc_log_spoofing(void __user *user_info);
void susfs_get_enabled_features(void __user *user_info);
void susfs_show_variant(void __user *user_info);
void susfs_show_version(void __user *user_info);
void susfs_enable_log(void __user *user_info);
void susfs_add_sus_path_loop(void __user *user_info);
void susfs_start_sdcard_monitor_fn(void);

extern struct work_struct susfs_extra_works;
DECLARE_STATIC_KEY_FALSE(susfs_is_avc_log_spoofing_enabled);

/*
 * v2 用 thread flag（TIF_PROC_UMOUNTED / TIF_PROC_NO_SU）标记当前进程，
 * v1.5.5 用 task_struct->susfs_task_state 的 TASK_STRUCT_NON_ROOT_USER_APP_PROC。
 * 语义等价：都表示「这是个普通应用进程，sus 挂载/路径要对它隐藏」。
 */
static inline void susfs_set_current_proc_umounted(void)
{
	current->susfs_task_state |= TASK_STRUCT_NON_ROOT_USER_APP_PROC;
}

static inline void susfs_clear_current_proc_umounted(void)
{
	current->susfs_task_state &= ~TASK_STRUCT_NON_ROOT_USER_APP_PROC;
}

static inline void susfs_set_current_proc_umounted_for_zygote_next(void)
{
	current->susfs_task_state |= TASK_STRUCT_NON_ROOT_USER_APP_PROC;
}

static inline void susfs_set_current_proc_no_su(void)
{
	current->susfs_task_state &= ~TASK_STRUCT_NON_ROOT_USER_APP_PROC;
}

#endif /* CONFIG_KSU_SUSFS */
"""

AVC_DECL = r"""
#ifdef CONFIG_KSU_SUSFS
#include <linux/jump_label.h>
DECLARE_STATIC_KEY_FALSE(susfs_is_avc_log_spoofing_enabled);
extern u32 susfs_ksu_sid;
#endif
/* SUSFS-HANDFIX:avc-decl */
"""

AVC_HOOK = r"""#ifdef CONFIG_KSU_SUSFS
	if (static_branch_likely(&susfs_is_avc_log_spoofing_enabled) &&
	    unlikely(tsid == susfs_ksu_sid)) {
		audit_log_format(ab, " tcontext=%s", "u:r:priv_app:s0:c512,c768");
		goto susfs_avc_spoofed;
	}
#endif
/* SUSFS-HANDFIX:avc-hook */
"""

AVC_LABEL = r"""#ifdef CONFIG_KSU_SUSFS
susfs_avc_spoofed:
#endif
/* SUSFS-HANDFIX:avc-label */
"""


def patch_dispatch(ks):
    """把驱动 dispatch.c 里所有 susfs_xxx(arg) 改成 susfs_xxx(*arg)。"""
    rel = "drivers/kernelsu/supercall/dispatch.c"
    p = ks / rel
    if not p.exists():
        die(f"缺文件 {rel}")
    t = read(p)
    if "SUSFS-HANDFIX:dispatch-arg" in t:
        print(f"  = {rel} [dispatch-arg] 已处理，跳过")
        return
    pat = re.compile(r"\bsusfs_([a-z_0-9]+)\(arg\)")
    names = sorted(set(m.group(1) for m in pat.finditer(t)))
    t2, n = pat.subn(r"susfs_\1(*arg)", t)
    if n == 0:
        die(f"{rel}: 找不到任何 susfs_xxx(arg) 调用")
    t2 = t2.replace("int ksu_handle_sys_reboot(",
                    "/* SUSFS-HANDFIX:dispatch-arg */\nint ksu_handle_sys_reboot(", 1)
    write(p, t2)
    print(f"  + {rel} [dispatch-arg] 改写 {n} 处: {', '.join(names)}")


def main():
    if len(sys.argv) < 3:
        die("用法: apply_susfs_419.py <kernel_src_root> <susfs4ksu_dir> [compat_c_dir]")
    ks = pathlib.Path(sys.argv[1]).resolve()
    sf = pathlib.Path(sys.argv[2]).resolve()
    cd = pathlib.Path(sys.argv[3]).resolve() if len(sys.argv) >= 4 \
         else pathlib.Path(__file__).resolve().parent
    if not (ks / "Makefile").exists():
        die(f"{ks} 不像内核源码根目录（没有 Makefile）")
    if not (sf / "kernel_patches").is_dir():
        die(f"{sf} 不像 susfs4ksu 目录（没有 kernel_patches/）")

    print("== 1) 拷入 susfs 源文件 ==")
    pairs = [
        ("kernel_patches/include/linux/susfs.h",     "include/linux/susfs.h"),
        ("kernel_patches/include/linux/susfs_def.h", "include/linux/susfs_def.h"),
        ("kernel_patches/include/linux/sus_su.h",    "include/linux/sus_su.h"),
        ("kernel_patches/fs/susfs.c",                "fs/susfs.c"),
        ("kernel_patches/fs/sus_su.c",               "fs/sus_su.c"),
    ]
    for src, dst in pairs:
        s = sf / src
        if not s.exists():
            die(f"缺补丁源文件 {s}")
        (ks / dst).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(s, ks / dst)
        print(f"  -> {dst}")

    compat = cd / "susfs_compat_419.c"
    if not compat.exists():
        die(f"缺兼容层源码 {compat}")
    shutil.copy2(compat, ks / "fs/susfs_compat_419.c")
    print("  -> fs/susfs_compat_419.c (v2 API 兼容层)")

    print("== 2) 打主补丁 (patch -F0) ==")
    patch = sf / "kernel_patches" / "50_add_susfs_in_kernel-4.19.patch"
    if not patch.exists():
        die(f"缺主补丁 {patch}")
    r = subprocess.run(["patch", "-p1", "-F0", "--forward", "--no-backup-if-mismatch",
                        "-i", str(patch)], cwd=str(ks),
                       capture_output=True, text=True)
    fails = [l for l in (r.stdout + r.stderr).splitlines() if "FAILED" in l]
    print(f"  patch 返回码={r.returncode}，失败 hunk {len(fails)} 个")
    for l in fails:
        print("    " + l)

    print("== 3) 手工补丁（仅启用特性的必要部分）==")

    # --- fs/dcache.c: SUS_PATH 需要的 include ---
    insert_after(ks, "fs/dcache.c", '#include "mount.h"\n', """
#ifdef CONFIG_KSU_SUSFS_SUS_PATH
#include <linux/susfs_def.h>
#endif
""" + HAND % "dcache-include" + "\n", "dcache-include")

    # --- fs/proc/task_mmu.c: SUS_KSTAT 需要的 include ---
    insert_after(ks, "fs/proc/task_mmu.c", "#include <linux/ctype.h>\n", """
#ifdef CONFIG_KSU_SUSFS_SUS_KSTAT
#include <linux/susfs_def.h>
#endif
""" + HAND % "taskmmu-include" + "\n", "taskmmu-include")

    # --- fs/namei.c: SUS_PATH / OPEN_REDIRECT 需要的 include ---
    insert_after(ks, "fs/namei.c", "#include <linux/build_bug.h>\n", """
#if defined(CONFIG_KSU_SUSFS_SUS_PATH) || defined(CONFIG_KSU_SUSFS_OPEN_REDIRECT)
#include <linux/susfs_def.h>
#endif
""" + HAND % "namei-include" + "\n", "namei-include")

    # --- fs/namei.c: may_delete() 里拦截对 sus 路径的删除 ---
    insert_before(ks, "fs/namei.c", "\tif (d_is_negative(victim))\n\t\treturn -ENOENT;\n", """#ifdef CONFIG_KSU_SUSFS_SUS_PATH
	if (victim->d_inode &&
	    unlikely(victim->d_inode->i_state & INODE_STATE_SUS_PATH) &&
	    likely(current->susfs_task_state & TASK_STRUCT_NON_ROOT_USER_APP_PROC)) {
		error = inode_permission2(mnt, dir, MAY_WRITE | MAY_EXEC);
		if (error)
			return error;
		return -ENOENT;
	}
#endif
""" + HAND % "namei-may-delete" + "\n", "namei-may-delete")

    # --- include/linux/sched.h: task_struct 增加两个字段 ---
    insert_after(ks, "include/linux/sched.h", "\tANDROID_KABI_USE(1, unsigned frozen:1);\n", """
#ifdef CONFIG_KSU_SUSFS
	u64 susfs_task_state;
	u64 susfs_last_fake_mnt_id;
#endif
""" + HAND % "sched-task-struct" + "\n", "sched-task-struct")

    # --- kernel/sys.c: newuname 里 spoof uname ---
    insert_after(ks, "kernel/sys.c",
                 "SYSCALL_DEFINE1(newuname, struct new_utsname __user *, name)\n{\n\tstruct new_utsname tmp;\n",
                 """#ifdef CONFIG_KSU_SUSFS_SPOOF_UNAME
	extern void susfs_spoof_uname(struct new_utsname *tmp);
#endif
""" + HAND % "sys-newuname-decl" + "\n", "sys-newuname-decl")

    insert_after_in_func(ks, "kernel/sys.c", "SYSCALL_DEFINE1(newuname",
                         "\tmemcpy(&tmp, utsname(), sizeof(tmp));\n", """
#ifdef CONFIG_KSU_SUSFS_SPOOF_UNAME
	susfs_spoof_uname(&tmp);
#endif
""" + HAND % "sys-newuname-call" + "\n", "sys-newuname-call")

    # --- fs/readdir.c: compat_fillonedir 里的隐藏 ---
    insert_after_in_func(ks, "fs/readdir.c", "compat_fillonedir(struct dir_context",
                         "\td_ino = ino;\n", """
#ifdef CONFIG_KSU_SUSFS_SUS_PATH
	if (likely(current->susfs_task_state & TASK_STRUCT_NON_ROOT_USER_APP_PROC) && susfs_sus_ino_for_filldir64(ino)) {
		return 0;
	}
#endif
""" + HAND % "readdir-compat-fillonedir" + "\n", "readdir-compat-fillonedir")

    # --- fs/Makefile: 同时编入兼容层 ---
    p = ks / "fs/Makefile"
    t = read(p)
    if "SUSFS-HANDFIX:fs-makefile" in t:
        print("  = fs/Makefile [fs-makefile] 已处理，跳过")
    else:
        old = "obj-$(CONFIG_KSU_SUSFS) += susfs.o"
        if old not in t:
            die("fs/Makefile: 找不到 obj-$(CONFIG_KSU_SUSFS) += susfs.o")
        t = t.replace(old, old + " susfs_compat_419.o", 1)
        t += "\n" + HAND % "fs-makefile" + "\n"
        write(p, t)
        print("  + fs/Makefile [fs-makefile] 已加入 susfs_compat_419.o")

    # --- include/linux/susfs.h: 追加 v2 API 声明 + 进程标志映射 ---
    p = ks / "include/linux/susfs.h"
    t = read(p)
    if "SUSFS-HANDFIX:v2-header" in t:
        print("  = include/linux/susfs.h [v2-header] 已处理，跳过")
    else:
        anchor = "/* susfs_init */\nvoid susfs_init(void);\n"
        if t.count(anchor) != 1:
            die("include/linux/susfs.h: 找不到 susfs_init 声明锚点")
        t = t.replace(anchor, anchor + SUSFS_H_TAIL + "\n" + HAND % "v2-header" + "\n", 1)
        write(p, t)
        print("  + include/linux/susfs.h [v2-header] v2 API 兼容声明已加入")

    # --- security/selinux/avc.c: AVC 日志伪装 ---
    insert_after(ks, "security/selinux/avc.c", "#include <linux/audit.h>\n",
                 AVC_DECL, "avc-decl")
    insert_before(ks, "security/selinux/avc.c",
                  "\trc = security_sid_to_context(state, tsid, &scontext, &scontext_len);\n",
                  AVC_HOOK, "avc-hook")
    insert_before_in_func(ks, "security/selinux/avc.c", "static void avc_dump_query",
                          "\tBUG_ON(!tclass || tclass >= ARRAY_SIZE(secclass_map));\n",
                          AVC_LABEL, "avc-label")

    # --- 驱动 dispatch.c: 统一改成 *arg ---
    patch_dispatch(ks)

    print("== 4) 校验 ==")
    checks = [
        ("fs/Makefile",            "susfs_compat_419.o"),
        ("fs/susfs.c",             "susfs_init"),
        ("fs/susfs_compat_419.c",  "susfs_set_avc_log_spoofing"),
        ("fs/susfs_compat_419.c",  "susfs_show_version"),
        ("fs/susfs_compat_419.c",  "susfs_get_enabled_features"),
        ("fs/susfs_compat_419.c",  "susfs_enable_log"),
        ("fs/susfs_compat_419.c",  "susfs_extra_works"),
        ("include/linux/susfs.h",  "susfs_set_current_proc_umounted"),
        ("include/linux/susfs.h",  "susfs_set_current_proc_no_su"),
        ("include/linux/sched.h",  "susfs_task_state"),
        ("fs/namei.c",             "INODE_STATE_SUS_PATH"),
        ("fs/dcache.c",            "linux/susfs_def.h"),
        ("fs/proc/task_mmu.c",     "linux/susfs_def.h"),
        ("kernel/sys.c",           "susfs_spoof_uname"),
        ("fs/readdir.c",           "SUSFS-HANDFIX:readdir-compat-fillonedir"),
        ("security/selinux/avc.c", "susfs_is_avc_log_spoofing_enabled"),
        ("security/selinux/avc.c", "susfs_avc_spoofed"),
        ("drivers/kernelsu/supercall/dispatch.c", "susfs_show_version(*arg)"),
    ]
    bad = 0
    for rel, needle in checks:
        p = ks / rel
        if not p.exists():
            print("  MISS " + f"{rel} (文件不存在)")
            bad += 1
            continue
        ok = needle in read(p)
        print(("  OK   " if ok else "  MISS ") + f"{rel} <- {needle}")
        if not ok:
            bad += 1

    for rel, needle, tag in [
        ("fs/readdir.c",     "susfs_sus_ino_for_filldir64", "SUS_PATH 隐藏目录项"),
        ("kernel/kallsyms.c","CONFIG_KSU_SUSFS_HIDE_KSU_SUSFS_SYMBOLS", "kallsyms 隐藏 ksu/susfs 符号"),
    ]:
        ok = needle in read(ks / rel)
        print(("  OK   " if ok else "  MISS ") + f"{rel} <- {tag}")
        if not ok:
            bad += 1

    if bad:
        die(f"校验未通过，{bad} 项缺失")
    print("\nALL OK: SUSFS v1.5.5 内核补丁 + v2 兼容层已就位")

if __name__ == "__main__":
    main()
