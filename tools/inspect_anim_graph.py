#!/usr/bin/env python3
"""Read-only inspector for Crucible EMotionFX `.animgraph` assets.

The installed `.animgraph` files are **XML** ObjectStream v3 documents, not the
EMotionFX native binary chunk format, so the whole authored animation graph is
readable without any reverse engineering.  This tool flattens the ObjectStream
`Class` tree and prints:

  --params      the ordered parameter list with type, default, min and max
  --nodes       every AnimGraphNode with its class and parent
  --node NAME   one node's full field dump plus its incoming connections
  --blendspace  every BlendSpace1DNode / BlendSpace2DNode: its evaluators, its
                motion list, and which parameter feeds each axis
  --grep REGEX  every node whose name matches

Nothing under the Steam install is written or executed; the game is never
launched.

Usage:
  python3 tools/inspect_anim_graph.py zero2.pak \
      animations/characters/player/marine/emfx/marine.animgraph --params
  python3 tools/inspect_anim_graph.py FILE.animgraph --blendspace
"""
import argparse
import hashlib
import os
import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pak  # noqa: E402

INSTALL = Path(os.environ.get(
    'CRUCIBLE_INSTALL',
    os.path.expanduser('~/.local/share/Steam/steamapps/common/Crucible')))


def load(pak_name, asset):
    """Return (bytes, sha256). `pak_name` may be None for a plain file."""
    if asset is None:
        blob = Path(pak_name).read_bytes()
    else:
        path = Path(pak_name)
        if not path.exists():
            path = INSTALL / pak_name
        entries = pak.load(str(path))
        match = [e for e in entries if e['name'].decode('utf-8') == asset]
        if not match:
            raise SystemExit(f'{asset} not in {path}')
        blob = pak.entry_bytes(match[0])
    return blob, hashlib.sha256(blob).hexdigest()


def field(node, name):
    """Direct child `Class` element with field== name."""
    for child in node:
        if child.get('field') == name:
            return child
    return None


def value(node, name, default=None):
    child = field(node, name)
    return default if child is None else child.get('value', default)


def base_chain(node):
    """Walk BaseClass1 down to the innermost class, yielding each level."""
    cur = node
    while cur is not None:
        yield cur
        cur = field(cur, 'BaseClass1')


def param_name(node):
    """Parameter name lives on the innermost `Parameter` BaseClass1."""
    for level in base_chain(node):
        if level.get('name') == 'Parameter':
            return value(level, 'name', ''), value(level, 'description', '')
    return None, None


def param_bits(node):
    """(default, min, max) searched across the whole BaseClass1 chain."""
    out = {}
    for level in base_chain(node):
        for key in ('defaultValue', 'minValue', 'maxValue',
                    'hasMinValue', 'hasMaxValue'):
            if key not in out:
                got = value(level, key)
                if got is not None:
                    out[key] = got
    return out


def parameters(root):
    """Ordered flat parameter list, depth-first through GroupParameter."""
    out = []

    def walk(group, path):
        children = field(group, 'childParameters')
        if children is None:
            return
        for element in children:
            name, description = param_name(element)
            cls = element.get('name')
            if cls == 'GroupParameter':
                walk(element, path + [name])
                continue
            bits = param_bits(element)
            out.append({'index': len(out), 'name': name, 'class': cls,
                        'group': '/'.join(p for p in path if p),
                        'description': description,
                        'default': bits.get('defaultValue'),
                        'min': bits.get('minValue') if bits.get('hasMinValue') == 'true' else None,
                        'max': bits.get('maxValue') if bits.get('hasMaxValue') == 'true' else None})

    walk(field(root, 'rootGroupParameter'), [])
    return out


def node_base(element):
    """The `AnimGraphNode` BaseClass1 level of a node element, or None.

    Every AnimGraphNode subclass stores id / name / posX / childNodes /
    connections on this one level, reached through a BaseClass1 chain.
    """
    for level in base_chain(element):
        if level.get('name') == 'AnimGraphNode':
            return level
    return None


def all_nodes(root):
    """Every AnimGraphNode in the graph, in document order.

    Nodes nest through the `childNodes` vector on their AnimGraphNode level, so
    the enclosing node element is the parent.
    """
    out = []

    def walk(element, parent):
        base = node_base(element)
        if base is None:
            return
        name = value(base, 'name')
        out.append({'id': value(base, 'id'), 'name': name,
                    'class': element.get('name'), 'parent': parent,
                    'element': element, 'base': base})
        children = field(base, 'childNodes')
        if children is not None:
            for child in children:
                walk(child, name)

    def descend(element, parent):
        """Find node elements anywhere below `element` (vectors, fields)."""
        if node_base(element) is not None:
            walk(element, parent)
            return
        for child in element:
            descend(child, parent)

    descend(root, None)
    return out


def connections(node):
    """Incoming BlendTreeConnections of a node dict from `all_nodes`."""
    out = []
    conns = field(node['base'], 'connections')
    if conns is None:
        return out
    for conn in conns:
        out.append({'source_id': value(conn, 'sourceNodeId'),
                    'source_port': value(conn, 'sourcePort'),
                    'target_port': value(conn, 'targetPort')})
    return out


def leaf_fields(element, prefix='', depth=0, out=None):
    if out is None:
        out = []
    if depth > 6:
        return out
    for child in element:
        name = child.get('name')
        fld = child.get('field') or ''
        if child.get('value') is not None:
            out.append((prefix + fld, name, child.get('value')))
        elif name == 'BaseClass1':
            leaf_fields(child, prefix, depth + 1, out)
        else:
            leaf_fields(child, prefix + fld + '.', depth + 1, out)
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('pak')
    ap.add_argument('asset', nargs='?')
    ap.add_argument('--params', action='store_true')
    ap.add_argument('--nodes', action='store_true')
    ap.add_argument('--node', action='append', default=[])
    ap.add_argument('--blendspace', action='store_true')
    ap.add_argument('--grep')
    ap.add_argument('--param-users', help='nodes referencing this parameter name')
    args = ap.parse_args()

    blob, digest = load(args.pak, args.asset)
    print(f'# {args.asset or args.pak} sha256={digest} bytes={len(blob)}')
    stream = ET.fromstring(blob.decode('utf-8', 'replace'))
    # <ObjectStream> wraps exactly one top-level Class; for .animgraph that is
    # AnimGraph, for .motionset it is MotionSet.
    root = stream if stream.tag != 'ObjectStream' else stream[0]

    if args.params:
        print(f'{"idx":>3} {"name":34} {"class":24} {"default":24} range')
        for p in parameters(root):
            rng = ''
            if p['min'] is not None or p['max'] is not None:
                rng = f'[{p["min"]} .. {p["max"]}]'
            print(f'{p["index"]:>3} {p["name"] or "":34} {p["class"]:24} '
                  f'{str(p["default"]):24} {rng}'
                  + (f'   group={p["group"]}' if p['group'] else ''))
        return

    nodes = all_nodes(root)
    by_id = {n['id']: n for n in nodes}

    def describe(node):
        print(f'\n=== {node["class"]} "{node["name"]}"  (parent {node["parent"]}, '
              f'id {node["id"]})')
        for fld, cls, val in leaf_fields(node['element']):
            if fld in ('id', 'posX', 'posY', 'visualizeColor', 'isCollapsed',
                       'isVisEnabled', 'name'):
                continue
            print(f'    {fld:46} {cls:18} {val}')
        for c in connections(node):
            src = by_id.get(c['source_id'])
            label = f'{src["class"]} "{src["name"]}"' if src else c['source_id']
            print(f'    input port {c["target_port"]:>3}  <-  {label} '
                  f'(out port {c["source_port"]})')

    if args.nodes or args.grep:
        pattern = re.compile(args.grep, re.I) if args.grep else None
        for n in nodes:
            if pattern and not pattern.search(f'{n["name"]} {n["class"]}'):
                continue
            print(f'{n["class"]:32} {str(n["name"]):48} parent={n["parent"]}')
        return

    if args.param_users:
        for n in nodes:
            for fld, cls, val in leaf_fields(n['element']):
                if val == args.param_users:
                    print(f'{n["class"]:30} {str(n["name"]):44} {fld} = {val}'
                          f'   parent={n["parent"]}')
        return

    if args.blendspace:
        for n in nodes:
            if 'BlendSpace' in n['class']:
                describe(n)
        return

    for want in args.node:
        for n in nodes:
            if n['name'] == want:
                describe(n)
    if not args.node:
        ap.print_help()


if __name__ == '__main__':
    main()
