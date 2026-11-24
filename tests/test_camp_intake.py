"""Camp intake shares report validation; medical symbols never assert availability."""
import json
from pathlib import Path
import urllib.error
import xml.etree.ElementTree as ET
import unittest
import test_operations as operations
from sanjay.registry import MEDICAL_TYPES,RegistryError

class CampIntakeTests(unittest.TestCase):
    setUp=operations.OperationsHTTPTests.setUp
    tearDown=operations.OperationsHTTPTests.tearDown
    post=operations.OperationsHTTPTests.post
    def fields(self):return {'category':'medical','description':'FICTIONAL camp test','lat':22.3,'lon':73.2,'severity':'high','reporter_contact':'PRIVATE-CAMP-TEST'}
    def test_camp_report_is_saved_unverified_with_server_source_and_private_contact(self):
        response=self.post(self.operator,self.base+'/api/reports',{'camp':'north',**self.fields()},self.csrf)
        self.assertEqual(response.status,201);saved=json.load(response)
        self.assertEqual(saved['source'],'camp');self.assertEqual(saved['camp_id'],'north');self.assertEqual(saved['status'],'unverified')
        state=json.load(self.operator.open(self.base+'/state'));record=next(r for r in state['registry']['reports'] if r['id']==saved['id'])
        self.assertNotIn('reporter_contact',record);self.assertEqual(state['state']['stocks'],{})
        self.assertEqual(self.ops.registry.get_report(saved['id'],True)['reporter_contact'],'PRIVATE-CAMP-TEST')
        html=self.operator.open(self.base+'/?view=reports').read().decode()
        self.assertIn('id="operator-report-form"',html);self.assertIn('id="operator-report-map"',html)
        self.assertIn('/assets/leaflet/leaflet.js',html)
    def test_invalid_operator_claim_and_phone_source_forgery_are_rejected(self):
        for fields,csrf in [({**self.fields(),'status':'confirmed'},self.csrf),(self.fields(),'wrong'),({**self.fields(),'lat':False},self.csrf)]:
            with self.assertRaises(urllib.error.HTTPError):self.post(self.operator,self.base+'/api/reports',fields,csrf)
        with self.assertRaises(urllib.error.HTTPError):self.post(self.phone,self.phone_base+'/api/reports',{**self.fields(),'source':'camp','camp_id':'north'},self.phone_csrf,self.ticket)
        self.assertEqual(self.ops.registry.snapshot()['reports'],[])
        saved=json.load(self.post(self.phone,self.phone_base+'/api/reports',self.fields(),self.phone_csrf,self.ticket))
        record=self.ops.registry.get_report(saved['id']);self.assertEqual(record['source'],'phone');self.assertIsNone(record['camp_id'])
    def test_medical_categories_icons_and_persistent_corrections(self):
        for category in MEDICAL_TYPES:
            record=self.ops.registry.add_facility({'name':'FICTIONAL '+category,'category':category,'lat':22.3,'lon':73.2})
            self.assertEqual(record['verification_status'],'unverified')
        svg=self.operator.open(self.base+'/assets/facility-icons.svg').read()
        ids={s.attrib['id'] for s in ET.fromstring(svg).findall('{http://www.w3.org/2000/svg}symbol')}
        self.assertTrue(set(MEDICAL_TYPES)<=ids)
        self.assertNotIn(b'<image',svg);self.assertNotIn(b'<script',svg)
        state=json.load(self.operator.open(self.base+'/state'));self.assertTrue(set(MEDICAL_TYPES)<=set(state['facility_types']))
        with self.assertRaises(urllib.error.HTTPError) as error:self.phone.open(self.phone_base+'/assets/facility-icons.svg')
        self.assertEqual(error.exception.code,404)
        with self.assertRaises(RegistryError):self.ops.registry.submit_report(self.fields(),source='camp',camp_id='forged')

if __name__=='__main__':unittest.main()
