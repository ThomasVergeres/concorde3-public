from pathlib import Path
import tempfile
import unittest
from worlds.engine import World
from worlds.observe import report
from worlds.store import Rejected
from worlds.test_world import Clock
from worlds.attribution import evidence

class AttributionTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.w=World(Path(self.tmp.name)/"world",Clock());self.w.create("market")
    def act(self,actor,key,**data):
        return self.w.act(actor,key,data)["result"]
    def test_message_adaptation_is_visible_only_after_actual_use(self):
        source=self.act("frontier","source",op="message",to="relay",text="Try session-0=0, session-1=1, session-2=2")
        adapted=self.act("relay","adapt",op="artifact",title="Our schedule",content={"assignments":{"session-0":0,"session-1":1,"session-2":2}},source_refs=[source["id"]])
        self.assertEqual(report(self.w)["actors"]["frontier"]["attributed_derived_use"]["distinct_users"],0)
        self.act("relay","run",op="consume",project="project:relay",artifact=adapted["id"])
        r=report(self.w)["actors"]["frontier"]
        self.assertEqual(r["delivered_use"]["distinct_users"],0)
        self.assertEqual(r["attributed_derived_use"]["distinct_users"],1)
        self.assertEqual(r["attributed_derived_use"]["source_refs"],[source["id"]])
        self.assertEqual(r["payments"],0)
    def test_unknown_and_private_sources_rejected_without_copying(self):
        private=self.act("frontier","private",op="artifact",title="Private",content={})
        for source in ("not-real",private["id"]):
            with self.assertRaises(Rejected):
                self.act("relay",source,op="artifact",title="Claim",content={},source_refs=[source])
    def test_citation_is_not_independent_proof_of_derivation(self):
        source=self.act("frontier","source",op="artifact",title="Public suggestion",content={},audience=["*"])
        adapted=self.act("relay","adapt",op="artifact",title="Unrelated payload",content={},source_refs=[source["id"]])
        self.act("relay","run",op="consume",project="project:relay",artifact=adapted["id"])
        r=report(self.w)["actors"]["frontier"]["attributed_derived_use"]
        self.assertIn("claim",r["basis"])
        self.assertNotIn("passed",r["outcomes"])

    def test_reviewer_paths_preserve_private_visibility_and_transitive_limits(self):
        source=self.act("frontier","source",op="message",to="relay",text="An example schedule")
        first=self.act("relay","first",op="artifact",title="Intermediate",content={},source_refs=[source["id"]])
        second=self.act("relay","second",op="artifact",title="Our schedule",content={"assignments":{"session-0":0,"session-1":1,"session-2":2}},source_refs=[first["id"]])
        self.act("relay","run",op="consume",project="project:relay",artifact=second["id"])
        with self.w.s.transaction() as db:
            found=evidence(db,["frontier"])["frontier"]
            short=evidence(db,["frontier"],depth=1)["frontier"]
        self.assertEqual(found["matched_observations"],1)
        use=found["observations"][0]
        self.assertEqual(use["source_paths"],[[second["id"],first["id"],source["id"]]])
        self.assertFalse(use["observation_visible_to_subject"])
        self.assertEqual(short["matched_observations"],0)
        self.assertIn("lineage depth omitted",short["lineage_limitations"])

    def test_unrelated_success_not_attributed(self):
        self.act("frontier","source",op="message",to="relay",text="A similar idea")
        artifact=self.act("relay","own",op="artifact",title="Independently supplied",content={"assignments":{"session-0":0,"session-1":1,"session-2":2}})
        self.act("relay","run",op="consume",project="project:relay",artifact=artifact["id"])
        with self.w.s.transaction() as db:
            self.assertEqual(evidence(db,["frontier"])["frontier"]["matched_observations"],0)

    def test_shared_ancestry_cannot_expand_unboundedly(self):
        refs=[]
        for i in range(8):
            a=self.act("relay","base"+str(i),op="artifact",title="Base",content={})
            refs.append(a["id"])
        for level in range(4):
            refs=[self.act("relay",f"layer-{level}-{i}",op="artifact",title="Derived",content={},source_refs=refs)["id"] for i in range(8)]
        final=self.act("relay","final",op="artifact",title="Used",content={},source_refs=refs)
        self.act("relay","run",op="consume",project="project:relay",artifact=final["id"])
        with self.w.s.transaction() as db:
            result=evidence(db,["frontier"],depth=8)["frontier"]
        self.assertEqual(result["matched_observations"],0)
        self.assertIn("lineage traversal budget exhausted",result["lineage_limitations"])
