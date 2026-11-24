"""Restricted SSH stdin/stdout bridge to the already-running loopback camp app."""
import argparse
import json
import re
import sys
import urllib.request
import urllib.error

LIMIT=1000000

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--port',type=int,default=8890);args=parser.parse_args()
    if not 1<=args.port<=65535:raise ValueError('Invalid local port')
    raw=sys.stdin.buffer.read(LIMIT+1)
    if len(raw)>LIMIT:raise ValueError('Peer exchange exceeds 1 MB')
    base=f'http://127.0.0.1:{args.port}'
    with urllib.request.urlopen(base,timeout=10) as response:html=response.read(LIMIT).decode()
    csrf=re.search('name="csrf-token" content="([^"]+)',html)[1]
    request=urllib.request.Request(base+'/api/peer-exchange',data=raw,headers={'Content-Type':'application/json','Origin':base,'X-CSRF-Token':csrf})
    with urllib.request.urlopen(request,timeout=30) as response:output=response.read(LIMIT+1)
    if len(output)>LIMIT:raise ValueError('Peer response exceeds 1 MB')
    sys.stdout.buffer.write(output);sys.stdout.buffer.flush()

if __name__=='__main__':
    try:main()
    except urllib.error.HTTPError as exc:
        try:
            data=json.loads(exc.read(4096));message=data.get('error','HTTP '+str(exc.code)) if isinstance(data,dict) else 'HTTP '+str(exc.code)
        except (ValueError,UnicodeError):message='HTTP '+str(exc.code)
        print(json.dumps({'error':str(message)[:300]}));raise SystemExit(1)
    except (OSError,ValueError,IndexError) as exc:
        print(json.dumps({'error':'Remote bridge: '+str(exc)[:250]}));raise SystemExit(1)
