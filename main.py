"""Run SANJAY locally. Never resets existing node data."""
import argparse
from pathlib import Path
import threading
import webbrowser

from sanjay.store import ExchangeError, Store
from sanjay.web import make_server

ROOT = Path(__file__).resolve().parent


def main():
    parser = argparse.ArgumentParser(description='SANJAY fictional offline coordination prototype')
    subs = parser.add_subparsers(dest='action')
    init = subs.add_parser('init', help='Create a NEW independent camp identity')
    init.add_argument('folder'); init.add_argument('node', choices=['A', 'B'])
    trust = subs.add_parser('trust', help='Pin a public identity after independently checking its source')
    trust.add_argument('folder'); trust.add_argument('public_file')
    serve = subs.add_parser('serve', help='Serve one local node; never binds to LAN')
    serve.add_argument('folder'); serve.add_argument('--port', type=int, default=8000)
    serve.add_argument('--open', action='store_true')
    demo = subs.add_parser('demo', help='Run two locally emulated camps with isolated data/keys')
    demo.add_argument('--root', default=str(ROOT / 'demo-nodes'))
    demo.add_argument('--port', type=int, default=8000)
    demo.add_argument('--open', action='store_true')
    backup = subs.add_parser('backup'); backup.add_argument('folder'); backup.add_argument('target')
    inspect = subs.add_parser('inspect-backup'); inspect.add_argument('backup'); inspect.add_argument('destination')
    operations = subs.add_parser('operations', help='Four independent local test camps plus scoped phone intake')
    operations.add_argument('--camp',choices=['south','north','east','west'],help='First-run camp selection for a NEW independent install; preserved on restart')
    operations.add_argument('--root', default=str(ROOT/'demo-operations'))
    operations.add_argument('--port', type=int, default=8890)
    operations.add_argument('--report-port', type=int, default=8892)
    operations.add_argument('--report-host', choices=['127.0.0.1','0.0.0.0'], default='127.0.0.1')
    operations.add_argument('--phone-ip', help='Laptop private Wi-Fi IPv4, for invitation QR')
    operations.add_argument('--open', action='store_true')
    camp_app=subs.add_parser('camp',help='General install: choose a camp once in the browser; retain its identity')
    camp_app.add_argument('--root',default=str(ROOT/'camp-data'))
    camp_app.add_argument('--camp',choices=['south','north','east','west'])
    camp_app.add_argument('--port',type=int,default=8890)
    camp_app.add_argument('--report-port',type=int,default=8892)
    camp_app.add_argument('--report-host',choices=['127.0.0.1','0.0.0.0'],default='0.0.0.0')
    camp_app.add_argument('--phone-ip')
    camp_app.add_argument('--open',action='store_true')
    args = parser.parse_args()
    servers = []
    operation_system = None
    try:
        if args.action in ('operations','camp'):
            import socket
            import ipaddress
            from sanjay.operations import Operations
            from sanjay.ops_web import make_services
            ip = args.phone_ip or '127.0.0.1'
            if args.report_host == '0.0.0.0' and not args.phone_ip:
                with socket.socket(socket.AF_INET,socket.SOCK_DGRAM) as probe:
                    try:probe.connect(('192.0.2.1',9));ip=probe.getsockname()[0]
                    except OSError:print('No LAN route detected. Operator/maps work locally; start camp Wi-Fi before sharing a phone invitation.',flush=True)
            if not ipaddress.ip_address(ip).is_private:raise ExchangeError('Phone link must use a private LAN address')
            if args.action=='camp' and not args.camp and not (Path(args.root)/'registry.sqlite').exists():
                from sanjay.first_run import choose_camp
                args.camp=choose_camp(args.port)
            operation_system = Operations(args.root,local_camp=args.camp)
            if args.action=='camp' and not operation_system.local_camp:raise ExchangeError('This root is a legacy four-camp demo. Use operations or a new camp root; nothing was reset')
            servers,url=make_services(operation_system,args.port,args.report_port,args.report_host,ip)
            for server in servers:threading.Thread(target=server.serve_forever,daemon=True).start()
            print(('INDEPENDENT CAMP: '+operation_system.local_camp) if operation_system.local_camp else 'LOCAL FOUR-NODE EMULATION: independent new keys; no A/B data loaded or stock seeded.',flush=True)
            if operation_system.local_camp:
                profile=operation_system.links.own();print('Camp ID:',profile['id'],flush=True);print('Camp share code:',profile['code'],flush=True)
            print('Local operator (no access token):',url,flush=True)
            print('Phone submission port:',servers[1].server_port,'bind:',args.report_host,'link host:',ip,flush=True)
            print('Use Phone invitation in operator navigation. HTTP is trusted-Wi-Fi only. No firewall changes.',flush=True)
            if args.open or args.action=='camp':webbrowser.open(url)
            threading.Event().wait()
        if args.action == 'init':
            store = Store.create(args.folder, args.node)
            print('Created camp', store.node, 'at', store.folder)
            print('Share ONLY public.json for trust provisioning. Keep private.key private.')
            return
        if args.action == 'trust':
            Store(args.folder).trust(Path(args.public_file).read_bytes())
            print('Public key pinned; existing identities cannot be silently replaced.')
            return
        if args.action == 'backup':
            print(Store(args.folder).backup(args.target)); return
        if args.action == 'inspect-backup':
            print('Inspection-only recovery:', Store.inspect_backup(args.backup, args.destination).folder); return
        if args.action == 'serve':
            stores = [Store(args.folder)]
            port, should_open = args.port, args.open
        else:
            path = Path(args.root) if args.action == 'demo' else ROOT / 'demo-nodes'
            port = args.port if args.action == 'demo' else 8000
            should_open = args.open if args.action == 'demo' else True
            if not path.exists():
                path.mkdir(parents=True)
                a, b = Store.create(path / 'A', 'A'), Store.create(path / 'B', 'B')
                a.trust((b.folder / 'public.json').read_bytes())
                b.trust((a.folder / 'public.json').read_bytes())
                a.command('stock', {'quantity': 200})
            stores = [Store(path / 'A'), Store(path / 'B')]
            print('LOCAL EMULATION: two isolated camp nodes on ONE computer, not physical USB validation.', flush=True)
        for index, store in enumerate(stores):
            server, url = make_server(store, port + index)
            servers.append(server)
            threading.Thread(target=server.serve_forever, daemon=True).start()
            print('Camp', store.node, url, flush=True)
            if should_open:
                webbrowser.open(url)
        print('Keep this process running. Existing records survive restart. Ctrl+C stops both local services.', flush=True)
        threading.Event().wait()
    except KeyboardInterrupt:
        pass
    except (ExchangeError, ValueError, FileExistsError, FileNotFoundError, OSError) as exc:
        if isinstance(exc,OSError) and (getattr(exc,'winerror',None)==10048 or getattr(exc,'errno',None) in (48,98,10048)):
            parser.exit(1,'SANJAY: A requested port is already in use. Keep the existing camp open, or use --port 8904 --report-port 8906. No records were reset.\n')
        parser.exit(1, 'SANJAY: ' + str(exc) + '\n')
    finally:
        for server in servers:
            server.shutdown(); server.server_close()
        if operation_system is not None:operation_system.close()


if __name__ == '__main__':
    main()
