// SPDX-License-Identifier: GPL-2.0
/*
 * susfs_compat_419.c
 *
 * SUSFS v2 API compatibility shim for Linux 4.19 + SukiSU Ultra (non-GKI).
 *
 * 背景：
 *   SukiSU Ultra 4.2.1 的 susfs 集成按 SUSFS v2.x 的接口编写，而 SUSFS 官方
 *   4.19 分支停留在 v1.5.5。两者相差若干符号：
 *     - 内核侧（v1.5.5 没有、SukiSU 会引用）：
 *         susfs_set_avc_log_spoofing / susfs_get_enabled_features /
 *         susfs_show_variant / susfs_show_version / susfs_enable_log /
 *         susfs_add_sus_path_loop / susfs_start_sdcard_monitor_fn /
 *         susfs_extra_works
 *     - 进程状态标志：v2 用 thread flag（TIF_PROC_UMOUNTED / TIF_PROC_NO_SU），
 *       v1.5.5 用 task_struct->susfs_task_state 的 TASK_STRUCT_NON_ROOT_USER_APP_PROC。
 *       本文件把两者等价映射（见头文件里的 static inline）。
 *
 * 调用约定：
 *   SukiSU 的 supercall 分发函数签名是
 *       int ksu_handle_sys_reboot(..., void __user **arg)
 *   v1.5.5 的入口函数接收「指向用户结构体的用户态指针」。为保持一致，
 *   dispatch.c 里统一改写成 susfs_xxx(*arg)，因此本文件全部按
 *   void __user *user_info 取值（即 *arg）。
 *
 * 这个文件是新增的，不修改 v1.5.5 的 susfs.c，避免补丁冲突。
 */

#include <linux/kernel.h>
#include <linux/slab.h>
#include <linux/string.h>
#include <linux/types.h>
#include <linux/jump_label.h>
#include <linux/workqueue.h>
#include <linux/sched.h>
#include <linux/uaccess.h>
#include <linux/version.h>
#include <linux/init.h>

#include <linux/susfs.h>
#include <linux/susfs_def.h>

/* ---------------------------------------------------------------- */
/* 与 SukiSU userspace (ksud/src/susfs/abi) 严格对齐的结构体        */
/* ---------------------------------------------------------------- */

#define SUSFS_COMPAT_MAX_VERSION_BUFSIZE 16
#define SUSFS_COMPAT_MAX_VARIANT_BUFSIZE 16
#define SUSFS_COMPAT_ENABLED_FEATURES_SIZE 8192

struct st_susfs_compat_version {
	char	susfs_version[SUSFS_COMPAT_MAX_VERSION_BUFSIZE];
	int	err;
};

struct st_susfs_compat_variant {
	char	susfs_variant[SUSFS_COMPAT_MAX_VARIANT_BUFSIZE];
	int	err;
};

struct st_susfs_compat_features {
	char	enabled_features[SUSFS_COMPAT_ENABLED_FEATURES_SIZE];
	int	err;
};

struct st_susfs_compat_avc {
	u32	enabled;
	int	err;
};

struct st_susfs_compat_log {
	u32	enabled;
	int	err;
};

/* ---------------------------------------------------------------- */
/* AVC 日志伪装：static key，供 security/selinux/avc.c 引用         */
/* ---------------------------------------------------------------- */

DEFINE_STATIC_KEY_FALSE(susfs_is_avc_log_spoofing_enabled);
EXPORT_SYMBOL_GPL(susfs_is_avc_log_spoofing_enabled);

bool susfs_is_avc_log_spoofing_enabled_query(void)
{
	return static_branch_likely(&susfs_is_avc_log_spoofing_enabled);
}

/* ---------------------------------------------------------------- */
/* extra works：SukiSU 的 lsm_hook.c 用 extern struct work_struct     */
/* ---------------------------------------------------------------- */

struct work_struct susfs_extra_works;

static void susfs_run_extra_works(struct work_struct *work)
{
	/* v1.5.5 没有延迟清理队列，这里只做一次轻量回收占位。
	 * 真正的 umount 由 TRY_UMOUNT 路径负责（本机型未启用）。 */
	pr_info("susfs_compat: extra works drained\n");
}

static int __init susfs_compat_init(void)
{
	INIT_WORK(&susfs_extra_works, susfs_run_extra_works);
	pr_info("susfs_compat: v2 API shim ready (v1.5.5 base)\n");
	return 0;
}
late_initcall(susfs_compat_init);

/* ---------------------------------------------------------------- */
/* CMD_SUSFS_ENABLE_AVC_LOG_SPOOFING                                */
/* ---------------------------------------------------------------- */

void susfs_set_avc_log_spoofing(void __user *user_info)
{
	struct st_susfs_compat_avc info = { 0 };

	if (!user_info) {
		pr_err("susfs_compat: avc_log_spoofing null arg\n");
		return;
	}
	if (copy_from_user(&info, user_info, sizeof(info))) {
		info.err = -EFAULT;
		goto out;
	}

	if (info.enabled) {
		static_branch_enable(&susfs_is_avc_log_spoofing_enabled);
		pr_info("susfs_compat: avc log spoofing enabled\n");
	} else {
		static_branch_disable(&susfs_is_avc_log_spoofing_enabled);
		pr_info("susfs_compat: avc log spoofing disabled\n");
	}

	info.err = 0;
out:
	if (copy_to_user(user_info, &info, sizeof(info)))
		pr_err("susfs_compat: avc_log_spoofing copy_to_user failed\n");
}

/* ---------------------------------------------------------------- */
/* CMD_SUSFS_SHOW_ENABLED_FEATURES                                  */
/* ---------------------------------------------------------------- */

static int susfs_compat_append_feature(char *buf, size_t bufsize,
				       size_t *used, const char *name)
{
	size_t len = strlen(name);

	if (*used + len + 1 >= bufsize)
		return -EINVAL;
	memcpy(buf + *used, name, len);
	*used += len;
	buf[*used++] = '\n';
	buf[*used] = '\0';
	return 0;
}

void susfs_get_enabled_features(void __user *user_info)
{
	struct st_susfs_compat_features *info;
	size_t used = 0;
	int err = 0;

	if (!user_info) {
		pr_err("susfs_compat: enabled_features null arg\n");
		return;
	}

	info = kzalloc(sizeof(*info), GFP_KERNEL);
	if (!info) {
		/* OOM：只回写 err 字段。注意不能 copy_to_user(&err, sizeof(*info))——
		 * 那会从栈上 4 字节的 int 往外读 8KB（越界读，可能踩到 guard page）。 */
		int __user *errp = (int __user *)((char __user *)user_info +
						 offsetof(struct st_susfs_compat_features, err));
		err = -ENOMEM;
		if (put_user(err, errp))
			pr_err("susfs_compat: enabled_features oom copy failed\n");
		return;
	}

	if (copy_from_user(info, user_info, sizeof(*info))) {
		info->err = -EFAULT;
		goto out;
	}

#ifdef CONFIG_KSU_SUSFS_SUS_PATH
	if ((err = susfs_compat_append_feature(info->enabled_features,
			SUSFS_COMPAT_ENABLED_FEATURES_SIZE, &used,
			"CONFIG_KSU_SUSFS_SUS_PATH")))
		goto out_err;
#endif
#ifdef CONFIG_KSU_SUSFS_SUS_MOUNT
	if ((err = susfs_compat_append_feature(info->enabled_features,
			SUSFS_COMPAT_ENABLED_FEATURES_SIZE, &used,
			"CONFIG_KSU_SUSFS_SUS_MOUNT")))
		goto out_err;
#endif
#ifdef CONFIG_KSU_SUSFS_SUS_KSTAT
	if ((err = susfs_compat_append_feature(info->enabled_features,
			SUSFS_COMPAT_ENABLED_FEATURES_SIZE, &used,
			"CONFIG_KSU_SUSFS_SUS_KSTAT")))
		goto out_err;
#endif
#ifdef CONFIG_KSU_SUSFS_SPOOF_UNAME
	if ((err = susfs_compat_append_feature(info->enabled_features,
			SUSFS_COMPAT_ENABLED_FEATURES_SIZE, &used,
			"CONFIG_KSU_SUSFS_SPOOF_UNAME")))
		goto out_err;
#endif
#ifdef CONFIG_KSU_SUSFS_ENABLE_LOG
	if ((err = susfs_compat_append_feature(info->enabled_features,
			SUSFS_COMPAT_ENABLED_FEATURES_SIZE, &used,
			"CONFIG_KSU_SUSFS_ENABLE_LOG")))
		goto out_err;
#endif
#ifdef CONFIG_KSU_SUSFS_HIDE_KSU_SUSFS_SYMBOLS
	if ((err = susfs_compat_append_feature(info->enabled_features,
			SUSFS_COMPAT_ENABLED_FEATURES_SIZE, &used,
			"CONFIG_KSU_SUSFS_HIDE_KSU_SUSFS_SYMBOLS")))
		goto out_err;
#endif
#ifdef CONFIG_KSU_SUSFS_SPOOF_CMDLINE_OR_BOOTCONFIG
	if ((err = susfs_compat_append_feature(info->enabled_features,
			SUSFS_COMPAT_ENABLED_FEATURES_SIZE, &used,
			"CONFIG_KSU_SUSFS_SPOOF_CMDLINE_OR_BOOTCONFIG")))
		goto out_err;
#endif
#ifdef CONFIG_KSU_SUSFS_OPEN_REDIRECT
	if ((err = susfs_compat_append_feature(info->enabled_features,
			SUSFS_COMPAT_ENABLED_FEATURES_SIZE, &used,
			"CONFIG_KSU_SUSFS_OPEN_REDIRECT")))
		goto out_err;
#endif
#ifdef CONFIG_KSU_SUSFS_SUS_MAP
	if ((err = susfs_compat_append_feature(info->enabled_features,
			SUSFS_COMPAT_ENABLED_FEATURES_SIZE, &used,
			"CONFIG_KSU_SUSFS_SUS_MAP")))
		goto out_err;
#endif

	info->err = 0;
	goto out;

out_err:
	info->err = err;
out:
	if (copy_to_user(user_info, info, sizeof(*info)))
		pr_err("susfs_compat: enabled_features copy_to_user failed\n");
	kfree(info);
}

/* ---------------------------------------------------------------- */
/* CMD_SUSFS_SHOW_VARIANT / SHOW_VERSION                            */
/* ---------------------------------------------------------------- */

void susfs_show_variant(void __user *user_info)
{
	struct st_susfs_compat_variant info = { 0 };

	if (!user_info)
		return;
	if (copy_from_user(&info, user_info, sizeof(info))) {
		info.err = -EFAULT;
		goto out;
	}

	strscpy(info.susfs_variant, SUSFS_VARIANT,
		SUSFS_COMPAT_MAX_VARIANT_BUFSIZE - 1);
	info.err = 0;
out:
	if (copy_to_user(user_info, &info, sizeof(info)))
		pr_err("susfs_compat: show_variant copy_to_user failed\n");
}

void susfs_show_version(void __user *user_info)
{
	struct st_susfs_compat_version info = { 0 };

	if (!user_info)
		return;
	if (copy_from_user(&info, user_info, sizeof(info))) {
		info.err = -EFAULT;
		goto out;
	}

	strscpy(info.susfs_version, SUSFS_VERSION,
		SUSFS_COMPAT_MAX_VERSION_BUFSIZE - 1);
	info.err = 0;
out:
	if (copy_to_user(user_info, &info, sizeof(info)))
		pr_err("susfs_compat: show_version copy_to_user failed\n");
}

/* ---------------------------------------------------------------- */
/* CMD_SUSFS_ENABLE_LOG —— SukiSU 叫 susfs_enable_log，v1.5.5 叫     */
/* susfs_set_log(bool)。这里做一次适配。                             */
/* ---------------------------------------------------------------- */

void susfs_enable_log(void __user *user_info)
{
	struct st_susfs_compat_log info = { 0 };

	if (!user_info)
		return;
	if (copy_from_user(&info, user_info, sizeof(info))) {
		info.err = -EFAULT;
		goto out;
	}

#ifdef CONFIG_KSU_SUSFS_ENABLE_LOG
	susfs_set_log(!!info.enabled);
	info.err = 0;
#else
	info.err = -EINVAL;
#endif
out:
	if (copy_to_user(user_info, &info, sizeof(info)))
		pr_err("susfs_compat: enable_log copy_to_user failed\n");
}

/* ---------------------------------------------------------------- */
/* CMD_SUSFS_ADD_SUS_PATH_LOOP —— v1.5.5 只有按 ino 单条添加，       */
/* loop 版本退化为单条添加（行为安全，不会漏隐藏）。                 */
/* ---------------------------------------------------------------- */

void susfs_add_sus_path_loop(void __user *user_info)
{
#ifdef CONFIG_KSU_SUSFS_SUS_PATH
	struct st_susfs_sus_path *info = (struct st_susfs_sus_path *)user_info;

	if (!user_info)
		return;
	pr_info("susfs_compat: ADD_SUS_PATH_LOOP degraded to ADD_SUS_PATH\n");
	/* v1.5.5 的 susfs_add_sus_path 自己会 copy_from_user 并回写 err */
	susfs_add_sus_path(info);
#else
	pr_info("susfs_compat: SUS_PATH disabled, ignoring LOOP cmd\n");
#endif
}

/* ---------------------------------------------------------------- */
/* susfs_start_sdcard_monitor_fn —— v2 用 fsnotify 监听             */
/* /data/media/0/Android；4.19 + v1.5.5 无对应机制，安全 no-op。      */
/* ---------------------------------------------------------------- */

void susfs_start_sdcard_monitor_fn(void)
{
	pr_info("susfs_compat: sdcard monitor not applicable on 4.19 (no-op)\n");
}

/* ---------------------------------------------------------------- */
/* susfs_starts_with / susfs_ends_with —— v1.5.5 的 susfs.h/.c 里都  */
/* 没有，但 SukiSU 的 feature/adb_root.c 用它们判断                      */
/* "/apex/..../adbd" 是不是 adbd 被 exec。缺实现就是链接错误：          */
/*   adb_root.c:13: undefined reference to `susfs_starts_with'         */
/*   adb_root.c:14: undefined reference to `susfs_ends_with'           */
/* 语义与 SUSFS v2 一致：纯粹的字符串前缀/后缀比较。                    */
/* ---------------------------------------------------------------- */

bool susfs_starts_with(const char *str, const char *prefix)
{
	size_t prefix_len;

	if (!str || !prefix)
		return false;

	prefix_len = strlen(prefix);
	return strncmp(str, prefix, prefix_len) == 0;
}

bool susfs_ends_with(const char *str, const char *suffix)
{
	size_t str_len;
	size_t suffix_len;

	if (!str || !suffix)
		return false;

	str_len = strlen(str);
	suffix_len = strlen(suffix);
	if (str_len < suffix_len)
		return false;

	return strcmp(str + str_len - suffix_len, suffix) == 0;
}
