#!/usr/bin/env python3
"""4.19 适配：修补官方 SukiSU builtin 线驱动在 4.19 内核上编译不过的几处。

用法: fix_driver_419.py <驱动根目录>          （驱动根目录下应有 kernel/ 子目录）

每一处都是确定性的精确替换 + 自检；任何一处没生效都会以非 0 退出。
"""
import re
import sys
from pathlib import Path

root = Path(sys.argv[1] if len(sys.argv) > 1 else "KernelSU").resolve()
K = root / "kernel"


def read(p: Path) -> str:
    return p.read_text(encoding="utf-8", errors="surrogateescape")


def write(p: Path, s: str) -> None:
    p.write_text(s, encoding="utf-8", errors="surrogateescape")


report: list[str] = []

# 1) include/uapi/feature.h
#    KSU_FEATURE_SELINUX_HIDE 被 "#if LINUX_VERSION_CODE >= 5.10" 包着，
#    但 policy/feature.c 无条件引用它 → 4.19 上枚举缺项、编译失败。去掉 guard。
p = K / "include" / "uapi" / "feature.h"
OLD1 = ("#if LINUX_VERSION_CODE >= KERNEL_VERSION(5, 10, 0)\n"
        "    KSU_FEATURE_SELINUX_HIDE = 4,\n"
        "#endif\n")
if p.is_file():
    s = read(p)
    if OLD1 in s:
        s = s.replace(
            OLD1,
            "    KSU_FEATURE_SELINUX_HIDE = 4, /* KSU-A52: 去掉 >=5.10 guard（feature.c 无条件引用） */\n",
        )
        write(p, s)
        report.append("[+] feature.h: KSU_FEATURE_SELINUX_HIDE 已解除 guard")
    else:
        report.append("[=] feature.h: 无需修改")
else:
    report.append("[!] 找不到 feature.h")
    report.append("[-] include/uapi/feature.h 缺失")
    print("\n".join(report))
    sys.exit(1)

# 2) kernel/kernel_includes.h —— 补 4.19 没有的 fallthrough 宏（5.x 才在 compiler_attributes.h）
p = K / "kernel_includes.h"
if p.is_file():
    s = read(p)
    if "KSU-A52 fallthrough compat" not in s:
        s += (
            "\n/* KSU-A52 fallthrough compat: 4.19 没有 fallthrough 宏（5.x 才有） */\n"
            "#ifndef fallthrough\n"
            "#define fallthrough do { } while (0)\n"
            "#endif\n"
        )
        write(p, s)
        report.append("[+] kernel_includes.h: 已补 fallthrough")
    else:
        report.append("[=] kernel_includes.h: fallthrough 已存在")
else:
    report.append("[!] 找不到 kernel_includes.h")
    print("\n".join(report))
    sys.exit(1)

# 3) kernel/extension/cpu_spoof.c —— struct clocksource::vdso_clock_mode 是 5.3 才加的字段
p = K / "extension" / "cpu_spoof.c"
if p.is_file():
    s = read(p)
    OLD3 = ("                        (*curr_cs_ptr)->vdso_clock_mode =\n"
            "                            VDSO_CLOCKMODE_ARCHTIMER;\n")
    hit = s.find(OLD3)
    if hit == -1:
        report.append("[=] cpu_spoof.c: 无需修改（或已处理）")
    elif "#if LINUX_VERSION_CODE >= KERNEL_VERSION(5, 3, 0)" in s[max(0, hit - 220):hit]:
        report.append("[=] cpu_spoof.c: guard 已存在（幂等）")
    else:
        s = s.replace(
            OLD3,
            "#if LINUX_VERSION_CODE >= KERNEL_VERSION(5, 3, 0)\n" + OLD3 + "#endif\n",
            1,
        )
        write(p, s)
        report.append("[+] cpu_spoof.c: vdso_clock_mode 赋值已加 >=5.3 guard")
else:
    report.append("[=] 没有 extension/cpu_spoof.c，跳过")

# 4) kernel/sulog/event.c —— 【不要动 user_arg_null_ptr()】
#    本项目必然打开 CONFIG_KSU_SUSFS，此时 event.c 走第 93 行起的 SUSFS 分支：
#        ksu_sulog_flatten_argv(struct user_arg_ptr *argv_user, ...)
#        ksu_sulog_capture(..., struct user_arg_ptr *argv_user, gfp)
#    而 user_arg_null_ptr() 上游本来就返回指针 → 原样即可。
#    早期版本把它改成「返回结构体值」，反而在 4.19 上编译失败：
#        error: passing 'struct user_arg_ptr' to parameter of incompatible type 'struct user_arg_ptr *'
#    这里只做「撤销旧改动 + 校验」，不再改写。
p = K / "sulog" / "event.c"
if p.is_file():
    s = read(p)
    if "KSU-A52: 返回结构体值" in s:
        s = s.replace(
            "static inline struct user_arg_ptr user_arg_null_ptr(void) /* KSU-A52: 返回结构体值 */",
            "static inline struct user_arg_ptr *user_arg_null_ptr(void)",
        )
        s = s.replace("return null_arg;", "return &null_arg;")
        write(p, s)
        report.append("[+] event.c: 已撤销「返回结构体值」改动（SUSFS 分支需要指针）")
    if "static inline struct user_arg_ptr *user_arg_null_ptr(void)" in s:
        report.append("[=] event.c: user_arg_null_ptr 返回指针，与 SUSFS 分支一致")
    else:
        report.append("[!] event.c: 未找到 user_arg_null_ptr 指针版本 —— 上游结构变了，需人工确认")
else:
    report.append("[=] 没有 sulog/event.c，跳过")

# 5) kernel/extension/kpm/kpm.c —— 5.x 的 access_ok(addr, size) 是 2 参数；
#    4.19 是 access_ok(type, addr, size)。arm64 的 4.19 根本不看 type，VERIFY_READ 完全安全。
p = K / "extension" / "kpm" / "kpm.c"
if p.is_file():
    s = read(p)
    n = len(re.findall(r"access_ok\((?!VERIFY_)", s))
    if n:
        s = re.sub(r"access_ok\((?!VERIFY_)", "access_ok(VERIFY_READ, ", s)
        write(p, s)
        report.append(f"[+] kpm.c: {n} 处 access_ok 已补 VERIFY_READ")
    else:
        report.append("[=] kpm.c: access_ok 无需修改")
else:
    report.append("[=] 没有 extension/kpm/kpm.c，跳过")

# 6) kernel/runtime/ksud.c —— selinux_hide 的【实现】被 >=5.10 的 guard 排除，
#    但这里的【调用】是无条件的 → 4.19 链接时报 undefined reference。
#    用同一个 guard 把调用包起来（保持缩进，幂等）。
p = K / "runtime" / "ksud.c"
if p.is_file():
    s = read(p)
    if "KSU-A52 selinux_hide guard" in s:
        report.append("[=] ksud.c: selinux_hide 调用已有 guard（幂等）")
    else:
        pat = re.compile(r"(?m)^([ \t]*)(ksu_selinux_hide_[a-z_]+\(\);)")
        n = len(pat.findall(s))
        if n:
            s = pat.sub(
                lambda m: ("#if LINUX_VERSION_CODE >= KERNEL_VERSION(5, 10, 0) /* KSU-A52 selinux_hide guard */\n"
                           f"{m.group(1)}{m.group(2)}\n#endif"),
                s,
            )
            write(p, s)
            report.append(f"[+] ksud.c: {n} 处 selinux_hide 调用已加 >=5.10 guard")
        else:
            report.append("[=] ksud.c: 未找到 selinux_hide 调用")
else:
    report.append("[=] 没有 runtime/ksud.c，跳过")

# 7) fs/exec.c —— 【实机崩溃点】官方 4.19 钩子补丁插入的 else 分支把 argv/envp/flags 传成 NULL，
#    而 builtin 驱动的 ksu_handle_execveat_sucompat 会解引用 argv。
#    开机时 ksu_execveat_hook 尚为 false，于是每次 execve 都走这一分支 →
#    第一次 execve 就 NULL 解引用 → Kernel panic。
#    实测崩溃栈：pc : ksu_handle_execveat_sucompat+0xd0 ; lr : ...+0x84 ; do_execve+0x50。
#    修法：把真实的 &argv/&envp 传进去；32 位那条分支（su 是 64 位，用不到）直接不启用。
pe = None
for cand in (K.parent.parent / "fs" / "exec.c", K.parent / "fs" / "exec.c"):
    if cand.is_file():
        pe = cand
        break
if pe is None:
    pe = K.parent.parent / "fs" / "exec.c"
    report.append(f"[!] 找不到 fs/exec.c（试过 {pe}）—— 钩子补丁是否已应用？")
if pe.is_file():
    se = read(pe)
    before = se
    # 先处理 32 位 compat 那一行（注释掉整条调用；其 argv 类型不同，不能照搬 &argv）
    se = se.replace(
        "\t\tksu_handle_execveat_sucompat((int *)AT_FDCWD, &filename, NULL, NULL, NULL); /* 32-bit su */",
        "\t\t/* KSU-A52: 32 位 su 路径原样传 NULL 会 panic，这里不启用 */",
    )
    # 再处理 64 位 do_execve 路径：传真实 argv/envp
    se = se.replace(
        "ksu_handle_execveat_sucompat((int *)AT_FDCWD, &filename, NULL, NULL, NULL);",
        "ksu_handle_execveat_sucompat((int *)AT_FDCWD, &filename, &argv, &envp, 0); /* KSU-A52: 原来传 NULL 会 panic */",
    )
    if se != before:
        write(pe, se)
        left = se.count("sucompat((int *)AT_FDCWD, &filename, NULL, NULL, NULL)")
        report.append(f"[+] fs/exec.c: sucompat 的 NULL 传参已修复（残留 NULL 调用 {left} 处）")
    else:
        report.append("[=] fs/exec.c: 未找到 sucompat NULL 调用（或已修复）")
else:
    report.append("[!] 找不到 fs/exec.c（钩子补丁是否已应用？）")

# 8) kernel/reboot.c —— 【内核 API 通道的唯一缺口】
#    驱动的 supercall 是"匿名 fd 上的 ioctl"（supercall/supercall.c: anon_ksu_ioctl）。
#    ksud 要拿到这个 fd，必须走 reboot(KSU_INSTALL_MAGIC1, KSU_INSTALL_MAGIC2, ...) 通道：
#        int ksu_handle_sys_reboot(int magic1, int magic2, unsigned int cmd, void __user **arg)
#        -> magic2 == KSU_INSTALL_MAGIC2 时调用 ksu_handle_fd_request() 安装 fd
#    该函数在驱动里存在、符号也已导出，但 4.19 钩子补丁【没有】在 kernel/reboot.c 里调用它。
#    后果：ksud debug info 的 version/uapi_version/features 全为 0，
#          Zygisk Next 靠这条通道探测 Root → root_impl: 0 → module.prop 写成 [Root: ❌None]。
pb = K.parent.parent / "kernel" / "reboot.c"
if pb.is_file():
    sb = read(pb)
    if "KSU-A52 sys_reboot hook" in sb:
        # 挂钩已存在，但早期版本把返回值判断写反了 —— 驱动里 ksu_handle_sys_reboot()
        # 返回 0 表示「已接管」，返回负值表示「这不是 KSU 的 reboot」。写成 if (返回值) 之后：
        #   普通 reboot（magic1 != KSU_INSTALL_MAGIC1）→ 返回 -EINVAL（真）→ return 0
        #   → reboot() 报告成功却根本不重启 → init 打印 reboot call returned →
        #   init 退出 → do_exit → !CONFIG_PID_NS 的 zap_pid_ns_processes(){BUG();}
        #   → kernel BUG at include/linux/pid_namespace.h:90 → panic（关机必崩）。
        if "(magic1, magic2, cmd, &arg) == 0)" in sb:
            report.append("[=] reboot.c: sys_reboot 挂钩已存在且判断正确（幂等）")
        elif "ksu_handle_sys_reboot(magic1, magic2, cmd, &arg))" in sb:
            sb = sb.replace(
                "if (ksu_handle_sys_reboot(magic1, magic2, cmd, &arg))",
                "if (ksu_handle_sys_reboot(magic1, magic2, cmd, &arg) == 0)",
                1,
            )
            write(pb, sb)
            report.append("[FIX] reboot.c: 挂钩判断方向已纠正（旧写法会吞掉正常 reboot 并导致关机 panic）")
        else:
            report.append("[!] reboot.c: 有挂钩但形态不认识 —— 需人工确认")
    else:
        idx = sb.find("SYSCALL_DEFINE4(reboot")
        brace = sb.find("\n{\n", idx) if idx != -1 else -1
        if idx != -1 and brace != -1:
            ins = brace + len("\n{\n")
            hook = (
                "#ifdef CONFIG_KSU\n"
                "\t{\n"
                "\t\textern int ksu_handle_sys_reboot(int magic1, int magic2, unsigned int cmd,\n"
                "\t\t\t\t\t\t    void __user **arg);\n"
                "\t\t/* KSU-A52 sys_reboot hook: 打通 ksud 获取 supercall fd 的通道 */\n"
                "\t\tif (ksu_handle_sys_reboot(magic1, magic2, cmd, &arg) == 0)\n"
                "\t\t\treturn 0;\n"
                "\t}\n"
                "#endif\n"
            )
            sb = sb[:ins] + hook + sb[ins:]
            write(pb, sb)
            report.append("[+] reboot.c: 已插入 ksu_handle_sys_reboot 挂钩（打通 userspace API）")
        else:
            report.append("[!] reboot.c: 未找到 SYSCALL_DEFINE4(reboot) 锚点 —— 需人工确认")
else:
    report.append("[!] 找不到 kernel/reboot.c")

# 9) preinit runner —— 4.19 的 uapi 没有 struct clone_args（5.3+ 才有），
#    nolibc/sys.h 里 sys_vfork() 的 clone3 回退分支因此编译失败：
#        error: variable has incomplete type 'struct clone_args'
#    arm64 本身有 __NR_vfork（= 58），不该走 clone3 分支。
#    让 arm64 明确走 vfork 分支即可绕开 clone_args/clone3。
#    preinit 必须能编出来，否则模块 post-fs-data/service 阶段会被"跑完即回收"，
#    Zygisk Next 的 zygiskd 无法常驻。
pns = K / "preinit" / "runner" / "nolibc" / "sys.h"
if pns.is_file():
    sns = read(pns)
    if "KSU-A52 vfork for arm64" in sns:
        report.append("[=] nolibc/sys.h: arm64 vfork 分支已处理（幂等）")
    elif "#if defined(__NR_vfork)\n" in sns:
        sns = sns.replace(
            "#if defined(__NR_vfork)\n",
            "#if defined(__NR_vfork) || defined(__aarch64__) /* KSU-A52 vfork for arm64 */\n",
            1,
        )
        write(pns, sns)
        report.append("[+] nolibc/sys.h: arm64 走 vfork 分支（绕开 clone_args/clone3）")
    else:
        report.append("[!] nolibc/sys.h: 未找到 __NR_vfork 锚点")
else:
    report.append("[=] 没有 preinit/runner/nolibc/sys.h，跳过")

# 10) kernel/sulog/event.c —— 4.19 没有 strncpy_from_user_nofault()（5.8 才有）。
#    驱动自带的 kernel_compat.h 已提供 ksu_strncpy_from_user_nofault()，
#    同一文件 81 行、supercall/dispatch.c 512 行都在用它，只有 143 行漏了 →
#    链接报错：
#        sulog/event.c:143: undefined reference to `strncpy_from_user_nofault'
#    这里只改这一处；带 (?<!ksu_) 负向断言，重复执行不会叠成 ksuksu_。
p = K / "sulog" / "event.c"
if p.is_file():
    s = read(p)
    npat = re.compile(r"(?<!ksu_)strncpy_from_user_nofault\([ \t]*arg,")
    if npat.search(s):
        s, n10 = npat.subn("ksu_strncpy_from_user_nofault(arg,", s)
        write(p, s)
        report.append(f"[+] event.c: {n10} 处 strncpy_from_user_nofault 改走 ksu_ 兼容实现（4.19 无此符号）")
    elif "ksu_strncpy_from_user_nofault(arg," in s:
        report.append("[=] event.c: nofault 拷贝已走 ksu_ 兼容实现（幂等）")
    else:
        report.append("[!] event.c: 未找到 strncpy_from_user_nofault(arg, ...) —— 上游结构变了")
else:
    report.append("[=] 没有 sulog/event.c，跳过")

# 11) feature/sucompat.{c,h} —— 【开机 1.2 秒 panic 的元凶】SUSFS 分支的钩子 ABI 只适用于 5.10+
#     驱动把 faccessat/stat 的实现分成两套：
#       #ifdef CONFIG_KSU_SUSFS  → 内核侧传 struct filename **（5.10+ 的钩子形态）
#       #else                    → 内核侧传 const char __user **（4.19 自己的钩子形态）
#     而本项目 4.19 的钩子补丁（fs/open.c、fs/stat.c）传的正是 const char __user **。
#     一旦 CONFIG_KSU_SUSFS=y，驱动就会编出 struct filename ** 那套，
#     于是 (*filename)->name 把「用户地址」当内核指针解引用 → PAN 故障：
#         Unable to handle kernel access to user memory outside uaccess routines
#         at virtual address 000000000020623d
#         pc : ksu_handle_faccessat+0x18/0xb4   lr : __arm64_sys_faccessat+0x40/0x74
#         Kernel panic - not syncing: Fatal exception    (~1.2 s，init pid 1)
#     → 开机即 panic，无限重启。
#     修法：守卫改成「版本感知」，4.19 一律走 #else 那套（与钩子补丁一致）。
#     安全性已核对：SUSFS 分支独有的 ksu_handle_execveat_init / ksu_handle_post_execveat_sucompat
#     在别处只被 extern 声明、从未被调用（runtime/ksud.c:186 那行是纯声明），
#     所以关掉 SUSFS 分支不会留下未定义符号。
VER_HDR = "#include <linux/version.h>\n"
OLD_GUARD = "#ifdef CONFIG_KSU_SUSFS\n"
NEW_GUARD = ("#if defined(CONFIG_KSU_SUSFS) && (LINUX_VERSION_CODE >= KERNEL_VERSION(5, 10, 0))"
             " /* KSU-A52: 4.19 钩子传 const char __user **，SUSFS 分支只适合 5.10+ */\n")
for rel in ("feature/sucompat.c", "feature/sucompat.h"):
    sp = K / rel
    if not sp.is_file():
        report.append(f"[=] 没有 {rel}，跳过")
        continue
    t = read(sp)
    if "SUSFS 分支只适合 5.10+" in t:
        report.append(f"[=] {rel}: SUSFS 分支已加版本守卫（幂等）")
        continue
    if OLD_GUARD not in t:
        report.append(f"[!] {rel}: 未找到 '#ifdef CONFIG_KSU_SUSFS' 锚点 —— 上游结构变了")
        continue
    if "linux/version.h" not in t:
        t = t.replace(OLD_GUARD, VER_HDR + OLD_GUARD, 1)
    t = t.replace(OLD_GUARD, NEW_GUARD, 1)
    write(sp, t)
    report.append(f"[+] {rel}: SUSFS 钩子分支已限定为 5.10+（4.19 改走 const char __user ** 实现）")

# 12) sulog/event.{c,h} —— 必须和第 11 组保持同一套 ABI（否则 4.19 编不过，且运行会 fault）
#     第 11 组把 feature/sucompat.{c,h} 的 SUSFS 分支限定为 5.10+ 后，4.19 走的是
#     「const char __user *filename + 传值 struct user_arg_ptr」那套：
#         ksu_sulog_capture_sucompat(*filename_user, *((struct user_arg_ptr *)argv), GFP_KERNEL);
#     但 sulog/event.h / event.c 仍按 CONFIG_KSU_SUSFS 声明「const char * + struct user_arg_ptr *」，
#     于是 4.19 编译直接失败（实测 run 40453939277）：
#         feature/sucompat.c:308:52: error: passing 'struct user_arg_ptr' to parameter of
#             incompatible type 'struct user_arg_ptr *'; remove *
#         feature/sucompat.c:328:59: 同上
#     而且 event.c 的 SUSFS 分支用 ksu_sulog_copy_filename_kernel()（纯内核 memcpy 语义），
#     把 __user 指针塞进去，在 arm64 + PAN 下会直接 fault —— 必须让 sulog 也走 _user 那套。
#     修法：event.h / event.c 的 CONFIG_KSU_SUSFS 守卫改成「版本感知」，
#           event.c 的 #ifndef CONFIG_KSU_SUSFS 反向守卫同步改，
#           user_arg_null_ptr() 改回「返回值」（非 SUSFS 的 ksu_sulog_capture 收结构体值）。
SL_NEW_GUARD = ("#if defined(CONFIG_KSU_SUSFS) && (LINUX_VERSION_CODE >= KERNEL_VERSION(5, 10, 0))"
                " /* KSU-A52: 4.19 用 _user 那套 sulog 实现 */\n")
SL_OLD_IFNDEF = "#ifndef CONFIG_KSU_SUSFS\n"
SL_NEW_IFNDEF = ("#if !defined(CONFIG_KSU_SUSFS) || (LINUX_VERSION_CODE < KERNEL_VERSION(5, 10, 0))"
                 " /* KSU-A52: 4.19 用 _user 那套 sulog 实现 */\n")
SL_MARK = "KSU-A52: 4.19 用 _user 那套 sulog 实现"
for rel in ("sulog/event.h", "sulog/event.c"):
    sp = K / rel
    if not sp.is_file():
        report.append(f"[=] 没有 {rel}，跳过")
        continue
    t = read(sp)
    changed = []
    if SL_MARK not in t:
        if "linux/version.h" not in t:
            t = VER_HDR + t
            changed.append("include")
        if rel == "sulog/event.h":
            if OLD_GUARD in t:
                t = t.replace(OLD_GUARD, SL_NEW_GUARD, 1)
                changed.append("decl-guard")
        else:
            n = t.count(OLD_GUARD)
            if n:
                t = t.replace(OLD_GUARD, SL_NEW_GUARD)
                changed.append(f"def-guard x{n}")
            if SL_OLD_IFNDEF in t:
                t = t.replace(SL_OLD_IFNDEF, SL_NEW_IFNDEF, 1)
                changed.append("ifndef-guard")
    # user_arg_null_ptr 必须「返回值」：4.19 的非 SUSFS ksu_sulog_capture() 收结构体值
    if "static inline struct user_arg_ptr *user_arg_null_ptr(void)" in t:
        t = t.replace(
            "static inline struct user_arg_ptr *user_arg_null_ptr(void)\n"
            "{\n    static struct user_arg_ptr null_arg = { 0 };\n    return &null_arg;\n}",
            "static inline struct user_arg_ptr user_arg_null_ptr(void)" + f" /* {SL_MARK} */\n"
            "{\n    struct user_arg_ptr null_arg = { 0 };\n    return null_arg;\n}")
        changed.append("null-ptr->value")
    if changed:
        write(sp, t)
        report.append(f"[+] {rel}: " + ", ".join(changed))
    else:
        report.append(f"[=] {rel}: sulog 4.19 ABI 已就位（幂等）")

# 13) feature/adb_root.c —— 和第 11/12 组同一个坑：SUSFS 分支只适用于 5.10+
#     第 11 组让 4.19 的 sucompat 走「非 SUSFS」那套后，ksu_handle_execveat() 里会调用
#         ksu_adb_root_handle_execve_manual((*filename_ptr)->name, (struct user_arg_ptr *)envp);
#     而这个函数只在 adb_root.c 的「非 SUSFS」分支（186-325 行）里实现，
#     SUSFS 分支没有 → 编译过了、链接挂（实测 run 40460210337）：
#         drivers/kernelsu/feature/sucompat.c:357: undefined reference to
#             `ksu_adb_root_handle_execve_manual'
#     修法：adb_root.c 的两处 CONFIG_KSU_SUSFS 守卫也改成版本感知，4.19 回到
#     和「无 SUSFS 的老版本内核」完全相同的普通实现（那条路本来就编过、能开机）。
AB_MARK = "KSU-A52: 4.19 用普通 adb_root 实现"
pab = K / "feature" / "adb_root.c"
if not pab.is_file():
    report.append("[=] 没有 feature/adb_root.c，跳过")
else:
    sab = read(pab)
    if AB_MARK in sab:
        report.append("[=] adb_root.c: SUSFS 分支已加版本守卫（幂等）")
    elif OLD_GUARD not in sab:
        report.append("[!] adb_root.c: 未找到 '#ifdef CONFIG_KSU_SUSFS' 锚点 —— 上游结构变了")
    else:
        nab = sab.count(OLD_GUARD)
        sab = sab.replace(
            OLD_GUARD,
            "#if defined(CONFIG_KSU_SUSFS) && (LINUX_VERSION_CODE >= KERNEL_VERSION(5, 10, 0))"
            f" /* {AB_MARK} */\n",
        )
        write(pab, sab)
        report.append(
            f"[+] adb_root.c: {nab} 处 SUSFS 守卫改成版本感知"
            "（4.19 用普通实现，补上 ksu_adb_root_handle_execve_manual）"
        )

print("\n".join(report))

# ---- 自检 ----
ok = True
if OLD1 in read(K / "include" / "uapi" / "feature.h"):
    ok = False
    print("[!] feature.h 的 guard 仍然存在")
if "KSU-A52 fallthrough compat" not in read(K / "kernel_includes.h"):
    ok = False
    print("[!] fallthrough 未补上")
cp = K / "extension" / "cpu_spoof.c"
if cp.is_file():
    sc = read(cp)
    hit = sc.find("vdso_clock_mode =")
    if hit != -1 and "#if LINUX_VERSION_CODE >= KERNEL_VERSION(5, 3, 0)" not in sc[max(0, hit - 200):hit]:
        ok = False
        print("[!] cpu_spoof.c 的 guard 未生效")
ev = K / "sulog" / "event.c"
if ev.is_file():
    se = read(ev)
    # 【关键】4.19 必须走「_user + 传值」那套 sulog：守卫版本感知 + user_arg_null_ptr 返回值
    if "KSU-A52: 4.19 用 _user 那套 sulog 实现" not in se:
        ok = False
        print("[!] 致命：event.c 的 sulog 分支未改成版本感知（4.19 编译会报 user_arg_ptr 类型不匹配）")
    if "static inline struct user_arg_ptr user_arg_null_ptr(void)" not in se:
        ok = False
        print("[!] 致命：event.c 的 user_arg_null_ptr 必须返回值（4.19 的非 SUSFS capture 收结构体值）")
    if "return &null_arg;" in se:
        ok = False
        print("[!] 致命：event.c 的 user_arg_null_ptr 仍返回指针（与 4.19 传值 API 不匹配）")
    # 【链接】4.19 没有 strncpy_from_user_nofault()，必须走 ksu_ 兼容实现
    if re.search(r"(?<!ksu_)strncpy_from_user_nofault\(", se):
        ok = False
        print("[!] 致命：event.c 仍有裸 strncpy_from_user_nofault（4.19 链接会 undefined reference）")
eh = K / "sulog" / "event.h"
if eh.is_file() and "KSU-A52: 4.19 用 _user 那套 sulog 实现" not in read(eh):
    ok = False
    print("[!] 致命：event.h 的 ksu_sulog_capture_sucompat 声明未版本感知（与 sucompat.c 实参不匹配）")
kp = K / "extension" / "kpm" / "kpm.c"
if kp.is_file() and re.search(r"access_ok\((?!VERIFY_)", read(kp)):
    ok = False
    print("[!] kpm.c 仍有 2 参数 access_ok")
ks = K / "runtime" / "ksud.c"
if ks.is_file():
    sk = read(ks)
    if re.search(r"(?m)^[ \t]*ksu_selinux_hide_[a-z_]+\(\);", sk) and "KSU-A52 selinux_hide guard" not in sk:
        ok = False
        print("[!] ksud.c 的 selinux_hide 调用未加 guard")
# 【最关键的一条】fs/exec.c 里绝不能剩下传 NULL 的 sucompat 调用
if pe.is_file():
    sfin = read(pe)
    if "sucompat((int *)AT_FDCWD, &filename, NULL, NULL, NULL)" in sfin:
        ok = False
        print("[!] 致命：fs/exec.c 里仍有 sucompat(..., NULL, NULL, NULL) —— 会 NULL 解引用 panic")
    elif "KSU-A52: 原来传 NULL 会 panic" not in sfin:
        ok = False
        print("[!] fs/exec.c 的 sucompat 修复未生效")
# 【内核 API 通道】reboot.c 必须调用 ksu_handle_sys_reboot，否则 ksud/znctl 全部查询返回 0
if pb.is_file():
    srb = read(pb)
    if "KSU-A52 sys_reboot hook" not in srb:
        ok = False
        print("[!] 致命：kernel/reboot.c 缺少 ksu_handle_sys_reboot 挂钩 —— 内核 API 会全部返回 0")
    elif "ksu_handle_sys_reboot(magic1, magic2, cmd, &arg)" not in srb:
        ok = False
        print("[!] reboot.c 的挂钩形式不对")
    elif "(magic1, magic2, cmd, &arg) == 0)" not in srb:
        ok = False
        print("[!] 致命：reboot.c 挂钩判断方向反了 —— 正常 reboot 会被吞掉，关机时 init 退出 → panic")
else:
    ok = False
    print("[!] 找不到 kernel/reboot.c")
# 【开机 panic 的元凶】sucompat 的 SUSFS 分支必须限定 5.10+，否则 4.19 一开机就 PAN 故障 panic
for rel in ("feature/sucompat.c", "feature/sucompat.h"):
    sp2 = K / rel
    if sp2.is_file() and "SUSFS 分支只适合 5.10+" not in read(sp2):
        ok = False
        print(f"[!] 致命：{rel} 的 SUSFS 分支未加版本守卫 —— 4.19 上开机约 1.2 秒必 panic 重启")
if pns.is_file() and "KSU-A52 vfork for arm64" not in read(pns):
    ok = False
    print("[!] 致命：preinit runner 的 arm64 vfork 分支未处理（4.19 会因 clone_args 编译失败）")
# 【链接缺口】adb_root.c 的 SUSFS 分支必须同样限定 5.10+，否则
#   sucompat.c:357 undefined reference to `ksu_adb_root_handle_execve_manual'
if pab.is_file() and AB_MARK not in read(pab):
    ok = False
    print("[!] 致命：adb_root.c 的 SUSFS 分支未版本感知 —— 4.19 链接会 undefined reference")
    print("    to `ksu_adb_root_handle_execve_manual'（该函数只在非 SUSFS 分支实现）")
print("[OK] 驱动 4.19 适配检查通过" if ok else "[!] 有改动未生效")
sys.exit(0 if ok else 1)