# A52xq SukiSU Ultra 内核 · 官方 builtin 线（UAPI 6）

> **机型**：Samsung Galaxy A52 5G（`SM-A5260` / 代号 `a52xq` / `a52xqzc`）
> **系统**：One UI 6 / Android 14（`UP1A.231005.007` / `A5260ZCUCGZE1`）
> **内核**：Linux `4.19.152`（三星 QCOM 4.19 树）
> **目标**：让**官方 SukiSU Ultra 管理器**显示「**工作中**」，不再提示「**需要更新内核**」

---

## 成果

刷入后，官方管理器（`com.sukisu.ultra`，`v4.2.1-r1` / `versionCode 41049`）首页显示：

| 界面项 | 值 | 说明 |
| --- | --- | --- |
| 状态 | **工作中 ✅** | 没有「需要更新内核」红字 |
| 管理器版本 | `v4.2.1-r1 (41049-6)` | 官方管理器，UAPI **6** |
| 内核完整版本 | `v4.2.1-r1-6bf351bd@OneUI8-dev` | builtin 线驱动，UAPI **6** |
| 钩子类型 | **Manual Hook** | 手动钩子模式（非 KPROBE / 非 Unknown） |
| Zygisk | Zygisk Next `1.5.0` | 正常工作 |
| 超级用户 / 模块 | 原有授权与模块**全部保留** | Hybrid Mount 照常 |

`adb shell su -c id` → `uid=0(root) context=u:r:ksu:s0` ✅

**只刷写 `boot` 分区**：不擦除 `/data`，不影响激活状态、不丢失数据。

---

## 为什么这套组合在「三星 4.19 非 GKI 树」上很难搞

SukiSU Ultra 官方 **builtin 线**（非 GKI，带 `drivers/kernelsu`，走**手动钩子**）落到这颗 4.19 三星内核上，
一共踩了 **9 个坑**。下面每一条都是实测（证据来自 `last_kmsg` / `pstore` / `build.log`），脚本里都有对应修复与**硬断言**。

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
| 9 | **`CONFIG_KPROBES=y`** | 会让驱动启用 kprobe 路径 → 运行时改写内核代码段 → **三星 RKP/Knox panic 风险** | 明确 `# CONFIG_KPROBES is not set`（已验证可正常启动的 tgk 内核也是关的） |

### 第 10 个坑 —— 真正的 bootloop 元凶 ⚠️

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

表现就是：**能开机到锁屏 → 黑屏 → 自动重启**（约 30 秒）。

**修法**：把真实的 `&argv, &envp` 传进去；32 位 `compat` 那条分支（`su` 是 64 位，用不到）直接不启用。
`scripts/fix_driver_419.py` 里做了这件事，并且带**硬断言**：

> 只要编译树 `fs/exec.c` 里还残留 `sucompat(..., NULL, NULL, NULL)`，就 `exit 1`、**不产出任何镜像**。
> 因此「Release 存在」本身就等价于「这个 bug 已修复」。

---

## 快速开始

### 方式一：GitHub Actions 云端编译（推荐，无需本地环境）

1. Fork 本仓库（或直接用本仓库的 `Actions`）
2. 触发 `.github/workflows/build.yml`（`workflow_dispatch`，或任意 push 也会触发）
3. 约 20 分钟后：产物同时出现在
   - **Releases**：`a52xq-sukisu-4.2.1-anykernel3.zip`（TWRP 直刷包）+ `build.log`
   - **Artifacts**：`kbuild-out`
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

> ⚠️ **不要 wipe / format data**。本包只写 `boot` 分区（`anykernel.sh`: `block=/dev/block/by-name/boot`、`is_slot_device=0`）。

### 验收清单

- [ ] `cat /proc/version` 里出现你的编译机标识（说明新内核在跑）
- [ ] 官方管理器首页显示「**工作中**」，无「需要更新内核」
- [ ] 管理器里「钩子类型」= **Manual Hook**
- [ ] `adb shell su -c id` → `uid=0(root)`
- [ ] 原有模块（Hybrid Mount / Zygisk Next 等）与超级用户授权**仍在**
- [ ] `/sdcard` 数据完好、激活状态未变

---

## 目录结构

```
scripts/build_builtin.sh            # 主构建脚本（自包含：内嵌补丁与修复脚本的 base64）
scripts/fix_driver_419.py           # 驱动 4.19 适配 + 【关键】fs/exec.c sucompat NULL 修复
scripts/fix_sources_for_419.py      # 内核源码侧 4.19 适配
scripts/strip_legacy_ksu_hooks.py   # 清掉旧的 ReSukiSU 静态钩子块（避免与官方钩子冲突/重定义）
patches/ksu_hooks_sukisu_4.19.patch # 官方 4.19 手动钩子补丁
.github/workflows/build.yml         # 云端编译
logs/                               # 每次构建回传的脚本输出与 build.log（自动生成）
```

## 设计要点

- **UAPI 必须对齐**：内核驱动的 UAPI 与管理器 UAPI 相等时，管理器才认为「全功能」。
  本方案固定 `UAPI = 6`，配套管理器 `v4.2.1-r1 (41049)`。
- **内置硬断言**（任一不满足即中止、不打包）：
  1. 驱动 UAPI 必须为 6
  2. `out/.config` 里 `CONFIG_KPROBES` / `CONFIG_KRETPROBES` **不得为 y**
  3. `fs/exec.c` 不得残留 `sucompat(..., NULL, NULL, NULL)`
- **只用手动钩子**，不引入 kprobe，避免与三星 RKP/Knox 冲突。

## 鸣谢 / 来源

- [SukiSU Ultra](https://github.com/SukiSU-Ultra/SukiSU-Ultra) —— 内核驱动与官方管理器（builtin 线 / 非 GKI）
- [SukiSU_patch](https://github.com/SukiSU-Ultra/SukiSU_patch) —— 4.19 手动钩子补丁
- [micr0softstore/samsung_android_kernel_a52xq](https://github.com/micr0softstore/samsung_android_kernel_a52xq) —— A52xq 内核源码（`OneUI8-dev`）与 AnyKernel3 打包骨架
- tgk 系列内核（`tgk_v1.5.1_a52xq_sukisu.zip`）—— 作为「已验证可正常启动」的对照基线，用于配置逐项比对与回滚

## 许可

内核源码与补丁遵循 **GPL-2.0**（见 `LICENSE`）。本仓库只包含**构建脚本与适配补丁**；
内核本体请从上游 `samsung_android_kernel_a52xq` 获取（GPL 要求随二进制提供对应源码）。

---

## English summary

Build **official SukiSU Ultra** (builtin / non-GKI line, UAPI 6) for the **Galaxy A52 5G (`a52xq`)**, Linux 4.19 Samsung tree.

Nine compile-time 4.19 gaps are handled by the scripts, plus one **runtime bootloop bug**:
the official 4.19 manual-hook patch calls `ksu_handle_execveat_sucompat(..., NULL, NULL, NULL)`
in the `else` branch of `fs/exec.c`, while the builtin driver dereferences `argv`
→ NULL-pointer panic on the first `execve` (`pc : ksu_handle_execveat_sucompat+0xd0` → `do_execve+0x50`).

`scripts/fix_driver_419.py` passes the real `&argv/&envp` instead and **hard-asserts** that no
`sucompat(..., NULL, NULL, NULL)` survives, so a produced release implies the fix is in.

Flash only the `boot` partition (AnyKernel3) — no data wipe, activation preserved.
Run `Actions → build.yml` to build in the cloud (~20 min).