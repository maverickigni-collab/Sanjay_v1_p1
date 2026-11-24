"""OS hotspot control; never exports camp signing keys or changes phone settings."""
import json
import os
from pathlib import Path
import secrets
import subprocess
import threading

class HotspotError(ValueError):pass

class Hotspot:
    def __init__(self,folder):
        self.path=Path(folder)/'camp-wifi.json';self.lock=threading.RLock();self.cached={'state':'Unknown','addresses':[]}
        if not self.path.exists():
            with self.path.open('x',encoding='utf-8') as f:json.dump({'ssid':'SANJAY-CAMP','password':secrets.token_urlsafe(15)},f)
            if os.name!='nt':self.path.chmod(0o600)
        self.config=json.loads(self.path.read_text(encoding='utf-8'))
    def run(self,action='status'):
        if action not in ('status','start','stop'):raise HotspotError('Invalid hotspot action')
        with self.lock:
            if os.name!='nt':raise HotspotError('This adapter is for Windows. Raspberry Pi hotspot provisioning is not yet installed.')
            script=Path(__file__).resolve().parent.parent/'tools/windows_hotspot.ps1'
            try:
                result=subprocess.run(['powershell.exe','-NoProfile','-File',str(script),'-Action',action,'-ConfigPath',str(self.path.resolve())],capture_output=True,text=True,timeout=100,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
            except (OSError,subprocess.TimeoutExpired):raise HotspotError('Windows hotspot helper failed or timed out') from None
            if result.returncode:
                # Keep PowerShell source/error dumps and all credentials out of web responses.
                raise HotspotError('Windows could not '+action+' the hotspot. Check Mobile Hotspot support and administrator/network policy.')
            try:self.cached=json.loads(result.stdout.strip())
            except ValueError:raise HotspotError('Invalid Windows hotspot status') from None
            self.cached['owned']=self.cached.get('owned') is True
            return dict(self.cached)
    def wifi_payload(self):
        escape=lambda s:s.replace('\\','\\\\').replace(';','\\;').replace(':','\\:').replace(',','\\,').replace('"','\\"')
        return 'WIFI:T:WPA;S:'+escape(self.config['ssid'])+';P:'+escape(self.config['password'])+';;'
