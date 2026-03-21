# wasm4th Specification

## Philosophy

`wasm4th` is a transpiler from a Forth-like language to WAT (WebAssembly Text format). It is Forth-like in
spirit — embracing the philosophy of Chuck Moore: simplicity, directness, and composability — but is not
concerned with ANS Forth compatibility.

The Forth stack and dictionary exist only at **compile time**. The transpiler uses the compile-time stack to
track types and constant values, allowing function signatures and stack effects to be inferred rather than
declared. The emitted WAT is intentionally naive; optimization is deferred entirely to external tooling such
as `wasm-opt`. Type checking is similarly deferred to `wat2wasm`. `wasm4th` does not duplicate work that
existing tools already do well.

There is no interpret/compile duality. The parser operates in a single mode at all times. Every token is
self-describing via a prefix character, inspired by ColorForth.

---

## Token Prefixes

| Prefix | Example      | Meaning                                                  |
|--------|--------------|----------------------------------------------------------|
| `:`    | `:add`       | Begin a word definition; create a dictionary entry       |
| `::`   | `::add`      | Begin a word definition and export it under its own name |
| `^`    | `^i32`       | Push a type onto the compile-time stack; used in type definitions and `call_indirect` |
| `:^`   | `:^read_type` | Define a named type signature                           |
| `#`    | `#42`        | Push a constant onto the compile-time stack; consumed as an immediate argument by WAT instructions that require one |
| `$`    | `$2`         | Compile a literal integer into the function body         |
| `,`    | `,add`       | Compile a call to a named word into the function body    |
| `!`    | `!swap`      | Execute a kernel word at compile time; manipulates the compile-time stack, emits nothing |
| `&`    | `&add`       | Push a func reference onto the compile-time stack; for unknown names, creates an unresolved reference resolved by `import` |
| `"`    | `"env"`      | Push a string onto the compile-time stack; only valid in module-level context |
| `[`    | `[`          | Push a new compile-time stack frame (begin a compile-time list) |
| `]`    | `]`          | Pop the top compile-time stack frame and push it as a single list slot onto the frame below |
| `--`   | `--`         | Separator in `:^` type definitions; divides input types from output types |
| *(none)* | `i32.add` | A core WAT instruction; looked up and emitted directly   |

Tokens with no prefix are core WAT words. If a token does not match a known WAT word or dictionary entry,
it is an error.

---

## Compile-Time Stack

The compile-time stack tracks **types** and optionally **constant values** for each slot. It is used to:

- Infer function parameter types and count
- Infer function result types
- Select the correct WAT instruction where type disambiguates (e.g. `i32.add` vs `f64.add`)
- Assign parameter names (`$0`, `$1`, ...) when emitting WAT — these are never written in `wasm4th` source
- Supply **immediate arguments** to WAT instructions that require them

The programmer never references parameter names directly. The transpiler introduces them during emission.

Stack effect inference means per-word stack effect annotations are not required.

### Immediates

Some WAT instructions take an immediate argument — an index or label that is part of the instruction
itself, not a runtime stack value. Examples include `local.get`, `local.set`, `local.tee`, `br`, and
`br_if`. The transpiler maintains a table of which instructions require immediates.

When such an instruction is encountered, the transpiler pops the top of the compile-time stack to use as
the immediate rather than treating it as a runtime value. The `#` prefix is the natural way to supply these:

```wasm4th
#1 local.get   ( pops #1 from compile-time stack, emits (local.get 1) )
#0 local.get   ( pops #0 from compile-time stack, emits (local.get 0) )
```

### Compile-Time Stack Slot Types

The compile-time stack can hold the following slot types:

- **Numeric types** — `i32`, `i64`, `f32`, `f64`, optionally with a known constant value
- **Type references** — pushed by `^`, used by `:^` definitions and `call_indirect`
- **Func references** — pushed by `&`, used by `export`, `import`, `elem`
- **String values** — pushed by `"`, only valid in module-level declarations
- **List slots** — pushed by `]`, contains a nested compile-time stack frame

### Stack of Stacks

The compile-time stack is a **stack of stacks**. At any point there is a current top frame; all compile-time operations act against it. `[` and `]` manage frames:

- `[` — push a new empty frame onto the stack of stacks; subsequent tokens operate against this frame
- `]` — pop the top frame and push it as a single list slot onto the frame below

This allows variable-length compile-time sequences to be constructed naturally:

```wasm4th
[ &abs &double &factorial ]   ( list of three func references )
[ #0 #1 #2 ]                  ( list of three label indices )
[ #1 #2 !min ]                ( compile-time computed list entry )
```

Words that consume list slots — `elem`, `br_table` — unpack the frame during emission. Everything inside `[ ]` uses existing prefix rules against the top frame with no special cases.

---

## Type Definitions

The `:^` prefix defines a named WAT type signature. The body uses `^` to push types onto the compile-time stack, with `--` separating inputs from outputs:

```wasm4th
:^unary_type ^i32 -- ^i32 ;
:^binary_type ^i32 ^i32 -- ^i32 ;
:^log_type ^i32 -- ;
:^thunk_type -- ^i32 ;
```

Emitting:
```wat
(type $unary_type (func (param i32) (result i32)))
(type $binary_type (func (param i32) (param i32) (result i32)))
(type $log_type (func (param i32)))
(type $thunk_type (func (result i32)))
```

Type definitions are module-level declarations. They do not define callable words — they define signatures that can be referenced by `&`, `import`, and `call_indirect`.

---

## Module-Level Declarations

All module-level declarations use the same single-mode token stream as word definitions. The `(module ...)` wrapper is implicit — the transpiler always emits one.

### `export`

```wasm4th
"increment" &increment export
```

The `::` shorthand defines and exports a word under its own name in one token:

```wasm4th
::increment ... ;
( equivalent to: :increment ... ; "increment" &increment export )
```

### `import`

Imports require a type definition and an unresolved name reference. The unresolved `&name` is registered in the dictionary by `import` using the referenced type's signature:

```wasm4th
:^read_type ^i32 -- ^i32 ;
"env" "read" &read_type &read import
```

Emitting:
```wat
(type $read_type (func (param i32) (result i32)))
(import "env" "read" (func $read (type $read_type)))
```

After this, `,read` is valid and the transpiler knows its stack effect from the type definition.

### `memory`

`memory` always takes two immediates — minimum and maximum page counts:

```wasm4th
#1 #1 memory        ( (memory 1 1) )
#1 !dup memory      ( same, using compile-time dup )
```

### `table` and `elem`

```wasm4th
#3 funcref table
[ &abs &double &factorial ] $0 elem
```

`elem` consumes a list slot of func references and a runtime offset, emitting the element segment.

### `call_indirect`

Consumes a type reference from the compile-time stack and a runtime index:

```wasm4th
:^unary_type ^i32 -- ^i32 ;
...
^unary_type call_indirect
```

---



A word definition begins with a `:` prefixed token and ends with `;`.

```
:add i32.add ;
```

`;` is not a traditional Forth semicolon that switches compiler state — it simply compiles a `return` into
the function body and closes the definition. Because of this, `;` can appear anywhere inside a definition
to produce an early return.

Words are visible in the dictionary immediately upon definition, including to themselves, so recursion
requires no special mechanism (no smudge bit).

---

## Control Flow

### `if / then`

```
if  ...  then
```

`if` **peeks** at the top of the stack rather than consuming it. The condition value remains available after
the `if` in both the taken and fall-through paths. This means explicit `drop` may occasionally be needed,
but avoids the common case where the tested value is still needed in the body or fall-through.

`else` is not needed — early return via `;` covers that case:

```
:word ... if ,branch ; then ,fallthrough ;
```

All branches of a conditional must yield the same stack effect. Unbalanced branches are an error.

WAT's `if` *does* consume its condition, so the transpiler is responsible for emitting the appropriate
`local.get` to restore the peeked value inside the branch and in the fall-through path.

### Tail Calls and Loops

Recursion is used to express loops. Because words see themselves during their own definition, a word can
call itself at the tail position:

```
:loop $0 i32.gt_s if ,exit ; then ,loop ;
```

The transpiler always emits `call` (never `return_call`). Tail call optimization is left to `wasm-opt`.

---

## Built-in Layers

The dictionary at compile time is seeded from two layers before any user source is parsed:

### Layer 1 — Raw WAT Instructions

Always available, no prefix required. These are the bedrock — every WAT instruction is a valid token,
including `local.get`, `local.set`, `local.tee`, `drop`, `br`, `br_if`, and all typed arithmetic and
comparison instructions. This layer ensures any WAT construct can be expressed in `wasm4th`.

### Layer 2 — Kernel

A small set of stack-shuffling and utility words (`swap`, `dup`, `drop`, `over`, `rot`, etc.). The kernel
is always a wasm blob — there are two sources:

**Default kernel** — hand-written WAT, compiled out of band as a bootstrapping step, and embedded as raw
bytes in the transpiler binary. No `wasm4th` source embedding, no circular toolchain dependency. The
kernel WAT is straightforward — all stack shuffling words are simple `local.get` reorderings:

```wat
(func $swap (param i32) (param i32) (result i32) (result i32)
  (local.get 1)
  (local.get 0)
)
(func $dup (param i32) (result i32) (result i32)
  (local.get 0)
  (local.get 0)
)
(func $drop (param i32))
(func $over (param i32) (param i32) (result i32) (result i32) (result i32)
  (local.get 0)
  (local.get 1)
  (local.get 0)
)
```

**Custom kernel blob** — an external wasm binary supplied at startup, which may be compiled from WAT, C,
Rust, or any other language that produces correct wasm exports. The transpiler inspects its exports at
load time — reading each exported function's name, parameter types, and result types — and seeds the
dictionary accordingly. The transpiler has no hardcoded knowledge of the blob's contents; export
inspection is the entire interface. This allows entirely different kernels for different targets
(embedded, numeric, etc.) with no changes to the transpiler.

If no kernel blob is supplied, the embedded default kernel is used.

### Compile-Time Execution (`!`)

Kernel words can be executed at compile time using the `!` prefix. `!word` pops the word's input types
from the compile-time stack, executes the actual wasm function, and pushes the results back onto the
compile-time stack. Nothing is emitted into the WAT output.

The `!` prefix uses the same export signature as `,` — no extra metadata is needed. Pop inputs, execute,
push outputs. This works uniformly for any kernel word including those in custom kernels.

```wasm4th
#12 #34 !min   ( compile-time stack contains 12 )
```

If the compile-time stack slots have known constant values, the wasm function executes against those
constants and the result is a known constant on the compile-time stack. This gives a natural form of
compile-time constant folding without any special optimizer logic.

**`!` is restricted to kernel words only.** User-defined words cannot be executed at compile time — doing
so would require compiling the word to wasm mid-compilation, invoking the full toolchain, and managing
partial compilation state. The kernel blob boundary is the clean line: if you need a word available at
compile time, put it in a custom kernel built out of band.

`,` and `!` are mirrors of each other:
- `,word` — runtime call, emits `(call $word)`, affects the runtime stack
- `!word` — compile-time execution, emits nothing, affects only the compile-time stack

---

## Stack Effect Inference

Stack effects are inferred by left-to-right simulation of the compile-time stack. No explicit stack effect
annotations are required on any word.

The algorithm for a word definition:

1. Start with an empty compile-time stack and an empty param list
2. For each token, look up its `(inputs, outputs)` in the primitive table or dictionary
3. For each input type required: if the stack has a value, pop it; if the stack is empty, the value must
   come from a function parameter — append its type to the param list and assign it the next index (`$0`, `$1`, ...)
4. Push the output types onto the stack
5. At the closing `;`, whatever remains on the stack becomes the `result` types

The dictionary entry for a word — its `(inputs, outputs)` — is written exactly once, at the **closing
`;`**. This is the `;` at nesting depth 0, i.e. not inside any `if/then` block. Early return `;`s inside
the body are at depth > 0 and emit `(return)` without closing the definition.

### `if / then` branches

At `if`, the transpiler saves a snapshot of the current stack state, simulates the taken branch, and
records its stack effect. At `then` the simulation resumes from the snapshot and verifies that the
fall-through path yields the same stack effect as the taken branch. Unbalanced branches are an error.

### Recursive calls

A recursive call (`,word` appearing inside `:word`) is encountered before the closing `;`, so the
dictionary entry has not yet been written. However by the time the recursive call is reached, the param
list is already fully determined by the simulation up to that point. The recursive call is assumed to have
the same signature as the word being defined — consuming the same param types and producing the same result
types. This is consistent by definition for well-formed recursive words, and any inconsistency will be
caught by `wat2wasm`.

---

## WAT Emission Rules

- Each word definition becomes a WAT `func`.
- Function parameters are derived from the param list accumulated during stack effect inference.
- Parameter names (`$0`, `$1`, ...) are assigned by the transpiler in order.
- Literals (`$n`) emit `i32.const n` (or the appropriate type).
- Calls (`,word`) emit `(call $word)`.
- `;` at depth > 0 emits `(return)`. `;` at depth 0 closes the definition; `(return)` is implicit in WAT at end of func.
- WAT's `if` consumes its condition; the transpiler emits `local.get` as needed to restore the peeked value in both the taken branch and the fall-through path.
- No inlining. No optimization. One `call` per `,word`.



---

## Examples

### Addition

```wasm4th
:add i32.add ;
```

```wat
(func $add (param i32) (param i32) (result i32)
  (i32.add)
)
```

---

### Double

```wasm4th
:double $2 i32.mul ;
```

```wat
(func $double (param i32) (result i32)
  (i32.const 2)
  (local.get $0)
  (i32.mul)
)
```

---

### Conditional with early return

```wasm4th
:countdown ,drop ;
:loop $0 i32.gt_s if ,countdown ; then ,loop ;
```

```wat
(func $countdown (param i32))

(func $loop (param i32)
  (i32.const 0)
  (local.get $0)
  (i32.gt_s)
  (if
    (then
      (call $countdown)
      (return)
    )
  )
  (call $loop)
)
```

---

### Absolute value (`abs`)

Demonstrates non-consuming `if` — the original value remains on the stack in both branches without any
explicit `local.get` in `wasm4th` source. The transpiler emits the necessary `local.get` in WAT.

```wasm4th
:abs $0 i32.lt_s if i32.neg ; then ;
```

```wat
(func $abs (param i32) (result i32)
  (i32.const 0)
  (local.get $0)
  (i32.lt_s)
  (if
    (then
      (local.get $0)
      (i32.neg)
      (return)
    )
  )
  (local.get $0)
)
```

---

### `min`, `max`, and `clamp`

Demonstrates compile-time execution via `!` — `!swap` and `!drop` manipulate the compile-time stack
without emitting any WAT. `clamp` composes `max` and `min` naturally and reads almost like a spec.

```wasm4th
:min i32.ge_s if !swap then !drop ;
:max i32.le_s if !swap then !drop ;
:clamp ,max ,min ;
```

---

### Factorial

Demonstrates recursion and the compile-time `!drop` needed in the base case due to non-consuming `if`.

```wasm4th
:factorial $1 i32.le_s if !drop $1 ; then $1 i32.sub ,factorial i32.mul ;
```

```wat
(func $factorial (param i32) (result i32)
  (i32.const 1)
  (local.get $0)
  (i32.le_s)
  (if
    (then
      (drop)
      (i32.const 1)
      (return)
    )
  )
  (local.get $0)
  (i32.const 1)
  (i32.sub)
  (call $factorial)
  (local.get $0)
  (i32.mul)
)
```

---

## Open Todos

These constructs are designed but not yet fully worked through with examples:

- **`block` / `loop` / `end`** — nesting and label depth tracking; `end` closes the scope, `#n br` and `#n br_if` are depth-relative
- **`local.set` / `local.tee` / local declaration** — scratch locals inside word bodies; tied to block/loop scoping design
- **`br_table`** — consumes a list slot of depth indices plus a default; e.g. `[ #0 #1 #2 ] br_table`
- **`global`** — module-level global declaration with name, mutability, type, and initial value
- **Macros** — will be added as a prefix; fall in naturally once the core is solid

## Out of Scope

- **Optimization** — entirely delegated to `wasm-opt`
- **Type checking** — entirely delegated to `wat2wasm`
- **`create` / `does>` / `postpone` / `immediate`** — not planned; the single-mode parser eliminates the need for most of these
- **`else`** — not needed; early return via `;` suffices

