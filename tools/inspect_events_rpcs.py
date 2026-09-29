#!/usr/bin/env python3
"""Read-only inspector for the client's INBOUND (server->client) RPC surface.

`docs/13` mapped the `EntityRpcs` envelope and `docs/25` histogrammed what the
client *sends*. What was missing is the other direction: which
`(component id, rpc id)` pairs the client will actually dispatch, and what each
one's payload looks like. That is recovered here, statically, from three
generated artefacts the NovaNet AutoComponent code generator leaves in the
executable:

1. The 180-entry component-type name table (jump table at RVA 0x4ba22cc), which
   is the authority for `component id -> component class`. The same table is
   already used by `tools/inspect_replication_schema.py`.

2. One `<Component>::Client[Base]::HandleRpcMessage` per component. Each is a
   generated switch on the inbound RPC type byte, guarded by a role compare.
   A component with no inbound RPC compiles to a 0x58-byte stub whose only
   content is the `NV_NETWORKASSERT: Got unhandled RPC Type %d in <Component>`
   call, so "has no inbound RPC" is directly observable, not inferred.

3. One `<Component>::<Name>RpcStruct::Serialize` per RPC payload struct. The
   struct's vtable is what `HandleRpcMessage` pushes on the stack before it
   calls the deserializer, so vtable -> Serialize -> assert string names the
   struct, and the `lea` operands inside `Serialize` are its field names in
   wire order.

Nothing is executed, nothing under the Steam install is written.

Usage:
  tools/inspect_events_rpcs.py --components
  tools/inspect_events_rpcs.py --dispatch [--all] [--json OUT]
  tools/inspect_events_rpcs.py --structs
"""
import argparse
import bisect
import json
import os
import re
import struct
import sys

import pefile
from capstone import Cs, CS_ARCH_X86, CS_MODE_64, CS_OP_MEM, CS_OP_REG, CS_OP_IMM
from capstone.x86_const import X86_REG_RIP

EXE = os.environ.get("CRUCIBLE_EXE", os.path.expanduser(
    "~/.local/share/Steam/steamapps/common/Crucible/bin/Crucible.exe"))

# The generated HandleRpcMessage reads the RPC type from message byte 9 and the
# network role from message byte 0. Both are literal in every generated switch.
RPC_TYPE_OFFSET = 9
ROLE_OFFSET = 0

NOISE = re.compile(r"^(d:\\|NV_NETWORK|UNKNOWN$|Did not handle|Got unhandled)")


class Image:
    def __init__(self, path=EXE):
        self.pe = pefile.PE(path)
        self.img = open(path, "rb").read()
        self.base = self.pe.OPTIONAL_HEADER.ImageBase
        secs = {s.Name.rstrip(b"\0").decode(): s for s in self.pe.sections}
        text = secs[".text"]
        self.tbase, self.traw = text.VirtualAddress, text.get_data()
        praw = secs[".pdata"].get_data()
        frags = []
        for i in range(0, len(praw) - 11, 12):
            b, e, _ = struct.unpack("<III", praw[i:i + 12])
            if e > b:
                frags.append((b, e))
        frags.sort()
        self.frags = frags
        self.frag_starts = [f[0] for f in frags]
        self.cs = Cs(CS_ARCH_X86, CS_MODE_64)
        self.cs.detail = True
        self._lea = None
        self._ptr = None

    # ---- primitives -----------------------------------------------------
    def fragment(self, rva):
        i = bisect.bisect_right(self.frag_starts, rva) - 1
        if i < 0:
            return None
        b, e = self.frags[i]
        return (b, e) if b <= rva < e else None

    def string_at(self, rva, maxlen=160):
        try:
            off = self.pe.get_offset_from_rva(rva)
        except Exception:
            return None
        raw = self.img[off:off + maxlen].split(b"\0", 1)[0]
        if len(raw) < 2 or any(c < 32 or c > 126 for c in raw):
            return None
        return raw.decode()

    def find_string(self, text):
        """RVA of the unique NUL-terminated occurrence of `text`, or None."""
        needle = text.encode() + b"\0"
        off = self.img.find(needle)
        # A shorter component name can be a suffix of a longer one
        # (`ObjectiveComponent` inside `GMEventSpawnedObjectiveComponent`), so
        # the match must start a string.
        while off > 0 and self.img[off - 1] != 0:
            off = self.img.find(needle, off + 1)
        if off < 0:
            return None
        return self.pe.get_rva_from_offset(off)

    @property
    def lea(self):
        """target RVA -> [instruction RVA] for every RIP-relative LEA in .text."""
        if self._lea is None:
            idx, d, i, n = {}, self.traw, 0, len(self.traw)
            while i < n - 7:
                if d[i] == 0x8D and (d[i + 1] & 0xC7) == 0x05:
                    disp = struct.unpack("<i", d[i + 2:i + 6])[0]
                    idx.setdefault(self.tbase + i + 6 + disp, []).append(self.tbase + i)
                    i += 6
                else:
                    i += 1
            self._lea = idx
        return self._lea

    @property
    def ptr(self):
        """pointed-to RVA -> [RVA of the 8-byte slot] over the whole image."""
        if self._ptr is None:
            idx = {}
            for sec in self.pe.sections:
                name = sec.Name.rstrip(b"\0").decode()
                if name not in (".rdata", ".data"):
                    continue
                data, va = sec.get_data(), sec.VirtualAddress
                for i in range(0, len(data) - 7, 8):
                    q = struct.unpack_from("<Q", data, i)[0]
                    if self.base <= q < self.base + 0x8000000:
                        idx.setdefault(q - self.base, []).append(va + i)
            self._ptr = idx
        return self._ptr

    def is_vtable(self, rva):
        """True when the two qwords at `rva` are both .text pointers.

        Every generated RpcStruct vtable is exactly {deleting dtor, Serialize},
        so this is a cheap, specific shape test and keeps string operands and
        stack scratch out of the switch decoder.
        """
        try:
            a, b = struct.unpack("<QQ", self.pe.get_data(rva, 16))
        except Exception:
            return False
        lo, hi = self.base + self.tbase, self.base + self.tbase + len(self.traw)
        return lo <= a < hi and lo <= b < hi

    def disasm(self, start, end):
        code = self.traw[start - self.tbase:end - self.tbase]
        return list(self.cs.disasm(code, self.base + start))

    def rip_target(self, ins):
        for op in ins.operands:
            if op.type == CS_OP_MEM and op.mem.base == X86_REG_RIP:
                return (ins.address + ins.size + op.mem.disp) - self.base
        return None


def component_names(im):
    """componentId -> class name, from the 180-entry dispatcher at 0x4ba22cc."""
    if im.pe.get_data(0xac2e40, 3) != bytes.fromhex("b0a9c3"):
        raise ValueError("unsupported PlayerComponent type getter")
    names = []
    for index in range(180):
        target, = struct.unpack("<I", im.pe.get_data(0x4ba22cc + index * 4, 4))
        code = im.pe.get_data(target, 8)
        if code[:3] != bytes.fromhex("488d05") or code[7:] != b"\xc3":
            raise ValueError("unsupported component-name dispatcher")
        relative, = struct.unpack_from("<i", code, 3)
        names.append(im.pe.get_data(target + 7 + relative, 128)
                     .split(b"\0", 1)[0].decode("ascii"))
    if names[58] != "GameModeComponent" or names[169] != "PlayerComponent":
        raise ValueError("unsupported component table")
    return names


# ---- RPC payload structs ------------------------------------------------
SERIALIZE = re.compile(
    rb"([A-Za-z_][A-Za-z0-9_]{2,60})::([A-Za-z_][A-Za-z0-9_]{2,60})"
    rb"RpcStruct::Serialize\x00")


def rpc_structs(im):
    """vtable RVA -> {component, name, fields, serialize}.

    Located from the generated assert inside each `Serialize`, so the name is
    the compiler's, not a guess. The vtable is found by looking for the 8-byte
    slot that points at `Serialize`; that slot is vtable+8 in every generated
    struct (slot 0 is the deleting destructor).
    """
    out = {}
    for m in SERIALIZE.finditer(im.img):
        comp, name = m.group(1).decode(), m.group(2).decode()
        rva = im.pe.get_rva_from_offset(m.start())
        for site in im.lea.get(rva, []):
            frag = im.fragment(site)
            if frag is None:
                continue
            fields = []
            for ins in im.disasm(*frag):
                if ins.mnemonic != "lea":
                    continue
                t = im.rip_target(ins)
                s = im.string_at(t) if t is not None else None
                if s and not NOISE.match(s) and "::" not in s and "\\" not in s:
                    if s not in fields:
                        fields.append(s)
            for slot in im.ptr.get(frag[0], []):
                out[slot - 8] = {"component": comp, "name": name,
                                 "fields": fields, "serialize": frag[0],
                                 "vtable": slot - 8}
    return out


# ---- inbound dispatch ---------------------------------------------------
def handle_rpc_fragment(im, component, role):
    sym = f"{component}::{role}::HandleRpcMessage"
    rva = im.find_string(sym)
    if rva is None:
        return None, sym
    for site in im.lea.get(rva, []):
        frag = im.fragment(site)
        if not frag:
            continue
        # A big generated switch is split into several .pdata chunks and the
        # assert that names it can land in a later one. Walk back until the
        # range contains the `movzx reg, byte [msg + 9]` that starts the
        # switch, so the decoder always sees the whole function.
        start, end = frag
        for _ in range(6):
            if any(i.mnemonic in ("movzx", "mov") and len(i.operands) == 2
                   and i.operands[1].type == CS_OP_MEM
                   and i.operands[1].mem.disp == RPC_TYPE_OFFSET
                   and i.operands[1].size == 1
                   for i in im.disasm(start, end)):
                return (start, end), sym
            j = bisect.bisect_left(im.frag_starts, start) - 1
            if j < 0:
                break
            start = im.frags[j][0]
        return frag, sym
    return None, sym


def decode_switch(im, frag, structs):
    """Recover {rpc id: vtable RVA} and the accepted role byte.

    The generated switch keeps the RPC type in one register and narrows it with
    `test`/`cmp`/`sub`+`je`. This walks the block graph carrying a symbolic
    `value = rpcId - bias` per register, which is exactly the shape the code
    generator emits, and refuses to guess anything else: an instruction that
    clobbers the carrier in an unmodelled way drops that register.
    """
    start, end = frag
    ins_list = im.disasm(start, end)
    by_rva = {i.address - im.base: n for n, i in enumerate(ins_list)}
    roles, found, unresolved = set(), {}, []
    msg_base = [None]          # register that holds the RPC message pointer
    bound = [None]             # last `cmp id, N / ja` upper bound

    # canonical 64-bit name so eax/rax/al alias
    def canon(rname):
        rname = rname.lower()
        table = {"al": "rax", "eax": "rax", "rax": "rax", "bl": "rbx", "ebx": "rbx",
                 "cl": "rcx", "ecx": "rcx", "rcx": "rcx", "dl": "rdx", "edx": "rdx",
                 "rdx": "rdx", "sil": "rsi", "esi": "rsi", "rsi": "rsi",
                 "dil": "rdi", "edi": "rdi", "rdi": "rdi", "bpl": "rbp",
                 "ebp": "rbp", "rbp": "rbp"}
        if rname in table:
            return table[rname]
        m = re.match(r"^r(\d+)[dwb]?$", rname)
        if m:
            return "r" + m.group(1)
        return rname

    visited = set()
    work = [(0, {})]           # (instruction index, {reg: bias})
    while work:
        pos, env = work.pop()
        key = (pos, tuple(sorted(env.items())))
        if key in visited:
            continue
        visited.add(key)
        n = pos
        while n < len(ins_list):
            ins = ins_list[n]
            env = dict(env)
            mn, ops = ins.mnemonic, ins.operands
            # pick up the carrier: movzx r, byte [r8 + 9]
            if mn in ("movzx", "mov") and len(ops) == 2 and ops[0].type == CS_OP_REG \
                    and ops[1].type == CS_OP_MEM and ops[1].mem.disp == RPC_TYPE_OFFSET \
                    and ops[1].mem.base != 0 and ops[1].mem.index == 0 \
                    and ops[1].size == 1:
                env[canon(ins.reg_name(ops[0].reg))] = 0
                msg_base[0] = canon(ins.reg_name(ops[1].mem.base))
                n += 1
                continue
            if mn in ("movzx", "mov") and len(ops) == 2 and ops[0].type == CS_OP_REG \
                    and ops[1].type == CS_OP_REG:
                src = canon(ins.reg_name(ops[1].reg))
                dst = canon(ins.reg_name(ops[0].reg))
                if src in env:
                    env[dst] = env[src]
                else:
                    env.pop(dst, None)
                n += 1
                continue
            if mn == "sub" and len(ops) == 2 and ops[0].type == CS_OP_REG \
                    and ops[1].type == CS_OP_IMM:
                r = canon(ins.reg_name(ops[0].reg))
                if r in env:
                    env[r] += ops[1].imm
                    # `sub reg, imm` sets the flags itself; the generated chain
                    # is `sub r,1 / je BLOCK`, i.e. "is the id this one?".
                    nxt = ins_list[n + 1] if n + 1 < len(ins_list) else None
                    if nxt is not None and nxt.mnemonic in ("je", "jne") \
                            and nxt.operands[0].type == CS_OP_IMM:
                        dest = by_rva.get(nxt.operands[0].imm - im.base)
                        after = n + 2
                        if nxt.mnemonic == "je":
                            if dest is not None:
                                work.append((dest, {"#": env[r]}))
                            work.append((after, env))
                        else:
                            if dest is not None:
                                work.append((dest, env))
                            work.append((after, {"#": env[r]}))
                        break
                n += 1
                continue
            # jump table: `cmp id,N / ja FAIL / lea r9,[rip-X] (=imagebase) /
            # mov edx,[r9 + id*4 + TABLE] / add rdx,r9 / jmp rdx`. The table
            # holds one image-relative RVA per case, 0..N.
            if mn == "mov" and len(ops) == 2 and ops[0].type == CS_OP_REG \
                    and ops[1].type == CS_OP_MEM and ops[1].mem.index != 0 \
                    and ops[1].mem.scale == 4 and ops[1].mem.disp > 0x1000:
                iv = canon(ins.reg_name(ops[1].mem.index))
                if iv in env and bound[0] is not None:
                    table = ops[1].mem.disp
                    for case in range(bound[0] + 1):
                        try:
                            off, = struct.unpack("<I", im.pe.get_data(table + case * 4, 4))
                        except Exception:
                            continue
                        nx = by_rva.get(off)
                        if nx is not None:
                            work.append((nx, {"#": env[iv] + case}))
                    break
            if mn == "cmp" and len(ops) == 2 and ops[0].type == CS_OP_REG \
                    and ops[1].type == CS_OP_IMM \
                    and canon(ins.reg_name(ops[0].reg)) in env \
                    and n + 1 < len(ins_list) and ins_list[n + 1].mnemonic == "ja":
                bound[0] = ops[1].imm
                n += 2
                continue
            if mn in ("test", "cmp") and len(ops) == 2 and ops[0].type == CS_OP_REG:
                r = canon(ins.reg_name(ops[0].reg))
                if r in env:
                    if mn == "test" and ops[1].type == CS_OP_REG \
                            and canon(ins.reg_name(ops[1].reg)) == r:
                        target_value = env[r]
                    elif mn == "cmp" and ops[1].type == CS_OP_IMM:
                        target_value = env[r] + ops[1].imm
                    else:
                        n += 1
                        continue
                    nxt = ins_list[n + 1] if n + 1 < len(ins_list) else None
                    if nxt is not None and nxt.mnemonic in ("je", "jne"):
                        dest = nxt.operands[0].imm - im.base
                        taken = by_rva.get(dest)
                        after = n + 2
                        if nxt.mnemonic == "je":
                            if taken is not None:
                                work.append((taken, {"#": target_value}))
                            work.append((after, env))
                        else:
                            if taken is not None:
                                work.append((taken, env))
                            work.append((after, {"#": target_value}))
                        break
                n += 1
                continue
            # role guard: cmp byte [msg + 0], N
            if mn == "cmp" and len(ops) == 2 and ops[0].type == CS_OP_MEM \
                    and ops[0].mem.disp == ROLE_OFFSET and ops[0].mem.base != 0 \
                    and ops[0].mem.index == 0 and ops[0].size == 1 \
                    and ops[1].type == CS_OP_IMM:
                roles.add((canon(ins.reg_name(ops[0].mem.base)), ops[1].imm))
                n += 1
                continue
            # the payload struct: lea r, [rip + vtable]
            if mn == "lea" and "#" in env:
                t = im.rip_target(ins)
                if t is not None and (t in structs or im.is_vtable(t)):
                    if env["#"] not in found:
                        found[env["#"]] = t
                    break
            if mn in ("jmp",):
                dest = ins.operands[0].imm - im.base if ins.operands[0].type == CS_OP_IMM else None
                nx = by_rva.get(dest)
                if nx is None:
                    break
                n = nx
                continue
            if mn.startswith("j"):
                if ins.operands[0].type == CS_OP_IMM:
                    nx = by_rva.get(ins.operands[0].imm - im.base)
                    if nx is not None:
                        work.append((nx, env))
                n += 1
                continue
            if mn == "ret":
                break
            n += 1
    # Only the compare against the register that actually holds the message is
    # a role guard; `cmp byte [rcx], 5` on a log-channel pointer is not.
    kept = sorted({imm for reg, imm in roles if reg == msg_base[0]})
    return kept, found, unresolved


def dispatch(im, show_all=False):
    names = component_names(im)
    structs = rpc_structs(im)
    rows = []
    for cid, comp in enumerate(names):
        frag, sym = None, None
        for role in ("Client", "ClientBase"):
            frag, sym = handle_rpc_fragment(im, comp, role)
            if frag:
                break
        if frag is None:
            rows.append({"id": cid, "component": comp, "handler": None,
                         "rpcs": [], "note": "no HandleRpcMessage located"})
            continue
        roles, found, _ = decode_switch(im, frag, structs)
        rpcs = []
        for rpc_id in sorted(found):
            vt = found[rpc_id]
            s = structs.get(vt)
            if s is None:
                rpcs.append({"rpc": rpc_id, "name": "<unnamed, no-field struct>",
                             "fields": [], "vtable": vt, "serialize": None})
            else:
                rpcs.append({"rpc": rpc_id, "name": s["name"], "fields": s["fields"],
                             "vtable": s["vtable"], "serialize": s["serialize"]})
        declared = sorted({s["name"] for s in structs.values()
                           if s["component"] == comp})
        rows.append({"id": cid, "component": comp, "handler": frag[0],
                     "handler_end": frag[1], "roles": roles,
                     "size": frag[1] - frag[0], "rpcs": rpcs,
                     "declared_structs": declared})
    if not show_all:
        rows = [r for r in rows if r.get("rpcs")]
    return rows


# ---- replicated properties ---------------------------------------------
FUNCSIG = re.compile(
    rb"bool __cdecl ([A-Za-z_][A-Za-z0-9_]{2,60})::(Client|ClientBase)"
    rb"::SerializeAuthorityTo(Client|Autonomous)Properties\(")


def whole_function(im, rva):
    """Extend `rva`'s .pdata chunk over every abutting chunk.

    A big generated serializer is split into several chunks; abutting chunks
    (previous end == next start) belong to the same function, so this recovers
    the full body without needing symbols.
    """
    frag = im.fragment(rva)
    if frag is None:
        return None
    start, end = frag
    i = bisect.bisect_left(im.frag_starts, start)
    while i > 0 and im.frags[i - 1][1] == start:
        i -= 1
        start = im.frags[i][0]
    j = bisect.bisect_left(im.frag_starts, end)
    while j < len(im.frags) and im.frags[j][0] == end:
        end = im.frags[j][1]
        j += 1
    return start, end


def properties(im, wanted=None):
    """component -> direction -> ordered replicated property names.

    Each generated `SerializeAuthorityTo*Properties` walks the component's
    replication records in declaration order, naming every one to the
    serializer as a string literal. Reading those `lea` operands in address
    order therefore recovers the record order the compressed traversal
    (`docs/13`, `shard_stub.build_replication_record`) has to match.
    """
    skip = {"UNKNOWN", "Serializing component that was not attached",
            "SerializableFixedSizeVector", "SerializableFixedSizeArray"}
    out = {}
    for m in FUNCSIG.finditer(im.img):
        comp, _role, direction = (g.decode() for g in m.groups())
        if wanted and comp not in wanted:
            continue
        rva = im.pe.get_rva_from_offset(m.start())
        for site in im.lea.get(rva, []):
            frag = whole_function(im, site)
            if frag is None:
                continue
            names, containers = [], []
            for ins in im.disasm(*frag):
                if ins.mnemonic != "lea":
                    continue
                t = im.rip_target(ins)
                s = im.string_at(t) if t is None else im.string_at(t)
                # A record name is a bare identifier. Everything else in these
                # functions is an assert message, a path or a type name.
                if not s or len(s) > 60 or not re.fullmatch(r"[A-Za-z_]\w*", s):
                    continue
                if s in ("SerializableFixedSizeVector", "SerializableFixedSizeArray"):
                    containers.append(s)
                    continue
                if s in skip or "::" in s:
                    continue
                if names and names[-1] == s:
                    names[-1] = s          # a two-slot struct names itself twice
                    continue
                names.append(s)
            out.setdefault(comp, {})[direction] = {"records": names,
                                                   "function": frag}
            break
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--components", action="store_true")
    ap.add_argument("--dispatch", action="store_true")
    ap.add_argument("--structs", action="store_true")
    ap.add_argument("--properties", nargs="*", metavar="COMPONENT",
                    help="ordered replicated property records, all or by name")
    ap.add_argument("--all", action="store_true",
                    help="with --dispatch, also list components with no inbound RPC")
    ap.add_argument("--json")
    args = ap.parse_args()
    im = Image()
    if args.components:
        for i, n in enumerate(component_names(im)):
            print(f"{i:3d}  {n}")
    if args.structs:
        for vt, s in sorted(rpc_structs(im).items()):
            print(f"{vt:#010x}  {s['component']}::{s['name']}  "
                  f"serialize={s['serialize']:#x}  fields={s['fields']}")
    if args.properties is not None:
        ids = {n: i for i, n in enumerate(component_names(im))}
        table = properties(im, set(args.properties) or None)
        for comp in sorted(table, key=lambda c: ids.get(c, 999)):
            for direction, info in sorted(table[comp].items()):
                if not info["records"]:
                    continue
                print(f"{ids.get(comp, '?'):>4}  {comp}  AuthorityTo{direction}"
                      f"  fn={info['function'][0]:#x}")
                for n, rec in enumerate(info["records"], start=1):
                    print(f"          record {n}: {rec}")
    if args.dispatch:
        rows = dispatch(im, show_all=args.all)
        for r in rows:
            if r.get("handler") is None:
                print(f"{r['id']:3d}  {r['component']:44s}  {r['note']}")
                continue
            tag = f"roles={r['roles']} handler={r['handler']:#x} size={r['size']:#x}"
            print(f"{r['id']:3d}  {r['component']:44s}  {tag}")
            for x in r["rpcs"]:
                print(f"        rpc {x['rpc']:2d}  {x['name']:44s} {x['fields']}")
            missing = [n for n in r["declared_structs"]
                       if n not in {x["name"] for x in r["rpcs"]}]
            if missing:
                print(f"        (declared, not inbound: {', '.join(missing)})")
        if args.json:
            with open(args.json, "w") as fh:
                json.dump(rows, fh, indent=1)
            print(f"wrote {args.json}")
    if not (args.components or args.dispatch or args.structs):
        print(__doc__)


if __name__ == "__main__":
    sys.exit(main())
