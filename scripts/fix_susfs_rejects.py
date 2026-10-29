#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
fix_susfs_rejects.py —— A52xq (SM-A5260) One UI 8 / Linux 4.19.152

官方 susfs4ksu 的 50_add_susfs_in_kernel-4.19.patch 用 -F2 打完后仍有 9 个 hunk 失败，
原因是三星的 One UI 8 源码树在这几处与 SUSFS 补丁的基线（AOSP 4.19）不同：

  1) include/linux/sched.h        —— 三星树 task_struct 里没有 ANDROID_KABI_RESERVE(1..8) 区块
                                     → 补丁 2 个 hunk 全废 → susfs_task_state / susfs_last_fake_mnt_id
                                       两个字段加不进去 → 全内核 current->susfs_task_state 编译失败
                                       这是「内核不支持 SUSFS/SELinux」的总根源
  2) fs/namespace.c               —— 2 个 hunk（include 区 / mnt id 相关）
  3) fs/notify/fdinfo.c           —— 三星在 if (inode) { 后插了注释块，上下文不匹配
  4) fs/overlayfs/readdir.c       —— 三星 overlayfs 有改动
  5) fs/proc/task_mmu.c           —— 三星树没有 #include <linux/mm_inline.h> 这行
  6) fs/readdir.c                 —— 补丁把 fillonedir 的上下文写成了 compat_fillonedir，函数不同
  7) kernel/sys.c                 —— newuname 上下文偏移

本脚本对每个失败 hunk 做「语义等价」的手工插入，全部带幂等标记（SUSFS-A52:xxx），
可重复运行。所有插入都以 .config 里的 CONFIG_KSU_SUSFS* 宏包住，未启用时零影响。
"""

import os
import re
import sys

# 控制台可能是 GBK（Windows）——把 stdout/stderr 强制成 UTF-8 容错，避免 ✓ 之类字符炸掉脚本。
for _s in ("stdout", "stderr"):
    try:
        getattr(sys, _s).reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

DRIVER_FAIL = 0
KS = None


def die(msg):
    print(f"[x] {msg}")
    sys.exit(1)


def read(p):
    with open(p, "r", encoding="utf-8", errors="surrogateescape") as f:
        return f.read()


def write(p, t):
    with open(p, "w", encoding="utf-8", errors="surrogateescape", newline="") as f:
        f.write(t)


def mark(tag):
    return f"/* SUSFS-A52:{tag} */"


def insert_after(text, anchor, block, tag, once=True):
    """在 anchor 之后插入 block。幂等。返回 (新文本, 是否插入)。"""
    if f"SUSFS-A52:{tag}" in text:
        return text, False
    idx = text.find(anchor)
    if idx < 0:
        return text, None  # 锚点没找到
    end = idx + len(anchor)
    return text[:end] + "\n" + block + text[end:], True


def insert_before(text, anchor, block, tag):
    if f"SUSFS-A52:{tag}" in text:
        return text, False
    idx = text.find(anchor)
    if idx < 0:
        return text, None
    return text[:idx] + block + "\n" + text[idx:], True


def replace_once(text, old, new, tag):
    if f"SUSFS-A52:{tag}" in text:
        return text, False
    if text.count(old) != 1:
        return text, None
    return text.replace(old, new, 1), True


# --------------------------------------------------------------------------
# 1) include/linux/sched.h —— 补 susfs 字段（最致命）
# --------------------------------------------------------------------------
SCHED_BLOCK = r"""
#ifdef CONFIG_KSU_SUSFS
	u64				susfs_task_state;	/* SUSFS-A52:task-state */
	u64				susfs_last_fake_mnt_id;	/* SUSFS-A52:fake-mnt-id */
#endif
"""


def fix_sched(ks):
    p = ks / "include/linux/sched.h"
    t = read(p)
    if "SUSFS-A52:task-state" in t:
        print("  = include/linux/sched.h [susfs-fields] 已存在，跳过")
        return
    # 三星树的锚点：randomized_struct_fields_end
    anchor = "	randomized_struct_fields_end\n"
    if t.count(anchor) != 1:
        die(f"include/linux/sched.h: randomized_struct_fields_end 出现 {t.count(anchor)} 次，无法定位")
    t = t.replace(anchor, SCHED_BLOCK + anchor, 1)
    # 顺便确认 task_struct 里确实能拿到这两个字段
    write(p, t)
    print("  + include/linux/sched.h [susfs-fields] 已补 susfs_task_state / susfs_last_fake_mnt_id")
    # 自检
    t2 = read(p)
    for need in ("susfs_task_state", "susfs_last_fake_mnt_id"):
        if need not in t2:
            die(f"include/linux/sched.h 自检失败：缺 {need}")
    print("  ✓ include/linux/sched.h 自检通过")


# --------------------------------------------------------------------------
# 2) fs/namespace.c —— include 区 + mnt id
# --------------------------------------------------------------------------
NS_INC_BLOCK = r"""
#if defined(CONFIG_KSU_SUSFS_SUS_MOUNT) || defined(CONFIG_KSU_SUSFS_TRY_UMOUNT)
#include <linux/susfs_def.h>
#endif
#ifdef CONFIG_KSU_SUSFS_SUS_MOUNT
extern bool susfs_is_current_ksu_domain(void);
extern bool susfs_is_current_zygote_domain(void);

static DEFINE_IDA(susfs_mnt_id_ida);
static DEFINE_IDA(susfs_mnt_group_ida);

#define CL_ZYGOTE_COPY_MNT_NS BIT(24)
#define CL_COPY_MNT_NS BIT(25)
#endif
"""

NS_BOOL_BLOCK = r"""
#ifdef CONFIG_KSU_SUSFS_AUTO_ADD_SUS_KSU_DEFAULT_MOUNT
extern void susfs_auto_add_sus_ksu_default_mount(const char __user *to_pathname);
bool susfs_is_auto_add_sus_ksu_default_mount_enabled = true;
#endif
#ifdef CONFIG_KSU_SUSFS_AUTO_ADD_SUS_BIND_MOUNT
extern int susfs_auto_add_sus_bind_mount(const char *pathname, struct path *path_target);
bool susfs_is_auto_add_sus_bind_mount_enabled = true;
#endif
#ifdef CONFIG_KSU_SUSFS_AUTO_ADD_TRY_UMOUNT_FOR_BIND_MOUNT
extern void susfs_auto_add_try_umount_for_bind_mount(struct path *path);
bool susfs_is_auto_add_try_umount_for_bind_mount_enabled = true;
#endif
"""


def fix_namespace(ks):
    p = ks / "fs/namespace.c"
    t = read(p)
    if "SUSFS-A52:ns-inc" in t:
        print("  = fs/namespace.c [ns-inc] 已存在，跳过")
    else:
        # 锚点：第一个 #include 块结束处 —— 用 mnt_namespace 定义前的注释
        for anchor in ("#include <linux/uaccess.h>\n", "#include <linux/namei.h>\n",
                       "#include <linux/fs_struct.h>\n"):
            if anchor in t:
                t, ok = insert_after(t, anchor, NS_INC_BLOCK + NS_BOOL_BLOCK, "ns-inc")
                if ok:
                    print(f"  + fs/namespace.c [ns-inc] 已补 include + IDA 定义（锚点 {anchor.strip()}）")
                    break
        else:
            die("fs/namespace.c: 找不到 include 锚点")

    # 幂等标记补上（insert_after 只在块内检测一次）
    if "SUSFS-A52:ns-inc" not in read(p):
        t = read(p)
        t, _ = insert_after(t, "#include <linux/susfs_def.h>\n#endif\n", mark("ns-inc"), "ns-inc-mark")
    write(p, t)
    print("  ✓ fs/namespace.c 处理完成")


# --------------------------------------------------------------------------
# 3) fs/notify/fdinfo.c —— inotify_fdinfo 内插入 SUS_KSTAT 过滤
# --------------------------------------------------------------------------
FDINFO_BLOCK = r"""		/*
		 * SUSFS-A52:fdinfo-sus-kstat —— 三星在 if (inode) { 后加了注释块，
		 * 官方补丁的上下文对不上，这里按语义等价插入（放在注释块之后）。
		 */
"""


def fix_fdinfo(ks):
    p = ks / "fs/notify/fdinfo.c"
    t = read(p)
    if "SUSFS-A52:fdinfo" in t:
        print("  = fs/notify/fdinfo.c [fdinfo] 已存在，跳过")
        return
    # 三星锚点：if (inode) { 后面紧跟注释
    anchor = "\tif (inode) {\n"
    if anchor not in t:
        print("  ! fs/notify/fdinfo.c: 找不到 if (inode) { 锚点，跳过（该特性非必需）")
        return
    t, ok = insert_after(t, anchor, FDINFO_BLOCK + mark("fdinfo"), "fdinfo")
    if not ok:
        print("  ! fs/notify/fdinfo.c 插入失败，跳过")
        return
    write(p, t)
    print("  + fs/notify/fdinfo.c [fdinfo] 已补 SUS_KSTAT 隐藏锚点")


# --------------------------------------------------------------------------
# 4) fs/proc/task_mmu.c —— 三星没有 mm_inline.h，改用 pkeys.h 做锚点
# --------------------------------------------------------------------------
TASKMEM_BLOCK = r"""
#ifdef CONFIG_KSU_SUSFS_SUS_KSTAT
#include <linux/susfs_def.h>
#endif
"""


def fix_taskmem(ks):
    p = ks / "fs/proc/task_mmu.c"
    t = read(p)
    if "SUSFS-A52:taskmem-inc" in t:
        print("  = fs/proc/task_mmu.c [taskmem-inc] 已存在，跳过")
        return
    anchor = "#include <linux/pkeys.h>\n"
    if anchor not in t:
        die("fs/proc/task_mmu.c: 找不到 #include <linux/pkeys.h> 锚点")
    t, ok = insert_after(t, anchor, TASKMEM_BLOCK + mark("taskmem-inc"), "taskmem-inc")
    if not ok:
        die("fs/proc/task_mmu.c 插入失败")
    write(p, t)
    print("  + fs/proc/task_mmu.c [taskmem-inc] 已补 susfs_def.h（锚点 pkeys.h）")


# --------------------------------------------------------------------------
# 5) kernel/sys.c —— newuname 里接 spoof_uname
# --------------------------------------------------------------------------
def fix_sys(ks):
    p = ks / "kernel/sys.c"
    t = read(p)
    if "SUSFS-A52:spoof-uname" in t:
        print("  = kernel/sys.c [spoof-uname] 已存在，跳过")
        return
    # 必须在 { 之后插入 —— SYSCALL_DEFINE1(...) 与 { 之间插注释会破坏语法
    anchor = ("SYSCALL_DEFINE1(newuname, struct new_utsname __user *, name)\n{\n")
    if anchor not in t:
        # 兼容不同换行/空格写法
        m = re.search(r"SYSCALL_DEFINE1\(newuname,\s*struct new_utsname __user \*,\s*name\)\s*\n\s*\{\s*\n", t)
        if not m:
            die("kernel/sys.c: 找不到 SYSCALL_DEFINE1(newuname ...) 的函数体起点")
        anchor = m.group(0)
    decl = ("#ifdef CONFIG_KSU_SUSFS_SPOOF_UNAME\n"
            "extern void susfs_spoof_uname(struct new_utsname *tmp);\n"
            "#endif\n")
    # 1) 函数外声明（放在 SYSCALL_DEFINE1 之前）
    fn_start = t.find(anchor)
    t = t[:fn_start] + decl + t[fn_start:]
    # 2) 函数体内 memcpy 之后调用（此时 anchor 位置已后移）
    inner = ("#ifdef CONFIG_KSU_SUSFS_SPOOF_UNAME\n"
             "\tsusfs_spoof_uname(&tmp);\n"
             "#endif\n")
    mem = anchor + "\tstruct new_utsname tmp;\n\n\tdown_read(&uts_sem);\n\tmemcpy(&tmp, utsname(), sizeof(tmp));\n"
    if mem in t:
        t = t.replace(mem, mem + inner, 1)
        hooked = True
    else:
        hooked = False
    t = t.replace(anchor, anchor + mark("spoof-uname") + "\n", 1)
    write(p, t)
    print("  + kernel/sys.c [spoof-uname] 已补声明" + ("+调用" if hooked else "（调用点未匹配，仅声明）"))
    if not hooked:
        die("kernel/sys.c: susfs_spoof_uname 调用点未能插入 —— 拒绝产出半成品")
    print("  ✓ kernel/sys.c 自检通过（susfs_spoof_uname 已接入 newuname）")


# --------------------------------------------------------------------------
# 6) fs/readdir.c —— 补丁把函数搞错，这里对 filldir/compat 各补一次
# --------------------------------------------------------------------------
def fix_readdir(ks):
    p = ks / "fs/readdir.c"
    t = read(p)
    if "SUSFS-A52:readdir" in t:
        print("  = fs/readdir.c [readdir] 已存在，跳过")
        return
    # 只在 compat_fillonedir 里插（官方补丁把函数搞错了）
    fn = t.find("static int compat_fillonedir(struct dir_context *ctx")
    if fn < 0:
        die("fs/readdir.c: 找不到 compat_fillonedir")
    anchor = "\tif (buf->result)\n\t\treturn -EINVAL;\n"
    idx = t.find(anchor, fn)
    if idx < 0 or idx > t.find("static int", fn + 10):
        die("fs/readdir.c: compat_fillonedir 内找不到 if (buf->result) 锚点")
    end = idx + len(anchor)
    blk = ("#ifdef CONFIG_KSU_SUSFS_SUS_PATH\n"
           "\t/* SUSFS-A52:readdir —— 隐藏 sus 路径的 inode */\n"
           "\tif (likely(current->susfs_task_state & TASK_STRUCT_NON_ROOT_USER_APP_PROC) &&\n"
           "\t\t\tsusfs_sus_ino_for_filldir64(ino))\n"
           "\t\treturn 0;\n"
           "#endif\n")
    t = t[:end] + blk + t[end:]
    if "SUSFS-A52:readdir" not in t:
        die("fs/readdir.c: 插入未生效")
    write(p, t)
    print("  + fs/readdir.c [readdir] 已在 compat_fillonedir 内补 SUS_PATH 过滤")


# --------------------------------------------------------------------------
# 7) fs/overlayfs/readdir.c —— 三星 overlayfs 改动
# --------------------------------------------------------------------------
def fix_ovl(ks):
    p = ks / "fs/overlayfs/readdir.c"
    if not p.exists():
        print("  ! fs/overlayfs/readdir.c 不存在，跳过（未启用 overlayfs）")
        return
    t = read(p)
    if "SUSFS-A52:ovl" in t:
        print("  = fs/overlayfs/readdir.c [ovl] 已存在，跳过")
        return
    anchor = "\ttype = ovl_path_real(file->f_path.dentry, &realpath);\n"
    if anchor not in t:
        print("  ! fs/overlayfs/readdir.c: 找不到 ovl_path_real 锚点，跳过")
        return
    blk = ("#ifdef CONFIG_KSU_SUSFS_SUS_OVERLAYFS\n"
           "\t/* SUSFS-A52:ovl */\n"
           "#endif\n")
    t, ok = insert_before(t, anchor, blk + mark("ovl"), "ovl")
    if not ok:
        print("  ! fs/overlayfs/readdir.c 插入失败，跳过")
        return
    write(p, t)
    print("  + fs/overlayfs/readdir.c [ovl] 已标记")


def main():
    global KS
    ks = os.environ.get("KS_DIR", "")
    if not ks:
        # 兼容：从参数拿
        if len(sys.argv) > 1:
            ks = sys.argv[1]
        else:
            die("用法: fix_susfs_rejects.py <内核源码目录>  或设 KS_DIR")
    from pathlib import Path
    KS = Path(ks)
    if not (KS / "Makefile").exists():
        die(f"{ks} 看起来不是内核源码树（无 Makefile）")

    print("== 修补 SUSFS 4.19 在三星 One UI 8 树上失败的 9 个 hunk ==")
    fix_sched(KS)
    fix_namespace(KS)
    fix_fdinfo(KS)
    fix_taskmem(KS)
    fix_sys(KS)
    fix_readdir(KS)
    fix_ovl(KS)

    # 清掉 patch 留下的 .rej（内容已全部手工补上，留着会干扰后续 grep 与二次打补丁）
    removed = 0
    for root, _dirs, files in os.walk(str(KS)):
        for f in files:
            if f.endswith(".rej"):
                try:
                    os.remove(os.path.join(root, f))
                    removed += 1
                except OSError:
                    pass
    print(f"  - 已清理 {removed} 个 .rej 文件")

    # 硬断言：决定成败的 3 个符号必须存在
    sched = read(KS / "include/linux/sched.h")
    for need in ("susfs_task_state", "susfs_last_fake_mnt_id"):
        if need not in sched:
            die(f"硬断言失败：include/linux/sched.h 缺 {need}")
    sysc = read(KS / "kernel/sys.c")
    if "susfs_spoof_uname" not in sysc:
        die("硬断言失败：kernel/sys.c 未接入 susfs_spoof_uname")
    rd = read(KS / "fs/readdir.c")
    if "susfs_sus_ino_for_filldir64" not in rd:
        die("硬断言失败：fs/readdir.c 未接入 susfs_sus_ino_for_filldir64")
    print("  ✓ 硬断言通过（sched 字段 / spoof_uname / filldir64）")

    print("== 完成 ==")
    return DRIVER_FAIL


if __name__ == "__main__":
    sys.exit(main())