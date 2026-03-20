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
- **composability** — policies can import other policies as functions, building
  higher-level coordinators from reusable leaf policies

yamwat is not intended as a general-purpose WAT authoring tool, though nothing
prevents that use. It shines when the boundary between host and workflow needs
to be clear, readable, and easy to review.

---

## yaml structure

yamwat files are valid YAML 1.2. Each file is a single document — no `---`
multi-document separators. A file's role is determined by its top-level keys.

A **macro file** has macro names as its top-level keys — there is no wrapper
block. Since a file named in an `include:` block can only contain macros, a
wrapper key would be redundant; the compiler knows the context from how the
file was loaded. A macro file emits no WAT directly and is only valid as an
`include:` target. Including a module file is an error.

A **module file** contains a `module:` key and produces WAT output. It may also
contain:

- `include:` — a list of macro files to load
- `macros:` — module-local macro declarations
- WAT declaration keys: `import $id`, `func $id`, `memory $id`, `table $id`,
  `type $id`, `global $id`, `data`, `elem`, `start`

Module-local macros are available within that file only. If a local macro name
collides with one from an included file, the local declaration takes precedence.
This lets a module override a shared macro without modifying the host contract.

```yaml
# module file — with local macros
include:
  - post.yaml
  - verdicts.yaml

module: $standard_policy

macros:
  # local override: treat scores <= -5 as low rather than the host default of -10
  low_score_threshold: [i32.const -5]

memory $mem:
  !use post_mem

import $get_post:
  !use import_get_post

func $moderate:
  !use moderate_func:
  body:
    - !use {load_post: {post: $post, post_id: $post_id}}
    - !use {post_score: {post: $post}}
    - !use low_score_threshold
    - i32.le_s
    - if:
        - !use verdict_hold
        - return
    - !use verdict_approve
```

The `include:` block is a file-level directive processed before the module is
compiled. It is distinct from `import $id:` declarations, which are WAT import
statements. The `$` sigil on `import $id:` makes the two unambiguous in context.

---

## custom tags

### `!use`

Expands a named macro inline at the point of use. The compiler looks up the
name in the macros loaded via `include:` and splices the expansion in place
before emitting WAT. `!use` is always resolved by the compiler, never by the
YAML parser — macro files are parsed independently with no cross-file anchor
scope required.

**No-argument form** — a tagged scalar:

```yaml
- !use load_post
```

**Parameterized form** — a tagged mapping with a single key. The key is the
macro name; its value is either a sequence of arguments (positional) or a
mapping of argument names to values (named). All parameters declared in the
macro's `params:` list are required.

Positional — arguments are matched to params by order. Concise when the
call-site names match the param names:

```yaml
- !use {load_post: [$post, $post_id]}
```

Named — arguments are matched by name. Use when call-site local names differ
from the macro's param names, or when explicitness aids readability:

```yaml
- !use {load_post: {post: $ptr, post_id: $id}}
```

Block style for readability with multiple arguments:

```yaml
- !use
  load_post:
    post: $ptr
    post_id: $id
```

**Mapping context** — `!use` in a mapping expands a macro whose value is a
mapping and contributes its keys to the declaration. Two forms are needed
depending on whether the `!use` is the sole content of the declaration or
appears alongside sibling keys.

**Sole value form** — when `!use` is the entire value of a declaration key,
write it as a tagged scalar value. The expansion replaces the value directly:

```yaml
memory $mem:
  !use post_mem             # sole value — expands to {pages: 1, export: mem}

import $get_post:
  !use import_get_post      # sole value — expands to {from: ..., param: ...}
```

**Key form** — when `!use` appears alongside sibling keys in the same mapping,
write it as a key with a colon and no value. The expansion is merged into the
parent mapping alongside the siblings:

```yaml
func $moderate:
  !use moderate_func:       # key form — merges export, param, result, local
  body:                     # sibling key — preserved alongside the expansion
    - ...
```

The colon is what distinguishes the two forms. A tagged scalar value (`!use
name`) cannot coexist with sibling keys in the same mapping — that is a YAML
constraint, not a yamwat one. The key form (`!use name:`) sidesteps it by
making the `!use` a mapping key rather than a value.

**Sequence context** — `!use` in a sequence splices the macro's instructions
inline:

```yaml
body:
  - !use load_post              # expands to one or more instructions
  - !use {post_flag_count: {post: $post}}
  - i32.const 10
  - i32.ge_s
```

### `!raw`

Passes a string through to WAT verbatim, bypassing the transpiler's structured
emitters. Use this for any WAT instruction or construct that yamwat does not
have structured support for:

```yaml
- !raw "(call_indirect (type $my_type))"
- !raw "i32.load offset=4"
```

In practice, `!raw` is most useful inside macro files as part of field accessor
macros — workflow authors use `!use` and never write `!raw` directly.

---

## macro files

A macro file's top-level keys are macro names — there is no wrapper key. Since
a file named in an `include:` block can only contain macros, the `macros:`
wrapper would be redundant. The compiler knows the context from how the file
was loaded.

A macro that accepts parameters declares them under a `params:` key alongside a
`body:` key. Parameter names use the `$` sigil, matching wasm local naming
convention — the compiler substitutes them during expansion before the result
reaches the WAT emitter. The `params:` list makes explicit which `$` identifiers
are substitution targets and which are literal wasm names passed through
verbatim.

A macro without `params:` has its entire value used as the expansion body, with
no substitution performed.

```yaml
# post.yaml

# Post struct layout written by the host into wasm linear memory.
#
#   offset  field          type
#   ------  -----          ----
#        0  score          i32
#        4  flag_count     i32
#        8  word_count     i32
#       12  author_tier    i32    0=new, 1=established, 2=trusted
#       16  is_repost      i32
#       20  community_id   i32
#       24  author_id      i32

load_post:
  params: [$post, $post_id]
  body:
    - i32.const 0
    - local.set $post
    - local.get $post_id
    - local.get $post
    - call $get_post

post_score:
  params: [$post]
  body:
    - local.get $post
    - i32.load

post_flag_count:
  params: [$post]
  body:
    - local.get $post
    - !raw "i32.load offset=4"

post_word_count:
  params: [$post]
  body:
    - local.get $post
    - !raw "i32.load offset=8"

post_author_tier:
  params: [$post]
  body:
    - local.get $post
    - !raw "i32.load offset=12"

post_is_repost:
  params: [$post]
  body:
    - local.get $post
    - !raw "i32.load offset=16"

moderate_func:
  export: true
  param: [$post_id i32]
  result: i32
  local: [$post i32]

import_get_post:
  from: [env, get_post]
  param: [$post_id i32, $ptr i32]

post_mem:
  pages: 1
  export: mem
```

```yaml
# verdicts.yaml

verdict_approve:  [i32.const 0]
verdict_hold:     [i32.const 1]
verdict_escalate: [i32.const 2]
verdict_remove:   [i32.const 3]
```

The macro file is the host contract. It is the single source of truth for
struct layout, field offsets, memory size, import signatures, and func
signatures. Both the host implementation and the policy files work from the same
file. If the host changes a field offset or memory page count, policies pick it
up on recompile without changes on their side.

The `include:` list at the top of a policy file is also its own documentation —
it tells a reader exactly which host contracts this policy depends on.

---

## macro expansion

### argument substitution

When a parameterized macro is expanded, the compiler substitutes each argument
value for its corresponding parameter name throughout the macro body before
splicing. Arguments may be passed positionally (matched by order) or by name
(matched by key) — both forms produce identical output.

```yaml
# macro definition
post_flag_count:
  params: [$post]
  body:
    - local.get $post
    - !raw "i32.load offset=4"

# positional call site
- !use {post_flag_count: [$my_post]}

# named call site — equivalent
- !use {post_flag_count: {post: $my_post}}

# both expand to
- local.get $my_post
- !raw "i32.load offset=4"
```

### nested macros

A macro body may itself contain `!use` tags. The compiler expands recursively —
after substituting arguments into a macro body, it walks the result and expands
any `!use` tags found there, with the same argument substitution rules applying
at each level. Arguments are resolved before recursing, so by the time the
compiler sees a nested `!use`, all parameter names from the outer expansion are
already substituted with their call-site values.

```yaml
load_field_offset4:
  params: [$ptr]
  body:
    - local.get $ptr
    - !raw "i32.load offset=4"

post_flag_count:
  params: [$post]
  body:
    - !use {load_field_offset4: {ptr: $post}}
```

### cycle detection

Circular macro expansions are detected and reported as errors. The compiler
maintains an expansion stack and raises an error if a macro appears in its own
expansion chain, reporting the full cycle:

```
error: circular macro expansion: post_flag_count -> load_field_offset4 -> post_flag_count
```

---

## module files

A module file's top-level keys after `include:` and `module:` map directly to
WAT declarations. Keys are prefixed with the declaration type and identifier:
`func $id`, `import $id`, `memory $id`, and so on, exactly as in WAT.

### func declarations

```yaml
func $my_func:
  param: [$x i32, $y i32]
  result: i32
  local: [$tmp i32]
  body: [...]
```

**export** — `export: true` infers the export name from the func id (stripping
the leading `$`). An explicit string value uses that name instead:

```yaml
func $add:
  export: true          # exports as "add"

func $internal_name:
  export: public_name   # exports as "public_name"
```

**body** — a flat list of instructions. Structured constructs use dict syntax:

`block` and `loop` — `end` is synthesized automatically:

```yaml
- block $done:
    - loop $top:
        - ...
        - br $top
```

`if` — two forms depending on whether the branch is a guard or produces a value:

flat form — one-armed guard, no result value:

```yaml
- if:
    - local.get $post_id
    - call $remove
    - return
```

structured form — two-armed branch or value-producing if (`result` required
when producing a value):

```yaml
- if:
    result: i32
    then:
      - i32.const 1
    else:
      - i32.const 0
```

---

## a complete example

`standard_policy.yaml` under the new format. The macro file (`post.yaml`)
gains explicit `params:` and `body:` structure; call sites gain explicit
argument bindings. The policy body structure is otherwise unchanged.

```yaml
# standard_policy.yaml

include:
  - post.yaml
  - verdicts.yaml

module: $standard_policy

memory $mem:
  !use post_mem

import $get_post:
  !use import_get_post

func $moderate:
  !use moderate_func:
  body:
    - !use {load_post: {post: $post, post_id: $post_id}}

    # remove if flag_count >= 10
    - !use {post_flag_count: {post: $post}}
    - i32.const 10
    - i32.ge_s
    - if:
        - !use verdict_remove
        - return

    # escalate if is_repost AND flag_count > 0
    - !use {post_is_repost: {post: $post}}
    - !use {post_flag_count: {post: $post}}
    - i32.const 0
    - i32.gt_s
    - i32.and
    - if:
        - !use verdict_escalate
        - return

    # hold if author_tier < 1
    - !use {post_author_tier: {post: $post}}
    - i32.const 1
    - i32.lt_s
    - if:
        - !use verdict_hold
        - return

    # hold if score <= -10
    - !use {post_score: {post: $post}}
    - i32.const -10
    - i32.le_s
    - if:
        - !use verdict_hold
        - return

    # default: approve
    - !use verdict_approve
```

The explicit argument bindings make the contract between the macro and its call
site visible at a glance. A reader no longer needs to know that `$post` is an
implicit convention — the call site says exactly which local is being passed.

---

## policy composition

Policies can import other policies as functions. This enables coordinator
policies that delegate to leaf policies, compose their verdicts, and return a
single result — without the host needing to know anything about the composition
structure.

### the verdict model

Rather than calling host outcome functions directly, policies return an i32
verdict. The host acts on the return value after `moderate` returns. Verdicts
are defined in `verdicts.yaml` and ordered by severity:

```
approve(0) < hold(1) < escalate(2) < remove(3)
```

Every policy exports a `moderate` function with the same signature:

```
(post_id: i32) -> i32
```

### coordinator patterns

**Veto chain** — first non-approve verdict wins:

```yaml
include:
  - verdicts.yaml

module: $community_1_policy

import $standard_moderate:
  from: [standard_policy, moderate]
  param: [$post_id i32]
  result: i32

import $no_curse_words_moderate:
  from: [no_curse_words_policy, moderate]
  param: [$post_id i32]
  result: i32

func $moderate:
  export: true
  param: [$post_id i32]
  result: i32
  local: [$verdict i32]
  body:
    - block $done:
        - local.get $post_id
        - call $standard_moderate
        - local.tee $verdict
        - br_if $done

        - local.get $post_id
        - call $no_curse_words_moderate
        - local.tee $verdict
        - br_if $done

        - !use verdict_approve
        - local.set $verdict

    - local.get $verdict
```

**Strictest wins** — calls all sub-policies, takes the most severe verdict:

```yaml
func $moderate:
  export: true
  param: [$post_id i32]
  result: i32
  body:
    - local.get $post_id
    - call $no_religion_moderate
    - local.get $post_id
    - call $max_three_flags_moderate
    - i32.gt_s
```

### self-describing dependencies

The wasm import section lists every `(module, field)` pair a binary needs. A
coordinator's dependencies are declared in the binary itself — no separate
manifest required. The host inspects imports before instantiation and builds
the full dependency graph automatically.

---

## compiler pipeline

```
for each module file:
  1. parse the module file as a YAML document
     - error if a top-level module: key is found in any included file
  2. extract the include: list
  3. for each path in include::
       parse the macro file as a YAML document
       register each top-level key/value pair as a named macro in the macro table
  4. register any macros: declared in the module file into the macro table
     - local declarations overwrite same-named entries from included files
  5. walk the parsed module tree; resolve all !use tags:
       no-arg form:   look up name, splice expansion
       parameterized: look up name, substitute arguments, splice expansion
       recurse into expansions until no !use tags remain
       detect and report cycles via expansion stack
  6. build the IR from the fully resolved tree
  7. emit WAT (bucket pass preserves WAT section ordering)
  8. write .wat and .d depfile
```

The `include:` list is the complete input for the `.d` depfile — available
after step 1 with no tracking needed during expansion.

---

## known limitations

yamwat passes plain string instructions straight through to WAT unchanged, so
the full WAT instruction set is always available. The limitations below are
specific to yamwat's *structured emitters* — the dict-based syntax for `if`,
`block`, `loop`, and so on. When the structured form doesn't cover a case,
`!raw` is the escape hatch.

### `i32.load` with offset or alignment

WAT's load and store instructions support `offset=N` and `align=N` modifiers.
These cannot be expressed as plain YAML mapping keys without a trailing null
value. Use `!raw` instead:

```yaml
# wrong — emits "i32.load offset=4 None"
- i32.load offset=4

# correct
- !raw "i32.load offset=4"
```

The same applies to `i32.store`, `i64.load`, and all other load/store variants
with modifiers. In practice this is best contained in macro files as named
field accessor macros — workflow authors use `!use` and never write `!raw`
directly.

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

```
uv run run.py --user_id=1
```

### async_approval

A multi-step approval workflow where steps are asynchronous. The workflow
registers callback functions in a table and passes their indices to host
functions that call back into wasm when ready. The host owns async execution;
the workflow owns sequencing.

Demonstrates `table`, `elem`, `type` declarations, and the callback index
pattern.

```
uv run run.py --report_id=1
```

### content_moderation

A community content moderation system demonstrating the verdict model and policy
composition. Leaf policies return i32 verdicts. Coordinator policies import leaf
policies as functions and compose their verdicts using the veto chain or
strictest-wins pattern.

```
uv run host.py --post_id=1
uv run host.py --community_id=2
```

---

## testing

Tests live in `test_yamwat.py` and run the full pipeline for each fixture:
`yamwat.py` → `wat2wasm` → wasmtime instantiate → assertions.

```
pytest test_yamwat.py -v
```

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
