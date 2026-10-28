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

# 14) feature/sucompat.c / runtime/ksud.c —— 两件事：
#     (a) 回滚我上一版「让 sucompat 和 ksud 钩子并行」的改法
#         （实测 run 40495354659 的产物刷上去 init 的 execve 直接 ENOENT、无限重启）
#     (b) 真正的原因：SUSFS 版 ksu_handle_execveat_ksud() 里漏了 stop_execve_hook()
#         非 SUSFS 版（ksud.c:343）在 zygote 启动后会把 ksu_execveat_hook 关掉，
#         之后 fs/exec.c 的钩子才会转去 sucompat，把 /system/bin/su 重定向到 ksud。
#         SUSFS 版只调了 on_post_fs_data()，钩子永远不关 →
#         execve("/system/bin/su") 命中 KSU 的幻影 inode（stat 有、read 0 字节）→ ENOENT
#         → 管理器取不到驱动 fd。
#     修法：在 SUSFS 版 zygote 分支补一句 stop_execve_hook()，与上游非 SUSFS 版完全一致。
SC_MARK = "KSU-A52: sucompat 必须和 ksud 钩子一起跑"
SC_OLD = """    if (unlikely(ksu_execveat_hook)) {
        return ksu_handle_execveat_ksud(fd, filename_ptr, argv, envp, flags);
    }

    return ksu_handle_execveat_sucompat(fd, filename_ptr, argv, envp, flags);"""
SC_NEW = """    /* KSU-A52: sucompat 必须和 ksud 钩子一起跑 —— SUSFS 分支从不关闭
       ksu_execveat_hook，若这里 return，su -> ksud 的重定向永远不执行，
       execve("/system/bin/su") 返回 ENOENT，管理器就取不到驱动 fd。 */
    if (unlikely(ksu_execveat_hook)) {
        ksu_handle_execveat_ksud(fd, filename_ptr, argv, envp, flags);
    }
    return ksu_handle_execveat_sucompat(fd, filename_ptr, argv, envp, flags);"""

psc = K / "feature" / "sucompat.c"
if not psc.is_file():
    report.append("[=] 没有 feature/sucompat.c，跳过 14(a)")
else:
    ssc = read(psc)
    if SC_MARK in ssc:
        write(psc, ssc.replace(SC_NEW, SC_OLD))
        report.append("[+] sucompat.c: 已回滚「并行调用」（上一版导致 init execve ENOENT）")
    elif SC_OLD in ssc:
        report.append("[=] sucompat.c: 分发逻辑已是上游原样（幂等）")
    else:
        report.append("[!] sucompat.c: 未找到 ksu_handle_execveat 分发锚点 —— 上游结构变了")

SE_MARK = "KSU-A52: SUSFS 版补 stop_execve_hook"
SE_OLD = """#ifdef KSU_COMPAT_USE_STATIC_KEY
                static_branch_disable(&is_first_zygote);
#else
                first_zygote = false;
#endif"""
SE_NEW = SE_OLD + """
                stop_execve_hook(); /* """ + SE_MARK + """（否则 su 重定向永不生效） */"""

pks = K / "runtime" / "ksud.c"
if not pks.is_file():
    report.append("[=] 没有 runtime/ksud.c，跳过 14(b)")
else:
    sks = read(pks)
    if SE_MARK in sks:
        report.append("[=] ksud.c: SUSFS 版已补 stop_execve_hook（幂等）")
    elif SE_OLD not in sks:
        report.append("[!] ksud.c: 未找到 SUSFS 版 zygote 分支锚点 —— 上游结构变了")
    else:
        write(pks, sks.replace(SE_OLD, SE_NEW, 1))
        report.append("[+] ksud.c: SUSFS 版 zygote 分支补上 stop_execve_hook()"
                      "（修 su 幻影 inode 的 ENOENT / 驱动 fd 取不到）")

# 15) manager/throne_tracker.c + runtime/ksud.c —— 管理器「未检出驱动」的确定性修复
#     现象：SUSFS 内核上管理器显示「未检到 SukiSU 驱动」，logcat 里是
#         KsuCli: gw2: Unable to create a shell!
#         Caused by: java.io.IOException: Cannot run program "su": error=2, No such file or directory
#     机理：驱动判「管理器永远放行」的唯一入口是
#         __ksu_is_allow_uid() -> is_uid_manager() -> ksu_manager_uid_authorized(uid)
#       而它只认两种来源：
#         (a) ksu_manager_appid == appid（由 track_throne() 扫 /data/app、校验 APK 签名后设置）
#         (b) dynamic_manager_enabled && 已登记 uid
#       4.19 + SUSFS 上 (a) 这条链（fsnotify 监听 packages.list + 内核读 APK 校验签名）不可靠；
#       (b) 又依赖管理器自己写 /data/adb/ksu/.dynamic_managers。
#       appid 一旦停在 KSU_INVALID_APPID，管理器进程的 su 就不被放行 →
#       execve("/system/bin/su") 命中幻影 inode → ENOENT → 「未检出驱动」。
#     修法：post-fs-data 阶段直接按包名从 /data/system/packages.list 取 uid 认领管理器；
#           只在 appid 未设置时执行，签名识别成功的情况下不会覆盖。
FM_MARK = "KSU-A52: 按包名兜底认领管理器"
FM_FUNC = """/* """ + FM_MARK + """（4.19+SUSFS 下签名识别链不可靠时的兜底） */
void ksu_force_crown_manager(void)
{
    struct file *fp;
    char *buf;
    int n;
    loff_t pos = 0;
    char *line, *nl, *sp;
    unsigned long uid;

    if (ksu_is_manager_appid_valid())
        return;
    fp = filp_open(SYSTEM_PACKAGES_LIST_PATH, O_RDONLY, 0);
    if (IS_ERR(fp)) {
        pr_info("KSU-A52: 打不开 " SYSTEM_PACKAGES_LIST_PATH "，跳过兜底认领\\n");
        return;
    }
    buf = kzalloc(65536, GFP_KERNEL);
    if (!buf) {
        filp_close(fp, 0);
        return;
    }
    n = kernel_read(fp, buf, 65536 - 1, &pos);
    filp_close(fp, 0);
    if (n <= 0) {
        kfree(buf);
        return;
    }
    buf[n] = '\\0';
    for (line = buf; line && *line; line = nl) {
        nl = strchr(line, '\\n');
        if (nl)
            *nl++ = '\\0';
        if (strncmp(line, KSU_PACKAGE_NAME " ", sizeof(KSU_PACKAGE_NAME)) != 0)
            continue;
        sp = strchr(line, ' ');
        if (!sp)
            break;
        uid = 0;
        for (sp = sp + 1; *sp >= '0' && *sp <= '9'; ++sp) {
            if (uid < 100000)
                uid = uid * 10 + (unsigned long)(*sp - '0');
        }
        if (uid > 0 && uid < 100000) {
            pr_info("KSU-A52: 按包名认定管理器 uid=%lu\\n", uid);
            ksu_set_manager_appid((uid_t)uid);
        } else {
            pr_info("KSU-A52: 管理器包名行解析失败\\n");
        }
        break;
    }
    kfree(buf);
}

"""
FM_ANCHOR = """struct data_path {
    char dirpath[DATA_PATH_LEN];
"""
pth = K / "manager" / "throne_tracker.c"
if not pth.is_file():
    report.append("[!] 找不到 manager/throne_tracker.c（无法兜底认领管理器）")
else:
    sth = read(pth)
    if FM_MARK in sth:
        report.append("[=] throne_tracker.c: 管理器兜底认领已存在（幂等）")
    elif FM_ANCHOR not in sth:
        report.append("[!] throne_tracker.c: 未找到 struct data_path 锚点 —— 上游结构变了")
    else:
        write(pth, sth.replace(FM_ANCHOR, FM_FUNC + FM_ANCHOR, 1))
        report.append("[+] throne_tracker.c: 新增 ksu_force_crown_manager()（按包名兜底认领管理器）")

# 在 on_post_fs_data() 里调用它，并把「管理器状态」打进内核日志，便于刷机后一条 dmesg 定位
FM_CALL = """    ksu_load_allow_list();
    ksu_dynamic_manager_load();
    ksu_observer_init();"""
FM_CALL_NEW = """    ksu_load_allow_list();
    ksu_dynamic_manager_load();
    /* """ + FM_MARK + """ */
    ksu_force_crown_manager();
    pr_info("KSU-A52: manager_appid=%d dynamic_enabled=%d\\n",
            (int)ksu_get_manager_appid(), (int)ksu_manager_enabled());
    ksu_observer_init();"""
FM_DECL = """/* KSU-A52: 由 manager/throne_tracker.c 实现 */
extern void ksu_force_crown_manager(void);
"""
if pks.is_file():
    sk2 = read(pks)
    if FM_MARK + " */\n    ksu_force_crown_manager();" in sk2:
        report.append("[=] ksud.c: 兜底认领调用已存在（幂等）")
    elif FM_CALL not in sk2:
        report.append("[!] ksud.c: 未找到 on_post_fs_data 三连调用锚点 —— 上游结构变了")
    else:
        sk2 = sk2.replace(FM_CALL, FM_CALL_NEW, 1)
        if "extern void ksu_force_crown_manager(void);" not in sk2:
            sk2 = FM_DECL + sk2
        write(pks, sk2)
        report.append("[+] ksud.c: on_post_fs_data 调用 ksu_force_crown_manager() 并打印管理器状态")

# 16) kernel/hook/lsm_hook.c —— SUSFS 分支补注册 LSM 的 task_fix_setuid
#     4.19 既没有 kprobe 也没有 kernel/sys.c 的 setresuid 挂钩；而 lsm_hook.c 的
#     ksu_hooks[] 在 CONFIG_KSU_SUSFS 下把 task_fix_setuid 整块 #ifndef 掉了，
#     于是 ksu_handle_setresuid() 在 SUSFS 版里永远没人调用。后果：
#       1. zygote 拉起管理器进程时不会 install fd for manager → 管理器拿不到 supercall fd
#          → 界面「内核上未检测到 SukiSU 驱动程序」；
#       2. 管理器的 seccomp 不会被 disable_seccomp() 解除。
LSM_MARK = "KSU-A52: SUSFS 版注册 task_fix_setuid"
plsm = K / "hook" / "lsm_hook.c"
LSM_FUNC = """#ifdef CONFIG_KSU_SUSFS
/* """ + LSM_MARK + """
 *
 * 4.19 既没有 kprobe 也没有 kernel/sys.c 的 setresuid 挂钩；而 lsm_hook.c 的
 * ksu_hooks[] 在 CONFIG_KSU_SUSFS 下把 task_fix_setuid 整块 #ifndef 掉了，
 * 于是 ksu_handle_setresuid() 在 SUSFS 版里永远没人调用。后果：
 *   1. 管理器进程被 zygote 拉起来时拿不到 supercall fd → 界面「未检出驱动」；
 *   2. 管理器的 seccomp 不会被 disable_seccomp() 解除。
 * 这里补一个壳子，把 LSM 的 task_fix_setuid 转发给 SUSFS 分支已有的实现。
 */
static int ksu_task_fix_setuid_susfs(struct cred *new, const struct cred *old, int flags)
{
    if (unlikely(!new || !old))
        return 0;
    return ksu_handle_setresuid(new->uid.val, new->euid.val, new->suid.val);
}
#endif

"""
LSM_ANCHOR = "static struct security_hook_list ksu_hooks[] = {"
LSM_OLD = """#ifndef CONFIG_KSU_SUSFS
    LSM_HOOK_INIT(task_fix_setuid, ksu_task_fix_setuid),
#endif"""
LSM_NEW = """#ifdef CONFIG_KSU_SUSFS
    /* """ + LSM_MARK + """ */
    LSM_HOOK_INIT(task_fix_setuid, ksu_task_fix_setuid_susfs),
#else
    LSM_HOOK_INIT(task_fix_setuid, ksu_task_fix_setuid),
#endif"""
if plsm.is_file():
    sl = read(plsm)
    if "ksu_task_fix_setuid_susfs" in sl:
        report.append("[=] lsm_hook.c: SUSFS 版 task_fix_setuid 已注册（幂等）")
    elif LSM_OLD not in sl:
        report.append("[!] lsm_hook.c: 未找到 task_fix_setuid 注册块 —— 上游结构变了")
    else:
        sl = sl.replace(LSM_OLD, LSM_NEW, 1)
        if LSM_ANCHOR in sl:
            sl = sl.replace(LSM_ANCHOR, LSM_FUNC + LSM_ANCHOR, 1)
        write(plsm, sl)
        report.append("[+] lsm_hook.c: SUSFS 版注册 task_fix_setuid（管理器才能拿到 fd）")
else:
    report.append("[!] 找不到 hook/lsm_hook.c")

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
# 【su 重定向】SUSFS 版 ksu_handle_execveat_ksud 必须补 stop_execve_hook()，否则钩子永远不关，
#    execve("/system/bin/su") 命中幻影 inode → ENOENT → 管理器「未检出驱动」
if pks.is_file() and SE_MARK not in read(pks):
    ok = False
    print("[!] 致命：runtime/ksud.c 的 SUSFS 版 zygote 分支未补 stop_execve_hook()")
    print("    → 4.19+SUSFS 上 execve(/system/bin/su) 会 ENOENT，管理器显示「未检出驱动」")
if psc.is_file() and SC_MARK in read(psc):
    ok = False
    print("[!] 致命：sucompat.c 仍是「ksud 钩子 + sucompat 并行」的坏改法")
    print("    → run 40495354659 实测该改法会让 init 的 execve ENOENT、无限重启")
# 【管理器识别】管理器只对 allowlist/管理器 uid 放行 su；appid 未被认领时就会「未检出驱动」
if pth.is_file() and FM_MARK not in read(pth):
    ok = False
    print("[!] 致命：throne_tracker.c 缺少按包名兜底认领管理器")
    print("    → 管理器（com.sukisu.ultra）的 su 不被放行，界面显示「未检到驱动」")
if pks.is_file() and "ksu_force_crown_manager();" not in read(pks):
    ok = False
    print("[!] 致命：on_post_fs_data() 未调用 ksu_force_crown_manager()")
# 【管理器 fd】SUSFS 版必须注册 task_fix_setuid，否则 ksu_handle_setresuid() 永不执行，
#   管理器进程 spawn 时拿不到 supercall fd → 界面一直显示「未检测到驱动程序」
if plsm.is_file() and "ksu_task_fix_setuid_susfs" not in read(plsm):
    ok = False
    print("[!] 致命：lsm_hook.c 的 SUSFS 分支没有注册 task_fix_setuid")
    print("    → 管理器拿不到 supercall fd，界面会一直「内核上未检测到 SukiSU 驱动程序」")
print("[OK] 驱动 4.19 适配检查通过" if ok else "[!] 有改动未生效")
sys.exit(0 if ok else 1)