"""Offline decode using the supplied source definitions; never load a driver."""
import argparse
import hashlib
import json
from pathlib import Path
import re


def definitions(source_root):
    base=source_root/'drivers/iommu/hisilicon'
    text=(base/'interrupt.c').read_text(encoding='utf-8')
    enum=re.search(r'enum ummu_evtq_evt_name\s*\{(.*?)\};',text,re.S).group(1)
    enum=re.sub(r'/\*.*?\*/|//[^\n]*','',enum,flags=re.S)
    value=-1;names={}
    for entry in enum.split(','):
        item=entry.strip()
        if not item:continue
        match=re.fullmatch(r'(\w+)(?:\s*=\s*(0x[0-9a-fA-F]+|\d+))?',item)
        if not match:raise ValueError('unsupported event enum expression')
        name,literal=match.groups();value=int(literal,0) if literal else value+1
        names[value]=name
    macros={}
    for line in (base/'queue.h').read_text(encoding='utf-8').splitlines():
        match=re.match(r'#define\s+(EVTQ_ENT\w+)\s+(.+)',line)
        if not match:continue
        name,expr=match.groups()
        bits=re.fullmatch(r'GENMASK(?:_ULL)?\((\d+),\s*(\d+)\)',expr.strip())
        bit=re.fullmatch(r'\(1U << (\d+)\)',expr.strip())
        if bits:
            high,low=map(int,bits.groups());macros[name]=(low,(1<<(high-low+1))-1)
        elif bit:macros[name]=(int(bit.group(1)),1)
    hashes={p.name:hashlib.sha256(p.read_bytes().replace(b'\r\n',b'\n')).hexdigest() for p in (base/'interrupt.c',base/'queue.h')}
    return names,macros,hashes


def decode(words, source_root):
    if len(words)!=8:raise ValueError('need all eight raw event words')
    raw=[int(x,16) for x in words]
    names,macros,hashes=definitions(source_root)
    def field(name,word):
        shift,mask=macros[name];return (raw[word]>>shift)&mask
    result=dict(code=field('EVTQ_ENT0_CODE',0),tid=field('EVTQ_ENT0_TID',0),
        read=bool(field('EVTQ_ENT0_RNW',0)),privileged=bool(field('EVTQ_ENT0_PNU',0)),
        instruction=bool(field('EVTQ_ENT0_IND',0)),stage2=bool(field('EVTQ_ENT0_S2',0)),
        stall=bool(field('EVTQ_ENT0_STALL',0)),iova=hex(field('EVTQ_ENT3_IADDR',3)),
        tect_tag=field('EVTQ_ENT4_TECTE_TAG',4),source_hashes=hashes)
    result['event_name']=names.get(result['code'],'unknown')
    result['note']='Source-based decoding; deployed-module provenance must be checked separately. TID is not a Proxy index. Raw fields do not identify which node/context should own this TID.'
    return result


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--diagnosis',type=Path,required=True)
    p.add_argument('--source-root',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True)
    a=p.parse_args()
    data=json.loads(a.diagnosis.read_text(encoding='utf-8'))
    for event in data['ummu_events']:event['decoded']=decode(event['raw_words'],a.source_root)
    a.output.parent.mkdir(parents=True,exist_ok=True)
    with a.output.open('x',encoding='utf-8') as stream:json.dump(data,stream,indent=2)
    print('UMMU_OFFLINE_DECODED output='+str(a.output),flush=True)
