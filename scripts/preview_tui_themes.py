import sys
import os
import io

if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")


RST = "\033[0m"
BOLD = "\033[1m"
DIM = "\033[2m"
INV = "\033[7m"

FG_CYAN = "\033[38;5;80m"
FG_BRIGHT_CYAN = "\033[1;96m"
FG_BLUE = "\033[38;5;75m"
FG_PURPLE = "\033[38;5;141m"
FG_MINT = "\033[38;5;120m"
FG_AMBER = "\033[38;5;215m"
FG_GREEN = "\033[38;5;114m"
FG_RED = "\033[38;5;203m"
FG_GRAY = "\033[38;5;242m"
FG_LIGHT_GRAY = "\033[38;5;250m"
FG_WHITE = "\033[38;5;255m"

BG_DARK_GRAY = "\033[48;5;236m"
BG_BLUE = "\033[48;5;24m"
BG_PURPLE = "\033[48;5;55m"
BG_GREEN = "\033[48;5;28m"

W = 86

def hr(char="─", color=FG_GRAY, width=W):
    return f"{color}{char * width}{RST}"

def render_theme_1():
    lines = []
    lines.append(f"{BOLD}{FG_BRIGHT_CYAN}方案 1: 【Claude Code 现代极简流】 Modern Minimalist{RST}")
    lines.append(f"{DIM}核心理念: 去除沉重包围框，用微呼吸感排版与纯粹留白凸显内容，轻盈无负担{RST}")
    lines.append(hr("─", FG_GRAY))
    lines.append("")
    lines.append(f" {FG_BRIGHT_CYAN}❯{RST} {BOLD}{FG_WHITE}请帮我排查测试用例失败的原因，并生成修复补丁{RST}")
    lines.append("")
    lines.append(f" {FG_PURPLE}✦{RST} {BOLD}{FG_PURPLE}Chaos Agent{RST} {DIM}正在分析工作区环境...{RST}")
    lines.append(f"   {FG_GRAY}├─{RST} {FG_CYAN}run{RST} pytest tests/test_runtime.py {DIM}(3 tests failed){RST}")
    lines.append(f"   {FG_GRAY}├─{RST} {FG_CYAN}read{RST} src/code_agent/interfaces/windows_tui.py {DIM}(lines 140-180){RST}")
    lines.append(f"   {FG_GRAY}╰─{RST} {FG_GREEN}✓{RST} 定位到状态刷新时 cursor_row 越界计算缺陷")
    lines.append("")
    lines.append(f"   我已经找到了导致死锁的问题根源。在 `windows_tui.py` 的 `redraw` 循环中，")
    lines.append(f"   `LiveTailGeometry` 未能在多行渲染时正确扣除高度预算。")
    lines.append("")
    lines.append(f"   {DIM}```python{RST}")
    lines.append(f"   {FG_PURPLE}-   budget = height - 3{RST}")
    lines.append(f"   {FG_MINT}+   budget = max(1, height - len(palette) - 3){RST}")
    lines.append(f"   {DIM}```{RST}")
    lines.append("")
    lines.append(f" {FG_BRIGHT_CYAN}┌── {BOLD}Prompt{RST} {FG_GRAY}────────────────────────────────────────── {FG_GRAY}[Ctrl+C 取消]{RST} {FG_BRIGHT_CYAN}┐{RST}")
    lines.append(f" {FG_BRIGHT_CYAN}│{RST}  {FG_BRIGHT_CYAN}❯{RST} {FG_WHITE}确认执行修复，并提交 git commit{RST}▌                                 {FG_BRIGHT_CYAN}│{RST}")
    lines.append(f" {FG_BRIGHT_CYAN}└─────────────────────────────────────── {DIM}Enter 发送 · Shift+Enter 换行{RST} {FG_BRIGHT_CYAN}─┘{RST}")
    status_left = f" {FG_GREEN}●{RST} {FG_LIGHT_GRAY}就绪{RST} {FG_GRAY}│{RST} {FG_CYAN}Claude 3.7 Sonnet{RST} {FG_GRAY}│{RST} {DIM}main*{RST}"
    status_right = f"{FG_LIGHT_GRAY}14.2k tokens (7%){RST} {FG_GRAY}│{RST} {FG_AMBER}48.5 tok/s{RST} "
    pad = W - len(" ● 就绪 │ Claude 3.7 Sonnet │ main*") - len("14.2k tokens (7%) │ 48.5 tok/s ") + 2
    lines.append(f"{status_left}{' ' * max(2, pad)}{status_right}")
    lines.append("")
    return "\n".join(lines)

def render_theme_2():
    lines = []
    lines.append(f"{BOLD}{FG_BLUE}方案 2: 【Linear Studio 精密工作台】 Studio Pro{RST}")
    lines.append(f"{DIM}核心理念: 一体化多功能卡片，内嵌模型/权限/上下文预算药丸，掌控感极强{RST}")
    lines.append(hr("─", FG_GRAY))
    lines.append("")
    lines.append(f" {FG_GRAY}╭─{RST} {BOLD}{FG_BLUE}You{RST} {DIM}15:42:10{RST} {FG_GRAY}──────────────────────────────────────────────────────╮{RST}")
    lines.append(f" {FG_GRAY}│{RST} 请帮我排查测试用例失败的原因，并生成修复补丁                         {FG_GRAY}│{RST}")
    lines.append(f" {FG_GRAY}╰───────────────────────────────────────────────────────────────────╯{RST}")
    lines.append("")
    lines.append(f" {FG_BLUE}╭─{RST} {BOLD}{FG_PURPLE}✦ Chaos Agent{RST} {DIM}15:42:12{RST} {FG_BLUE}───────────────────────────────────────────────╮{RST}")
    lines.append(f" {FG_BLUE}│{RST} 已执行测试套件诊断并检查相关源码:                                 {FG_BLUE}│{RST}")
    lines.append(f" {FG_BLUE}│{RST}   {BG_DARK_GRAY} ⚙ TEST {RST} pytest tests/test_runtime.py -> {FG_RED}FAILED (3){RST}                {FG_BLUE}│{RST}")
    lines.append(f" {FG_BLUE}│{RST}   {BG_DARK_GRAY} ✎ EDIT {RST} 修复 `windows_tui.py` 中的 budget 计算逻辑               {FG_BLUE}│{RST}")
    lines.append(f" {FG_BLUE}│{RST}                                                                   {FG_BLUE}│{RST}")
    lines.append(f" {FG_BLUE}│{RST} 补丁已准备就绪，可以随时应用并运行自动化校验。                     {FG_BLUE}│{RST}")
    lines.append(f" {FG_BLUE}╰───────────────────────────────────────────────────────────────────╯{RST}")
    lines.append("")
    lines.append(f" {FG_BLUE}╭──{RST} {BOLD}{FG_WHITE}Active Prompt{RST} {FG_GRAY}─────────────────────────────{RST} {BG_BLUE}{FG_WHITE} ⚙ Plan 模式 {RST} {FG_BLUE}──╮{RST}")
    lines.append(f" {FG_BLUE}│{RST}  {FG_BRIGHT_CYAN}✧{RST} {FG_WHITE}确认执行修复，并提交 git commit{RST}▌                                      {FG_BLUE}│{RST}")
    lines.append(f" {FG_BLUE}├───────────────────────────────────────────────────────────────────┤{RST}")
    lines.append(f" {FG_BLUE}│{RST}  {FG_PURPLE}⬡ Sonnet-3.7{RST}  {FG_MINT}⛊ ReadWrite{RST}  {FG_CYAN}▰▰▰▱▱ 18k/128k (14%){RST}  {FG_AMBER}📎 0 附件{RST}  {DIM}[Tab 补全]{RST}  {FG_BLUE}│{RST}")
    lines.append(f" {FG_BLUE}╰───────────────────────────────────────────────────────────────────╯{RST}")
    lines.append(f"  {BG_GREEN}{FG_WHITE}{BOLD} READY {RST} {FG_LIGHT_GRAY}耗时 1.2s{RST} {FG_GRAY}·{RST} {FG_LIGHT_GRAY}无待处理审批{RST} {FG_GRAY}·{RST} {DIM}按 Ctrl+P 唤起功能面板{RST}")
    lines.append("")
    return "\n".join(lines)

def render_theme_3():
    lines = []
    lines.append(f"{BOLD}{FG_AMBER}方案 3: 【硬核极客 / Powerline】 Cyber-Brutalist{RST}")
    lines.append(f"{DIM}核心理念: 高对比块状色标、硬朗双线框、反色 Tag、状态栏分段箭角，密度极高{RST}")
    lines.append(hr("═", FG_AMBER))
    lines.append("")
    lines.append(f" {INV}{BOLD} USER {RST}  {FG_WHITE}请帮我排查测试用例失败的原因，并生成修复补丁{RST}")
    lines.append("")
    lines.append(f" {BG_PURPLE}{FG_WHITE}{BOLD} AGENT {RST} {BOLD}{FG_BRIGHT_CYAN}EXECUTION TRACE{RST} {DIM}#turn-482{RST}")
    lines.append(f" ╠═══ {FG_AMBER}TOOL[RUN]{RST} -> pytest tests/test_runtime.py [{FG_RED}ERR 3{RST}]")
    lines.append(f" ╠═══ {FG_AMBER}TOOL[INSPECT]{RST} -> src/code_agent/interfaces/windows_tui.py")
    lines.append(f" ╚═══ {FG_GREEN}STATE[RESOLVED]{RST} -> 成功定位 layout budget 越界")
    lines.append("")
    lines.append(f" >>> 根因确认为 LiveTailGeometry 渲染高度截断。准备写入修复。")
    lines.append("")
    lines.append(f" {FG_AMBER}╔═[{RST} {BOLD}{FG_WHITE}TERMINAL INPUT{RST} {FG_AMBER}]═════════════════════════════[{RST} {BG_PURPLE}{FG_WHITE} CODE-MODE {RST} {FG_AMBER}]═╗{RST}")
    lines.append(f" {FG_AMBER}║{RST} {FG_AMBER}❯{RST} {FG_WHITE}确认执行修复，并提交 git commit{RST}▌                                    {FG_AMBER}║{RST}")
    lines.append(f" {FG_AMBER}╚═══════════════════════════════════════════════[{RST} {FG_MINT}⚡ 62.4 tok/s{RST} {FG_AMBER}]══╝{RST}")
    pl = (
        f"{BG_BLUE}{FG_WHITE}{BOLD} NORMAL {RST}"
        f"{BG_DARK_GRAY}{FG_BLUE} > {RST}{FG_WHITE} git:main* {RST}"
        f"{BG_PURPLE}{FG_GRAY} > {RST}{FG_WHITE} sonnet-3.7 {RST}"
        f"{BG_GREEN}{FG_PURPLE} > {RST}{FG_WHITE} QUEUE:IDLE {RST}"
        f"{FG_GREEN} > {RST}{DIM}Esc 暂停 · / 菜单{RST}"
    )
    lines.append(f" {pl}")
    lines.append("")
    return "\n".join(lines)

def render_theme_4():
    lines = []
    lines.append(f"{BOLD}{FG_MINT}方案 4: 【双栏分屏仪表盘】 Dual-Pane Dashboard{RST}")
    lines.append(f"{DIM}核心理念: 左右分栏，左侧专注交互流，右侧实时看板（会话/Token/修改文件/任务步骤）{RST}")
    lines.append(hr("─", FG_GRAY))
    lines.append("")
    w_left = 50
    w_right = 30
    
    def row(l_text, r_text, l_color="", r_color=""):
        l_clean = l_text.replace("\033[0m", "").replace("\033[1m", "").replace("\033[2m", "").replace("\033[38;5;80m", "").replace("\033[38;5;141m", "").replace("\033[38;5;120m", "").replace("\033[38;5;114m", "").replace("\033[38;5;215m", "").replace("\033[38;5;203m", "")
        r_clean = r_text.replace("\033[0m", "").replace("\033[1m", "").replace("\033[2m", "").replace("\033[38;5;80m", "").replace("\033[38;5;141m", "").replace("\033[38;5;120m", "").replace("\033[38;5;114m", "").replace("\033[38;5;215m", "").replace("\033[38;5;203m", "")
        # 估算宽度
        l_len = sum(2 if ord(c) > 127 else 1 for c in l_clean)
        r_len = sum(2 if ord(c) > 127 else 1 for c in r_clean)
        l_pad = " " * max(0, w_left - l_len)
        r_pad = " " * max(0, w_right - r_len)
        return f"│ {l_color}{l_text}{RST}{l_pad} │ {r_color}{r_text}{RST}{r_pad} │"

    lines.append(f"┌{'─' * (w_left + 2)}┬{'─' * (w_right + 2)}┐")
    lines.append(row(f"{BOLD}💬 Conversation Flow{RST}", f"{BOLD}📊 Session Inspector{RST}"))
    lines.append(f"├{'─' * (w_left + 2)}┼{'─' * (w_right + 2)}┤")
    lines.append(row(f"{FG_CYAN}❯ You:{RST} 帮我排查测试用例失败原因", f"{DIM}Model:{RST} {FG_CYAN}Claude-3.7-Sonnet{RST}"))
    lines.append(row(f"  并生成修复补丁", f"{DIM}Context:{RST} {FG_GREEN}14.2k / 128k (11%){RST}"))
    lines.append(row("", f"{DIM}Rate:{RST} {FG_AMBER}48.5 tok/s{RST}"))
    lines.append(row(f"{FG_PURPLE}✦ Agent:{RST} 已执行测试诊断:", f"──────────────────────────────"))
    lines.append(row(f"  • pytest run (3 failed)", f"{BOLD}📁 Changed Files (2){RST}"))
    lines.append(row(f"  • 定位到 windows_tui.py 溢出", f"  {FG_AMBER}M{RST} windows_tui.py"))
    lines.append(row(f"  即将修改 `budget` 计算公式。", f"  {FG_GREEN}A{RST} test_tail.py"))
    lines.append(row("", f"──────────────────────────────"))
    lines.append(row(f"{FG_CYAN}╭─ Prompt ────────────────────────╮{RST}", f"{BOLD}🎯 Active Goals{RST}"))
    lines.append(row(f"{FG_CYAN}│{RST} ❯ 确认修复并提交 git commit▌    {FG_CYAN}│{RST}", f"  {FG_GREEN}✓{RST} 诊断测试失败原因"))
    lines.append(row(f"{FG_CYAN}╰─────────────────────────────────╯{RST}", f"  {FG_CYAN}►{RST} 写入代码补丁"))
    lines.append(row(f" {FG_GREEN}● Ready{RST} {DIM}[Enter 提交]{RST}", f"  {DIM}○ 运行回归测试{RST}"))
    lines.append(f"└{'─' * (w_left + 2)}┴{'─' * (w_right + 2)}┘")
    lines.append("")
    return "\n".join(lines)

def interactive_mode():
    themes = [
        ("1", "现代极简流 (Claude Code 风格)", render_theme_1),
        ("2", "精密工作台 (Linear Studio 风格)", render_theme_2),
        ("3", "硬核极客流 (Cyber-Brutalist 风格)", render_theme_3),
        ("4", "双栏仪表盘 (Dual-Pane Dashboard 风格)", render_theme_4),
    ]
    current = 0
    while True:
        os.system("cls" if os.name == "nt" else "clear")
        print(f"{BOLD}{BG_DARK_GRAY} === TUI UI 方案切换预览器 (按 1/2/3/4 切换，按 Q 退出) === {RST}\n")
        print(themes[current][2]())
        print(f"{DIM}当前正在预览: [{current + 1}/4] {themes[current][1]}{RST}")
        print(f"快捷键: [1] 现代极简  [2] 精密工作台  [3] 硬核极客  [4] 双栏仪表盘  [Q] 退出")
        try:
            import msvcrt
            ch = msvcrt.getch().decode("utf-8", errors="ignore").lower()
        except Exception:
            break
        if ch in ("1", "2", "3", "4"):
            current = int(ch) - 1
        elif ch in ("q", "\x03", "\x1b"):
            break

def main():
    if "--interactive" in sys.argv:
        interactive_mode()
        return
    if "--theme" in sys.argv:
        idx = sys.argv.index("--theme")
        if idx + 1 < len(sys.argv):
            val = sys.argv[idx + 1]
            if val == "1":
                print(render_theme_1())
            elif val == "2":
                print(render_theme_2())
            elif val == "3":
                print(render_theme_3())
            elif val == "4":
                print(render_theme_4())
            return

    print(f"\n{BOLD}{INV}                                                                                 {RST}")
    print(f"{BOLD}{INV}   Chaos-Agent TUI 界面重新设计：4 套全新美学与交互风格候选展示                 {RST}")
    print(f"{BOLD}{INV}                                                                                 {RST}\n")
    print(render_theme_1())
    print("\n" + "=" * W + "\n")
    print(render_theme_2())
    print("\n" + "=" * W + "\n")
    print(render_theme_3())
    print("\n" + "=" * W + "\n")
    print(render_theme_4())
    print(f"\n{FG_CYAN}💡 提示: 你可以在终端中运行 `python chaos-agent/scripts/preview_tui_themes.py --interactive` 实时动态切屏查看效果！{RST}\n")

if __name__ == "__main__":
    main()
