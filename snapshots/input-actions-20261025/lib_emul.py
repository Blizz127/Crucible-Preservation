"""Mini straight-line emulator for ActionsLibrary populate (0x36fe370): record args of every call to an AddAction-like function."""
import sys, struct, re, json
sys.dont_write_bytecode = True
import os
SP = os.environ.get('INPUT_ACTIONS_WORK') or os.getcwd()   # writes lib_calls_*.json here
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))   # ier.py
import ier
from capstone import CS_OP_MEM, CS_OP_IMM, CS_OP_REG
from capstone.x86_const import X86_REG_RIP
im = ier.Image()
START = int(sys.argv[1], 16) if len(sys.argv) > 1 else 0x36fe370
fr = ier.whole_function(im, START)
ins_list = im.disasm(*fr)
cs = im.cs
# canonical register naming
def canon(name):
    name = name.lower()
    m = {'al':'rax','ax':'rax','eax':'rax','rax':'rax','bl':'rbx','bx':'rbx','ebx':'rbx','rbx':'rbx','cl':'rcx','cx':'rcx','ecx':'rcx','rcx':'rcx',
         'dl':'rdx','dx':'rdx','edx':'rdx','rdx':'rdx','sil':'rsi','si':'rsi','esi':'rsi','rsi':'rsi','dil':'rdi','di':'rdi','edi':'rdi','rdi':'rdi',
         'bpl':'rbp','bp':'rbp','ebp':'rbp','rbp':'rbp','spl':'rsp','rsp':'rsp'}
    if name in m: return m[name]
    mm = re.match(r'^r(\d+)[dwb]?$', name)
    if mm: return 'r' + mm.group(1)
    return name
def width(name):
    name = name.lower()
    if name.endswith('b') or name in ('al','bl','cl','dl','sil','dil','bpl'): return 8
    if name.endswith('w') or name in ('ax','bx','cx','dx','si','di','bp'): return 16
    if name.startswith('e') or name.endswith('d'): return 32
    return 64
regs = {}
stack = {}
calls = []
names_seen = []     # string loads in order since last call
TARGET = {0x3211610}
def val(op, ins):
    if op.type == CS_OP_IMM: return op.imm
    if op.type == CS_OP_REG:
        return regs.get(canon(ins.reg_name(op.reg)))
    return None
pending_name = None
for ins in ins_list:
    mn, ops = ins.mnemonic, ins.operands
    rva = ins.address - im.base
    # string loads from rip-relative rdata
    t = im.rip_target(ins)
    if t is not None and mn in ('mov', 'movsd', 'movzx', 'movss', 'movups', 'movdqu', 'movaps', 'movdqa', 'movq') and ops[0].type == CS_OP_REG:
        s = im.string_at(t)
        if s and len(s) >= 3 and not s.startswith('d:') and (pending_name is None or True):
            # keep the most informative: first load of a string start
            names_seen.append((rva, s))
    if mn == 'call':
        tgt = ops[0].imm - im.base if ops[0].type == CS_OP_IMM else None
        if tgt in TARGET:
            a = {
                'at': rva, 'dl': regs.get('rdx'), 'r8': regs.get('r8'), 'r9': regs.get('r9'),
                'word20': stack.get(0x20), 'dword28': stack.get(0x28),
                'names': [s for _, s in names_seen],
            }
            calls.append(a)
        names_seen = []
        regs.pop('rax', None); regs.pop('rcx', None); regs.pop('rdx', None); regs.pop('r8', None); regs.pop('r9', None); regs.pop('r10', None); regs.pop('r11', None)
        continue
    if mn in ('mov', 'movzx') and len(ops) == 2 and ops[0].type == CS_OP_REG:
        d = canon(ins.reg_name(ops[0].reg))
        w = width(ins.reg_name(ops[0].reg))
        if ops[1].type == CS_OP_IMM:
            v = ops[1].imm & ((1 << w) - 1)
            if w < 32 and d in regs and regs[d] is not None:
                mask = (1 << w) - 1
                v = (regs[d] & ~mask) | v
            regs[d] = v
        elif ops[1].type == CS_OP_REG:
            sv = val(ops[1], ins)
            if sv is not None:
                sw = width(ins.reg_name(ops[1].reg))
                sv &= (1 << sw) - 1
                if w < 32 and mn == 'mov' and d in regs and regs[d] is not None:
                    mask = (1 << w) - 1
                    sv = (regs[d] & ~mask) | (sv & mask)
                regs[d] = sv
            else:
                regs.pop(d, None)
        else:
            regs.pop(d, None)
        continue
    if mn == 'lea' and ops[0].type == CS_OP_REG:
        d = canon(ins.reg_name(ops[0].reg))
        m = ops[1].mem
        if m.base != 0 and m.index == 0 and m.base != X86_REG_RIP:
            b = regs.get(canon(ins.reg_name(m.base)))
            if b is not None and canon(ins.reg_name(m.base)) not in ('rsp', 'rbp'):
                regs[d] = (b + m.disp) & 0xffffffffffffffff
                continue
        regs.pop(d, None)
        continue
    if mn in ('xor', 'sub') and len(ops) == 2 and ops[0].type == CS_OP_REG and ops[1].type == CS_OP_REG and ops[0].reg == ops[1].reg:
        regs[canon(ins.reg_name(ops[0].reg))] = 0
        continue
    if mn in ('inc', 'dec', 'add', 'sub', 'shl', 'shr', 'and', 'or', 'xor') and ops and ops[0].type == CS_OP_REG:
        d = canon(ins.reg_name(ops[0].reg))
        regs.pop(d, None)
        continue
    if mn == 'mov' and len(ops) == 2 and ops[0].type == CS_OP_MEM and ops[0].mem.base != 0:
        if ins.reg_name(ops[0].mem.base) == 'rsp' and ops[0].mem.index == 0:
            off = ops[0].mem.disp
            if off in (0x20, 0x28, 0x30):
                v = val(ops[1], ins)
                w = ops[0].size * 8
                if v is not None:
                    v &= (1 << w) - 1
                stack[off] = v
        continue
print('calls', len(calls))
out = []
for c in calls:
    out.append(c)
json.dump(out, open(SP + '/lib_calls_%x.json' % START, 'w'), indent=0)
for c in calls[:200]:
    print(hex(c['at']), 'dl', c['dl'], 'r8', c['r8'], 'r9', c['r9'], 'w20', c['word20'], 'd28', c['dword28'], c['names'][:2])
