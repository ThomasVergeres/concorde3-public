import hashlib
from pathlib import Path
import tempfile
import unittest

from worlds.audit_donor import read_blob, inert_deployment


class AuditDonorTests(unittest.TestCase):
    def test_historical_names_cannot_remain_operational_references(self):
        old = {"subjects": {"steward": "live-original-steward"}, "containers": ["live-original-steward"],
               "networks": {"steward": {"network": "live-original-network"}}, "prefix": "live-original", "image": "sha256:pinned"}
        new = inert_deployment(old)
        self.assertEqual(new["subjects"], {})
        self.assertEqual(new["inactive_subjects"], ["steward"])
        self.assertEqual(new["containers"], [])
        self.assertEqual(new["networks"], {})
        self.assertNotIn("prefix", new)
        self.assertEqual(old["subjects"]["steward"], "live-original-steward")

    def test_exact_blob_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            data = b"retained historical evidence"
            digest = hashlib.sha256(data).hexdigest()
            (root / "blobs").mkdir()
            (root / "blobs" / digest).write_bytes(data)
            ref = {"path": "subjects/self/artifact.txt", "blob": "blobs/" + digest,
                   "sha256": digest, "captured_bytes": len(data), "truncated": False}
            self.assertEqual(read_blob(root, ref), data)
            for changes in ({"truncated": True}, {"path": "../escape"}, {"path": "subjects/self/.env"},
                            {"captured_bytes": 1}, {"blob": "../elsewhere"}, {"sha256": "x" * 64}):
                with self.assertRaises(ValueError):
                    read_blob(root, {**ref, **changes})
            (root / "blobs" / digest).write_bytes(b"corrupt")
            with self.assertRaisesRegex(ValueError, "integrity"):
                read_blob(root, ref)


if __name__ == "__main__":
    unittest.main()
