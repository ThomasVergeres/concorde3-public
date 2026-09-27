import hashlib
import json
from pathlib import Path
import tempfile
import unittest
import sqlite3
from unittest.mock import patch
from worlds.call_networks import reclaim


class ReclaimTests(unittest.TestCase):
    def test_scan_releases_database_before_slow_cleanup(self):
        from worlds.call_networks import main
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);db=root/'world.sqlite'
            with sqlite3.connect(db) as c:
                c.execute('CREATE TABLE calls(id, status, body, category, at)')
                c.executemany('INSERT INTO calls VALUES(?,?,?,?,?)',
                    [(f'{i:012x}'+'a'*12,'completed',json.dumps({'process_stopped':True}),'counterpart',i) for i in range(600)])
            def slow_cleanup(*args,**kwargs):
                # A live writer must be able to commit during Docker work.
                with sqlite3.connect(db,timeout=0) as writer:
                    writer.execute('BEGIN EXCLUSIVE')
                    writer.execute('UPDATE calls SET at=at+1')
                return {'status':'dry_run'}
            with patch('sys.argv',['reclaim',str(root),'--limit','1']), \
                 patch('worlds.call_networks.docker',return_value='c3-world-call-000000000000-net'), \
                 patch('worlds.call_networks.reclaim',side_effect=slow_cleanup) as cleanup, \
                 patch('builtins.print'):
                main()
                cleanup.assert_called_once()

    def fixture(self,root,live=False,foreign=False):
        identity='a'*24;prefix='c3-world-call-'+identity[:12]
        net={'Name':prefix+'-net','Internal':True,'Containers':{'cid':{'Name':prefix}}}
        container={'Id':'cid','Name':'/'+prefix,
                   'State':{'Running':live,'Status':'running' if live else 'exited'},
                   'Config':{'Labels':{'concorde.world':'foreign' if foreign else hashlib.sha256(str(root.resolve()).encode()).hexdigest()[:12]}}}
        commands=[]
        def run(*args):
            commands.append(args)
            if args[:2]==('network','inspect'):return json.dumps([net])
            if args[0]=='inspect':return json.dumps([container])
            return ''
        return identity,net,commands,run

    def test_dry_run_and_scoped_reclaim_preserve_evidence(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);i,net,commands,run=self.fixture(root)
            self.assertEqual(reclaim(root,i,'completed',{'process_stopped':True},run=run)['status'],'dry_run')
            self.assertFalse(any(x[:2]==('network','disconnect') for x in commands))
            self.assertEqual(reclaim(root,i,'completed',{'process_stopped':True},True,run)['status'],'reclaimed')
            self.assertIn(('network','disconnect',net['Name'],'cid'),commands)
            self.assertIn(('network','rm',net['Name']),commands)
            self.assertFalse(any(x[0] in ('rm','stop','kill') for x in commands))
            self.assertTrue((root/'calls'/i/'network-reclamation.json').exists())

    def test_live_foreign_and_unknown_attachments_refused(self):
        for mode in ['live','foreign','unexpected']:
            with self.subTest(mode=mode),tempfile.TemporaryDirectory() as temp:
                root=Path(temp);i,net,commands,run=self.fixture(root,mode=='live',mode=='foreign')
                if mode=='unexpected':net['Containers']['cid']['Name']='another-project'
                with self.assertRaises(ValueError):reclaim(root,i,'completed',{'process_stopped':True},True,run)
                self.assertFalse(any(x[:2] in [('network','disconnect'),('network','rm')] for x in commands))

    def test_nonterminal_or_unverified_calls_refused(self):
        for status,body in [('running',{}),('completed',{}),('uncertain',{'process_stopped':True})]:
            with self.assertRaises(ValueError):reclaim('/tmp/example','a'*24,status,body,run=lambda *a:self.fail('Docker should not be called'))

    def test_rechecks_liveness_before_detaching(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);i,net,commands,base=self.fixture(root)
            inspections=0
            def run(*args):
                nonlocal inspections
                result=base(*args)
                if args[0]=='inspect':
                    inspections+=1
                    if inspections==2:
                        value=json.loads(result);value[0]['State']['Running']=True
                        return json.dumps(value)
                return result
            with self.assertRaises(ValueError):reclaim(root,i,'completed',{'process_stopped':True},True,run)
            self.assertFalse(any(x[:2]==('network','disconnect') for x in commands))
            self.assertEqual(json.loads((root/'calls'/i/'network-reclamation.json').read_text())['status'],'prepared')
