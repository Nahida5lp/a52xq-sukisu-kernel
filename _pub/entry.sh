#!/usr/bin/env bash
# ============================================================================
# A52xq (Galaxy A52 5G / SM-A5260) SukiSU Ultra 内核 —— CI 入口（薄壳）
# 真正的入口逻辑放在 paste 里，这里只负责拉取并执行；
# 以后换链路只需改下面这一行 URL（以及重新上传 paste）。
# ============================================================================
set +e

ENTRY_URL='https://paste.c-net.org/FibersBosses'

curl -fsSL --max-time 120 "$ENTRY_URL" -o /tmp/entry_real.sh
if [ ! -s /tmp/entry_real.sh ]; then
  echo "[!] 入口脚本拉取失败: $ENTRY_URL"
  exit 1
fi
echo "--- 入口脚本 $(wc -c < /tmp/entry_real.sh) B (来自 $ENTRY_URL) ---"
bash /tmp/entry_real.sh
RC=$?
echo "=== WRAPPER DONE RC=$RC ==="
exit $RC
