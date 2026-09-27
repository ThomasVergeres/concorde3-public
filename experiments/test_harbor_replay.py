import hashlib
import tempfile
import unittest
from pathlib import Path
from experiments.harbor_replay import blob, visible

class HistoricalCaptureTests(unittest.TestCase):
    def test_digest_and_truncation_fail_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);(root/'blobs').mkdir();raw=b'captured bytes';h=hashlib.sha256(raw).hexdigest()
            (root/'blobs'/h).write_bytes(raw)
            ref={'blob':'blobs/'+h,'sha256':h,'captured_bytes':len(raw),'truncated':False}
            self.assertEqual(blob(root,ref),raw)
            for change in ({'truncated':True},{'captured_bytes':1},{'blob':'../outside'}):
                with self.assertRaises(ValueError):blob(root,{**ref,**change})
    def test_private_counterpart_records_are_not_subject_evidence(self):
        self.assertFalse(visible({'owner':'ledgerbird','audience':'["ledgerbird"]'}))
        self.assertTrue(visible({'owner':'ledgerbird','audience':'["steward"]'}))
        self.assertTrue(visible({'owner':'juniper','audience':'["*"]'}))

if __name__=='__main__':unittest.main()
