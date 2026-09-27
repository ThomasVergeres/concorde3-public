"""Claim-specific discovery map, not test success or automatic certification."""
import json
import re
from . import readiness
from .cases import IMPLEMENTED, materialize

# Historical fixture tags predate namespaces. These families derive their tags
# from the business inventory; HD01 and the adaptation families derive core tags.
BUSINESS_TAGS={"FC01","CF01","LR01","RG01","AU01","AU02","BP01","PC01","UX01","SY01","RC01","BP02","BP03","UX02","SY02","AC01","GC01"}


def audit():
    claims=readiness.read("claims.json")["claims"]
    mapping=readiness.read("mechanical-map.json")
    expected=readiness.expected_claims()
    if set(mapping)!={f"CORE.F{i}" for i in range(1,13)}|{f"CORE.N{i}" for i in range(1,8)}:
        raise ValueError("all hard core requirements need an explicit mapping")
    definitions={}
    for path in readiness.ROOT.rglob("*_test.go"):
        for name in re.findall(r"^func (Test\w+)\(t \*testing.T\)",path.read_text(),re.M):
            if name in definitions: raise ValueError("ambiguous test name "+name)
            definitions[name]=str(path.relative_to(readiness.ROOT))
    for claim,names in mapping.items():
        if not names or any(n not in definitions for n in names):
            raise ValueError("missing mechanical test for "+claim+": "+str(names))
    hints={key:[] for key in expected}
    unmatched=[]
    for case in IMPLEMENTED:
        fixture=materialize(case)
        facts=fixture["facts"] if isinstance(fixture,dict) else fixture[-1]
        for tag in facts.get("requirements",[]):
            claim=tag if tag.startswith(("CORE.","READY.")) else ("READY." if case in BUSINESS_TAGS else "CORE.")+tag
            if claim in hints:hints[claim].append(case)
            else:unmatched.append({"case":case,"tag":tag})
    return {"basis":"Discovery references only. A fixture's declared tags are not proof that its oracle covers every clause. Mechanical passes establish mechanisms, not intelligent use or deployment readiness.",
            "unmapped_fixture_tags":unmatched,
            "claims":[{"id":c["id"],"title":c["title"],"status":c["status"],
                       "mechanical_tests":[{"name":n,"file":definitions[n]} for n in mapping.get(c["id"],[])],
                       "candidate_sim_families":hints[c["id"]],"evidence":c["evidence"],"counterexamples":c["counterexamples"],
                       "remaining_gap":c["next_decision"]} for c in claims]}


if __name__=="__main__":print(json.dumps(audit(),indent=2))
