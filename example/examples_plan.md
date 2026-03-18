# yamwat examples plan

## goals

A well-crafted example should:
- tell a story that is immediately recognizable
- show the host/wasm boundary clearly
- demonstrate key constructs without feeling like a feature checklist
- be something a non-WAT person could read and roughly follow

The test files (simple, math, memory, table, snippet) are great at exercising
the transpiler but are not the right first impression. Examples should be built
around the north star use case: the host supplies capabilities, the workflow
supplies policy and orchestration.

---

## the north star

A hosting provider exposes a set of host functions. Users upload yamwat
workflows. The workflow can only do what the host explicitly provides — a clean,
capability-based security model. The wasm binary is the policy; swapping
policies is just swapping which blob gets loaded.

Key properties this enables:
- **sandboxing** — workflows can only call known host functions
- **portability** — same host functions, different wasm blobs per context
  (e.g. `validate_order_for_new_york.wasm`, `validate_order_for_texas.wasm`)
- **testability** — stub the host imports, test workflow logic in isolation

---

## planned examples

### 1. access_check.yaml — hello world ✅

The simplest meaningful example. One condition, two outcomes. Fits on one
screen. Leaves the reader asking "what if I changed 18 to 21?" — which is
exactly the point.

- **constructs**: imports, single func, `if/then/else`
- **boundary**: `i32` in and out — host provides data, workflow provides policy
- **key idea**: the host doesn't decide who gets access, it just provides age.
  The policy (18? 21? any age?) lives entirely in the wasm.

```yaml
# the host supplies the data; the workflow supplies the policy
```

Uses `if/then/else` rather than `block/br_if` — for a single condition with
two outcomes this reads more naturally and the intent is immediately clear.

---

### 2. order_processing.yaml — workflow orchestration (next)

A chain of host calls with early exit on failure. Validates an order, checks
inventory, charges payment, then either confirms or rejects.

- **constructs**: multiple imports, single func, `block`/`br_if` early-exit pattern
- **boundary**: still `i32` — order_id passed to each step
- **key idea**: shows why `block`/`br_if` is the right pattern for a chain of
  checks. Each step can bail early without nesting. `if/then/else` would produce
  deeply nested structure here.
- **note**: also a good place to show that different regions/contexts can swap
  in different wasm blobs while the host stays stable.

---

### 3. access_check_with_struct.yaml — richer host boundary (later)

A follow-up to access_check. Instead of `get_user_age` returning an i32, the
host provides `get_user` which writes a struct into linear memory. The workflow
reads fields from memory to make a more complex policy decision.

Examples of richer policy logic this enables:
- `if residence == nevada && age > 18`
- `if residence == california then deny`

- **constructs**: memory, struct layout, `i32.load` variants
- **boundary**: pointer into linear memory — host and workflow share a agreed
  struct layout
- **key idea**: motivates the memory/struct story naturally. Once policy needs
  more than one attribute, a single i32 isn't enough.
- **convention**: likely introduces `host_types.yaml` — a shared definitions
  file that declares struct layout and memory conventions, included by any
  workflow that works with a user object.

---

## construct coverage across examples

| construct         | access_check | order_processing | access_check_with_struct |
|-------------------|:------------:|:----------------:|:------------------------:|
| imports           | ✅           | ✅               | ✅                       |
| if/then/else      | ✅           |                  | ✅                       |
| block/br_if       |              | ✅               |                          |
| multiple imports  |              | ✅               | ✅                       |
| memory/struct     |              |                  | ✅                       |
| shared defs file  |              |                  | ✅                       |

---

## notes on style

- Comments at the top of each example should do real work. The one-liner
  `# the host supplies the data; the workflow supplies the policy` tells the
  core story before the reader sees a single line of yaml.
- Inline comments should be used sparingly — only when something isn't
  self-evident from the structure.
- The `---` doc separator is only needed when a definitions block precedes the
  module in the same file.
- The yaml filename should match the module name (e.g. `$access_check` →
  `access_check.yaml`) — this is a de facto convention enforced by how
  `yamwat.py` derives the output filename from `doc['module']`.
- Snippets should only appear in an example if they genuinely improve
  readability — not as a feature demonstration.
