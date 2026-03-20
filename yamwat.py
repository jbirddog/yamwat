#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.11"
# dependencies = [
#   "pyyaml",
# ]
# ///
"""
yamwat — YAML to WAT compiler
usage: yamwat.py <module.yaml> [module2.yaml ...]
"""

import sys
import os
import copy
import yaml


# ---------------------------------------------------------------------------
# !raw and !use tags
#
# Both are application-defined single-! tags resolved by the compiler,
# never by the YAML parser. The parser just hands us tagged nodes.
# ---------------------------------------------------------------------------

class RawString(str):
    pass

class UseNode:
    """Represents a !use tagged node before expansion."""
    def __init__(self, name, args=None):
        self.name = name    # macro name string
        self.args = args    # None | list (positional) | dict (named)

    def __repr__(self):
        return f"UseNode({self.name!r}, {self.args!r})"

def raw_constructor(loader, node):
    return RawString(loader.construct_scalar(node))

def use_constructor(loader, node):
    if isinstance(node, yaml.ScalarNode):
        # !use macro_name  — no-arg form
        return UseNode(loader.construct_scalar(node))
    elif isinstance(node, yaml.MappingNode):
        # !use {macro_name: [args]} or !use {macro_name: {k: v}}
        pairs = loader.construct_pairs(node, deep=True)
        if len(pairs) != 1:
            raise yaml.YAMLError(
                f"!use mapping must have exactly one key, got {len(pairs)}")
        name, args = pairs[0]
        return UseNode(name, args)
    else:
        raise yaml.YAMLError(f"!use applied to unexpected node type: {type(node)}")

yaml.add_constructor('!raw', raw_constructor, Loader=yaml.SafeLoader)
yaml.add_constructor('!use', use_constructor, Loader=yaml.SafeLoader)


# ---------------------------------------------------------------------------
# macro file loading
# ---------------------------------------------------------------------------

def load_macro_file(path):
    """
    Parse a macro file. Top-level keys are macro names; values are their
    expansion bodies. Returns a dict of name -> value.
    """
    try:
        text = open(path).read()
    except FileNotFoundError:
        raise SystemExit(f"error: include not found: {path}")
    try:
        doc = yaml.safe_load(text)
    except yaml.YAMLError as e:
        raise SystemExit(f"error: yaml parse failed in {path}:\n  {e}")
    if doc is None:
        return {}
    if not isinstance(doc, dict):
        raise SystemExit(
            f"error: macro file must be a mapping at the top level: {path}")
    if 'module' in doc:
        raise SystemExit(
            f"error: cannot include a module file: {path}")
    return doc


def build_macro_table(paths, base_dir):
    """
    Load each macro file in order, returning a combined macro table.
    Later files do not override earlier ones — module-local macros handle
    overrides separately by merging after this call.
    """
    table = {}
    for rel_path in paths:
        path = os.path.normpath(os.path.join(base_dir, rel_path))
        macros = load_macro_file(path)
        table.update(macros)
    return table


# ---------------------------------------------------------------------------
# !use expansion
# ---------------------------------------------------------------------------

def substitute(value, params, args_dict):
    """
    Recursively substitute parameter names with argument values throughout
    value. params is a list of param name strings (e.g. ['$post', '$post_id']).
    args_dict maps param name -> argument value string.
    """
    if isinstance(value, str) and not isinstance(value, RawString):
        for param, arg in args_dict.items():
            value = value.replace(param, arg)
        return value
    elif isinstance(value, RawString):
        result = str(value)
        for param, arg in args_dict.items():
            result = result.replace(param, arg)
        return RawString(result)
    elif isinstance(value, list):
        return [substitute(item, params, args_dict) for item in value]
    elif isinstance(value, dict):
        return {
            substitute(k, params, args_dict): substitute(v, params, args_dict)
            for k, v in value.items()
        }
    elif isinstance(value, UseNode):
        new_name = value.name
        for param, arg in args_dict.items():
            new_name = new_name.replace(param, arg)
        new_args = substitute(value.args, params, args_dict) \
            if value.args is not None else None
        return UseNode(new_name, new_args)
    else:
        return value


def resolve_use_node(node, macro_table, stack):
    """
    Expand a single UseNode. Returns the expanded value (may be a list,
    dict, string, etc.). Raises on unknown macro or cycle.
    """
    name = node.name
    if name not in macro_table:
        raise SystemExit(f"error: unknown macro: !use {name!r}")
    if name in stack:
        cycle = ' -> '.join(list(stack) + [name])
        raise SystemExit(f"error: circular macro expansion: {cycle}")

    macro_def = macro_table[name]

    # Parameterized macro — has params: and body: keys
    if isinstance(macro_def, dict) and 'params' in macro_def and 'body' in macro_def:
        params = macro_def['params']   # list of '$name' strings
        body   = copy.deepcopy(macro_def['body'])

        if node.args is not None:
            # build args_dict from positional list or named mapping
            if isinstance(node.args, list):
                if len(node.args) != len(params):
                    raise SystemExit(
                        f"error: macro {name!r} expects {len(params)} args, "
                        f"got {len(node.args)}")
                args_dict = dict(zip(params, node.args))
            elif isinstance(node.args, dict):
                # named args — keys may omit the leading $ for convenience
                args_dict = {}
                for param in params:
                    key = param          # try '$name'
                    alt = param.lstrip('$')  # try 'name'
                    if key in node.args:
                        args_dict[param] = node.args[key]
                    elif alt in node.args:
                        args_dict[param] = node.args[alt]
                    else:
                        raise SystemExit(
                            f"error: macro {name!r} missing argument {param!r}")
            else:
                raise SystemExit(
                    f"error: macro {name!r} args must be a list or mapping")
            body = substitute(body, params, args_dict)
        elif params:
            raise SystemExit(
                f"error: macro {name!r} requires arguments: {params}")

        return expand(body, macro_table, stack | {name})

    # Parameter-free macro — value is the expansion directly
    expanded = copy.deepcopy(macro_def)
    return expand(expanded, macro_table, stack | {name})


def expand(value, macro_table, stack=None):
    """
    Recursively walk value, expanding all UseNodes encountered.
    Returns the fully expanded value.
    """
    if stack is None:
        stack = frozenset()

    if isinstance(value, UseNode):
        return expand(
            resolve_use_node(value, macro_table, stack),
            macro_table, stack)

    elif isinstance(value, list):
        result = []
        for item in value:
            expanded = expand(item, macro_table, stack)
            # splice lists returned from sequence-context expansions
            if isinstance(item, UseNode) and isinstance(expanded, list):
                result.extend(expanded)
            else:
                result.append(expanded)
        return result

    elif isinstance(value, dict):
        result = {}
        for k, v in value.items():
            if isinstance(k, UseNode):
                # !use macro_name: — key-position !use merges expanded
                # mapping keys into the parent (used for func/import shapes)
                expanded_k = expand(resolve_use_node(k, macro_table, stack),
                                    macro_table, stack)
                if isinstance(expanded_k, dict):
                    result.update(expanded_k)
                else:
                    raise SystemExit(
                        f"error: !use {k.name!r} in key position must expand "
                        f"to a mapping, got {type(expanded_k).__name__}")
            else:
                result[k] = expand(v, macro_table, stack)
        return result

    else:
        return value


# ---------------------------------------------------------------------------
# module loading
# ---------------------------------------------------------------------------

def load_module(path):
    """
    Parse a module file. Returns (doc, macro_table, dep_paths).
    Processes include: block, builds macro table, expands all !use tags.
    """
    base_dir = os.path.dirname(os.path.abspath(path))

    try:
        text = open(path).read()
    except FileNotFoundError:
        raise SystemExit(f"error: file not found: {path}")
    try:
        doc = yaml.safe_load(text)
    except yaml.YAMLError as e:
        raise SystemExit(f"error: yaml parse failed in {path}:\n  {e}")

    if not isinstance(doc, dict) or 'module' not in doc:
        raise SystemExit(f"error: no 'module:' key found in {path}")

    # collect include: paths and build dep list
    include_paths = doc.pop('include', []) or []
    dep_paths = [path] + [
        os.path.normpath(os.path.join(base_dir, p)) for p in include_paths
    ]

    # build macro table from included files
    macro_table = build_macro_table(include_paths, base_dir)

    # merge module-local macros (take precedence over included ones)
    local_macros = doc.pop('macros', {}) or {}
    macro_table.update(local_macros)

    # expand all !use tags in the module doc
    doc = expand(doc, macro_table)

    return doc, dep_paths


# ---------------------------------------------------------------------------
# emission helpers  (unchanged from original)
# ---------------------------------------------------------------------------

def indent(lines, n=2):
    pad = " " * n
    return [pad + l for l in lines]

BLOCK_OPS = {'block', 'loop'}

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
    lines = []
    items = instructions if isinstance(instructions, list) else [instructions]

    for item in items:
        if isinstance(item, RawString):
            lines.append(item)
        elif isinstance(item, str):
            lines.append(item.strip())
        elif isinstance(item, dict):
            if 'if' in item:
                spec = item['if']
                lines.append('if')
                if isinstance(spec, list):
                    lines.extend(indent(emit_body(spec)))
                else:
                    if 'result' in spec:
                        lines[-1] = f'if (result {spec["result"]})'
                    if 'then' in spec:
                        lines.extend(indent(emit_body(spec['then'])))
                    if 'else' in spec:
                        lines.append('else')
                        lines.extend(indent(emit_body(spec['else'])))
                lines.append('end')
            else:
                for k, v in item.items():
                    opcode = k.split()[0]
                    if opcode in BLOCK_OPS:
                        lines.append(k)
                        if v:
                            lines.extend(indent(emit_body(v)))
                        lines.append('end')
                    else:
                        lines.append(f'{k} {v}' if v is not None else k)
        elif isinstance(item, list):
            lines.extend(emit_body(item))
        else:
            raise SystemExit(
                f"error: unexpected instruction type {type(item).__name__}: {item!r}")

    return lines

def emit_func(name, spec):
    params  = emit_params(spec.get('param', []))
    result  = emit_result(spec.get('result'))
    locals_ = emit_locals(spec.get('local', []))

    lines = [f'(func {name}{"".join(params + result)}']
    for l in locals_:
        lines.append(f'  {l}')
    lines.extend(indent(emit_body(spec.get('body', []))))
    lines.append(')')
    return lines


# ---------------------------------------------------------------------------
# module emitter
# ---------------------------------------------------------------------------

SECTION_ORDER = [
    'type', 'import', 'memory', 'table', 'global',
    'data', 'elem', 'func', 'export', 'start'
]

def emit_module(doc):
    name = doc['module']
    buckets = {s: [] for s in SECTION_ORDER}

    for key, val in doc.items():
        if key in ('module',):
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
            func_id = key.split(' ', 1)[1]
            export_val = val.get('export') if isinstance(val, dict) else None
            export_name = func_id.lstrip('$') \
                if export_val is True else export_val or None
            buckets['func'].extend(emit_func(func_id, val))
            if export_name:
                buckets['export'].append(
                    f'(export "{export_name}" (func {func_id}))')
        else:
            raise SystemExit(
                f"error: unknown key in module '{name}': '{key}'")

    lines = [f'(module {name}']
    for section in SECTION_ORDER:
        lines.extend(indent(buckets[section]))
    lines.append(')')
    return '\n'.join(lines)


# ---------------------------------------------------------------------------
# entry point
# ---------------------------------------------------------------------------

def write_depfile(dep_path, target, deps):
    dep_list = ' '.join(deps)
    open(dep_path, 'w').write(f'{target}: {dep_list}\n')


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(0)

    for path in sys.argv[1:]:
        doc, deps = load_module(path)
        wat = emit_module(doc)
        mod_name = doc['module'].lstrip('$')
        out_path = f'{mod_name}.wat'
        open(out_path, 'w').write(wat)
        write_depfile(f'{mod_name}.d', out_path, deps)
        print(f'wrote {out_path} (deps: {", ".join(deps)})')


if __name__ == '__main__':
    main()
