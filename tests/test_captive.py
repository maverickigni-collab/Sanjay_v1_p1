"""Captive DNS is local-only and rejects malformed requests without forwarding."""
import ipaddress
import json
from pathlib import Path
import socket
import struct
import tempfile
import unittest
from unittest.mock import patch
from sanjay.captive import dns_reply,CaptivePortal
from sanjay.hotspot import Hotspot,HotspotError

class CaptiveTests(unittest.TestCase):
    def query(self,kind=1):
        name=b''.join(bytes([len(s)])+s for s in (b'captive',b'apple',b'com'))+b'\0'
        return struct.pack('!6H',123,0x100,1,0,0,0)+name+struct.pack('!HH',kind,1)
    def test_dns_ipv4_and_ipv6_no_fake_tls(self):
        reply=dns_reply(self.query(),'192.168.137.1')
        self.assertEqual(reply[-4:],ipaddress.IPv4Address('192.168.137.1').packed)
        self.assertEqual(struct.unpack('!6H',reply[:12])[3],1)
        self.assertEqual(struct.unpack('!6H',dns_reply(self.query(28),'192.168.137.1')[:12])[3],0)
        for packet in (b'',self.query()[:16],b'\0'*4097,self.query()[:12]+b'\xc0\x0c'):
            self.assertIsNone(dns_reply(packet,'192.168.137.1'))
    def test_portal_refuses_public_loopback_and_unspecified_binding(self):
        for host in ('0.0.0.0','127.0.0.1','8.8.8.8','169.254.1.1'):
            with self.assertRaises(ValueError):CaptivePortal(host,lambda:'http://'+host+':8892/')
    def test_wifi_secret_persists_and_payload_has_no_signed_keys(self):
        root=Path(tempfile.gettempdir())
        with tempfile.TemporaryDirectory(dir=root) as folder:
            hotspot=Hotspot(folder);again=Hotspot(folder)
            self.assertEqual(hotspot.wifi_payload(),again.wifi_payload())
            self.assertTrue(hotspot.wifi_payload().startswith('WIFI:T:WPA;S:SANJAY-CAMP;P:'))
            self.assertNotIn('private.key',hotspot.wifi_payload())
            with self.assertRaises(HotspotError):hotspot.run('invalid')

if __name__=='__main__':unittest.main()
