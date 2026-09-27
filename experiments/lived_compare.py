"""Bounded A/B continuations of one real frozen, program-free C3 world.

Original state is never resumed or edited. Candidate-only default practice updates
are explicit interventions; no purpose, business evidence or contract is rewritten.
"""
import argparse
import datetime as dt
import hashlib
import json
from pathlib import Path
import tempfile
import time

from evals.lab import command, save
from worlds.engine import World
from worlds.replay import capture, fork


PRACTICES=("memory-practice", "rectification-practice", "capabilities")


def selected_practices(state):
    return {key:state["items"][key]["text"] for key in PRACTICES}


def image_practices(image):
    with tempfile.TemporaryDirectory(prefix="c3-image-practices-") as tmp:
        command(["docker","run","--rm","--network","none","--mount",f"type=bind,source={tmp},target=/instance",
                 image,"init","--goal","Inspect default practice text only","/instance"])
        return selected_practices(json.loads((Path(tmp)/".concorde2/state.json").read_text()))


def prepare(source, root, baseline, candidate, fixture, minutes=20, starts=2):
    if type(minutes) is not int or not 10<=minutes<=60 or type(starts) is not int or not 1<=starts<=4:
        raise ValueError("bounded integer minutes 10..60 and starts 1..4 required")
    source,root=Path(source).resolve(),Path(root).resolve()
    if root.exists() or root.is_relative_to(source):
        raise ValueError("fresh independent comparison directory required")
    root.mkdir(parents=True,mode=0o700)
    images={arm:command(["docker","image","inspect",image,"--format","{{.Id}}"]) for arm,image in (("baseline",baseline),("candidate",candidate))}
    captured=capture(source,root/"snapshot")
    templates=image_practices(images["candidate"])
    started=time.time();cutoff=started+minutes*60
    manifest={"started":started,"cutoff":cutoff,"status":"preparing","worlds":{},
              "world_images":images,"image":images["candidate"],"source_revision":command(["git","rev-parse","HEAD"]),
              "source":str(source),"snapshot":captured,"shared_calls_per_hour":120,"shared_concurrency":8,
              "purpose":"Lived-state comparison, no forced solution or automatic competence verdict",
              "interventions":["New finite window and start ceiling in both copies; original timestamps/history retained.",
                               "One identical generic reentry notification per subject; dormant source disposition otherwise retained.",
                               "Candidate replaces only default memory/rectification/capability text, via revision-aware MCP.",
                               "Counterpart allowance is three calls with one reserved for response in both worlds; no injected choices."]}
    save(root/"cohort.json",manifest)
    try:
        for arm,image in images.items():
            path=root/arm
            fork(root/"snapshot",path,fixture,minutes/60,starts=starts,acknowledge_dormancy=True)
            world=World(path)
            with world.s.transaction() as db:
                cfg=world.s.meta(db,"config")
                cfg.update(cutoff=cutoff,counterpart_response_reserve=1,experiment="lived-practice-comparison")
                world.s.meta(db,"config",cfg)
                world.s.event(db,"_operator","comparison_protocol",{"arm":arm,"interventions":manifest["interventions"]})
            for actor in captured["subjects"]:
                instance=path/"subjects"/actor
                base=["docker","run","--rm","--network","none","--mount",f"type=bind,source={instance},target=/instance",image]
                state=json.loads((instance/".concorde2/state.json").read_text())
                if arm=="candidate":
                    changes=[]
                    for key,text in templates.items():
                        item={**state["items"][key],"text":text}
                        changes.append({"expected_revision":item["revision"],"item":item})
                    command(base+["call","/instance","mutate",json.dumps({"reason":"Explicit candidate practice update for isolated comparison; business state retained", "changes":{"expected_seq":state["seq"],"items":changes}})])
                command(base+["configure","/instance",json.dumps({"freeze_at":dt.datetime.fromtimestamp(cutoff,dt.timezone.utc).isoformat()})])
                command(base+["notify","/instance",state["config"]["reconsideration"],"operator-reentry",
                              "A new finite execution window is available for this copied undertaking. Review current state and operative limits before deciding what is worthwhile next."])
            manifest["worlds"][arm]=str(path)
        manifest.update(status="prepared",candidate_practice_sha256={key:hashlib.sha256(text.encode()).hexdigest() for key,text in templates.items()})
        save(root/"cohort.json",manifest)
        return manifest
    except BaseException as error:
        manifest.update(status="failed",error=str(error))
        save(root/"cohort.json",manifest)
        raise


if __name__=="__main__":
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("source",type=Path);p.add_argument("root",type=Path)
    p.add_argument("--baseline",required=True);p.add_argument("--candidate",required=True)
    p.add_argument("--fixture",required=True,type=Path)
    p.add_argument("--minutes",type=int,default=20);p.add_argument("--starts",type=int,default=2)
    a=p.parse_args()
    print(json.dumps(prepare(a.source,a.root,a.baseline,a.candidate,a.fixture,a.minutes,a.starts),indent=2))
