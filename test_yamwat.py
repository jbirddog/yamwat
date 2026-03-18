"""
test_yamwat.py - yamwat build and instantiation tests

Replaces the Makefile. For each fixture, runs the full pipeline:
  yamwat.py → wat2wasm → wasmtime instantiate

To add a new fixture:
  - Add an entry to FIXTURES with its path (relative to this file)
    and any host imports the module needs to instantiate.

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
# Fixtures
#
# Each entry is:
#   (path_relative_to_this_file, host_imports)
#
# host_imports is a dict of the form:
#   { "module_name": { "func_name": (FuncType, callable) } }
#
# Leave host_imports as {} for modules with no imports.
# ---------------------------------------------------------------------------

FIXTURES = [
    ("simple.yaml",      {}),
    ("math.yaml",        {"env": {"log": (FuncType([ValType.i32()], []), lambda x: None)}}),
    ("memory_demo.yaml", {}),
    ("table_demo.yaml",  {}),
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

        subprocess.run(
            ["wat2wasm", wat_path, "-o", wasm_path],
            capture_output=True,
            text=True,
            check=True,
        )

        with open(wasm_path, "rb") as f:
            return f.read()

    finally:
        for path in (wat_path, wasm_path):
            if os.path.exists(path):
                os.unlink(path)

# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("yaml_rel,host_imports", FIXTURES, ids=[f[0] for f in FIXTURES])
def test_compiles_and_instantiates(yaml_rel, host_imports):
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

    linker.instantiate(store, Module(engine, wasm))
