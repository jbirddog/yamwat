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

**if** — two forms depending on whether the branch is a guard or produces a value:

flat form — one-armed guard, no result value:

```yaml
- if:
    - local.get $post_id
    - call $remove
    - return
```

structured form — two-armed branch or value-producing if (`result` required when producing a value):

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
directly. Four common uses:

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

**memory declarations** — the host contract specifies page count and export
name; workflows merge it in rather than declaring memory independently:

```yaml
definitions:
  memory:
    post_mem: &post_mem
      pages: 1
      export: mem
```

```yaml
memory $mem:
  <<: *post_mem
```

This keeps memory configuration in the host contract alongside import signatures
and struct field accessors. If the host changes the export name or page count,
workflows pick it up on recompile without any changes on their side.

Definitions can live in a separate file (included via `!include`) or as the
first document in the same file. Separate files are preferred when the
definitions are shared across multiple modules — see `host_types.yaml` below.

### `::` short keys

In definitions blocks the key name to the left of the anchor is often
redundant — the anchor is the only thing that matters. The `::` short key
convention drops the noise:

```yaml
definitions:
  snippets:
    :: &post_score
      - i32.load
    :: &post_flag_count
      - !raw "i32.load offset=4"
```

`::` is just a conventional key name with no special compiler support. It works
because PyYAML silently accepts duplicate keys within a mapping (last value
wins), which is technically out of spec but is consistent and intentional here
since the key is never referenced. A future compiler pass will handle `::` at
the text level for strict spec-compliance. In the meantime, avoid using `::` as
a real key name elsewhere in your YAML.

---

## shared definitions files

When host and workflow need to agree on a contract — struct layout, import
signatures, memory conventions — that agreement belongs in a shared definitions
file included by both sides.

`host_types.yaml` is the conventional name for a file that declares the struct
layout and import signatures for a user object. At larger scales, a `host/`
directory with one file per entity (e.g. `host/user.yaml`, `host/post.yaml`,
`host/order.yaml`) is preferred over a single monolithic file. A workflow then
includes only what it needs:

```yaml
!include host/user.yaml
!include logic/guards.yaml
```

A complete host contract file declares four things together: the struct layout
(as comments), field accessor snippets, import signatures, and the memory
declaration. Workflows merge all of these in via anchors:

```yaml
definitions:
  # User struct layout written by the host into wasm linear memory:
  #   offset 0: age              (i32)
  #   offset 4: residence        (i32)
  #   offset 8: membership_tier  (i32)

  snippets:
    user_age: &user_age
      - i32.load                   # field: age at offset 0
    user_residence: &user_residence
      - !raw "i32.load offset=4"   # field: residence

  imports:
    get_user: &import_get_user
      from: [env, get_user]
      param: [$user_id i32, $ptr i32]

  memory:
    user_mem: &user_mem
      pages: 1
      export: mem
```

The workflow includes this file and merges in what it needs — import signatures
with `<<:`, field accessors with `*`, and memory with `<<:`. The host contract
is the single source of truth for struct layout, field offsets, memory size, and
capability surface. Both sides work from the same file.

The include list at the top of a workflow is also its own form of documentation
— it tells a reader exactly which host capabilities the policy depends on.

---

## known limitations

yamwat passes plain string instructions straight through to WAT unchanged, so
the full WAT instruction set is always available. The limitations below are
specific to yamwat's *structured emitters* — the dict-based syntax for `if`,
`block`, `loop`, and so on. When the structured form doesn't cover a case,
`!raw` is the escape hatch.

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
with modifiers. In practice this friction is best contained in host contract
files (e.g. `host/post.yaml`) as field accessor snippets — workflow authors
use the snippet name and never write `!raw` directly.

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
