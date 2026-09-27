"""Read-only in-container process proof for ended subject harness calls."""
import json
from pathlib import Path
import subprocess

SCAN = r'''
import hashlib,json,pathlib,sys
p=pathlib.Path(sys.argv[1] if len(sys.argv)>1 else '/instance/.concorde2/state.json')
raw=p.read_bytes(); state=json.loads(raw); live=set()
for proc in pathlib.Path(sys.argv[2] if len(sys.argv)>2 else '/proc').iterdir():
 if not proc.name.isdigit(): continue
 try:
  stat=(proc/'stat').read_text().rsplit(')',1)[1].split()
  if stat[0]=='Z': continue
  data=(proc/'environ').read_bytes()
 except (FileNotFoundError,ProcessLookupError): continue
 for entry in data.split(b'\0'):
  if entry.startswith(b'CONCORDE3_ACTIVATION='):
   live.add(entry.split(b'=',1)[1].decode())
print(json.dumps({'state':{'seq':state['seq'],'activations':{
 k:{f:v.get(f) for f in ('status','recovery_of')} for k,v in state['activations'].items()}},
 'live_activations':sorted(live),'state_sha256':hashlib.sha256(raw).hexdigest()}))
'''


def inspect_subject(world, manifest, actor):
    name = manifest["subjects"][actor]
    def command(args):
        return subprocess.check_output(args, text=True, timeout=20)
    before = json.loads(command(["docker", "inspect", name]))[0]
    if not before["State"]["Running"] or before["State"].get("Restarting"):
        return None
    expected = (world.s.root / "subjects" / actor).resolve()
    mounts = {m["Destination"]: Path(m["Source"]).resolve() for m in before["Mounts"] if m["Type"] == "bind"}
    if mounts.get("/instance") != expected or before["Image"] != manifest["image"]:
        raise ValueError("subject process-scan provenance mismatch")
    result = json.loads(command(["docker", "exec", name, "python3", "-c", SCAN]))
    after = json.loads(command(["docker", "inspect", name]))[0]
    if before["Id"] != after["Id"] or before["State"]["StartedAt"] != after["State"]["StartedAt"] or not after["State"]["Running"]:
        raise ValueError("subject changed during process scan")
    result["proof"] = {"verified": True, "container": before["Id"],
                       "started_at": before["State"]["StartedAt"],
                       "state_sha256": result["state_sha256"]}
    return result


def reconcile(world, manifest, actor):
    from .runtime import reconcile_ended_calls
    with world.s.transaction() as db:
        count = db.execute("SELECT count(*) FROM calls WHERE actor=? AND category='subject' AND status IN ('reserved','running','uncertain')", (actor,)).fetchone()[0]
    if not count: return []
    result = inspect_subject(world, manifest, actor)
    if result is None: return []  # The separate terminal-container path owns this.
    return reconcile_ended_calls(world, actor, result["state"], set(result["live_activations"]), result["proof"])


def reconcile_shared(world):
    from .budget import release_many
    with world.s.transaction() as db:
        terminal = [r[0] for r in db.execute("SELECT id FROM calls WHERE status IN ('completed','failed','undispatched')")]
    release_many(terminal)
