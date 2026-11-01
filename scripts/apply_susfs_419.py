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


# SUSFS v1.5.5 的 include/linux/susfs_def.h 里没有 v2 才引入的 3 个常量，
# 而 SukiSU 4.2.1 的 supercall/dispatch.c 直接引用它们 → 4.19 上编译报
#   error: use of undeclared identifier 'SUSFS_MAGIC'
#   error: use of undeclared identifier 'CMD_SUSFS_ADD_SUS_PATH_LOOP'
#   error: use of undeclared identifier 'CMD_SUSFS_ENABLE_AVC_LOG_SPOOFING'
# 取值与 SUSFS v2.3.0 的 susfs_def.h 完全一致。
SUSFS_DEF_TAIL = r"""
/* ==================== SUSFS v2 命令常量（A52 / 4.19 兼容，v1.5.5 缺） ==================== */
#ifndef SUSFS_MAGIC
#define SUSFS_MAGIC                         0xFAFAFAFA
#endif
#ifndef CMD_SUSFS_ADD_SUS_PATH_LOOP
#define CMD_SUSFS_ADD_SUS_PATH_LOOP         0x55553
#endif
#ifndef CMD_SUSFS_ENABLE_AVC_LOG_SPOOFING
#define CMD_SUSFS_ENABLE_AVC_LOG_SPOOFING   0x60010
#endif
"""

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

/* SukiSU 的 feature/adb_root.c 用这两个判断 adbd 路径；v1.5.5 的 susfs.h/.c 里
 * 都没有（v2 才加），由 fs/susfs_compat_419.c 实现。缺声明 → 编译警告；
 * 缺实现 → 链接 undefined reference。 */
bool susfs_starts_with(const char *s, const char *prefix);
bool susfs_ends_with(const char *s, const char *suffix);

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

    print("== 1.5) 给 include/linux/susfs_def.h 补 v2 命令常量 ==")
    p = ks / "include/linux/susfs_def.h"
    t = read(p)
    if "SUSFS-HANDFIX:v2-def-cmds" in t:
        print(f"  = include/linux/susfs_def.h [v2-def-cmds] 已存在，跳过")
    else:
        anchor = "#endif // #ifndef KSU_SUSFS_DEF_H"
        if t.count(anchor) != 1:
            die("include/linux/susfs_def.h: 找不到尾部 #endif 锚点")
        t = t.replace(anchor, SUSFS_DEF_TAIL + "\n" + anchor, 1)
        write(p, t)
        print("  + include/linux/susfs_def.h [v2-def-cmds] 已补 "
              "SUSFS_MAGIC / CMD_SUSFS_ADD_SUS_PATH_LOOP / CMD_SUSFS_ENABLE_AVC_LOG_SPOOFING")

    # 关键：必须用 -F2（默认 fuzz）。三星 One UI 8 树与 SUSFS 补丁基线（AOSP 4.19）有偏移：
    #   -F0 零模糊匹配 → 16 个 hunk 失败；-F2 → 9 个失败（其余 7 处由 fix_susfs_rejects.py 手工补）
    print("== 2) 打主补丁 (patch -F2) ==")
    patch = sf / "kernel_patches" / "50_add_susfs_in_kernel-4.19.patch"
    if not patch.exists():
        die(f"缺主补丁 {patch}")
    r = subprocess.run(["patch", "-p1", "-F2", "--forward", "--no-backup-if-mismatch",
                        "-i", str(patch)], cwd=str(ks),
                       capture_output=True, text=True)
    # 只统计真正的 hunk 失败行（"Hunk #N FAILED at"）；否则 "N out of M hunks FAILED" 会被重复计入
    allout = (r.stdout or "") + (r.stderr or "")
    fails = [l for l in allout.splitlines() if re.search(r"Hunk #\d+ FAILED", l)]
    print(f"  patch 返回码={r.returncode}，失败 hunk {len(fails)} 个")
    for l in allout.splitlines():
        if "FAILED" in l:
            print("    " + l)
    # 期望 9 个失败 hunk（对应 7 个手工修补点）；超过说明树不对，直接终止而不是产半成品
    if len(fails) > 12:
        die(f"失败 hunk 过多（{len(fails)} > 12）—— 源码树与 SUSFS 4.19 补丁基线差别过大，中止")

    # 立刻用手工修补脚本补掉这 9 个失败 hunk（含最致命的 task_struct 字段）
    # 注意：compat 脚本目录是 cd（argv[3]），不是 sf（argv[2] = susfs4ksu 仓库）
    fixer = cd / "fix_susfs_rejects.py"
    if fixer.exists():
        print("== 2.5) 修补失败的 hunk（三星 One UI 8 树差异）==")
        rr = subprocess.run([sys.executable, str(fixer), str(ks)],
                            capture_output=True, text=True)
        out = (rr.stdout or "") + (rr.stderr or "")
        for l in out.splitlines():
            if l.strip():
                print("    " + l)
        if rr.returncode != 0:
            die("fix_susfs_rejects.py 失败 —— 中止（避免产出无法编译的半成品）")
    else:
        die(f"缺 {fixer}（SUSFS 在三星树上必需）")

    print("== 3) 手工补丁（仅启用特性的必要部分）==")

    # --- fs/dcache.c: SUS_PATH 需要的 include ---
    insert_after(ks, "fs/dcache.c", '#include "mount.h"\n', """
#ifdef CONFIG_KSU_SUSFS_SUS_PATH
#include <linux/susfs_def.h>
#endif
""" + HAND % "dcache-include" + "\n", "dcache-include")

    # 注意：以下已由 fix_susfs_rejects.py 统一处理（它对应主补丁失败的 9 个 hunk），
    # 这里不再重复插入，否则会产生「两份 susfs_task_state / 两份 susfs_spoof_uname」冲突：
    #   fs/proc/task_mmu.c 的 include、include/linux/sched.h 的字段、
    #   kernel/sys.c 的 newuname 声明与调用、fs/readdir.c 的 compat_fillonedir、
    #   fs/notify/fdinfo.c 的 SUS_KSTAT 隐藏。
    # 保留：fs/namei.c 的 may_delete 拦截（主补丁 -F2 打上的部分不含这段语义等价插入）。

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

    # --- drivers/tty/pty.c: SUSFS 构建下 ksu_handle_devpts 没有定义 ---
    # 官方 4.19 钩子补丁在 pts_unix98_lookup() 里调 ksu_handle_devpts()，
    # 但驱动的 feature/sucompat.c 把这个函数放在 "#else"（非 SUSFS）分支里，
    # 而且它本身是 dead code（恒 return 0）→ CONFIG_KSU_SUSFS=y 时链接报
    #   drivers/tty/pty.c:734: undefined reference to `ksu_handle_devpts'
    # 直接去掉这次调用（行为完全不变），并留标记便于复查。
    p = ks / "drivers/tty/pty.c"
    if p.exists():
        t = read(p)
        if "SUSFS-HANDFIX:devpts-dead-hook" in t:
            print("  = drivers/tty/pty.c [devpts-dead-hook] 已处理，跳过")
        else:
            devpts_call = re.compile(
                r"#ifdef CONFIG_KSU\n[ \t]*ksu_handle_devpts\([^\n]*\);[ \t]*\n#endif\n")
            hits = devpts_call.findall(t)
            if len(hits) != 1:
                die(f"drivers/tty/pty.c: ksu_handle_devpts 调用命中 {len(hits)} 次（要求 1 次）")
            t = devpts_call.sub(
                "/* SUSFS-HANDFIX:devpts-dead-hook —— CONFIG_KSU_SUSFS 下 sucompat.c\n"
                " * 不定义 ksu_handle_devpts（上游 dead code，恒返回 0），故不调用 */\n",
                t, count=1)
            write(p, t)
            print("  + drivers/tty/pty.c [devpts-dead-hook] 已移除死钩子调用")
    else:
        print("  = 没有 drivers/tty/pty.c，跳过")

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
        ("include/linux/susfs.h",  "susfs_starts_with"),
        ("include/linux/susfs.h",  "susfs_ends_with"),
        ("fs/susfs_compat_419.c",  "susfs_starts_with"),
        ("fs/susfs_compat_419.c",  "susfs_ends_with"),
        ("drivers/tty/pty.c",      "SUSFS-HANDFIX:devpts-dead-hook"),
        ("include/linux/susfs_def.h", "0xFAFAFAFA"),
        ("include/linux/susfs_def.h", "CMD_SUSFS_ADD_SUS_PATH_LOOP"),
        ("include/linux/susfs_def.h", "CMD_SUSFS_ENABLE_AVC_LOG_SPOOFING"),
        ("include/linux/sched.h",  "susfs_task_state"),
        ("fs/namei.c",             "INODE_STATE_SUS_PATH"),
        ("fs/dcache.c",            "linux/susfs_def.h"),
        ("fs/proc/task_mmu.c",     "linux/susfs_def.h"),
        ("kernel/sys.c",           "susfs_spoof_uname"),
        # compat_fillonedir 的插入已由 fix_susfs_rejects.py 负责（主补丁 -F2 失败的那个
        # hunk），此处不再校验它自己的 HAND 标记（那是 rejects 脚本的职责，校验会误报）。
        # 改为校验真正生效的功能点：4 个 filldir actor 里都必须有 SUS_PATH ino 拦截。
        ("fs/readdir.c",           "susfs_sus_ino_for_filldir64"),
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
        ("kernel/kallsyms.c","CONFIG_KSU_SUSFS_HIDE_KSU_SUSFS_SYMBOLS", "kallsyms 隐藏 ksu/susfs 符号"),
    ]:
        ok = needle in read(ks / rel)
        print(("  OK   " if ok else "  MISS ") + f"{rel} <- {tag}")
        if not ok:
            bad += 1

    # fs/readdir.c 必须 4 个 filldir actor 全挂拦截：
    #   filldir(206) / filldir64(301) / compat_fillonedir(406) / compat_filldir(482)
    # 只数「存在」不够 —— 少一个 actor 就会在对应 syscall 路径上漏掉 SUS_PATH。
    rd = ks / "fs/readdir.c"
    if rd.exists():
        rdt = read(rd)
        actors = ["filldir", "filldir64", "compat_fillonedir", "compat_filldir"]
        n_actor = sum(1 for a in actors
                      if re.search(r"^static int " + a + r"\(", rdt, re.M))
        expect = 4
        n_hook = rdt.count("susfs_sus_ino_for_filldir64(ino)")
        rd_ok = (n_actor == expect and n_hook >= n_actor)
        print(("  OK   " if rd_ok else "  MISS ") +
              f"fs/readdir.c <- SUS_PATH 隐藏目录项 "
              f"(actor {n_actor}/{expect}, hook {n_hook})")
        if not rd_ok:
            bad += 1

    # 反向检查：死钩子调用不能残留（残留就会 undefined reference）
    pt = ks / "drivers/tty/pty.c"
    if pt.exists():
        left = read(pt).count("ksu_handle_devpts((struct inode")
        print(("  OK   " if left == 0 else "  MISS ") +
              "drivers/tty/pty.c <- 死钩子调用残留数 0")
        if left:
            bad += 1

    if bad:
        die(f"校验未通过，{bad} 项缺失")
    print("\nALL OK: SUSFS v1.5.5 内核补丁 + v2 兼容层已就位")

if __name__ == "__main__":
    main()
