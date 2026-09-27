"""Read-only interface-shape evidence, not a new outcome grader or contract."""
import copy
import hashlib
import json
from pathlib import Path
from worlds import scenarios
from worlds.scenarios import receiving_contract


def json_type(value):
    if value is None: return 'null'
    if isinstance(value,bool): return 'boolean'
    if isinstance(value,int): return 'integer'
    if isinstance(value,float): return 'number'
    if isinstance(value,str): return 'string'
    if isinstance(value,list): return 'array'
    if isinstance(value,dict): return 'object'
    return 'unknown'


def describe(task, output):
    contract=receiving_contract(task)
    if contract.get('coverage')=='undocumented':
        return {'status':'undocumented','meaning':'No registered receiving interface; do not invent one.'}
    required=contract.get('required',[])
    if not isinstance(required,list) or not all(isinstance(k,str) for k in required):
        return {'status':'undocumented','meaning':'No inspectable root-field contract.'}
    missing=[k for k in required if not isinstance(output,dict) or k not in output]
    mismatches=[]
    for key,spec in contract.get('properties',{}).items():
        if not isinstance(output,dict) or key not in output: continue
        expected=spec.get('type'); actual=json_type(output[key])
        if isinstance(expected,str) and actual!=expected and not (expected=='number' and actual=='integer'):
            mismatches.append({'path':'$.'+key,'expected':expected,'actual':actual})
    locations=[]; visited=0; truncated=False
    def visit(value,path,depth):
        nonlocal visited,truncated
        if visited>=128 or len(locations)>=16 or depth>4:
            truncated=True; return
        visited+=1
        if isinstance(value,dict):
            if path!='$' and required and all(k in value for k in required): locations.append(path)
            for key,child in list(value.items())[:128]:
                if isinstance(child,(dict,list)): visit(child,path+'.'+str(key),depth+1)
            if len(value)>128: truncated=True
        elif isinstance(value,list):
            for index,child in enumerate(value[:128]):
                if isinstance(child,(dict,list)): visit(child,path+f'[{index}]',depth+1)
            if len(value)>128: truncated=True
    visit(output,'$',0)
    return {'status':'derived_interface_evidence','receiving_contract':contract,
        'actual_root_type':json_type(output),'missing_root_fields':missing,
        'root_type_mismatches':mismatches,'nested_objects_with_required_fields':locations,
        'nested_search_truncated':truncated,
        'meaning':'Schema paths are not interchangeable: matching nested values do not satisfy a root-field interface. No flattening, extraction, new verdict or repair occurred. Root compatibility does not prove correct values or usefulness. This is the receiver boundary, not proof that the producer was given or accepted that contract; compare actual exposed requests and purchased scope.'}


def annotate_packet(packet, manifest):
    """Add only derived shape facts to an original, otherwise identical packet."""
    result=copy.deepcopy(packet)
    digest=hashlib.sha256(Path(scenarios.__file__).read_bytes()).hexdigest()
    if manifest.get('source_hashes',{}).get('worlds/scenarios.py')!=digest:
        raise ValueError('Historical receiver implementation is not the current schema source')
    annotated=[]
    for key,source in result['sources'].items():
        if '/outcome/' not in key or source.get('truncated'): continue
        try: value=json.loads(source['text'])
        except (ValueError,TypeError): continue
        if not isinstance(value,dict) or not isinstance(value.get('task'),dict) or 'output' not in value: continue
        value['receiving_interface_shape']=describe(value['task'],value['output'])
        value['receiving_interface_shape']['scenario_source_sha256']=digest
        source['text']=json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=False)
        annotated.append(key)
    result['derived_shape_assistance']={'source_keys':annotated,'scenario_source_sha256':digest,
        'original_packet_unchanged':True,'meaning':'Read-only derived receiver-interface shape; original labels, records and case index preserved. Not necessarily the producer agreement.'}
    return result
