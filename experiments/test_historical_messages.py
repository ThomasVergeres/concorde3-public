import json
import sqlite3
import unittest

from experiments.historical_messages import qualify_messages, qualify_records


class HistoricalMessagesTest(unittest.TestCase):
    def setUp(self):
        self.before = sqlite3.connect(':memory:')
        self.after = sqlite3.connect(':memory:')
        for db in (self.before, self.after):
            db.row_factory = sqlite3.Row
            db.execute('CREATE TABLE records(id,kind,owner,audience,revision,body)')
            db.execute('CREATE TABLE events(seq,at,kind,body)')
            self.addCleanup(db.close)

    def message(self, identity='m', at=15, to='seller', witness=True):
        body = {'at': at, 'text': 'Observed mismatch', 'to': to}
        self.after.execute('INSERT INTO records VALUES (?,?,?,?,?,?)',
                           (identity, 'message', 'buyer', json.dumps([to]), 1, json.dumps(body)))
        if witness:
            result = dict(body, id=identity, kind='message', owner='buyer', revision=1)
            self.after.execute('INSERT INTO events VALUES (?,?,?,?)',
                               (11, at, 'message', json.dumps({'result': result})))

    def test_exact_intervening_message(self):
        self.message()
        result = qualify_messages(self.before, self.after, 'seller', 10, 20)
        self.assertEqual([r['row']['id'] for r in result], ['m'])
        self.assertEqual(result[0]['birth_seq'], 11)

    def test_later_message_not_leaked(self):
        self.message(at=25)
        self.assertEqual(qualify_messages(self.before, self.after, 'seller', 10, 20), [])

    def test_private_message_not_leaked(self):
        self.message(to='someone_else')
        self.assertEqual(qualify_messages(self.before, self.after, 'seller', 10, 20), [])

    def test_missing_birth_not_inferred_from_timestamp(self):
        self.message(witness=False)
        with self.assertRaisesRegex(ValueError, 'no birth witness'):
            qualify_messages(self.before, self.after, 'seller', 10, 20)

    def test_mutated_content_rejected(self):
        self.message()
        self.after.execute('UPDATE records SET revision=2')
        with self.assertRaisesRegex(ValueError, 'differs'):
            qualify_messages(self.before, self.after, 'seller', 10, 20)

    def test_changed_existing_message_rejected(self):
        self.message()
        row = tuple(self.after.execute('SELECT * FROM records').fetchone())
        self.before.execute('INSERT INTO records VALUES (?,?,?,?,?,?)', row)
        self.after.execute('UPDATE records SET revision=2')
        with self.assertRaisesRegex(ValueError, 'Existing visible'):
            qualify_messages(self.before, self.after, 'seller', 10, 20)

    def artifact(self, at=15, audience='seller', witness=True):
        body={'at':at,'content':{'unconfirmed':True}}
        self.after.execute('INSERT INTO records VALUES (?,?,?,?,?,?)',
                           ('a','artifact','buyer',json.dumps([audience]),1,json.dumps(body)))
        if witness:
            result=dict(body,id='a',kind='artifact',owner='buyer',revision=1)
            self.after.execute('INSERT INTO events VALUES (?,?,?,?)',
                               (12,at,'artifact',json.dumps({'result':result})))

    def test_message_and_artifact_exact_births(self):
        self.message();self.artifact()
        rows=qualify_records(self.before,self.after,'seller',10,20,('message','artifact'))
        self.assertEqual({r['row']['id'] for r in rows},{'m','a'})

    def test_artifact_missing_birth_rejected(self):
        self.artifact(witness=False)
        with self.assertRaisesRegex(ValueError,'no birth witness'):
            qualify_records(self.before,self.after,'seller',10,20,('artifact',))

    def test_later_and_private_artifacts_excluded(self):
        self.artifact(at=25)
        self.assertEqual(qualify_records(self.before,self.after,'seller',10,20,('artifact',)),[])
        self.after.execute('DELETE FROM records');self.after.execute('DELETE FROM events')
        self.artifact(audience='someone_else')
        self.assertEqual(qualify_records(self.before,self.after,'seller',10,20,('artifact',)),[])

    def test_artifact_content_mutation_rejected(self):
        self.artifact()
        self.after.execute('UPDATE records SET body=?',(json.dumps({'at':15,'content':'changed'}),))
        with self.assertRaisesRegex(ValueError,'differs'):
            qualify_records(self.before,self.after,'seller',10,20,('artifact',))

    def test_mutable_record_kinds_rejected(self):
        with self.assertRaisesRegex(ValueError,'Only immutable'):
            qualify_records(self.before,self.after,'seller',10,20,('contract',))


if __name__ == '__main__':
    unittest.main()
