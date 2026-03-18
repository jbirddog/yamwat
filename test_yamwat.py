"""
test_yamwat.py - yamwat build and instantiation tests

Replaces the Makefile. For each fixture, runs the full pipeline:
  yamwat.py → wat2wasm → wasmtime instantiate

To add a new fixture:
  - Optionally define an assert_<n>(exports, store) function below.
  - Add an entry to FIXTURES with its path (relative to this file),
    any host imports the module needs, and the assertion function (or None).

Run with:
  pytest test_yamwat.py -v
"""

import os
import subprocess
import pytest
from wasmtime import Engine, Store, Linker, Module, WasiConfig, FuncType, ValType, Func

HERE = os.path.dirname(os.path.abspath(__file__))
YAMWAT = os.path.join(HERE, "yamwat.py")

# ---------------------------------------------------------------------------
# Assertion functions
#
# Each receives (exports, store) after instantiation.
# exports["name"](store, arg, ...) calls an exported function.
# ---------------------------------------------------------------------------

def assert_simple(exports, store):
    assert exports["add"](store, 0, 0) == 0
    assert exports["add"](store, 3, 4) == 7
    assert exports["add"](store, -1, 1) == 0


def assert_math(exports, store):
    assert exports["double"](store, 0) == 0
    assert exports["double"](store, 6) == 12

    assert exports["factorial"](store, 0) == 1
    assert exports["factorial"](store, 1) == 1
    assert exports["factorial"](store, 5) == 120

    assert exports["abs"](store, 0) == 0
    assert exports["abs"](store, 3) == 3
    assert exports["abs"](store, -3) == 3


def assert_table_demo(exports, store):
    assert exports["call_by_index"](store, 0) == 42
    assert exports["call_by_index"](store, 1) == 13


def assert_snippet_test(exports, store):
    # safe_double: returns -1 for negative input, double otherwise
    assert exports["safe_double"](store, 3) == 6
    assert exports["safe_double"](store, 0) == 0
    assert exports["safe_double"](store, -1) == -1

    # clamp_and_double: negatives clamp to 0 then double (so 0), positives double
    assert exports["clamp_and_double"](store, 4) == 8
    assert exports["clamp_and_double"](store, 0) == 0
    assert exports["clamp_and_double"](store, -5) == 0


# ---------------------------------------------------------------------------
# Fixtures
#
# Each entry is:
#   (path_relative_to_this_file, host_imports, assertions)
#
# host_imports is a dict of the form:
#   { "module_name": { "func_name": (FuncType, callable) } }
#
# assertions is a function (exports, store) -> None, or None for a smoke test.
# ---------------------------------------------------------------------------

FIXTURES = [
    ("tests/simple.yaml",
     {},
     assert_simple),

    ("tests/math.yaml",
     {"env": {"log": (FuncType([ValType.i32()], []), lambda x: None)}},
     assert_math),

    ("tests/memory_demo.yaml",
     {},
     None),

    ("tests/table_demo.yaml",
     {},
     assert_table_demo),

    ("tests/snippet_test.yaml",
     {},
     assert_snippet_test),
]

# ---------------------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------------------

def compile_to_wasm(yaml_path):
    """Run yamwat.py then wat2wasm. Returns wasm bytes. Cleans up generated files."""
    yaml_path = os.path.abspath(yaml_path)
    base = os.path.splitext(yaml_path)[0]
    wat_path = base + ".wat"
    wasm_path = base + ".wasm"

    try:
        result = subprocess.run(
            ["python3", YAMWAT, yaml_path],
            capture_output=True,
            text=True,
            cwd=os.path.dirname(yaml_path),
        )
        if result.returncode != 0:
            pytest.fail(f"yamwat failed:\n{result.stderr}")

        result = subprocess.run(
            ["wat2wasm", wat_path, "-o", wasm_path],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            pytest.fail(f"wat2wasm failed:\n{result.stderr}")

        with open(wasm_path, "rb") as f:
            return f.read()

    finally:
        for path in (wat_path, wasm_path):
            if os.path.exists(path):
                os.unlink(path)

# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("yaml_rel,host_imports,assertions", FIXTURES, ids=[f[0] for f in FIXTURES])
def test_compiles_and_instantiates(yaml_rel, host_imports, assertions):
    yaml_path = os.path.join(HERE, yaml_rel)
    if not os.path.exists(yaml_path):
        pytest.skip(f"{yaml_rel} not found")

    wasm = compile_to_wasm(yaml_path)

    engine = Engine()
    store = Store(engine)
    linker = Linker(engine)
    store.set_wasi(WasiConfig())
    linker.define_wasi()

    for module_name, funcs in host_imports.items():
        for func_name, (ftype, impl) in funcs.items():
            linker.define(store, module_name, func_name, Func(store, ftype, impl))

    instance = linker.instantiate(store, Module(engine, wasm))

    if assertions:
        assertions(instance.exports(store), store)
