"""Build a clean, reproducible public folder; never copy live camp directories."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import zipfile

ROOT=Path(__file__).resolve().parents[1]

def build(destination,source=ROOT):
    source=Path(source).resolve();destination=Path(destination).resolve()
    archive=destination.with_suffix('.zip')
    if destination.exists() or archive.exists():raise FileExistsError('Export already exists; choose a new folder rather than overwrite it')
    if destination.is_relative_to(source):raise ValueError('Export outside the active source/data tree')
    files=[source/'main.py',source/'start.py',source/'requirements.txt']
    files+=list((source/'sanjay').glob('*.py'))
    files+=[p for p in (source/'sanjay/static').rglob('*') if p.is_file()]
    files+=list((source/'tests').glob('*.py'))+list((source/'tests').glob('*.cjs'))
    files+=[source/'tools'/n for n in ('ssh_bridge.py','windows_hotspot.ps1','build_release.py')]
    files+=[source/'data/map/operations'/n for n in ('manifest.json','vadodara-roads.osm','facilities.json')]
    extras={source/'docs/FINAL_QUICKSTART.txt':'README.txt',source/'docs/FINAL_RELEASE_NOTES.txt':'RELEASE_NOTES.txt',(source/'frontend/REACT_BITS_LICENSE.md' if (source/'frontend/REACT_BITS_LICENSE.md').exists() else source/'REACT_BITS_LICENSE.txt'):'REACT_BITS_LICENSE.txt'}
    files += [source/'docs/FINAL_QUICKSTART.txt',source/'docs/FINAL_RELEASE_NOTES.txt']
    destination.mkdir(parents=True)
    for path in files:
        relative=path.relative_to(source)
        if path.is_symlink() or not path.resolve().is_relative_to(source) or any(part in ('.ssh','__pycache__','node_modules') for part in relative.parts):raise ValueError('Unsafe public source path')
        if 'static' in relative.parts and relative.as_posix()!='sanjay/static/vendor/leaflet/LICENSE' and path.suffix.lower() not in ('.js','.css','.json','.jpg','.png','.svg'):raise ValueError('Unknown static file type blocked')
        if path.suffix.lower() in ('.sqlite','.key','.docx','.pem','.pfx','.p12') or path.name in ('camp-wifi.json','transport_ed25519','deploy_ed25519') or path.name.startswith('phone-camp-'):raise ValueError('Private/raw file blocked')
        target=destination/relative;target.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(path,target)
    for path,name in extras.items():shutil.copy2(path,destination/name)
    (destination/'start.bat').write_text('@echo off\ncd /d "%~dp0"\npython start.py %*\npause\n',encoding='utf-8')
    shell=destination/'start.sh';shell.write_text('#!/bin/sh\ncd -- "$(dirname -- "$0")"\nexec python3 start.py "$@"\n',encoding='utf-8');shell.chmod(0o755)
    manifest={'format':1,'files':{p.relative_to(destination).as_posix():hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(destination.rglob('*')) if p.is_file()}}
    (destination/'RELEASE_MANIFEST.json').write_text(json.dumps(manifest,indent=2),encoding='utf-8')
    paths=sorted(p for p in destination.rglob('*') if p.is_file())
    with zipfile.ZipFile(archive,'w',zipfile.ZIP_DEFLATED) as bundle:
        for path in paths:bundle.write(path,path.relative_to(destination).as_posix())
    with zipfile.ZipFile(archive) as bundle:
        if bundle.testzip() is not None or len(bundle.infolist())!=len(paths):raise ValueError('Archive verification failed')
    return {'folder':str(destination),'archive':str(archive),'files':len(paths),'bytes':archive.stat().st_size,'sha256':hashlib.sha256(archive.read_bytes()).hexdigest()}

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('destination');args=parser.parse_args();print(json.dumps(build(args.destination)))
