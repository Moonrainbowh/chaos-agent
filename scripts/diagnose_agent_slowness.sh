#!/usr/bin/env bash
# 诊断 WorkBuddy Agent 单任务耗时异常。
#
# 用法：
#   bash scripts/diagnose_agent_slowness.sh
#
# 注意：本机 PATH 缺失 /usr/bin，会导致两件连锁事故 ——
#   1) coreutils（ls/cat/grep/sed/dirname…）全部 command not found
#   2) 裸写 `bash` 会落到 WindowsApps\bash.exe（实为 wsl.exe 的符号链接），被安全策略拦截
# 所以脚本第一件事就是自愈 PATH；这也是推荐的临时解法。

export PATH="/usr/bin:$PATH"

WS="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LOGDIR="$HOME/.workbuddy/logs"
TODAY="$(date +%Y-%m-%d)"
SHIM="$HOME/AppData/Local/Programs/WorkBuddy/resources/app.asar.unpacked/cli/vendor/shim"

hr() { printf '%s\n' "------------------------------------------------------------"; }

hr
echo "[1] coreutils 是否可用"
hr
missing=0
for c in ls cat head dirname grep sed awk find wc sort; do
  if command -v "$c" >/dev/null 2>&1; then
    printf '  %-8s OK   %s\n' "$c" "$(command -v "$c")"
  else
    printf '  %-8s MISSING\n' "$c"
    missing=$((missing + 1))
  fi
done
if [ "$missing" -gt 0 ]; then
  echo "  >> 异常：$missing 个命令缺失"
  echo "     临时解法：命令前加 PATH=\"/usr/bin:\$PATH\""
else
  echo "  >> 正常"
fi

hr
echo "[2] PATH 中是否包含 /usr/bin"
hr
case ":$PATH:" in
  */usr/bin:*) echo "  包含（正常）" ;;
  *)           echo "  缺失 —— 这是问题 1 与问题 3 的共同根因" ;;
esac
echo "  PATH 条目总数：$(echo "$PATH" | tr ':' '\n' | wc -l)"
echo "  （条目异常膨胀说明被 shim 反复叠加，会拖慢每次外部命令查找）"

hr
echo "[3] bash 名字解析 —— 最隐蔽的一环"
hr
resolved="$(command -v bash 2>/dev/null)"
echo "  command -v bash -> ${resolved:-<未解析>}"
case "$resolved" in
  *WindowsApps*)
    echo "  >> 严重：解析到了 WindowsApps\\bash.exe"
    echo "     该文件是指向 wsl.exe 的符号链接，会被 sandbox 指令安全策略拦截，"
    echo "     整条命令直接判 blocked by policy，且提示无法通过批准绕过。"
    ;;
  /usr/bin/bash|*/PortableGit/*)
    echo "  >> 正常"
    ;;
esac
echo "  WindowsApps 在 PATH 中的位置（越靠前越危险，且常见重复）："
echo "$PATH" | tr ':' '\n' | grep -n -i "WindowsApps" | sed 's/^/    /' || echo "    未出现"
if [ -f "$HOME/AppData/Local/Microsoft/WindowsApps/bash.exe" ]; then
  echo "  证据：WindowsApps\\bash.exe -> $(readlink -f "$HOME/AppData/Local/Microsoft/WindowsApps/bash.exe" 2>/dev/null)"
fi

hr
echo "[4] shim 启动脚本是否报错（每次 bash 调用都会触发）"
hr
if [ -f "$SHIM/shell-runtime-bash-env.sh" ]; then
  echo "  shim（每类子 shell 都会 source）：$SHIM/shell-runtime-bash-env.sh"
  echo "  该脚本第 3 行用 dirname 定位自身目录。dirname 缺失时整段初始化失效，"
  echo "  包括 safe-delete 与 sandbox 包装的加载；每次 bash 调用都会吐 2 行 stderr。"
  echo "  >> PATH 补上 /usr/bin 后此项应自动消失"
else
  echo "  未找到 shim 目录，跳过"
fi

hr
echo "[5] PowerShell 输出回流（需在 Agent 侧人工确认）"
hr
echo "  现象：PowerShell 工具返回 exit 0，但 stdout 为空，Agent 无法判断命令结果。"
echo "  注意：本脚本刻意不从 bash 里拉起 powershell.exe ——"
echo "        在 sandbox 下这样做同样会踩策略，反而更慢。"
echo "  人工验证：直接调用 PowerShell 工具执行下面这条，看 Agent 侧是否收到 stdout："
echo "    \$p=\"\$HOME/.workbuddy/_diag_probe.txt\"; \"PROBE\" | Set-Content \$p; Get-Content \$p"
echo "  若文件内容已写入、但 Agent 侧 stdout 为空 —— 即为输出未回流。"

hr
echo "[6] 工作区规模（Glob 超时的直接原因）"
hr
if [ -d "$WS" ]; then
  files=$(find "$WS" -type f 2>/dev/null | wc -l)
  dirs=$(find "$WS" -type d 2>/dev/null | wc -l)
  echo "  文件总数：$files"
  echo "  目录总数：$dirs"
  echo "  Glob 需遍历全部条目；万级规模即会撞上 30s 硬超时，且会反复重试。"
  echo "  主要贡献者："
  for d in artifacts src build tests code_agent_win context-ab-20260909; do
    [ -d "$WS/$d" ] && printf '    %-24s %s files\n' "$d" "$(find "$WS/$d" -type f 2>/dev/null | wc -l)"
  done
  if [ -f "$WS/.gitignore" ]; then
    echo "  .gitignore 现有条目："
    grep -v '^#' "$WS/.gitignore" | grep -v '^$' | sed 's/^/    /'
    echo "  >> 比对上面清单：未被忽略的大目录应补进 .gitignore"
  fi
else
  echo "  工作区不存在：$WS"
fi

hr
echo "[7] 最近会话日志：工具耗时与重试中断"
hr
if [ -d "$LOGDIR/$TODAY" ]; then
  if [ -n "$1" ] && [ -f "$1" ]; then
    latest="$1"
  else
    latest=$(ls -t "$LOGDIR/$TODAY"/*.log 2>/dev/null \
      | grep -viE -- '-diag\.log$|/daemon|automation-|edge-sync|unknown-workspace|workbuddyMainThread|system32__' \
      | head -1)
  fi
  echo "  目标日志：${latest:-<未找到会话日志>}"
  echo "  （可用参数显式指定某个日志：bash scripts/diagnose_agent_slowness.sh <log路径>）"
  if [ -n "$latest" ]; then
    echo "  工具调用总次数：$(grep -c 'ToolManager\] execute' "$latest")"
    echo
    echo "  单次耗时 > 2000ms 的调用："
    grep -o 'tool=[A-Za-z]*[^|]*elapsed=[0-9]*ms' "$latest" 2>/dev/null \
      | sed -E 's/.*tool=([A-Za-z]*).*elapsed=([0-9]+)ms/\2 \1/' \
      | awk '$1 > 2000' | sort -rn | head -10 \
      | awk '{printf "    %-10s %sms\n", $2, $1}'
    echo
    echo "  重试 / 中断 / 拦截信号："
    printf '    interceptorGate    %s\n' "$(grep -c 'interceptorGate' "$latest")"
    printf '    TOOL_STARTED       %s\n' "$(grep -c 'TOOL_STARTED' "$latest")"
    printf '    TOOL_ENDED         %s\n' "$(grep -c 'TOOL_ENDED' "$latest")"
    printf '    timeout            %s\n' "$(grep -c 'timeout' "$latest")"
    printf '    blocked by policy  %s\n' "$(grep -ci 'blocked by policy\|BLOCKED BY SECURITY POLICY' "$latest")"
    echo
    echo "  注：脚本日志可能是环形缓冲，只保留最近若干行。"
    echo "      若首行时间明显晚于会话开始，说明早期记录已被覆盖，需当轮即时排查。"
  fi
else
  echo "  今日日志目录不存在：$LOGDIR/$TODAY"
fi

hr
echo "[8] daemon 层 SLOW 告警数量"
hr
if [ -f "$LOGDIR/daemon.log" ]; then
  echo "  被标记 SLOW 的次数：$(grep -c 'SLOW' "$LOGDIR/daemon.log")"
  echo "  （daemon RPC 频繁超 500ms 时，前端每一步交互都会卡顿）"
else
  echo "  未找到 daemon.log"
fi

hr
echo "诊断结束"
hr
