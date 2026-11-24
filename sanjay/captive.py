"""Offline captive-network DNS and HTTP landing, bound to the camp interface only."""
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
import html
import ipaddress
import socket
import socketserver
import struct
import threading


def dns_reply(packet,address):
    if len(packet)<12 or len(packet)>4096:return None
    ident,flags,questions,_,_,_=struct.unpack('!6H',packet[:12])
    if flags&0x8000 or questions!=1:return None
    pos=12
    while True:
        if pos>=len(packet) or pos>267:return None
        size=packet[pos];pos+=1
        if size==0:break
        if size>63 or pos+size>len(packet):return None
        pos+=size
    if pos+4>len(packet):return None
    kind,cls=struct.unpack('!HH',packet[pos:pos+4]);question=packet[12:pos+4]
    answer=b''
    if kind==1 and cls==1:answer=b'\xc0\x0c'+struct.pack('!HHIH',1,1,20,4)+ipaddress.IPv4Address(address).packed
    return struct.pack('!6H',ident,0x8400|(flags&0x0100),1,int(bool(answer)),0,0)+question+answer


class CaptivePortal:
    def __init__(self,address,join_url,http_port=80,dns_port=53):
        ip=ipaddress.IPv4Address(address)
        if not ip.is_private or ip.is_loopback or ip.is_unspecified or ip.is_link_local:raise ValueError('Captive portal requires the actual private hotspot address')
        self.address=address;self.servers=[]
        class UDP(socketserver.UDPServer):allow_reuse_address=False
        class TCP(socketserver.ThreadingTCPServer):allow_reuse_address=False;daemon_threads=True
        class DNSUDP(socketserver.BaseRequestHandler):
            def handle(self):
                raw,sock=self.request;answer=dns_reply(raw,address)
                if answer:sock.sendto(answer,self.client_address)
        class DNSTCP(socketserver.BaseRequestHandler):
            def handle(self):
                self.request.settimeout(3)
                try:
                    size=self.request.recv(2)
                    if len(size)!=2:return
                    length=struct.unpack('!H',size)[0]
                    if length>4096:return
                    raw=b''
                    while len(raw)<length:
                        part=self.request.recv(length-len(raw))
                        if not part:return
                        raw+=part
                    answer=dns_reply(raw,address)
                    if answer:self.request.sendall(struct.pack('!H',len(answer))+answer)
                except OSError:return
        class Landing(BaseHTTPRequestHandler):
            def setup(self):super().setup();self.connection.settimeout(5)
            def log_message(self,*_):pass
            def do_GET(self):
                target=join_url()
                if not target.startswith('http://'+address+':'):self.send_error(503,'Camp reporting is not ready');return
                url=html.escape(target,quote=True)
                body=('<!doctype html><html><head><meta name="viewport" content="width=device-width,initial-scale=1"><meta http-equiv="refresh" content="0;url='+url+'"><title>SANJAY camp Wi-Fi</title></head><body><h1>SANJAY camp Wi-Fi</h1><p>Local reporting network. No internet access is provided by this portal.</p><p><a href="'+url+'">Open camp reporting</a></p><p>No phone security settings need to be disabled. Use this network sign-in window for reporting.</p></body></html>').encode()
                self.send_response(200)
                for k,v in {'Content-Type':'text/html; charset=utf-8','Content-Length':str(len(body)),'Cache-Control':'no-store','Referrer-Policy':'no-referrer','X-Content-Type-Options':'nosniff','Content-Security-Policy':"default-src 'none'; base-uri 'none'; frame-ancestors 'none'"}.items():self.send_header(k,v)
                self.end_headers()
                try:self.wfile.write(body)
                except (BrokenPipeError,ConnectionResetError,ConnectionAbortedError):pass
        try:
            self.servers.append(UDP((address,dns_port),DNSUDP))
            self.servers.append(TCP((address,self.servers[0].server_address[1]),DNSTCP))
            self.servers.append(ThreadingHTTPServer((address,http_port),Landing))
        except OSError:
            for server in self.servers:server.server_close()
            raise
        for server in self.servers:threading.Thread(target=server.serve_forever,daemon=True).start()
    def close(self):
        for server in self.servers:server.shutdown();server.server_close()
        self.servers=[]
