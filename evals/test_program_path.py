"""Opt-in mechanical witness, NOT LLM evidence or a subject-supplied solution.

C3_PROGRAM_WITNESS_IMAGE=<local image> python3 -m unittest evals.test_program_path
The independently authored worker executes only in a networkless container.
Accelerated source changes qualify feasibility, not behavioral response latency.
"""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import time
import unittest
import uuid


WORKER = '''import json,os,time
from pathlib import Path
out=Path('/instance/artifacts/results.json')
last=None
while True:
 raw=Path('/exchange/inbox.json').read_text()
 if raw!=last:
  results=[]
  for message in json.loads(raw):
   source=message.get('source')
   if not source: continue
   totals={};missing=[]
   for i,line in enumerate(source['lines']):
    units=line.get('units')
    if type(units) is not int or units<0:
     missing.append('lines['+str(i)+'].units');continue
    totals[line['sku']]=totals.get(line['sku'],0)+units
   row={'request_id':source['id'],'revision':source['revision']}
   row.update({'status':'needs_input','missing':missing} if missing else
              {'status':'ok','by_sku':totals,'total_units':sum(totals.values())})
   results.append(row)
  tmp=out.with_suffix('.tmp');tmp.write_text(json.dumps({'results':results}));os.replace(tmp,out)
  last=raw
 time.sleep(.1)
'''


@unittest.skipUnless(os.environ.get("C3_PROGRAM_WITNESS_IMAGE"), "explicit local Docker image required")
class ProgramPathTests(unittest.TestCase):
    def test_repeated_service_runs_with_zero_cognitive_starts_and_freezes(self):
        from evals.cases import materialize
        from evals.systemization_cases import receive_results
        image=os.environ["C3_PROGRAM_WITNESS_IMAGE"]
        name="c3-program-witness-"+uuid.uuid4().hex[:12]
        def command(args):
            result=subprocess.run(args,capture_output=True,text=True,timeout=30)
            self.assertEqual(result.returncode,0,result.stderr or result.stdout)
            return result.stdout
        with tempfile.TemporaryDirectory(prefix="c3-program-witness-") as tmp:
            root=Path(tmp);instance=root/"instance";exchange=root/"exchange"
            instance.mkdir();exchange.mkdir()
            base=["docker","run","--rm","--network","none","--mount",f"type=bind,source={instance},target=/instance",image]
            command(base+["init","--workspace","--goal","Operate an ordinary batch service","/instance"])
            state=json.loads((instance/".concorde2/state.json").read_text())
            item=state["items"]["purpose"];item["attention"]["effort_state"]="dormant"
            command(base+["call","/instance","mutate",json.dumps({"reason":"Mechanical program-only witness; no cognitive calls", "changes":{
                "expected_seq":state["seq"],
                "items":[{"expected_revision":item["revision"],"item":item}],
                "programs":[{"id":"batch-service","command":["python3","/instance/worker.py"],"intention":"purpose","enabled":True}]}})])
            command(base+["configure","/instance",json.dumps({"harness":"command","command":["/bin/false"]})])
            (instance/"worker.py").write_text(WORKER)
            (instance/"artifacts").mkdir(exist_ok=True)
            accepted=instance/"artifacts/accepted.json";accepted.write_text('{"unrelated":"preserved"}')
            inbox=exchange/"inbox.json";inbox.write_text("[]")
            try:
                command(["docker","run","-d","--name",name,"--network","none","--read-only",
                         "--mount",f"type=bind,source={instance},target=/instance",
                         "--mount",f"type=bind,source={exchange},target=/exchange,readonly",image,"run","/instance"])
                family=os.environ.get("C3_PROGRAM_WITNESS_CASE", "SY04")
                _,_,_,facts=materialize(family,"challenge","situated",113)
                sources=[facts["initial_request"]]+[s for e in facts["schedule"] for s in e["sources"]]
                messages=[]
                for source in sources:
                    messages.append({"source":source})
                    fresh=exchange/"inbox.tmp";fresh.write_text(json.dumps(messages));fresh.replace(inbox)
                    until=time.monotonic()+8
                    while True:
                        try: output=json.loads((instance/"artifacts/results.json").read_text())
                        except (FileNotFoundError,json.JSONDecodeError): output=None
                        if receive_results(output,source)["correct"]:break
                        if time.monotonic()>=until:self.fail("ordinary program did not fulfill source: "+str(source))
                        time.sleep(.1)
                self.assertEqual(json.loads(accepted.read_text()),{"unrelated":"preserved"})
                command(["docker","exec",name,"concorde3","freeze","/instance"])
                command(["docker","wait",name])
                after=json.loads((instance/".concorde2/state.json").read_text())
                self.assertEqual(after["mode"],"frozen")
                self.assertEqual(after["activations"],{})
                self.assertNotEqual(after["programs"]["batch-service"]["status"],"running")
            finally:
                subprocess.run(["docker","stop","-t","2",name],capture_output=True,timeout=15)
                subprocess.run(["docker","rm",name],capture_output=True,timeout=15)


if __name__=="__main__": unittest.main()
