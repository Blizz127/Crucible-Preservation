#!/usr/bin/env python3
"""Read-only, build-specific solo startup inspection of a user-launched client.

Reports entity states, phase, local match slot and match-store replication graph
indices. No strings, identity tokens, process writes, launch or termination.
Graph node layout follows retail 4aa8700/4aa8600; properties follow 876333.
"""
from pathlib import Path
from datetime import datetime, timezone
import sys,os,struct,json,hashlib
from inspect_replication_schema import find_client
executable=Path('/home/blizz/.local/share/Steam/steamapps/common/Crucible/bin/Crucible.exe')
with executable.open('rb') as image:
 executable_sha256=hashlib.file_digest(image,'sha256').hexdigest()
pid,base=find_client(executable)
f=os.open(f'/proc/{pid}/mem',os.O_RDONLY)
def rd(a,n):
 b=os.pread(f,n,a)
 if len(b)!=n:raise ValueError('short read')
 return b
def q(a):return struct.unpack('<Q',rd(a,8))[0]
def u(a):return struct.unpack('<I',rd(a,4))[0]
assert rd(base,2)==b'MZ'
g=q(base+0x760bb18);manager=q(g+0x60)
assert q(manager)-base==0x67a1370
agent=q(g+0x78)
if not agent:raise SystemExit('Client network agent not ready')
rep=q(agent+0x4112488)
if not rep:raise SystemExit('Client replication manager not ready')
result={'observed_at':datetime.now(timezone.utc).isoformat(),'executable':str(executable),
 'executable_sha256':executable_sha256,'image_base':hex(base),
 'last_server_game_time_ms':u(agent+0x4112610),'pid':pid,'game_state':u(g+0xa0),'connection_status':u(q(g+0x508)+0x28),'entities':[],
 'local_entity_slots':[{'entity_id':hex(q(base+0x7c9b490+i*16)), 'network_id':hex(u(base+0x7c9b498+i*16))} for i in range(7)]}
stack=[q(rep+0x78)&~1];seen=set()
while stack:
 n=stack.pop()
 if not n or n in seen:continue
 seen.add(n)
 if len(seen)>10000:raise ValueError('invalid tree')
 stack.extend([q(n+8)&~1,q(n+0x10)&~1]);key=u(n+0x18)
 if key not in (0xfe00007d,0xfe000081,0xfe000087,0x1000001,0x1000002,0x1000003,0x1000004,0x1000005,0xfe00067a,0xfe00067e,0xfe000684,0xfe00098b,0xfe00098f,0xfe000995,*range(0xfe00004b,0xfe000053)):continue
 r=q(n+0x20);ent=q(r+0x40)
 item={'network_id':hex(key),'slice_hash':u(r+0x68),'slice_entry':u(r+0xf0),'active_state':rd(ent+0x60,1)[0],'components':[]}
 start,end=q(ent+0x10),q(ent+0x18)
 if not 0<end-start<8192:raise ValueError('bad component vector')
 for ptr in range(start,end,8):
  c=q(ptr);v=q(c);fn=q(v+0x70)
  if not base <= fn < base+0x6240000:
   item['components'].append({'vtable':hex(v-base), 'network_getter':False})
   continue
  code=rd(fn,6)
  entry={'vtable':hex(v-base),'type_getter':hex(fn-base),'getter_bytes':code.hex()}
  if v-base==0x674a508:entry['phase']=struct.unpack('<H',rd(c+0x130,2))[0]
  if code[:3]==bytes.fromhex('b058c3'):entry['match_player_id']=struct.unpack('<b',rd(c+0xf0,1))[0]
  if code[:3]==bytes.fromhex('b055c3'):entry['loadout_character']=struct.unpack('<H',rd(c+0x160,2))[0]
  if code[:3]==bytes.fromhex('b0acc3'):
   autonomous=q(c+0xc8)
   if autonomous and base <= q(autonomous) < base+0x8000000:
    entry['input_autonomous_vtable']=hex(q(autonomous)-base)
    entry['input_server_time_ms']=u(autonomous+0x118)
    entry['input_server_multiplier']=rd(autonomous+0x11c,1)[0]
    entry['input_client_offset']=rd(autonomous+0x11d,1)[0]
  if code[:3]==bytes.fromhex('b07dc3'):
   entry['last_processed_game_time_ms']=u(c+0xd8)
  if code[:3]==bytes.fromhex('b091c3'):
   entry['authoritative_position']=list(struct.unpack('<3f',rd(c+0x6e0,12)))
   entry['authoritative_rotation']=list(struct.unpack('<4f',rd(c+0x6f0,16)))
  if code[:3]==bytes.fromhex('b04fc3'):
   entry['rpc_handler']=hex(q(v+0x78)-base)
   entry['state_delta_serializer']=hex(q(v+0x80)-base)
   resource=q(c+0x1ad0+0x58)
   if resource:
    entry['health_value_triplet']=list(struct.unpack('<3f',rd(resource+0xc,12)))
    entry['health_rate']=struct.unpack('<f',rd(resource+8,4))[0]
    entry['health_timing_fields']=list(struct.unpack('<5f',rd(resource+0x18,20)))
  if code[:3]==bytes.fromhex('b09ec3'):
   entry['following_entity_id']=hex(u(c+0x1f8))
   entry['controlling_entity_id']=hex(u(c+0x1fc))
   entry['hierarchy_kind']=struct.unpack('<H',rd(c+0x420,2))[0]
   entry['child_count']=u(c+0x1c8)
   if entry['child_count']<=16:entry['child_ids']=[hex(u(c+0x188+i*4)) for i in range(entry['child_count'])]
  if code[:3]==bytes.fromhex('b025c3'):
   entry['player_list_size']=u(c+0x16b0)
   entry['first_character_id']=hex(u(c+0x1790))
   if entry['player_list_size']==1:
    data=c+0x3b0
    entry['first_player']={'id':struct.unpack('<b',rd(data+0x10,1))[0],
                           'character_class':struct.unpack('<H',rd(data+0x124,2))[0],
                           'character_status':u(data+0x12c),
                           'team':struct.unpack('<b',rd(data+0x126,1))[0]}

  # Include AnimationParameter (13), DamageFloater (36), HitReaction (80),
  # and IncomingHitIndicator (81). Event-only directions can have no graph
  # entries or property descriptors; an empty vector is valid, not unreadable.
  # Animation registration offsets identify wrappers, so no unverified inner
  # value offsets are read here.
  if code[:3] in tuple(bytes.fromhex(x) for x in ('b002c3', 'b00dc3', 'b024c3', 'b025c3', 'b04fc3', 'b050c3', 'b051c3', 'b055c3', 'b058c3', 'b073c3', 'b09ec3')):
   graph=q(c+0x28);begin,end=q(graph),q(graph+8)
   if not 0 <= end-begin <= 65536 or (end-begin)%8:raise ValueError('invalid replication graph')
   entry['replication_graph']=[list(struct.unpack('<HHHBB',rd(a,8))) for a in range(begin,end,8)]
   info=q(c+0x20);pb,pe=q(info+0x58),q(info+0x60)
   if not 0 <= pe-pb <= 1024 or (pe-pb)%8:raise ValueError('invalid property info vector')
   entry['property_record_indices']=[struct.unpack('<H',rd(q(a)+0x20,2))[0] for a in range(pb,pe,8)]
   if code[:3] in tuple(bytes.fromhex(x) for x in ('b024c3','b051c3','b055c3','b058c3','b09ec3')):
    begin,end=q(graph+0x40),q(graph+0x48)
    if not 0 <= end-begin <= 65536 or (end-begin)%8:raise ValueError('invalid autonomous graph')
    entry['autonomous_graph']=[list(struct.unpack('<HHHBB',rd(a,8))) for a in range(begin,end,8)]

  item['components'].append(entry)
 result['entities'].append(item)
os.close(f)
print(json.dumps(result,indent=2))
