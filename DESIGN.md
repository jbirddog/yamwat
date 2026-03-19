# yamwat

`yamwat` is a transpiler from YAML to WAT, the WebAssembly text format. From
there a tool like `wat2wasm` can be used to produce WebAssembly binaries.

The main design goal is to keep things straightforward and preserve the
character of WAT itself — the YAML structure maps as closely as possible to
WAT's own structure, with a small number of additions that make authoring
practical.

The project lives here: https://github.com/jbirddog/yamwat

---

## the north star use case

yamwat is designed around a specific hosting model: a provider exposes a fixed
set of host functions, and users upload yamwat workflows that can only call what
the host explicitly provides. The wasm binary is the policy — swapping policies
means swapping which binary gets loaded, with no changes to the host.

This gives three properties that are difficult to get any other way:

- **sandboxing** — a workflow can only do what the host explicitly allows
- **portability** — the same host functions can back many different wasm blobs,
  each encoding different policy (e.g. `validate_order_us_east.wasm` vs.
  `validate_order_us_west.wasm`)
- **testability** — stub the host imports and test workflow logic in isolation

The examples in the `examples/` directory are built around this model. They are
the intended first introduction to yamwat for new readers.

---

## yaml structure

The yaml file structure maps as directly as possible to WAT. Top-level keys
correspond to WAT declarations: `module`, `func`, `import`, `memory`, `table`,
`elem`, `type`, `global`, `data`, `start`. The transpiler emits these in the
order WAT requires regardless of the order they appear in the yaml.

A minimal example:

```yaml
module: $simple

func $add:
  export: True
  param: [$a i32, $b i32]
  result: i32
  body: [local.get $a, local.get $b, i32.add]
```

### func bodies

Instructions in a func body are written as a yaml list. Plain strings emit
directly. Structured constructs (`block`, `loop`, `if`) use dict syntax and
synthesize their `end` automatically:

```yaml
body:
  - block $done:
      - loop $top:
          - local.get $n
          - i32.const 1
          - i32.le_s
          - br_if $done
          - br $top
  - local.get $result
```

`if` uses `then`/`else` keys with an optional `result` type:

```yaml
- if:
    result: i32
    then:
      - i32.const 0
      - local.get $x
      - i32.sub
    else:
      - local.get $x
```

### export: True

`func` declarations support `export: True` as a shorthand — the export name is
inferred from the func id:

```yaml
func $check_access:
  export: True   # emits (export "check_access" (func $check_access))
```

An explicit string value can be used instead when the export name should differ
from the func id.

---

## custom tags

Two custom YAML tags extend the base syntax.

### !include

Pulls in the contents of another file before parsing. Includes are resolved at
the text level so YAML anchors defined in the included file remain in scope for
the rest of the document:

```yaml
!include host_types.yaml

---
module: $my_workflow
...
```

The transpiler also tracks all included files and writes a `.d` dependency file
alongside each `.wat` output, listing every file that contributed to that
output. This is compatible with make-style build systems for incremental
rebuilds.

### !raw

Passes a string through to WAT verbatim, bypassing the transpiler's emitter.
Use this for any instruction or construct that yamwat does not have structured
support for:

```yaml
- !raw "(call_indirect (type $decision_t))"
```

A common case is load/store instructions with explicit `offset=` or `align=`
modifiers — these must be written with `!raw` since the bare key form
(`i32.load offset=4`) is parsed by YAML as a dict key and emits incorrectly:

```yaml
# correct
- !raw "i32.load offset=4"

# incorrect — emits as "i32.load offset=4 None"
- i32.load offset=4
```

---

## definitions and reusable declarations

A yaml file may begin with a `definitions` document (separated from the module
document by `---`). Definitions declare YAML anchors for import signatures,
function signatures, and code snippets that can be merged or inlined elsewhere.

```yaml
definitions:
  imports:
    get_user: &import_get_user
      from: [env, get_user]
      param: [$user_id i32, $ptr i32]

  signatures:
    i32_to_i32: &sig_i32_to_i32
      param: [$x i32]
      result: i32

  snippets:
    guard_positive: &guard_positive
      - i32.const 0
      - i32.lt_s
      - br_if $abort
```

Definitions can live in a separate file and pulled in with `!include`, or
declared inline as the first document in the same file.

### host_types.yaml convention

When a workflow passes a pointer into linear memory and the host writes a struct
at that address, both sides must agree on the field layout. The convention is to
declare this layout in a shared definitions file — typically named
`host_types.yaml` — that documents the struct, declares the import signature for
the host function that writes it, and is included by any workflow that works
with that struct.

```yaml
# host_types.yaml
definitions:
  # User struct — written by the host at a caller-supplied pointer
  #   offset 0: age              (i32)
  #   offset 4: residence        (i32)  1=CA, 2=NV, 3=other
  #   offset 8: membership_tier  (i32)  0=none, 1=basic, 2=premium

  imports:
    get_user: &import_get_user
      from: [env, get_user]
      param: [$user_id i32, $ptr i32]
```

---

## examples

The `examples/` directory contains worked examples built around the north star
use case. Each example lives in its own subdirectory with its yaml source,
generated `.wat`, pre-built `.wasm`, and a `run.py` uv script that wires host
stubs and drives the workflow.

### access_check

The simplest meaningful example. One host-provided attribute, one policy
condition, two outcomes. Demonstrates `import`, a single `func`, and
`if/then/else`. A policy variant (`access_check_21.yaml`) shows that swapping
policy means only changing the wasm — the host is untouched.

### order_processing

A chain of host calls with early exit on failure. Demonstrates the
`block`/`br_if` pattern for sequential checks where any failure should short-
circuit to a rejection path.

### access_check_with_struct

Extends access_check with a richer host boundary. Instead of returning a single
i32, the host writes a user struct into linear memory. The workflow reads
multiple fields and enforces a compound policy. Introduces `host_types.yaml` as
a shared definitions file and demonstrates `i32.load` with `offset=` via
`!raw`.

### async_approval

A multi-step approval workflow where some steps are asynchronous. The workflow
registers callback functions in an exported table and passes their indices to
host functions, which call back into the wasm when ready. Demonstrates `table`,
`elem`, `type` declarations, and the continuation pattern where the workflow
owns sequencing entirely — the host only knows which index to call back at.

---

## testing

Tests live in `test_yamwat.py` and run the full pipeline for each fixture:
`yamwat.py` → `wat2wasm` → wasmtime instantiation → assertions.

Each fixture in `FIXTURES` is one of two forms:

**Simple** — host stubs are stateless and can be defined upfront:

```python
("tests/simple.yaml",
 {},
 assert_simple)
```

**Factory** — stubs need post-instantiation state (e.g. a memory reference for
writes, or an exported table for callbacks). The `host_imports` entry is a
factory `make_<n>_imports(store_ref)` that returns `(host_imports_dict,
assert_fn)`. Stubs close over a `ctx` dict; `assert_fn(exports, store)` sets
`ctx` entries after instantiation before making any wasm calls:

```python
("tests/access_check_with_struct.yaml",
 make_access_check_with_struct_imports,
 None)
```

Tests run in a Docker container. CI uses the same Dockerfile as the development
environment.
