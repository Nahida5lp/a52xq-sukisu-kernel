# A52xq SukiSU 内核：SUSFS v1.5.5 + SELinux 隐藏 集成记录

- 设备：Galaxy A52 5G `SM-A5260` / `a52xq` / `a52xqzc`，One UI 6（Android 14，`UP1A.231005.007`）
- 源码：`micr0softstore/samsung_android_kernel_a52xq` 分支 `OneUI8-dev`（Linux 4.19.152）
- 驱动：SukiSU Ultra builtin（非 GKI）`ce75122`
- 实证：`version: 41098`、`uapi_version: 6`、`features: 0x11`
- banner：`4.19.152-touchGrassKernel+ (android@localhost) #1 SMP PREEMPT`

## 1. 结论

1. 能开机、能用：SUSFS 全开（除 `SUS_MOUNT`/`SUS_MAP`）+ SELinux 隐藏 + 管理器可识别驱动。
2. 难点不在"加功能"，而在把驱动里 5 处只对 5.10+ 成立的 `#ifdef CONFIG_KSU_SUSFS` 分支按版本关掉（第 3 节）。
3. Play Integrity 的 `NETWORK_ERROR (-3)` 是网络到不了 Google，与隐藏无关。
4. Momo 的「Bootloader 未锁定」只能靠 keybox 伪造认证链消除（TrickyStore + `keybox.xml`），改属性无效。

## 2. 开关清单（与"能开机的好包"唯一差异）

好包与 SUSFS 包的内嵌 `.config` 逐行 diff 只有 11 行，全部是 SUSFS 开关：

```
CONFIG_KSU_SUSFS=y
CONFIG_KSU_SUSFS_ENABLE_LOG=y
CONFIG_KSU_SUSFS_HIDE_KSU_SUSFS_SYMBOLS=y
CONFIG_KSU_SUSFS_OPEN_REDIRECT=y
CONFIG_KSU_SUSFS_SPOOF_CMDLINE_OR_BOOTCONFIG=y
CONFIG_KSU_SUSFS_SPOOF_UNAME=y
CONFIG_KSU_SUSFS_SUS_KSTAT=y
CONFIG_KSU_SUSFS_SUS_PATH=y
# CONFIG_KSU_SUSFS_SUS_MAP is not set
# CONFIG_KSU_SUSFS_SUS_MOUNT is not set
```

其余一致：`CONFIG_KSU=y`、`CONFIG_KSU_FEATURE_ADBROOT=y`、`CONFIG_KALLSYMS_ALL=y`、
`# CONFIG_KPROBES is not set`、`# CONFIG_KRETPROBES is not set`、`# CONFIG_KPM is not set`。

> 只留 `CONFIG_KSU_SUSFS=y` 而把 6 个子开关全关会开机循环，不要走这条路。

## 3. 驱动必改的 5 处（核心）

补丁脚本：`scripts/fix_driver_419.py`（共 17 组，下面是 SUSFS 相关的 5 组）。

| 位置 | 症状 | 处理 |
|---|---|---|
| `feature/sucompat.c` / `.h` | `fs/exec.c` 传 `struct filename **`，`fs/open.c`/`fs/stat.c` 传 `const char __user **`；文件级开关同时满足不了，错配直接 panic（`pc: ksu_handle_faccessat`） | 按 `LINUX_VERSION_CODE` 分组守卫 |
| `sulog/event.h` + `event.c` | `passing 'struct user_arg_ptr' to parameter of incompatible type 'struct user_arg_ptr *'` | 版本守卫 + 返回值修正 |
| `feature/adb_root.c` | `undefined reference to ksu_adb_root_handle_execve_manual` | 两处 `#ifdef` 改版本守卫 |
| `runtime/ksud.c`（SUSFS 分支） | 缺 `stop_execve_hook()`：post-fs-data 后 execve 钩子仍开着，`su` 重定向不走 ksud → `execve("/system/bin/su")` 返回 `ENOENT` → 管理器「未检测到驱动」 | 在 SUSFS 变体 `on_post_fs_data` 后补 `stop_execve_hook()` |
| `hook/lsm_hook.c` + `fs/exec.c` | 管理器拿不到 supercall fd（`task_fix_setuid` 未注册） | 补 `ksu_handle_task_fix_setuid` 注册 + `ksu_a52_setresuid_glue` |

与 SUSFS 无关但 4.19 上必做的三件事：

- `kernel/reboot.c` 必须调 `ksu_handle_sys_reboot(...)` 且判 **`== 0`**；判反会吞掉正常 reboot → init 退出 → `kernel BUG at include/linux/pid_namespace.h:90` 无限重启。
- `fs/exec.c` 的 `else` 分支不能给 `ksu_handle_execveat_sucompat` 传 `NULL, NULL, NULL`（NULL 解引用 panic）。
- `kernel/preinit/runner/nolibc/sys.h`：arm64 上 `struct clone_args` 不完整，走 vfork 分支。

验证：

```sh
su -c 'ksud debug info'    # version / uapi_version / features
su -c 'grep -E "susfs is initialized|install fd|manager_appid" /data/adb/ksu/log/dmesg.log'
```

合格输出：

```
susfs:[0][1][susfs_init] susfs is initialized! version: v1.5.5
KernelSU: KSU-A52: 按包名认定管理器 uid=10287
KernelSU: KSU-A52: manager_appid=10287 dynamic_enabled=1
KernelSU: [ksu_driver] fd installed: 6 for pid 918
```

## 4. 刷机与回滚

TWRP 直接刷 AnyKernel3 包，只写 boot 分区（`block=/dev/block/by-name/boot`、`is_slot_device=0`、`do.modules=0`）。
不要 wipe/format data（内核里没有任何激活相关代码）。
回滚用 Odin 刷 `.tar.md5`（`.tar` 会报 `SHA256 is invalid`）。

开机循环时取日志：

```sh
adb shell su -c 'cat /sys/fs/pstore/console-ramoops-0 | strings > /tmp/x'
adb pull /tmp/x
```

## 5. 隐藏面实测（Momo 4.4.1 + KeyAttestation 1.8.4）

| 检测项 | 结果 | 做法 |
|---|---|---|
| `ksu_*` 符号名 | 已隐藏（`grep -c ksu_ /proc/kallsyms` = 0） | `CONFIG_KSU_SUSFS_HIDE_KSU_SUSFS_SYMBOLS=y` |
| `/proc/cmdline` | App 读到 `Permission denied` | 同上 + `SPOOF_CMDLINE_OR_BOOTCONFIG=y` |
| 引导状态属性 | `green` / `flash.locked=1` / `warranty_bit=0` | 开机**早期**（zygote 之前）`resetprop` 写入 |
| 指纹一致性 | `ro.build.fingerprint` 与 `android.os.Build` 一致 | 必须在 post-fs-data 阶段写；等 `boot_completed` 再写会导致 zygote 仍是旧值 |
| Zygisk Next | `root_status: ✅KernelSU`、`modules_with_issue: 0` | — |
| Momo「Bootloader 未锁定」 | 已消除 | TrickyStore 1.4.1 + `/data/adb/tricky_store/keybox.xml`（TEESim 在本机不生效，已 disable） |
| Momo「已开启调试模式」 | 仍红 | 关掉 USB 调试即可 |

`post-fs-data.sh` 关键行：

```sh
RP=/data/adb/ksu/bin/resetprop
$RP ro.build.fingerprint 'google/tangorpro_beta/tangorpro:CANARY/ZP11.260918.007/16484274:user/release-keys'
$RP ro.product.model 'Pixel Tablet'
$RP ro.boot.verifiedbootstate green
$RP ro.boot.flash.locked 1
$RP ro.boot.vbmeta.device_state locked
$RP ro.secureboot.lockstate locked
```

## 6. 已知限制

- `MEETS_STRONG_INTEGRITY` 需要硬件级认证，解锁过的 bootloader 拿不到真值。
- Play Integrity `NETWORK_ERROR (-3)` = 网络到不了 Google。
- `HMA` 开启会让 Play Integrity 报 `APP_UID_MISMATCH (-7)`（两次可复现），测 PI 时先把 GMS 移出生效列表。
- 管理器显示「未检测到驱动」先看第 3 节第 4 条。

## 7. 目录

```
scripts/build_builtin.sh      # 全流程构建（拉源码、打补丁、编译、打包 AnyKernel3）
scripts/fix_driver_419.py     # 4.19 适配 17 组补丁
scripts/apply_susfs_419.py    # 打官方 SUSFS 4.19 补丁并修 reject
scripts/susfs_compat_419.c    # SUSFS v2 API 兼容层
patches/                       # 官方 hook 补丁
```
