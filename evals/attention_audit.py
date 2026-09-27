"""Read-only notification/admission evidence. No automatic waste/usefulness verdict."""
import argparse
import datetime as dt
import hashlib
import json
from pathlib import Path
import re

from .trial_replay import read_regular, root_path
from worlds.forensics import replay, state_matches_replay


def instant(value):
    """RFC3339 nanoseconds, retaining distinctions smaller than a microsecond."""
    if not value or value == "0001-01-01T00:00:00Z":
        return None
    match=re.fullmatch(r"(.+T\d{2}:\d{2}:\d{2})(?:\.(\d{1,9}))?(Z|[+-]\d{2}:\d{2})",value)
    if not match:
        raise ValueError("invalid recorded timestamp")
    whole=dt.datetime.fromisoformat(match[1]+match[3].replace("Z","+00:00"))
    return int(whole.timestamp())*1_000_000_000+int((match[2] or "").ljust(9,"0"))


def analyze(state, events, packets, inherited=(), maximum=256):
    if type(maximum) is not int or not 1<=maximum<=1000:
        raise ValueError("bounded positive activation count required")
    inherited=set(inherited)
    all_new=sorted((a for key,a in state.get("activations",{}).items()
                    if key not in inherited and a.get("usage",{}).get("basis")!="constructed"),
                   key=lambda a:(instant(a["started"]),a["id"]))
    acknowledgments,admissions={},{}
    for event in events:
        if event["seq"]>state["seq"]:continue
        if event.get("kind")=="activation.started":
            for c in event.get("changes",[]):
                consumer=c.get("value",{}).get("consumed_by") if c["field"]=="wakes" else None
                if consumer:admissions.setdefault(consumer,set()).add(c["key"])
        if event.get("kind")!="mutation" or not event.get("actor"):continue
        ids=[c["key"] for c in event.get("changes",[]) if c["field"]=="wakes" and
             not c.get("delete") and c.get("value",{}).get("consumed_by")==event.get("actor")]
        if ids:
            acknowledgments.setdefault(event["actor"],[]).append({"sequence":event["seq"],"at":event["at"],"ids":sorted(ids)})
    rows=[]
    for a in all_new[-maximum:]:
        entry=packets.get(a["id"],{})
        packet=entry.get("packet") or {}
        start,finish=instant(a["started"]),instant(a.get("finished"))
        prepared={w["id"] for w in packet.get("observations",[])}
        inflight=sorted(key for key in prepared if key in state.get("wakes",{}) and
            (at:=instant(state["wakes"][key]["at"])) is not None and at>start and (finish is None or at<=finish))
        completion=a.get("completion") or {}
        next_a=next((other for other in all_new if finish is not None and
            other["id"]!=a["id"] and other.get("intention")==a.get("intention") and
            instant(other["started"])>=finish),None)
        consumed=[]
        if next_a:
            consumed=sorted(admissions.get(next_a["id"],set()))
        rows.append({"id":a["id"],"intention":a.get("intention"),"status":a["status"],
            "recovery_of":a.get("recovery_of"),"started":a["started"],"finished":a.get("finished"),
            "selection_reason":a.get("reason"),"continuation":completion.get("continuation"),
            "context_evidence":entry.get("basis","missing rectification packet"),
            "packet_sha256":entry.get("sha256"),"packet_omitted_observations":packet.get("observation_batch",{}).get("omitted"),
            "packet_starts_remaining":packet.get("starts_remaining"),
            "prepared_inflight_observation_ids":inflight,
            "explicitly_considered_inflight_ids":sorted(set(inflight)&set(completion.get("considered",[]))),
            "acknowledgments":acknowledgments.get(a["id"],[]),
            "next_same_intention_activation":next_a["id"] if next_a else None,
            "next_is_recovery":bool(next_a and next_a.get("recovery_of")),
            "seconds_to_next":(instant(next_a["started"])-finish)/1e9 if next_a else None,
            "prepared_inflight_ids_consumed_by_next":sorted(set(inflight)&set(consumed)),
            "other_ids_consumed_by_next":sorted(set(consumed)-set(inflight))})
    return {"state_sequence":state["seq"],"mode":state["mode"],"new_activations":len(all_new),
            "omitted_activations":max(0,len(all_new)-maximum),"activations":rows,
            "limitations":["Prepared context plus turn.started is exposure evidence, not comprehension or a verified business outcome.",
                "Observations consumed at the next admission do not establish that they were its only cause; other notices, timers and deliberate continuation may matter.",
                "Acknowledgment events show explicit wake disposition, not resolution of the underlying obligation. Recovery is reported separately.",
                "No fixed latency threshold or automatic wasted-activation score. Read receiving results, tool calls and unfinished work before judging usefulness.",
                "Only the standard rectification packet is inspected; progressive MCP reads and returned-work packets may contain additional evidence."]}


def audit(trial, maximum=256):
    if type(maximum) is not int or not 1<=maximum<=1000:
        raise ValueError("bounded positive activation count required")
    trial=root_path(trial)
    manifest=json.loads(read_regular(trial/"manifest.json",1024*1024)[0])
    runtime=trial/"subject/.concorde2"
    state_raw=read_regular(runtime/"state.json",16*1024*1024)[0]
    state=json.loads(state_raw)
    history=read_regular(runtime/"events.jsonl",64*1024*1024)[0]
    reconstructed=replay(history,state["seq"])
    if not state_matches_replay(state,reconstructed):
        raise ValueError("state does not match its verified journal prefix")
    records=[]
    for line in history.decode().splitlines():
        event=json.loads(line)["event"]
        records.append(event)
        if event["seq"]==state["seq"]:break
    inherited=manifest.get("inherited_activation_ids",[])
    selected=sorted((a for key,a in state.get("activations",{}).items() if key not in inherited
                     and a.get("usage",{}).get("basis")!="constructed"),key=lambda a:(instant(a["started"]),a["id"]))[-maximum:]
    packets,errors={},[]
    remaining=32*1024*1024
    for a in selected:
        if remaining<=0:
            errors.append({"error":"packet/log read budget exhausted"});break
        if not re.fullmatch(r"[A-Za-z0-9_.-]{1,200}",a["id"]):
            raise ValueError("unsafe activation record ID")
        path=runtime/"contexts"/(a["id"]+".rectification.json")
        if not path.exists():continue
        try:
            raw=read_regular(path,min(1024*1024,remaining))[0];remaining-=len(raw);p=json.loads(raw)
            if p.get("activation")!=a["id"] or p.get("phase")!="rectification" or p.get("sequence",state["seq"]+1)>state["seq"]:
                raise ValueError("packet identity/phase/watermark mismatch")
            entry={"packet":p,"sha256":hashlib.sha256(raw).hexdigest(),"basis":"prepared packet only; harness delivery not established"}
            log=runtime/"harness-logs"/(a["id"]+".rectification.jsonl")
            if log.exists():
                raw_log=read_regular(log,min(8*1024*1024,remaining))[0];remaining-=len(raw_log)
                lines=raw_log.decode().splitlines()
                for line in lines:
                    try:event=json.loads(line)
                    except ValueError:continue  # A live log can have an incomplete tail.
                    if event.get("type")=="turn.started":entry["basis"]="prepared packet and matching rectification turn.started";break
            packets[a["id"]]=entry
        except (OSError,ValueError) as error:
            errors.append({"activation":a["id"],"error":str(error)})
    result=analyze(state,records,packets,inherited,maximum)
    result.update(trial=manifest["id"],arm=manifest.get("arm"),variant=manifest.get("variant"),
                  state_sha256=hashlib.sha256(state_raw).hexdigest(),errors=errors,
                  consistency="State/journal prefix verified; separately read packets/logs are time-bounded observations, not an atomic snapshot.")
    return result


if __name__=="__main__":
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("trial",type=Path);parser.add_argument("--maximum",type=int,default=256)
    args=parser.parse_args();print(json.dumps(audit(args.trial,args.maximum),indent=2))
