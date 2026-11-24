"""Create this computer's environment once, then open first-run camp setup."""
from pathlib import Path,PurePosixPath
import argparse
import urllib.request
import webbrowser
import hashlib
import json
import re
import os
import shutil
import subprocess
import sys

ROOT=Path(__file__).resolve().parent

def check_release(root=ROOT):
    root=Path(root).resolve();manifest=root/'RELEASE_MANIFEST.json'
    required=('main.py','start.py','requirements.txt','sanjay/operations.py','sanjay/camp_links.py','sanjay/static/maps/road-detail.json','data/map/operations/manifest.json','data/map/operations/vadodara-roads.osm','data/map/operations/facilities.json')
    if any(not (root/name).is_file() for name in required):raise ValueError('Required application/map files are missing; restore the complete exported folder')
    if not manifest.exists():return {'ok':True,'mode':'source checkout','required_files':len(required)}
    data=json.loads(manifest.read_text(encoding='utf-8'))
    if not isinstance(data,dict) or type(data.get('format')) is not int or data.get('format')!=1 or not isinstance(data.get('files'),dict) or not data['files']:raise ValueError('Invalid release manifest')
    for name,digest in data['files'].items():
        if not isinstance(name,str) or not isinstance(digest,str):raise ValueError('Invalid manifest entry')
        part=PurePosixPath(name)
        if part.is_absolute() or '..' in part.parts or '\\' in name or ':' in name or not re.fullmatch('[0-9a-f]{64}',digest):raise ValueError('Invalid manifest entry')
        path=root/name
        if path.is_symlink() or not path.resolve().is_relative_to(root) or not path.is_file():raise ValueError('Release file missing or unsafe: '+name)
        if hashlib.sha256(path.read_bytes()).hexdigest()!=digest:raise ValueError('Release file changed: '+name+'; restore the clean release, not camp-data')
    return {'ok':True,'mode':'verified release','files':len(data['files'])}

def reopen_existing(args):
    if '--help' in args or any(a=='--camp' or a.startswith('--camp=') for a in args):return False
    parser=argparse.ArgumentParser(add_help=False);parser.add_argument('--root',default=str(ROOT/'camp-data'));parser.add_argument('--port',type=int,default=8890)
    options,_=parser.parse_known_args(args)
    data_path=Path(options.root)
    if not data_path.is_absolute():data_path=ROOT/data_path
    expected=hashlib.sha256(os.path.normcase(str(data_path.resolve())).encode()).hexdigest()
    try:
        with urllib.request.urlopen(f'http://127.0.0.1:{options.port}/api/instance',timeout=1) as response:data=json.loads(response.read(4096))
    except (OSError,ValueError):return False
    if not isinstance(data,dict) or data.get('root_id')!=expected:return False
    url=f'http://127.0.0.1:{options.port}/';print('Camp is already running; reopening '+url,flush=True);webbrowser.open(url);return True

def main(argv=None):
    args=list(sys.argv[1:] if argv is None else argv);status=check_release()
    if args==['--check']:print(json.dumps(status));return 0
    if reopen_existing(args):return 0
    env=ROOT/'.venv';python=env/('Scripts/python.exe' if os.name=='nt' else 'bin/python')
    uv=shutil.which('uv')
    if not python.exists():
        if env.exists():raise ValueError("Incomplete .venv; inspect/rename only the environment and retry. Never delete camp-data")
        if uv:subprocess.run([uv,'venv','--python','3.11',str(env)],check=True)
        else:
            if sys.version_info<(3,11):raise ValueError('Python 3.11+ or uv is required')
            subprocess.run([sys.executable,'-m','venv',str(env)],check=True)
    requirements=ROOT/'requirements.txt';digest=hashlib.sha256(requirements.read_bytes()).hexdigest();marker=env/'.sanjay-requirements'
    if not marker.exists() or marker.read_text()!=digest:
        if uv:subprocess.run([uv,'pip','install','--python',str(python),'-r',str(requirements)],check=True)
        else:subprocess.run([str(python),'-m','pip','install','--disable-pip-version-check','-r',str(requirements)],check=True)
        marker.write_text(digest)
    return subprocess.call([str(python),str(ROOT/'main.py'),'camp',*args],cwd=ROOT)

if __name__=='__main__':
    try:raise SystemExit(main())
    except (OSError,ValueError,subprocess.CalledProcessError) as exc:print('SANJAY setup: '+str(exc),file=sys.stderr);raise SystemExit(1)
