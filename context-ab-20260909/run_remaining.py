import subprocess,sys,json,time
from pathlib import Path
r=Path('F:/code-ai-chaos/chaos-16-agent');o=r/'context-ab-20260909'
for case,arms in [('single',['A','B']),('two',['B','A']),('failing',['A','B']),('file',['B','A']),('tight',['A','B'])]:
 for arm in arms:
  p=subprocess.run([sys.executable,'-X','utf8',str(o/'run_case.py'),arm,case],cwd=r,capture_output=True,text=True,encoding='utf-8',timeout=360)
  (o/f'{case}-{arm}-process.log').write_text(p.stdout+p.stderr,encoding='utf-8');print(case,arm,p.returncode,p.stdout[-700:],p.stderr[-600:],flush=True)
  if p.returncode:sys.exit(p.returncode)
