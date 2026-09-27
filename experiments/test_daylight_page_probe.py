import json
from pathlib import Path
import shutil
import subprocess
import unittest


@unittest.skipUnless(shutil.which('node'), 'Node needed for the page-logic probe')
class PageProbeTests(unittest.TestCase):
    def probe(self, wrong=False, missing=False, mode='select'):
        script="""fetch('/api/catalog').then(r=>r.json()).then(d=>{const t=d.tracks[0];
        const tile=document.createElement('article'),title=document.createElement('h3'),button=document.createElement('button');
        title.textContent=t.title;button.onclick=()=>{document.getElementById('sessionTitle').textContent=TITLE;
        document.getElementById('quick').textContent=t.cards[0].quick;};tile.append(title,button);document.getElementById('tracks').append(tile);});"""
        script=script.replace('TITLE',"'Wrong'" if wrong else 't.cards[0].title')
        data={'html':'<script>'+script+'</script>','catalog':{'tracks':[{'id':'track','title':'Choice','cards':[{'title':'First','quick':'Do a thing'}]}]},'track':'missing' if missing else 'track'}
        data['mode']=mode
        return subprocess.run(['node',str(Path(__file__).with_name('daylight_page_probe.js'))],input=json.dumps(data),text=True,capture_output=True,timeout=5)

    def test_actual_selection_path(self):
        self.assertEqual(self.probe().returncode,0)

    def test_selection_does_not_prove_automatic_open(self):
        self.assertNotEqual(self.probe(mode='initial').returncode,0)

    def test_wrong_or_absent_card_fails(self):
        self.assertNotEqual(self.probe(wrong=True).returncode,0)
        self.assertNotEqual(self.probe(missing=True).returncode,0)
