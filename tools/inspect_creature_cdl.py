#!/usr/bin/env python3
"""Read-only summariser for Crucible .cdl gameplay scripts.

A .cdl is a binary ObjectStream v3 serialisation of a node graph (the
engine's visual scripting language): an enter trigger event, a set of exit
trigger events, script-portion nodes, conditions and the connections between
their slots. This prints the graph in a form a human can read: node names,
each node's variables with hidden-literal values resolved, designer comments
and the execution edges.

Usage:
  python3 tools/inspect_creature_cdl.py misc.pak cdl/creatures/spitter/spitter_attack.cdl
  python3 tools/inspect_creature_cdl.py misc.pak <asset> --json out.json

Nothing under the Steam install is written or executed.
"""
import argparse
import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import objectstream  # noqa: E402
from inspect_creature_ai import (decode_value, load_asset,  # noqa: E402
                                 name_dictionary, _preferred)

LITERAL_BLACKBOARDS = ('m_hiddenLiteralsBlackboard', 'm_userBlackboard',
                       'm_generatedBlackboard', 'm_usedMemberVariablesBlackboard',
                       'm_blackboard')


def to_dict(node, names):
    """Nested dict/list view of a parsed ObjectStream element."""
    label = _preferred(names.get(node.name)) or f'crc:{node.name:08x}'
    kind, value = decode_value(node)
    if not node.children:
        return label, value
    body = {}
    for child in node.children:
        key, sub = to_dict(child, names)
        if key in body:
            if not isinstance(body[key], list) or key != 'Element':
                body[key] = [body[key]] if not isinstance(body[key], list) else body[key]
            body[key].append(sub)
        else:
            body[key] = [sub] if key == 'Element' else sub
    if value is not None:
        body['@value'] = value
    return label, body


def as_list(value):
    """Containers serialise as one element whose children are all 'Element'."""
    if value is None:
        return []
    if isinstance(value, dict) and set(value) <= {'Element', '@value'}:
        return as_list(value.get('Element'))
    return value if isinstance(value, list) else [value]


def collect_literals(doc):
    """variableId -> literal value, from every blackboard in the script."""
    table = {}

    def visit(obj):
        if isinstance(obj, dict):
            entry = obj.get('m_variable')
            if isinstance(entry, dict):
                ident = entry.get('m_variableId')
                value = entry.get('m_Value')
                if isinstance(value, dict):
                    value = value.get('m_data')
                if ident is not None:
                    table[ident] = value
            for sub in obj.values():
                visit(sub)
        elif isinstance(obj, list):
            for sub in obj:
                visit(sub)

    for name in LITERAL_BLACKBOARDS:
        visit(doc.get(name))
    return table


def variable_summary(var, literals):
    if not isinstance(var, dict):
        return None
    name = var.get('m_displayName') or var.get('m_typeInfo', {}).get('m_typeName')
    value = var.get('m_Value')
    data = value.get('m_data') if isinstance(value, dict) else value
    resolved = None
    if isinstance(data, dict):
        ident = data.get('m_variableId')
        resolved = literals.get(ident)
        if resolved is None:
            resolved = data.get('m_name')
    else:
        resolved = data
    target = var.get('m_variableTarget', {})
    symlink = target.get('m_symlink', {}).get('m_name') if isinstance(target, dict) else None
    return {'name': name, 'value': resolved, 'target': symlink,
            'type': var.get('m_typeInfo', {}).get('m_typeName')}


def node_summary(node, literals, kind):
    base = node.get('BaseClass1', {})
    if 'm_nodeName' not in node and isinstance(base, dict) and 'm_nodeName' in base:
        node = {**base, **{k: v for k, v in node.items() if k != 'BaseClass1'}}
        base = node.get('BaseClass1', {})
    instance = base.get('m_InstanceId') if isinstance(base, dict) else None
    variables = [variable_summary(v, literals)
                 for v in as_list(node.get('m_variables'))]
    event = node.get('m_eventVariable')
    if isinstance(event, dict):
        variables.append(variable_summary(event, literals))
    return {'kind': kind, 'instance': instance,
            'name': node.get('m_nodeName') or
                    (event or {}).get('m_typeInfo', {}).get('m_typeName'),
            'comment': node.get('m_MetaData', {}).get('m_comment')
                       if isinstance(node.get('m_MetaData'), dict) else None,
            'variables': [v for v in variables if v]}


def summarise(doc, literals):
    nodes = []
    trigger = doc.get('m_enterTrigger')
    if trigger:
        nodes.append(node_summary(trigger, literals, 'enter'))
    for exit_node in as_list(doc.get('m_exitTriggers')):
        nodes.append(node_summary(exit_node, literals, 'exit'))
    for portion in as_list(doc.get('m_scriptPortionsData')):
        body = portion.get('Value2', {}) if isinstance(portion, dict) else {}
        for item in as_list(body.get('m_nodes')):
            nodes.append(node_summary(item, literals, 'node'))
        for item in as_list(body.get('m_conditions')):
            nodes.append(node_summary(item, literals, 'condition'))
    for item in as_list(doc.get('m_conditions')):
        nodes.append(node_summary(item, literals, 'condition'))

    slot_owner = {}
    for portion in as_list(doc.get('m_scriptPortionsData')):
        body = portion.get('Value2', {}) if isinstance(portion, dict) else {}
        groups = as_list(body.get('m_nodes')) + as_list(body.get('m_conditions'))
        groups += as_list(doc.get('m_conditions'))
        for item in groups:
            base = item.get('BaseClass1', {})
            holder = item if 'm_slots' in item else base
            owner = (base.get('m_InstanceId') if isinstance(base, dict) else None)
            if isinstance(holder, dict) and 'm_slots' in holder:
                if owner is None and isinstance(holder.get('BaseClass1'), dict):
                    owner = holder['BaseClass1'].get('m_InstanceId')
                for slot in as_list(holder.get('m_slots')):
                    ident = slot.get('BaseClass1', {}).get('m_InstanceId')
                    slot_owner[ident] = (owner, slot.get('m_slotType'))

    connections = list(as_list(doc.get('m_connections')))
    for portion in as_list(doc.get('m_scriptPortionsData')):
        body = portion.get('Value2', {}) if isinstance(portion, dict) else {}
        connections += as_list(body.get('m_connections'))
    edges = []
    for conn in connections:
        src, dst = conn.get('m_source', {}), conn.get('m_target', {})
        edges.append({'from_node': src.get('m_nodeInstanceId'),
                      'from_slot': src.get('m_slotInstanceId'),
                      'to_node': dst.get('m_nodeInstanceId'),
                      'to_slot': dst.get('m_slotInstanceId')})
    comments = [c.get('m_comment') for c in as_list(doc.get('m_commentNodes'))
                if isinstance(c, dict)]
    return {'nodes': nodes, 'edges': edges, 'comments': comments}


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('pak')
    parser.add_argument('asset')
    parser.add_argument('--json')
    args = parser.parse_args()

    data = load_asset(args.pak, args.asset)
    names = name_dictionary()
    roots = objectstream.parse(data)
    _, doc = to_dict(roots[0], names)
    literals = collect_literals(doc)
    summary = summarise(doc, literals)
    digest = hashlib.sha256(data).hexdigest()

    print(f'# {args.asset} sha256={digest} bytes={len(data)}')
    for comment in summary['comments']:
        print(f'# comment: {comment}')
    by_instance = {}
    for node in summary['nodes']:
        by_instance[node['instance']] = node['name']
        line = f"[{node['instance']}] {node['kind']:9s} {node['name']}"
        if node['comment']:
            line += f"   # {node['comment']}"
        print(line)
        for var in node['variables']:
            bits = [f"{var['name']}"]
            if var['value'] is not None:
                bits.append(f"= {var['value']}")
            if var['target'] and var['target'] != '@Self':
                bits.append(f"on {var['target']}")
            print('      ' + ' '.join(str(b) for b in bits))
    print('# edges (node instance -> node instance)')
    for edge in summary['edges']:
        source = by_instance.get(edge['from_node'], '?')
        target = by_instance.get(edge['to_node'], '?')
        print(f"  [{edge['from_node']}] {source}:{edge['from_slot']}"
              f" -> [{edge['to_node']}] {target}:{edge['to_slot']}")
    if args.json:
        Path(args.json).write_text(json.dumps(
            {'asset': args.asset, 'sha256': digest, **summary}, indent=1))
    return 0


if __name__ == '__main__':
    sys.exit(main())
