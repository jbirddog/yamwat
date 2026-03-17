# yamwat

`yamwat` is a simplistic transpiler from `yaml` to `wat`, the webassembly text format. From there a tool like 
`wat2wasm` can be used to compile a `wasm` file. It is used as a way for me to explore the webassembly text 
and binary formats and the tooling around them.

## Example

A very basic example, `simple.yaml`:

```
module: $simple

func $add:
  export: True
  param: [$a i32, $b i32]
  result: i32
  body: [local.get $a, local.get $b, i32.add]
```

Is transpiled to `simple.wat`:

```
(module $simple
  (func $add (param $a i32) (param $b i32) (result i32)
    local.get $a
    local.get $b
    i32.add
  )
  (export "add" (func $add))
)
```

Which when compiled to wasm can be run:

```
$ wasmer run simple.wasm --entrypoint add 5 6
11
```

## Yaml Structure

The structure of the yaml file is almost a 1:1 with wat, however two custom yaml tags are used:

`!include` allows including another yaml file, which typically contain reusuable definitions.

`!raw` which acts as an escape hatch and can allow embedding wat strings directly
