# A52xq SukiSU Ultra 内核 · 官方 builtin 线（UAPI 6）

> **机型**：Samsung Galaxy A52 5G（`SM-A5260` / 代号 `a52xq` / `a52xqzc`）
> **系统**：One UI 6 / Android 14（`UP1A.231005.007` / `A5260ZCUCGZE1`）
> **内核**：Linux `4.19.152`（三星 QCOM 4.19 树）
> **目标**：让**官方 SukiSU Ultra 管理器**显示「**工作中**」，不再提示「**需要更新内核**」，
> 并且让 **Zygisk Next / LSPosed / HMA** 等依赖 Zygisk 的模块**真正工作**

---

## 成果

刷入后，官方管理器（`com.sukisu.ultra`，`v4.2.1-r1` / `versionCode 41049`）首页显示：

| 界面项 | 值 | 说明 |
| --- | --- | --- |
| 状态 | **工作中 ✅** | 没有「需要更新内核」红字 |
| 管理器版本 | `v4.2.1-r1 (41049-6)` | 官方管理器，UAPI **6** |
| 内核完整版本 | `v4.2.1-r1-6bf351bd@OneUI8-dev` | builtin 线驱动，UAPI **6** |
| 钩子类型 | **Manual Hook** | 手动钩子模式（非 KPROBE / 非 Unknown） |
| 超级用户 / 模块 | 原有授权与模块**全部保留** | Hybrid Mount 照常 |

运行时验证（**这才是真正的判据**）：

```bash
su -c id                                  # uid=0(root) gid=0(root) context=u:r:ksu:s0
su -c "/data/adb/ksud debug info"         # uapi_version: 6   features: 非 0
su -c "/data/adb/ksu/bin/znctl status"    # root_status: ✅KernelSU (...)  root_impl: 非 0
```

Zygisk Next 应当报：

```
zygote_states:2
zygote_state_0:1,0,<pid>,zygote,3
zygote_state_1:1,1,<pid>,zygote_secondary,3
inject_state:1
root_status:✅KernelSU (<驱动版本>)
root_impl:3
modules64:3,playintegrityfix,zygisk_lsposed,hma_oss_zygisk
modules_with_issue:0
```

以及进程里能看到 `zn-daemon`、`zn-nsdaemon-zygote`、`zn-nsdaemon-zygote_secondary`、`lspd`。

**只刷写 `boot` 分区**：不擦除 `/data`，不影响激活状态、不丢失数据。

---

## ⚠️ 先读这一节：激活与数据安全

这一段回答最常被问的问题：**刷这个内核、或者启停 Magisk/KernelSU 模块，会不会导致重新激活？**

### 结论

**不会。** 原因是这套机器的激活绕过**不是模块**，而是**已经持久化写进 `/system` 分区**了。

### 激活绕过的真实机制（实测）

常用的是 `samsung-activation-bypass-v2`（别名 `tiaoguo.zip`，作者 Yufz，`id=samsung-activation-bypass`）。
它的核心只有一个文件：

```ini
# customize.sh
SKIPUNZIP=0
REPLACE="
/system/app/ActivationDevice_V2
"
```

```ini
# module.prop
id=samsung-activation-bypass
name=绕过激活
version=v2
description=绕过三星设备激活验证
```

`REPLACE` 是 Magisk/KernelSU 的**目录替换**指令：它把 `/system/app/ActivationDevice_V2`
（三星的**设备激活校验 App**）整目录替换成**空目录** → 激活校验进程无从启动 → 开机不再要求激活。

**在这台机器上的实际落地状态**（`adb shell` 实测）：

```bash
$ su -c "ls -ld /system/app/ActivationDevice_V2"
drwxr-xr-x 2 root root 4096 /system/app/ActivationDevice_V2      # 目录在，但【是空的】
$ su -c "ls -d /data/adb/modules/*activ* /data/adb/modules/*bypass*"
No such file or directory                                        # 并没有安装任何激活相关模块
$ su -c "mount | grep -i overlay"
KSU on /system/etc type overlay ... reqable-magisk/system/etc    # 只有普通模块的 overlay
```

也就是说：**激活 App 的 apk 已经从 `/system` 里被真实删除**（持久化），
不是靠模块在开机时临时遮盖。这带来两个重要推论：

| 操作 | 会不会触发重新激活 |
| --- | --- |
| 刷写 `boot` 分区（本内核） | ❌ 不会（`boot` 与激活无关） |
| 启用 / 禁用任意 Magisk/KernelSU 模块 | ❌ 不会 |
| 重启、开关机、刷 TWRP | ❌ 不会 |
| 恢复原厂 `system` 分区（Odin 刷 `AP`） | ⚠️ 会（把空目录变回带 apk） |
| **格式化 / 擦除 `/data`（factory reset）** | ✅ **会** |

### 关于 `multidisabler-12_NO_DECRYPTION.zip`

这是 Ian Macdonald 的三星服务禁用脚本（`NO_DECRYPTION` 变体，TWRP 刷入），
作用是「装完 TWRP 后给机器做初始化」：禁用三星的恢复还原、Knox 相关服务等。
**`NO_DECRYPTION` 的含义是：不做解密 / 不格式化 `/data`** —— 这正是数据与激活能保住的原因。
原版 `multidisabler` 会强制解密 `/data`（等于格式化），那样激活就没了。

### 仓库内附带的绕过模块

```
activation-bypass/samsung-activation-bypass-v2.zip    # 可在 Magisk/KernelSU 里直接安装
```

这是把上面那套机制打包成的**可刷入模块**（原版 by **Yufz**，本仓库补齐了说明文档；
`customize.sh` 里的 `REPLACE="/system/app/ActivationDevice_V2"` 原样保留）。

**什么时候需要它？**

- 如果你的机器是「**模块式**」绕过（卸载该模块后开机就会重新要求激活）→ 直接装这个 zip 即可
- 如果你的机器已经像上面实测那样，把 apk 从 `system` 分区里**真实删除**了 → 本模块是**冗余的备份**，装了也只是再替换一次空目录，无副作用

⚠️ 注意：它是**模块**（在管理器里安装），**不是** TWRP 刷入的包；卸载后重启即可移除。

> 顺带一提：**内核里没有任何激活相关代码**。本项目构建的内核只增加 KernelSU 挂钩
> （`fs/exec.c`、`fs/open.c`、`fs/stat.c`、`fs/read_write.c`、`fs/reboot.c` 等），
> 与 `/system` 的激活绕过完全无关 —— 这也是"刷内核不会影响激活"的根本原因。

### 因此，本项目的所有操作都遵守一条红线

> **只写 `boot` 分区（`anykernel.sh`: `block=/dev/block/by-name/boot`，`is_slot_device=0`）。
> 永远不 `wipe` / 不 `format data`。**

---

## 为什么这套组合在「三星 4.19 非 GKI 树」上很难搞

SukiSU Ultra 官方 **builtin 线**（非 GKI，带 `drivers/kernelsu`，走**手动钩子**）落到这颗 4.19 三星内核上，
一共踩了 **13 个坑**。每一条都是实测（证据来自 `last_kmsg` / `pstore` / `build.log` / 真机 `dmesg`），
脚本里都有对应修复与**硬断言**。

### A. 编译期（9 个）

| # | 坑 | 现象 | 修法 |
| --- | --- | --- | --- |
| 1 | `#include <linux/static_call.h>` | 4.19 无此头文件，编译失败 | 注释掉（`kernel/kernel_includes.h`） |
| 2 | `fallthrough` 宏缺失 | 编译报 `'fallthrough' undeclared` | 补 `#define fallthrough do {} while (0)` |
| 3 | `linux/hex.h` / `uapi/linux/mount.h` | 4.19 没有这两个头 | 改 `linux/kernel.h` / `linux/fs.h` |
| 4 | `KSU_FEATURE_SELINUX_HIDE` 被 `>=5.10` guard 包住 | 引用枚举时报未声明 | 去掉 guard |
| 5 | `(*curr_cs_ptr)->vdso_clock_mode` | `vdso_clock_mode` 是 5.3+ 才有 | 加 `>= KERNEL_VERSION(5,3,0)` guard |
| 6 | `access_ok(addr, size)` 两参数写法 | 4.19 是三参数（`VERIFY_READ`） | 补 `VERIFY_READ`（10 处，`extension/kpm/kpm.c`） |
| 7 | `user_arg_null_ptr()` 返回指针 | 与调用方期望的结构体不一致 | 改成返回值 |
| 8 | `ksu_selinux_hide_*` 实现被 `>=5.10` 排除、调用却无条件 | **链接期** `undefined reference` | 用同样的 guard 包住 3 处调用 |
| 9 | **`CONFIG_KPROBES=y`** | 会让驱动启用 kprobe 路径 → 运行时改写内核代码段 → **三星 RKP/Knox panic 风险** | 明确 `# CONFIG_KPROBES is not set` |

### B. 运行期 —— 真正让机器开不起来 / 让 Root 无效的坑

#### 坑 10 ⚠️ 真正的 bootloop 元凶：`sucompat` 传 NULL 导致内核 panic

官方 4.19 手动钩子补丁（`patches/ksu_hooks_sukisu_4.19.patch`）插进 `fs/exec.c` 的这段代码：

```c
if (unlikely(ksu_execveat_hook))
        ksu_handle_execveat((int *)AT_FDCWD, &filename, &argv, &envp, 0);
else
        ksu_handle_execveat_sucompat((int *)AT_FDCWD, &filename, NULL, NULL, NULL);  /* ← 传了 NULL */
```

而 builtin 驱动的 `ksu_handle_execveat_sucompat` **会解引用 `argv`**。开机时 `ksu_execveat_hook` 还是 `false`，
于是**每次 `execve` 都走 `else` 分支** → **第一次 `execve` 就 NULL 解引用**。

实测崩溃现场（`/proc/last_kmsg`）：

```
Unable to handle kernel NULL pointer dereference at virtual address 0000000000000000
Internal error: Oops: 96000006 [#1] PREEMPT SMP
pc : ksu_handle_execveat_sucompat+0xd0/0x1c0
lr : ksu_handle_execveat_sucompat+0x84/0x1c0
Call trace:
    ksu_handle_execveat_sucompat+0xd0/0x1c0
    do_execve+0x50/0xa8
Kernel panic - not syncing: Fatal exception
```

表现：**能开机到锁屏 → 黑屏 → 自动重启**（约 30 秒）。

**修法**：把真实的 `&argv, &envp` 传进去；32 位 `compat` 那条分支直接不启用。
脚本带**硬断言**：只要编译树 `fs/exec.c` 里还残留 `sucompat(..., NULL, NULL, NULL)` 就 `exit 1`、**不产出任何镜像**。
因此「Release 存在」本身就等价于「这个 bug 已修复」。

#### 坑 11 ⚠️⚠️ **最隐蔽也最关键**：`kernel/reboot.c` 没有调用 `ksu_handle_sys_reboot`

**症状**（很多人卡在这里）：

- `su` 完全正常（`su -c id` → `uid=0`），管理器也显示「工作中」
- 但是 **Zygisk Next 一直显示「需要重启」**，`module.prop` 里被写成 **`[Root: ❌None]`**
- **HMA（`hma_oss_zygisk`）一打开就崩**：「System service crashed」
- `znctl status` → `err:❌ Could not connect to service!`
- `ps` 里**没有** `zn-daemon`

**排查**（这几条一跑就能定性）：

```bash
su -c "/data/adb/ksud debug info"     # ← 关键：version/uapi_version/features 全是 0
su -c "/data/adb/ksud debug version"  # ← 输出 "Kernel Version: 0"
su -c "/data/adb/ksud feature list"   # ← 全部 [NOT_SUPPORTED]
```

**根因**：SukiSU 的内核 ↔ 用户空间通道，是**「匿名 inode fd 上的 ioctl」**
（`kernel/supercall/supercall.c`: `anon_ksu_ioctl`）。而 `ksud` 要通过
**`reboot(KSU_INSTALL_MAGIC1, KSU_INSTALL_MAGIC2, ...)`** 才能拿到那个 fd：

```c
/* kernel/supercall/supercall.c（非 SUSFS 分支） */
int ksu_handle_sys_reboot(int magic1, int magic2, unsigned int cmd, void __user **arg)
{
        if (magic1 != KSU_INSTALL_MAGIC1) return -EINVAL;
        void __user *argp = (void __user *)*arg;
        if (magic2 == KSU_INSTALL_MAGIC2)
                return ksu_handle_fd_request(argp);   /* ← fd 在这里下发 */
        return 0;
}
```

**这个函数在驱动里存在、符号也导出了，但官方 4.19 钩子补丁从来没有在 `kernel/reboot.c` 里调用它。**
结果所有「向内核查询」都拿不到 fd → 全部返回 0。
Zygisk Next 正是用这条通道判断 Root 实现的 → `root_impl: 0` → 写出 `[Root: ❌None]` → 不注入 Zygisk。

**验证方法**（一句话确认符号有没有人调用）：

```bash
su -c "grep -oE ' ksu_handle_[a-z_]+' /proc/kallsyms | sort -u"
# 会看到 ksu_handle_sys_reboot / ksu_handle_umount 存在，但查询全 0 —— 说明只有实现、没有挂钩点
```

**修法**（本项目的核心补丁，`scripts/fix_driver_419.py` 第 8 组）：

```c
SYSCALL_DEFINE4(reboot, int, magic1, int, magic2, unsigned int, cmd,
                void __user *, arg)
{
#ifdef CONFIG_KSU
        {
                extern int ksu_handle_sys_reboot(int magic1, int magic2, unsigned int cmd,
                                                    void __user **arg);
                /* KSU-A52 sys_reboot hook: 打通 ksud 获取 supercall fd 的通道 */
                if (ksu_handle_sys_reboot(magic1, magic2, cmd, &arg))
                        return 0;
        }
#endif
        ...
```

修好后 `ksud debug info` 会立刻变成 `uapi_version: 6`、`features: 0x11`，
`dmesg` 里出现 `KernelSU: [ksu_driver] fd installed: 5 for pid ...`。

#### 坑 12 ⚠️ `preinit` 被静默关闭 → Zygisk 守护进程无法常驻

驱动 `kernel/Makefile` 里：

```makefile
KSU_UAPI_INC := $(objtree)/usr/include
$(warning -- KernelSU: $(KSU_UAPI_INC) not found, preinit disabled)
ccflags-y += -DKSU_NO_PREINIT
```

它在 **Makefile 解析阶段**就检查 `out/usr/include`，而这个目录要等 `headers_install` 才生成 →
判断失败 → 定义 `-DKSU_NO_PREINIT` → **preinit / initrc 集成被关闭** →
模块的 `post-fs-data` / `service` 阶段改由用户空间以「跑完即回收」的方式执行 →
**`zygiskd` 写完上下文就被收走，无法常驻**。

症状：`znctx` / `modules_info` 是**本次开机**写入的（说明脚本跑过），但 `ps` 里没有 `zn-daemon`，
`znctl status` 报 `Could not connect to service!`。

**修法**：在主 `make` 之前先跑一次 `headers_install`（脚本自动注入）：

```bash
make -j$(nproc) ARCH=arm64 O=out headers_install INSTALL_HDR_PATH="$PWD/out/usr"
```

⚠️ **注意**：打开 preinit 后会暴露第二个编译问题 —— runner 里
`preinit/runner/nolibc/sys.h` 的 `clone3` 回退分支用到了 **5.3+ 才有的 `struct clone_args`**：

```
error: variable has incomplete type 'struct clone_args'
```

但 **arm64 本身是有 `__NR_vfork`（= 58）的**，本来就不该走 clone3 分支。
脚本把条件改成 `#if defined(__NR_vfork) || defined(__aarch64__)`，
让 arm64 明确走 vfork 分支，从而绕开 `clone_args` / `clone3`。

#### 坑 13 ⚠️ `disable` 标记 / `magisk-brick-guardian` 批量禁用模块

`/data/adb/modules/<模块>/disable` 这个**空文件一存在，KernelSU 就会完全跳过该模块的所有脚本**
（`post-fs-data.sh` / `service.sh`）。

实测踩到的情形：之前两次内核 bootloop 之后，**`magisk-brick-guardian`**
（它的说明就是「自动检测并防止模块导致的系统无法启动 / 首次启动后请等待 1.5 分钟」）
**把 8 个模块全部自动禁用了**，包括：

```
DISABLED  zygisksu          ← Zapisk Next 自己被禁 → zygiskd 从不启动
DISABLED  zygisk_lsposed    ← lspd 不运行
DISABLED  hma_oss_zygisk    ← HMA 的 Zygisk 注入不存在 → 一打开就崩
DISABLED  playintegrityfix / hybrid_mount / tricky_store / scene_systemless / ...
```

排查：

```bash
su -c "ls -d /data/adb/modules/*/disable"          # 列出所有被禁用的模块
su -c "/data/adb/ksud module list | grep -B2 -A6 zygisksu"   # 看 enabled: true/false
```

修法：

```bash
su -c "rm -f /data/adb/modules/<模块名>/disable"    # 启用某个模块
# 然后重启（Zygisk 守护进程只能在开机阶段启动）
```

> 如果你不想要 bootloop 自动保护，可以 `touch /data/adb/modules/magisk-brick-guardian/disable` 关掉它；
> 代价是失去"模块导致起不来时自动禁用"的保护。

### 一句话总结 13 个坑

| 类别 | 坑号 | 关键词 |
| --- | --- | --- |
| 编译期 API/头文件差异 | 1–8 | static_call / fallthrough / hex.h / guard / vdso_clock_mode / access_ok / user_arg_null_ptr / selinux_hide |
| 配置 | 9 | `CONFIG_KPROBES` 必须关闭 |
| **运行期崩溃** | 10 | `sucompat(NULL)` → NULL 解引用 → panic → bootloop |
| **内核 API 通道** | 11 | `kernel/reboot.c` 缺 `ksu_handle_sys_reboot` → 查询全 0 → **Zygisk Next `Root: ❌None`、HMA 崩** |
| **模块阶段/常驻** | 12 | `preinit` 被 `-DKSU_NO_PREINIT` 关闭 → `zygiskd` 不能常驻（+ arm64 vfork 绕开 `clone_args`） |
| **环境/管理器** | 13 | `disable` 标记 / `magisk-brick-guardian` 批量禁用模块 |

---

## 快速开始

### 方式一：GitHub Actions 云端编译（推荐，无需本地环境）

1. Fork 本仓库（或直接用本仓库的 `Actions`）
2. 触发 `.github/workflows/build.yml`（`workflow_dispatch`，或任意 push 也会触发）
3. 约 20 分钟后产物出现在 **Releases**：`a52xq-sukisu-4.2.1-anykernel3.zip`（TWRP 直刷包）+ `build.log`
4. 工作流会把**完整脚本输出**回传到仓库的 `logs/` 目录，方便排查

### 方式二：本地编译

```bash
git clone https://github.com/micr0softstore/samsung_android_kernel_a52xq -b OneUI8-dev kernel
# 仓库自带的 clang + gcc 工具链就在树内：toolchain/clang/... 与 toolchain/toolchains-gcc-10.3.0/...
bash scripts/build_builtin.sh
# 产物：out/a52xq-sukisu-4.2.1-anykernel3.zip
```

依赖：`flex bison bc libssl-dev libelf-dev zip unzip patch python3 git curl cpio xz-utils`

### 刷入（TWRP）

1. **Install → Select Storage → USB-OTG**（或 `/sdcard`）
2. 选 `a52xq-sukisu-4.2.1-anykernel3.zip` → Swipe to confirm
3. **Reboot → System**

> ⚠️ **不要 wipe / format data**。本包只写 `boot` 分区。
> 由于 `/data` 是加密的，TWRP 里「挂载 data 失败」是**正常现象**，只刷 boot 不需要挂载 data。

### 验收清单

**A. 启动与基础**

- [ ] `cat /proc/version` 里出现你的编译机标识（说明新内核在跑）
- [ ] 官方管理器首页显示「**工作中**」，无「需要更新内核」
- [ ] 管理器里「钩子类型」= **Manual Hook**
- [ ] `adb shell su -c id` → `uid=0(root)`
- [ ] 原有模块与超级用户授权**仍在**；`/sdcard` 数据完好、激活状态未变

**B. 内核 API 通道（决定 Zygisk 能否工作）**

- [ ] `su -c "/data/adb/ksud debug info"` → `uapi_version: 6`，`features` 非 `0x0`
- [ ] `su -c "dmesg | grep 'install ksu fd'"` → 有 `[ksu_driver] fd installed: ...`

**C. Zygisk Next**

- [ ] `su -c "/data/adb/ksu/bin/znctl status"` → `root_status: ✅KernelSU (...)`，`root_impl` 非 0
- [ ] 同上输出里 `zygote_state_0/1` 以 `,3` 结尾（已注入）
- [ ] `ps -A -o USER,PID,NAME | grep -E 'zn-daemon|zn-nsdaemon'` → 有进程
- [ ] `su -c "grep description /data/adb/modules/zygisksu/module.prop"` → 含 `Root: ✅KernelSU`
- [ ] LSPosed：`ps -A | grep lspd` → 有进程

---

## 诊断命令速查（遇到问题先跑这些）

```bash
# ── 0. 设备与内核
adb devices -l
adb shell cat /proc/version

# ── 1. ★ 内核 API 通道是否通（最重要，一眼定性）
su -c "/data/adb/ksud debug info"       # version / uapi_version / features 必须非 0
su -c "/data/adb/ksud debug version"    # 期望驱动版本号，不是 0
su -c "/data/adb/ksud feature list"     # 不应全是 [NOT_SUPPORTED]

# ── 2. 钩子是否真的挂上了
su -c "dmesg | grep -i ksu | tail -20"                              # 期望 do_execveat_common su->ksud!
su -c "grep -oE ' ksu_handle_[a-z_]+' /proc/kallsyms | sort -u"     # 实际存在的处理函数
su -c "grep -iE 'install ksu fd|ksu_driver' /proc/kallsyms"         # 通道符号

# ── 3. Zygisk Next
su -c "/data/adb/ksu/bin/znctl status"
su -c "grep description /data/adb/modules/zygisksu/module.prop"
su -c "cat /data/adb/zygisksu/.abort_msg 2>/dev/null"               # 存在=守护进程曾被中止
su -c "ls -la /data/adb/zygisksu/"
su -c "ps -A -o USER,PID,PPID,NAME | grep -iE 'zygisk|zn-daemon|zn-nsdaemon'"
su -c "logcat -d -b all | grep -iE 'zygiskd|KernelSU' | tail -30"

# ── 4. 模块启用状态（很多人栽在这里）
su -c "ls -d /data/adb/modules/*/disable"                            # 列出被禁用的模块
su -c "/data/adb/ksud module list | head -40"

# ── 5. 崩溃现场（bootloop 之后）
su -c "cat /proc/last_kmsg | tail -200"
su -c "tail -200 /data/adb/ksu/log/dmesg.log"
```

### 症状 → 坑 对照表

| 症状 | 大概率是 | 怎么办 |
| --- | --- | --- |
| 开机到锁屏就黑屏重启（约 30s） | **坑 10**（`sucompat` NULL panic） | 确认使用了含修复的构建；查 `/proc/last_kmsg` 是否有 `ksu_handle_execveat_sucompat` |
| 管理器显示「工作中」，但 ZN 一直「需要重启」 | **坑 11**（内核 API 全 0） | 跑 `ksud debug info`；`uapi_version` 为 0 就是没打 `reboot.c` 挂钩 |
| `znctl` 报 `Could not connect to service!` 且无 `zn-daemon` | **坑 13**（模块被 `disable`）或 **坑 12**（preinit 关闭） | 先 `ls -d /data/adb/modules/*/disable`；再看 `logs/out.txt` 是否出现 `preinit disabled` |
| HMA 打开提示 `System service crashed` | `hma_oss_zygisk` 被禁用 → Zygisk 没注入 | 删掉它的 `disable` 后重启 |
| 管理器显示 UAPI 不匹配 / 「需要更新内核」 | 内核 UAPI ≠ 管理器 UAPI | 本方案固定 UAPI 6 + 管理器 `v4.2.1-r1 (41049)` |

---

## 目录结构

```
scripts/build_builtin.sh            # 主构建脚本（自包含：内嵌补丁与修复脚本的 base64）
scripts/fix_driver_419.py           # 驱动 4.19 适配（含坑 10 sucompat、坑 11 reboot.c、坑 12 vfork）
scripts/fix_sources_for_419.py      # 内核源码侧 4.19 适配
scripts/strip_legacy_ksu_hooks.py   # 清掉旧的 ReSukiSU 静态钩子块（避免与官方钩子冲突/重定义）
patches/ksu_hooks_sukisu_4.19.patch # 官方 4.19 手动钩子补丁
.github/workflows/build.yml         # 云端编译
activation-bypass/                  # 绕过激活模块（可刷入 zip，见「激活与数据安全」一节）
logs/                               # 每次构建回传的脚本输出与 build.log（自动生成）
```

## 设计要点

- **UAPI 必须对齐**：内核驱动 UAPI 与管理器 UAPI 相等时，管理器才认为「全功能」。
  本方案固定 `UAPI = 6`，配套管理器 `v4.2.1-r1 (41049)`。
- **内置硬断言**（任一不满足即中止、不打包）：
  1. 驱动 UAPI 必须为 6
  2. `out/.config` 里 `CONFIG_KPROBES` / `CONFIG_KRETPROBES` **不得为 y**
  3. `fs/exec.c` 不得残留 `sucompat(..., NULL, NULL, NULL)`
  4. `kernel/reboot.c` 必须存在 `ksu_handle_sys_reboot` 挂钩
  5. `preinit/runner/nolibc/sys.h` 必须已处理 arm64 vfork 分支
- **只用手动钩子**，不引入 kprobe，避免与三星 RKP/Knox 冲突。

## 鸣谢 / 来源

- [SukiSU Ultra](https://github.com/SukiSU-Ultra/SukiSU-Ultra) —— 内核驱动与官方管理器（builtin 线 / 非 GKI）
- [SukiSU_patch](https://github.com/SukiSU-Ultra/SukiSU_patch) —— 4.19 手动钩子补丁（`4.19/ksu_hooks_sukisu_4.19.patch`）
- [micr0softstore/samsung_android_kernel_a52xq](https://github.com/micr0softstore/samsung_android_kernel_a52xq) —— A52xq 内核源码（`OneUI8-dev`）与 AnyKernel3 打包骨架
- [Zygisk Next](https://github.com/Dr-TSNG/ZygiskNext) —— 独立 Zygisk 实现
- tgk 系列内核（`tgk_v1.5.1_a52xq_sukisu.zip`）—— 作为「已验证可正常启动」的对照基线，用于配置逐项比对与回滚
- 激活绕过模块 `samsung-activation-bypass`（作者 Yufz）
- `multidisabler` by Ian Macdonald

## 许可

内核源码与补丁遵循 **GPL-2.0**（见 `LICENSE`）。本仓库只包含**构建脚本与适配补丁**；
内核本体请从上游 `samsung_android_kernel_a52xq` 获取（GPL 要求随二进制提供对应源码）。

---

## English summary

Build **official SukiSU Ultra** (builtin / non-GKI line, UAPI 6) for the
**Galaxy A52 5G (`a52xq`)**, Linux 4.19 Samsung tree. **13 pitfalls** are documented and fixed.

Compile-time (1–8): missing `static_call.h`/`hex.h`, `fallthrough`, `KSU_FEATURE_SELINUX_HIDE` guard,
`vdso_clock_mode` (5.3+), 2-arg `access_ok`, `user_arg_null_ptr`, guarded `ksu_selinux_hide_*`
linker error. Config (9): keep `CONFIG_KPROBES` **off** (Samsung RKP).

Runtime:

- **#10** the official 4.19 patch calls `ksu_handle_execveat_sucompat(..., NULL, NULL, NULL)`
  in `fs/exec.c`, while the builtin driver dereferences `argv`
  → NULL-pointer panic on the first `execve`
  (`pc : ksu_handle_execveat_sucompat+0xd0` → `do_execve+0x50`) → bootloop.
- **#11 (most important, hardest to spot)** the driver's userspace API is an
  `ioctl` on an anonymous-inode fd, handed out via
  `reboot(KSU_INSTALL_MAGIC1, KSU_INSTALL_MAGIC2, ...)` →
  `ksu_handle_sys_reboot()` → `ksu_handle_fd_request()`.
  The official 4.19 hook patch **never calls it from `kernel/reboot.c`**, so every kernel query
  returns 0 (`ksud debug info` → `uapi_version: 0`), Zygisk Next reads `root_impl: 0`
  and writes `[Root: ❌None]` → no Zygisk injection → HMA crashes.
  Fix: add the `ksu_handle_sys_reboot` call at the top of `SYSCALL_DEFINE4(reboot, ...)`.
- **#12** the driver's Makefile defines `-DKSU_NO_PREINIT` when `$(objtree)/usr/include` is missing
  at parse time, disabling preinit → module `post-fs-data`/`service` stages are reaped →
  `zygiskd` cannot stay resident. Fix: run `headers_install` before the main `make`
  (and let arm64 use the `vfork` path to avoid the 5.3-only `struct clone_args`).
- **#13** an empty `/data/adb/modules/<mod>/disable` makes KernelSU skip that module entirely;
  `magisk-brick-guardian` auto-disabled 8 modules after the earlier bootloops,
  including `zygisksu` itself, `zygisk_lsposed` and `hma_oss_zygisk`.

**Activation / data safety:** the activation bypass on this device is **not a module** — the
`ActivationDevice_V2` system app has been **persistently removed from the `/system` partition**
(verified: the directory exists but is empty, and no activation module is installed).
Therefore flashing **only the `boot` partition** and enabling/disabling modules
**cannot** trigger re-activation. Only formatting `/data` (or restoring the stock system image) can.

Flash only the `boot` partition (AnyKernel3) — no data wipe, activation preserved.
Run `Actions → build.yml` to build in the cloud (~20 min).