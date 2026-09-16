# Web Access
为 Agent 提供分层、可追溯的网络检索与浏览器能力。

## 边界
- 负责：按 web search/fetch、站点专用 API、已批准 MCP、可选浏览器自动化、受控进程兜底的顺序提供网络能力；限制超时、响应大小、重定向并返回来源元数据。
- 不负责：保存账号 Cookie、绕过站点验证、代替中央 ActionPolicy，或把搜索候选当作已验证事实。

## Units
- `WebAccessService.search/query_site/fetch`: 执行第一、二层 HTTP 访问并返回有界正文、状态和来源。
- `WebAccessService.browser_*`: 在安装 Playwright 时执行第四层浏览器操作；未安装时失败闭合。
- `build_web_access_tools`: 暴露稳定的 typed tool schema；MCP 和 PowerShell 由现有 Host 层负责。
