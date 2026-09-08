"""Offline, single-theme production renderer preview."""
PAGE = r'''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Chaos Agent · Muted Slate</title>
<style>
body{background:#07090e;color:#cbd5e1;margin:40px;font:15px system-ui}
main{max-width:1120px;margin:auto}h1{color:#7dd3fc;font-weight:500}
p{color:#73849c}button{background:#141b29;color:#cbd5e1;border:1px solid #223046;padding:9px 16px;margin:5px;cursor:pointer}
button[aria-pressed=true]{border-color:#7dd3fc;color:#7dd3fc}
.terminal{background:#0a0d14;border:1px solid #223046;border-radius:10px;padding:24px;overflow:auto;margin-top:20px}
pre{font:14px/1.6 "Cascadia Code","JetBrains Mono",Consolas,"Microsoft YaHei UI",monospace;white-space:pre;margin:0;font-feature-settings:"calt" 1,"liga" 1}
</style><main><h1>冷萃冰阶 / Muted Slate</h1><p>方案 A · 唯一默认主题 · 真实终端渲染器输出 · 离线示例数据</p>
<nav id="states"></nav><button id="motion">暂停动效</button>
<div class="terminal"><pre id="transcript"></pre><pre id="tail"></pre></div>
<p>Enter 发送 / 排队 · Shift+Enter 或 Ctrl+J 换行 · Tab 转向 · Esc 暂停。状态与用量为示例，不代表实际执行。</p>
<p>终端动效使用字符颜色巡移；背景仅供深色终端参考，程序不修改终端设置。</p></main>
<script>const data=__PREVIEW_DATA__.slate;let state='idle',tick=0,motion=!matchMedia('(prefers-reduced-motion: reduce)').matches;
const labels={idle:'就绪',building_context:'准备上下文',streaming_response:'生成中',completed:'已完成',paused:'已暂停',approval:'等待审批'};
for(const [key,label] of Object.entries(labels)){const b=document.createElement('button');b.textContent=label;b.onclick=()=>{state=key;tick=0;draw()};b.dataset.state=key;states.append(b)}
function draw(){transcript.innerHTML=data.transcript+'\n\n';tail.innerHTML=data.frames[state][motion?tick:8];document.querySelectorAll('[data-state]').forEach(b=>b.setAttribute('aria-pressed',b.dataset.state===state));document.querySelector('#motion').textContent=motion?'暂停动效':'开启动效'}
document.querySelector('#motion').onclick=()=>{motion=!motion;draw()};setInterval(()=>{if(motion){tick=(tick+1)%9;draw()}},150);draw();</script></html>'''
