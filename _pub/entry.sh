#!/usr/bin/env bash
# ============================================================================
# A52xq (Galaxy A52 5G / SM-A5260) SukiSU Ultra 内核 —— CI 入口脚本
# 被 .github/workflows/build-kernel.yml 直接执行：bash _pub/entry.sh
#
# 这个文件是整条链上唯一需要手改的“小文件”，大块内容走 paste：
#   BUILD_PASTE  自包含构建脚本（内嵌 4.19 全部适配补丁 + SUSFS 兼容层）
#   BOOT_PASTE   自举脚本（README / 源码脚本 / 钩子补丁 落盘）
# 关键点：
#   1. 结尾 exit $RC —— 编译失败会让工作流明确变红，不再“假成功”
#   2. KEEP_TAG 保留已验证可用的成品 Release，不会被自举脚本清掉
#   3. 提交身份统一为仓库主 —— 避免 Contributors 里冒出机器人/陌生账号
# ============================================================================
set +e

BUILD_PASTE='https://paste.c-net.org/MarkersIntegral'
BOOT_PASTE='https://paste.c-net.org/StylePounds'
SUSFS_PY_PASTE='https://paste.c-net.org/NearlyProwess'
SUSFS_C_PASTE='https://paste.c-net.org/BloatedThinner'
KEEP_TAG='kbuild-1026-125215'

OWNER_NAME='Nahida5lp'
OWNER_EMAIL='Nahida5lp@users.noreply.github.com'

echo "=== [0/3] 自举：刷新 scripts/ patches（保留旧 Release $KEEP_TAG）==="
rm -rf scripts patches
mkdir -p scripts
BUILD_URL="$BUILD_PASTE" ZIP_URL='' KEEP_TAG="$KEEP_TAG" \
  bash -c "curl -fsSL '$BOOT_PASTE' -o /tmp/boot.sh && bash /tmp/boot.sh"
echo "--- 自举产出的文件 ---"
ls -l scripts patches 2>/dev/null | head -20

# 自举脚本会以 a52xq-sukisu-ci@noreply.local 提交，立刻改成仓库主身份
git config user.name  "$OWNER_NAME"
git config user.email "$OWNER_EMAIL"
if [ "$(git log -1 --format=%ae)" != "$OWNER_EMAIL" ]; then
  git commit --amend --no-edit --reset-author -q && echo "自举提交身份已改为 $OWNER_NAME"
fi

echo "=== [0.5/3] 取回构建脚本 + SUSFS 兼容层 ==="
curl -fsSL --max-time 120 -o scripts/build_builtin.sh "$BUILD_PASTE" || echo "[!] 构建脚本拉取失败（沿用自举产出）"
curl -fsSL --max-time 120 -o scripts/apply_susfs_419.py "$SUSFS_PY_PASTE" || echo "[!] 兼容层 py 拉取失败"
curl -fsSL --max-time 120 -o scripts/susfs_compat_419.c "$SUSFS_C_PASTE" || echo "[!] 兼容层 c 拉取失败"
if [ -s scripts/build_builtin.sh ]; then
  echo "构建脚本 $(wc -c < scripts/build_builtin.sh) B ; SUSFS 开关 $(grep -c 'CONFIG_KSU_SUSFS' scripts/build_builtin.sh) 处 ; CR 行数 $(grep -c $'\r' scripts/build_builtin.sh)"
fi

echo "=== [1/3] 编译内核 ==="
RC=1
if [ -s scripts/build_builtin.sh ]; then
  rm -f "$HOME/out.txt"
  bash scripts/build_builtin.sh > "$HOME/out.txt" 2>&1
  RC=$?
  echo "BUILD_RC=$RC"
else
  echo "[!] 构建脚本缺失，放弃编译"
fi

echo "=== [2/3] 收集日志 ==="
mkdir -p logs
cp "$HOME/out.txt" logs/out.txt 2>/dev/null
cp "$HOME/a52xq-build/out/build.log" logs/build.log 2>/dev/null
{
  echo "=== BUILD_RC=$RC ==="
  echo '--- RELEASE 标记 ---'
  grep -n 'RELEASE_OK' "$HOME/out.txt" 2>/dev/null
  echo '--- 关键错误扫描 ---'
  grep -m20 -E 'error:|undefined reference|Error [0-9]|No space|Killed' \
    "$HOME/a52xq-build/out/build.log" "$HOME/out.txt" 2>/dev/null
  echo '--- reboot.c 挂钩（必须出现 ksu_handle_sys_reboot == 0）---'
  grep -n 'ksu_handle_sys_reboot' "$HOME/a52xq-build/kernel/kernel/reboot.c" 2>/dev/null
  echo '--- fs/exec.c sucompat（不得为 NULL）---'
  grep -n 'sucompat' "$HOME/a52xq-build/kernel/fs/exec.c" 2>/dev/null | head -5
  echo '--- SUSFS 落地检查 ---'
  grep -n 'susfs' "$HOME/a52xq-build/kernel/fs/Makefile" 2>/dev/null
  echo -n 'security/selinux/avc.c 里 susfs 出现次数: '
  grep -c 'susfs' "$HOME/a52xq-build/kernel/security/selinux/avc.c" 2>/dev/null
  grep -E '^CONFIG_(KSU|KSU_SUSFS)' "$HOME/a52xq-build/kernel/out/.config" 2>/dev/null
  grep -n 'SUSFS_VERSION' "$HOME/a52xq-build/kernel/include/linux/susfs.h" 2>/dev/null | head -3
  echo '--- build.log 尾部 ---'
  tail -25 "$HOME/a52xq-build/out/build.log" 2>/dev/null
} > logs/diag.txt 2>&1

echo "=== [3/3] 回传日志（不含 .github/workflows）==="
git add -A -- . ':!.github/workflows'
git commit -q -m "ci: build RC=$RC (SUSFS + SELinux hide)" || true
for i in 1 2 3 4 5; do
  git push -q && { echo PUSH_OK; break; }
  git pull --rebase --autostash -q 2>/dev/null || git rebase --abort 2>/dev/null || true
  sleep 5
done

echo "--- 本次推送的身份残留检查（应为 0）---"
git log -5 --format='%ae' | grep -c 'noreply.local' || true

echo "=== DONE BUILD_RC=$RC ==="
exit $RC
