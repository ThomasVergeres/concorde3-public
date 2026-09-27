import copy
from pathlib import Path
import tempfile
import unittest

from experiments.lived_compare import selected_practices, prepare, PRACTICES


class LivedComparisonTests(unittest.TestCase):
    def test_only_declared_practice_text_selected_without_mutation(self):
        state={"items":{key:{"text":key,"revision":4} for key in PRACTICES}}
        state["items"]["purpose"]={"text":"Original undertaking"}
        state["items"]["contract"]={"text":"Original purchased right"}
        before=copy.deepcopy(state)
        self.assertEqual(selected_practices(state),{key:key for key in PRACTICES})
        self.assertEqual(state,before)

    def test_invalid_bounds_or_overlapping_source_rejected_before_setup(self):
        with tempfile.TemporaryDirectory() as tmp:
            for minutes,starts in ((0,2),(61,2),(20,0),(20,5),(True,2),(20,True)):
                target=Path(tmp)/"unused"
                with self.assertRaises(ValueError):prepare(Path(tmp)/"source",target,"baseline","candidate","fixture",minutes,starts)
                self.assertFalse(target.exists())
            with self.assertRaises(ValueError):prepare(tmp,Path(tmp)/"nested","baseline","candidate","fixture")


if __name__=="__main__":unittest.main()
