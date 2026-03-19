# yamwat

`yamwat` is a simple transpiler from YAML to WAT, the WebAssembly text format.
From there a tool like `wat2wasm` can be used to create WebAssembly binaries. A
main design goal is to keep things straightforward and try to maintain the
beauty of the WAT format itself — as much as that can be done in YAML.

The project lives here — https://github.com/jbirddog/yamwat

---

## the north star

The intended use case is a hosting provider that exposes a set of host
functions. Users upload yamwat workflows. The workflow can only do what the host
explicitly provides — a clean, capability-based security model. The wasm binary
is the policy; swapping policies is just swapping which blob gets loaded.

Key properties this enables:

- **sandboxing** — workflows can only call known host functions
- **portability** — same host functions, different wasm blobs per context
  (e.g. `validate_order_for_new_york.wasm`, `validate_order_for_texas.wasm`)
- **testability** — stub the host imports, test workflow logic in isolation

yamwat is not intended as a general-purpose WAT authoring tool, though nothing
prevents that use. It shines when the boundary between host and workflow needs
to be clear, readable, and easy to review.

---

## yaml structure

The YAML file structure tries to be as 1:1 with WAT as possible. A module
document maps directly to a WAT `(module ...)` block. Top-level keys correspond
to WAT declarations: `import`, `func`, `memory`, `table`, `type`, `global`,
`data`, `elem`, `start`.

A YAML file may contain one or more documents separated by `---`. If a
definitions block precedes the module, the `---` separator is required:

```yaml
definitions:
  snippets:
    my_snippet: &my_snippet
      - i32.const 0

---
module: $my_module
...
```

If the file contains only a module, the `---` separator is optional.

---

## custom tags

### `!include`

Pulls in the contents of another file before YAML parsing, so anchors defined
in the included file remain in scope throughout the current file:

```yaml
!include host_types.yaml
```

`!include` is resolved at the text level, not the parse level. This means
anchors defined in included files can be referenced anywhere in the including
file. The transpiler also generates `.d` dependency files listing all included
paths, suitable for use with make-style build systems.

### `!raw`

Passes a string through to WAT verbatim, bypassing the transpiler's structured
emitters. Use this for any WAT instruction or construct that yamwat does not
have structured support for:

```yaml
- !raw "(call_indirect (type $my_type))"
- !raw "i32.load offset=4"
```

---

## func declarations

### params, results, locals

```yaml
func $my_func:
  param: [$x i32, $y i32]
  result: i32
  local: [$tmp i32]
  body: [...]
```

### export

`export: True` infers the export name from the func id (stripping the leading
`$`). An explicit string value uses that name instead:

```yaml
func $add:
  export: True        # exports as "add"

func $internal_name:
  export: public_name # exports as "public_name"
```

### body

The body is a flat list of instructions. Structured constructs use dict syntax:

**block and loop** — `end` is synthesized automatically:

```yaml
- block $done:
    - loop $top:
        - ...
        - br $top
```

**if/then/else** — `result` is required when the if produces a value:

```yaml
- if:
    result: i32
    then:
      - i32.const 1
    else:
      - i32.const 0
```

---

## definitions blocks

A definitions block declares reusable YAML anchors. It does not emit any WAT
directly. Three common uses:

**import signatures** — shared param/result shapes for host functions:

```yaml
definitions:
  imports:
    get_user: &import_get_user
      from: [env, get_user]
      param: [$user_id i32, $ptr i32]
```

**func signatures** — reusable param/result pairs merged into func declarations:

```yaml
definitions:
  signatures:
    i32_to_i32: &sig_i32_to_i32
      param: [$x i32]
      result: i32
```

**snippets** — inline instruction sequences referenced in func bodies:

```yaml
definitions:
  snippets:
    guard_positive: &guard_positive
      - i32.const 0
      - i32.lt_s
      - br_if $abort
```

Definitions can live in a separate file (included via `!include`) or as the
first document in the same file. Separate files are preferred when the
definitions are shared across multiple modules — see `host_types.yaml` below.

---

## shared definitions files

When host and workflow need to agree on a contract — struct layout, import
signatures, memory conventions — that agreement belongs in a shared definitions
file included by both sides.

`host_types.yaml` is the conventional name for a file that declares the struct
layout and import signatures for a user object:

```yaml
definitions:
  # User struct layout written by the host into wasm linear memory:
  #   offset 0: age              (i32)
  #   offset 4: residence        (i32)
  #   offset 8: membership_tier  (i32)

  imports:
    get_user: &import_get_user
      from: [env, get_user]
      param: [$user_id i32, $ptr i32]
```

Any workflow that works with a user object includes this file. The host runner
implements the same field layout. Both sides are working from the same source of
truth.

---

## known limitations

### `i32.load` with offset or alignment

WAT's load and store instructions support `offset=N` and `align=N` modifiers.
These cannot be expressed as structured YAML keys because yamwat would emit a
trailing `None` for the value, producing invalid WAT. Use `!raw` instead:

```yaml
# wrong — emits "i32.load offset=4 None"
- i32.load offset=4

# correct
- !raw "i32.load offset=4"
```

The same applies to `i32.store`, `i64.load`, and all other load/store variants
with modifiers.

### `call_indirect`

`call_indirect` takes a type reference that yamwat has no structured form for.
Always use `!raw`:

```yaml
- !raw "(call_indirect (type $my_type))"
```

---

## examples

The `examples/` directory contains worked examples built around the north star
use case. Each example lives in its own subdirectory with the yaml source,
generated WAT, pre-built wasm, and a `run.py` uv script that wires host stubs
and exercises all branches.

### access_check

The simplest meaningful example. One condition, two outcomes. The host provides
a user's age; the workflow decides the minimum. Demonstrates `if/then/else`.

```
uv run run.py --user_id=1
```

Variants: `access_check.yaml` (18+), `access_check_21.yaml` (21+). Swapping
policies is just swapping which wasm blob gets loaded.

### order_processing

A chain of host calls with early exit on failure. Validates an order, checks
inventory, charges payment, then confirms or rejects. Demonstrates the
`block`/`br_if` early-exit pattern for sequential checks.

```
uv run run.py --order_id=1
```

### access_check_with_struct

A follow-up to `access_check`. The host provides a richer user record via
linear memory rather than a single i32. The workflow reads fields and enforces a
compound policy: age >= 21, not a CA resident, membership required.

Introduces `host_types.yaml` — a shared definitions file declaring the struct
layout and import signatures used by both host and workflow.

```
uv run run.py --user_id=1
```

### async_approval

A multi-step approval workflow where steps are asynchronous. The workflow
registers callback functions in a table and passes their indices to host
functions that call back into wasm when ready. The host owns async execution;
the workflow owns sequencing.

Demonstrates `table`, `elem`, `type` declarations, and the callback index
pattern. The runner simulates async by calling back synchronously — the wasm
instance is re-entered the same way either way.

```
uv run run.py --report_id=1
```

---

## testing

Tests live in `test_yamwat.py` and run the full pipeline for each fixture:
`yamwat.py` → `wat2wasm` → wasmtime instantiate → assertions.

```
pytest test_yamwat.py -v
```

CI runs the same Dockerfile used in development.

Two fixture patterns are supported:

**Simple** — stateless host stubs, plain `host_imports` dict:

```python
("tests/simple.yaml", {}, assert_simple)
```

**Factory** — stubs need post-instantiation state (memory writes, table
callbacks). `host_imports` is a `make_<n>_imports(store_ref)` factory that
returns `(host_imports_dict, assert_fn)`. Stubs close over a `ctx` dict;
`assert_fn` receives `(exports, store)` and populates `ctx` before asserting:

```python
("tests/access_check_with_struct.yaml", make_access_check_with_struct_imports, None)
```
