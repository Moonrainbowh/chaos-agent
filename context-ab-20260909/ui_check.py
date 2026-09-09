import subprocess,os,json,pathlib
root=pathlib.Path('F:/code-ai-chaos/chaos-16-agent'); out=root/'context-ab-20260909'
tests=['test_startup_responsiveness.StartupResponsivenessTests.test_escape_during_preparation_waits_for_cleanup_and_keeps_input','test_windows_tui_interaction.WindowsTerminalAppTests.test_running_icon_changes_but_completion_icon_is_static']
results=[]
for arm,cwd in [('A','F:/code-ai-chaos/context-ab-baseline-20260909'),('current',str(root))]:
 for test in tests:
  env=dict(os.environ,PYTHONPATH=f'{cwd}/src;{cwd};{cwd}/src/code_agent/interfaces/tests')
  try:
   p=subprocess.run([str(root/'.venv/Scripts/python.exe'),'-X','utf8','-m','unittest',test,'-v'],cwd=cwd,env=env,capture_output=True,text=True,encoding='utf-8',timeout=25)
   row=dict(arm=arm,test=test,returncode=p.returncode,output=p.stdout+p.stderr)
  except subprocess.TimeoutExpired as e:
   row=dict(arm=arm,test=test,timeout=25,output=str(e.stdout)+str(e.stderr))
  results.append(row); (out/'ui-results.json').write_text(json.dumps(results,indent=2),encoding='utf-8'); print(json.dumps(row),flush=True)
