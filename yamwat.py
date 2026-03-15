#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.11"
# dependencies = [
#   "pyyaml",
# ]
# ///
"""
yamwat — YAML to WAT compiler
usage: yamwat.py [definitions.yaml ...] module.yaml [module2.yaml ...]
"""

import sys
import re
import yaml


# ---------------------------------------------------------------------------
# !raw tag — pass string through to WAT verbatim
# ---------------------------------------------------------------------------

class RawString(str):
    pass

def raw_constructor(loader, node):
    return RawString(loader.construct_scalar(node))

yaml.add_constructor('!raw', raw_constructor, Loader=yaml.SafeLoader)


# ---------------------------------------------------------------------------
# file loading
# ---------------------------------------------------------------------------

def load_files(paths):
    """Read all files, separate definitions blocks from module blocks."""
    definition_texts = []
    module_texts = []

    for path in paths:
        text = open(path).read()
        defs, mods = split_file(text)
        if defs:
            definition_texts.append(defs)
        module_texts.extend(mods)

    return definition_texts, module_texts


def split_file(text):
    """
    Split a file into (definitions_text, [module_texts]).
    Uses text-level inspection so we never parse a definitions chunk in
    isolation — anchors stay intact for cross-document resolution.
    """
    docs = re.split(r'^---\s*$', text, flags=re.MULTILINE)
    docs = [d.strip() for d in docs if d.strip()]

    definitions_text = ""
    module_texts = []

    for doc in docs:
        keys = set(re.findall(r'^(\S+):', doc, re.MULTILINE))
        if 'definitions' in keys:
            definitions_text += doc + "\n"
        if 'module' in keys:
            module_texts.append(doc)

    return definitions_text, module_texts


# ---------------------------------------------------------------------------
# parsing — prepend definitions so anchors are in scope
# ---------------------------------------------------------------------------

def parse_module(definitions_text, module_text):
    """Parse a module document with definitions in scope."""
    return yaml.safe_load(definitions_text + "\n" + module_text)


# ---------------------------------------------------------------------------
# emission helpers
# ---------------------------------------------------------------------------

def indent(lines, n=2):
    pad = " " * n
    return [pad + l for l in lines]


# block/loop opcodes that require a matching end
BLOCK_OPS = {'block', 'loop'}


# ---------------------------------------------------------------------------
# section emitters
# ---------------------------------------------------------------------------

def emit_import(name, spec):
    module_name, field_name = spec['from']
    params = emit_params(spec.get('param', []))
    result = emit_result(spec.get('result'))
    type_str = "".join(params + result)
    return [f'(import "{module_name}" "{field_name}" (func {name}{type_str}))']


def emit_memory(name, spec):
    lines = [f'(memory {name} {spec["pages"]})']
    if spec.get('export'):
        lines.append(f'(export "{spec["export"]}" (memory {name}))')
    return lines


def emit_table(name, spec):
    reftype = spec.get('type', 'funcref')
    lines = [f'(table {name} {spec["size"]} {reftype})']
    if spec.get('export'):
        lines.append(f'(export "{spec["export"]}" (table {name}))')
    return lines


def emit_global(name, spec):
    typ = spec['type']
    mutable = spec.get('mutable', False)
    init = spec['init']
    type_str = f'(mut {typ})' if mutable else typ
    return [f'(global {name} {type_str} ({typ}.const {init}))']


def emit_data(segments):
    lines = []
    for seg in segments:
        offset = seg['offset']
        if 'string' in seg:
            escaped = seg['string'].encode('utf-8').decode('latin-1')
            escaped = escaped.replace('\\', '\\\\').replace('"', '\\"')
            lines.append(f'(data (i32.const {offset}) "{escaped}")')
        elif 'bytes' in seg:
            hex_str = ''.join(f'\\{b:02x}' for b in seg['bytes'])
            lines.append(f'(data (i32.const {offset}) "{hex_str}")')
    return lines


def emit_elem(spec):
    funcs = ' '.join(spec['funcs'])
    return [f'(elem (i32.const {spec["offset"]}) {funcs})']


def emit_type(name, spec):
    params = emit_params(spec.get('param', []))
    result = emit_result(spec.get('result'))
    return [f'(type {name} (func{"".join(params + result)}))']


def emit_start(func_name):
    return [f'(start {func_name})']


def emit_params(params):
    if not params:
        return []
    out = []
    for p in params:
        parts = str(p).split()
        if len(parts) == 2:
            out.append(f' (param {parts[0]} {parts[1]})')
        else:
            out.append(f' (param {parts[0]})')
    return out


def emit_result(result):
    if result is None:
        return []
    if isinstance(result, list):
        return [f' (result {" ".join(str(r) for r in result)})']
    return [f' (result {result})']


def emit_locals(locals_):
    if not locals_:
        return []
    out = []
    for l in locals_:
        parts = str(l).split()
        out.append(f'(local {parts[0]} {parts[1]})')
    return out


def emit_body(instructions):
    """
    Emit a flat list of instructions.

    block/loop require explicit `end` markers from the author — matching
    WAT's own model exactly.

    if mappings use structured then/else keys and an optional result type:
      - if:
          result: i32        # required when the if produces a value
          then: [...]
          else: [...]

    !raw strings pass through to WAT verbatim.
    """
    lines = []
    items = instructions if isinstance(instructions, list) else [instructions]

    for item in items:

        if isinstance(item, RawString):
            lines.append(item)

        elif isinstance(item, str):
            op = item.strip()
            opcode = op.split()[0]
            if opcode in BLOCK_OPS:
                lines.append(op)
            else:
                lines.append(op)

        elif isinstance(item, dict):
            if 'if' in item:
                spec = item['if']
                result = f' (result {spec["result"]})' if 'result' in spec else ''
                lines.append(f'if{result}')
                if 'then' in spec:
                    lines.append('then')
                    lines.extend(indent(emit_body(spec['then'])))
                if 'else' in spec:
                    lines.append('else')
                    lines.extend(indent(emit_body(spec['else'])))
                lines.append('end')
            else:
                for k, v in item.items():
                    lines.append(f'{k} {v}' if v is not None else k)

        else:
            lines.append(str(item))

    return lines


def emit_func(name, spec):
    export = spec.get('export')
    params = emit_params(spec.get('param', []))
    result = emit_result(spec.get('result'))
    locals_ = emit_locals(spec.get('local', []))

    lines = [f'(func {name}{"".join(params + result)}']
    for l in locals_:
        lines.append(f'  {l}')
    lines.extend(indent(emit_body(spec.get('body', []))))
    lines.append(')')

    if export:
        lines.append(f'(export "{export}" (func {name}))')

    return lines


# ---------------------------------------------------------------------------
# module emitter — top level
# ---------------------------------------------------------------------------

# WAT requires a specific declaration order. We bucket items in one pass
# then emit each bucket in order.
SECTION_ORDER = ['type', 'import', 'memory', 'table', 'global', 'data', 'elem', 'func', 'start']

def emit_module(doc):
    name = doc['module']
    buckets = {s: [] for s in SECTION_ORDER}

    for key, val in doc.items():
        if key in ('definitions', 'module'):
            continue
        elif key == 'data':
            buckets['data'].extend(emit_data(val))
        elif key == 'start':
            buckets['start'].extend(emit_start(val))
        elif key.startswith('type '):
            buckets['type'].extend(emit_type(key.split(' ', 1)[1], val))
        elif key.startswith('import '):
            buckets['import'].extend(emit_import(key.split(' ', 1)[1], val))
        elif key.startswith('memory '):
            buckets['memory'].extend(emit_memory(key.split(' ', 1)[1], val))
        elif key.startswith('table '):
            buckets['table'].extend(emit_table(key.split(' ', 1)[1], val))
        elif key.startswith('global '):
            buckets['global'].extend(emit_global(key.split(' ', 1)[1], val))
        elif key.startswith('elem '):
            buckets['elem'].extend(emit_elem(val))
        elif key.startswith('func '):
            func_id = key.split(' ', 1)[1].split(' ')[0]
            buckets['func'].extend(emit_func(func_id, val))

    lines = [f'(module {name}']
    for section in SECTION_ORDER:
        lines.extend(indent(buckets[section]))
    lines.append(')')
    return '\n'.join(lines)


# ---------------------------------------------------------------------------
# entry point
# ---------------------------------------------------------------------------

def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)

    paths = sys.argv[1:]
    definition_texts, module_texts = load_files(paths)
    preamble = '\n'.join(definition_texts)

    for module_text in module_texts:
        doc = parse_module(preamble, module_text)
        wat = emit_module(doc)
        mod_name = doc['module'].lstrip('$')
        out_path = f'{mod_name}.wat'
        open(out_path, 'w').write(wat)
        print(f'wrote {out_path}')


if __name__ == '__main__':
    main()
