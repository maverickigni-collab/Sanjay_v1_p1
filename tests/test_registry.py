"""Persistence, concurrent revisions, geometry and private report lifecycle."""
from copy import deepcopy
from pathlib import Path
import tempfile
import threading
import unittest
import uuid

from sanjay.registry import Registry,RegistryError,Conflict,validate_polygon

SCRATCH=Path.home()/'AppData/Local/hermes/cache/scratch'

class RegistryTests(unittest.TestCase):
    def setUp(self):
        SCRATCH.mkdir(parents=True,exist_ok=True);self.tmp=tempfile.TemporaryDirectory(dir=SCRATCH)
        self.folder=Path(self.tmp.name)/'registry';self.db=Registry(self.folder)
    def tearDown(self):self.tmp.cleanup()
    def test_seed_once_edits_persist_and_shapes_are_independent(self):
        original=self.db.get_camp('south');polygon={'type':'Polygon','coordinates':[[[73.17,22.26],[73.18,22.26],[73.18,22.27],[73.17,22.26]]]}
        saved=self.db.update_camp('south',{'name':'Edited camp','perimeter':polygon},1)
        self.assertEqual(saved['version'],2);self.assertEqual(saved['service_area'],original['service_area'])
        reopened=Registry(self.folder);self.assertEqual(reopened.get_camp('south')['perimeter'],polygon)
        self.assertEqual(reopened.get_camp('south')['name'],'Edited camp');self.assertEqual(len(reopened.snapshot()['camps']),4)
        overlapping=self.db.get_camp('north')['service_area']
        self.db.update_camp('south',{'service_area':overlapping},2)
        self.assertEqual(self.db.get_camp('south')['service_area'],self.db.get_camp('north')['service_area'])
    def test_invalid_polygons_versions_and_unknown_fields_leave_data_unchanged(self):
        before=self.db.snapshot()
        bad=[{'type':'Polygon','coordinates':[[[0,0],[1,1],[0,1],[1,0],[0,0]]]}, {'type':'Polygon','coordinates':[[[True,0],[1,0],[0,1],[True,0]]]}, {'type':'Polygon','coordinates':[[[0,0],[1,0],[2,0],[0,0]]]}]
        for shape in bad:
            with self.assertRaises(RegistryError):self.db.update_camp('south',{'perimeter':shape},1)
        for changes,version in [({'id':'x'},1),({'capacity':True},1),({'service_area':None},1),({'name':'x'},True)]:
            with self.assertRaises(RegistryError):self.db.update_camp('south',changes,version)
        self.assertEqual(before,self.db.snapshot())
    def test_competing_camp_edits_only_one_succeeds(self):
        results=[];barrier=threading.Barrier(2)
        def edit(name):
            barrier.wait()
            try:Registry(self.folder).update_camp('south',{'name':name},1);results.append('saved')
            except Conflict:results.append('stale')
        threads=[threading.Thread(target=edit,args=(name,)) for name in ('first','second')]
        for t in threads:t.start()
        for t in threads:t.join()
        self.assertCountEqual(results,['saved','stale'])
    def test_facility_validation_search_data_and_import_idempotence(self):
        fields={'name':'Fictional test clinic','category':'clinic','lat':22.3,'lon':73.2,'source_url':'https://example.org/fixture','verification_status':'unverified'}
        record=self.db.add_facility(fields);self.db.update_facility(record['id'],{'active':False},1)
        self.assertFalse(next(f for f in self.db.snapshot()['facilities'] if f['id']==record['id'])['active'])
        with self.assertRaises(Conflict):self.db.update_facility(record['id'],{'active':True},1)
        with self.assertRaises(RegistryError):self.db.add_facility({**fields,'lat':False})
        self.assertEqual(self.db.import_facilities([fields],'hash'),0)
        self.assertEqual(self.db.import_facilities([fields],'hash'),0)
    def test_report_review_privacy_revisions_and_persistence(self):
        fields={'category':'road_obstruction','description':'Fictional test obstruction','lat':22.3,'lon':73.2,'severity':'high','reporter_contact':'PRIVATE-TEST-CONTACT'}
        report=self.db.submit_report(fields);self.assertEqual(report['status'],'unverified')
        self.assertNotIn('reporter_contact',self.db.snapshot()['reports'][0])
        self.assertNotIn('reporter_contact',self.db.get_report(report['id']))
        self.assertEqual(self.db.get_report(report['id'],True)['reporter_contact'],'PRIVATE-TEST-CONTACT')
        with self.assertRaises(RegistryError):self.db.review_report(report['id'],'resolved',1)
        self.db.review_report(report['id'],'confirmed',1)
        with self.assertRaises(Conflict):self.db.review_report(report['id'],'rejected',1)
        self.db.review_report(report['id'],'resolved',2)
        self.assertEqual(Registry(self.folder).snapshot()['reports'][0]['status'],'resolved')
        self.assertEqual(self.db.snapshot()['report_revision'],3)
        with self.assertRaises(RegistryError):self.db.submit_report({**fields,'status':'confirmed'})
        with self.assertRaises(RegistryError):self.db.submit_report({**fields,'lon':float('nan')})
    def test_route_choices_persist_and_reject_noncanonical_ids(self):
        key=str(uuid.uuid4());self.db.save_route_choice(key,{'route_id':'local-test','report_revision':0})
        self.assertEqual(Registry(self.folder).get_route_choice(key)['route_id'],'local-test')
        with self.assertRaises(RegistryError):self.db.save_route_choice('not-a-uuid',{})

if __name__=='__main__':unittest.main()
