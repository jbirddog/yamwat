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
import struct
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


def assert_access_check_with_struct(exports, store):
    # User struct layout: age (i32 @ 0), residence (i32 @ 4), membership_tier (i32 @ 8)
    # residence: 1=CA, 2=NV, 3=other  |  membership_tier: 0=none, 1=basic, 2=premium
    memory = exports["mem"]

    def write_user(ptr, age, residence, tier):
        packed = struct.pack("<iii", age, residence, tier)
        data = memory.data_ptr(store)
        for i, b in enumerate(packed):
            data[ptr + i] = b

    # host stubs — get_user writes the struct, grant/deny record the outcome
    ctx = {"last": None}

    def get_user(user_id, ptr):
        users = {
            1: (25, 2, 2),  # age=25, NV, premium  — passes all
            2: (19, 2, 1),  # age=19, NV, basic     — fails age
            3: (30, 1, 2),  # age=30, CA, premium   — fails residence
            4: (22, 3, 0),  # age=22, other, none   — fails tier
        }
        write_user(ptr, *users[user_id])

    def grant(user_id):
        ctx["last"] = "grant"

    def deny(user_id):
        ctx["last"] = "deny"

    i32 = ValType.i32()
    # bind stubs directly against exports — host_imports mechanism can't reach
    # memory until after instantiation, so we exercise via direct calls here
    get_user_f  = Func(store, FuncType([i32, i32], []), get_user)
    grant_f     = Func(store, FuncType([i32],      []), grant)
    deny_f      = Func(store, FuncType([i32],      []), deny)

    def check(user_id):
        # manually invoke the workflow: push user struct then call check_access
        get_user(user_id, 0)
        exports["check_access"](store, user_id)
        return ctx["last"]

    # Note: check_access calls get_user via the import, not directly — we need
    # to go through the full wasm instance. Use a fresh linker approach is not
    # possible here since the instance is already built. Instead we wire stubs
    # at instantiation time via host_imports in the FIXTURES entry below, and
    # assert_access_check_with_struct only verifies outcomes via grant/deny
    # which are captured in the ctx closure shared with those stubs.
    assert exports["check_access"](store, 1) is None  # smoke — just ensure no trap
    # full branch assertions are done via the host_imports closure below


def assert_access_check_with_struct_full(exports, store, ctx):
    """Called from the host_imports closure after stubs are wired."""
    # user 1: passes all checks
    exports["check_access"](store, 1)
    assert ctx["last"] == "grant"

    # user 2: fails age (19 < 21)
    exports["check_access"](store, 2)
    assert ctx["last"] == "deny"

    # user 3: fails residence (CA)
    exports["check_access"](store, 3)
    assert ctx["last"] == "deny"

    # user 4: fails membership tier (none)
    exports["check_access"](store, 4)
    assert ctx["last"] == "deny"


def assert_async_approval_full(exports, store, ctx):
    """Called from the host_imports closure after table is wired."""
    # report 1: approved by both
    exports["process_report"](store, 1)
    assert ctx["last"] == "confirm"

    # report 2: rejected by manager
    exports["process_report"](store, 2)
    assert ctx["last"] == "decline"

    # report 3: approved by manager, rejected by finance
    exports["process_report"](store, 3)
    assert ctx["last"] == "decline"


# ---------------------------------------------------------------------------
# host_imports factories
#
# For fixtures that need post-instantiation state (memory writes, table
# callbacks), we use a factory that returns (host_imports_dict, assert_fn).
# The host stubs close over a shared ctx dict; assert_fn is called with
# (exports, store, ctx) after instantiation.
# ---------------------------------------------------------------------------

def make_access_check_with_struct_imports(store_ref):
    """
    Returns (host_imports, assert_fn). host_imports stubs close over a ctx
    dict and a memory reference filled in after instantiation.
    """
    ctx = {"last": None, "memory": None}

    def get_user(user_id, ptr):
        users = {
            1: (25, 2, 2),  # age=25, NV, premium  — passes all
            2: (19, 2, 1),  # age=19, NV, basic     — fails age
            3: (30, 1, 2),  # age=30, CA, premium   — fails residence
            4: (22, 3, 0),  # age=22, other, none   — fails tier
        }
        age, residence, tier = users[user_id]
        packed = struct.pack("<iii", age, residence, tier)
        ctx["memory"].write(store_ref[0], packed, ptr)

    def grant(user_id):
        ctx["last"] = "grant"

    def deny(user_id):
        ctx["last"] = "deny"

    i32 = ValType.i32()
    host_imports = {"env": {
        "get_user": (FuncType([i32, i32], []), get_user),
        "grant":    (FuncType([i32],      []), grant),
        "deny":     (FuncType([i32],      []), deny),
    }}

    def assert_fn(exports, store):
        ctx["memory"] = exports["mem"]
        assert_access_check_with_struct_full(exports, store, ctx)

    return host_imports, assert_fn


def make_async_approval_imports(store_ref):
    """
    Returns (host_imports, assert_fn). Callback stubs re-enter wasm via the
    exported table, which is set into ctx after instantiation.
    """
    ctx = {"last": None, "table": None}

    REPORTS = {
        1: {"manager_approves": True,  "finance_approves": True},
        2: {"manager_approves": False, "finance_approves": True},
        3: {"manager_approves": True,  "finance_approves": False},
    }

    def validate_report(report_id):
        return int(report_id in REPORTS)

    def request_manager_approval(report_id, callback_index):
        approved = int(REPORTS.get(report_id, {}).get("manager_approves", False))
        ctx["table"].get(store_ref[0], callback_index)(store_ref[0], report_id, approved)

    def request_finance_review(report_id, callback_index):
        approved = int(REPORTS.get(report_id, {}).get("finance_approves", False))
        ctx["table"].get(store_ref[0], callback_index)(store_ref[0], report_id, approved)

    def confirm_report(report_id):
        ctx["last"] = "confirm"

    def decline_report(report_id):
        ctx["last"] = "decline"

    i32 = ValType.i32()
    host_imports = {"env": {
        "validate_report":           (FuncType([i32],      [i32]), validate_report),
        "request_manager_approval":  (FuncType([i32, i32], []),    request_manager_approval),
        "request_finance_review":    (FuncType([i32, i32], []),    request_finance_review),
        "confirm_report":            (FuncType([i32],      []),    confirm_report),
        "decline_report":            (FuncType([i32],      []),    decline_report),
    }}

    def assert_fn(exports, store):
        ctx["table"] = exports["callbacks"]
        assert_async_approval_full(exports, store, ctx)

    return host_imports, assert_fn


# ---------------------------------------------------------------------------
# Fixtures
#
# Each entry is:
#   (path_relative_to_this_file, host_imports, assertions)
#
# host_imports is a dict of the form:
#   { "module_name": { "func_name": (FuncType, callable) } }
#
# For fixtures that need post-instantiation state, host_imports is a factory
# callable (store_ref) -> (host_imports_dict, assert_fn). The test runner
# detects this and calls it appropriately.
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

    ("tests/access_check_with_struct.yaml",
     make_access_check_with_struct_imports,
     None),  # assert_fn returned by factory

    ("tests/async_approval.yaml",
     make_async_approval_imports,
     None),  # assert_fn returned by factory
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
    store_ref = [store]  # mutable ref for factory closures
    linker = Linker(engine)
    store.set_wasi(WasiConfig())
    linker.define_wasi()

    # host_imports may be a plain dict or a factory callable
    factory_assert_fn = None
    if callable(host_imports) and not isinstance(host_imports, dict):
        host_imports, factory_assert_fn = host_imports(store_ref)

    for module_name, funcs in host_imports.items():
        for func_name, (ftype, impl) in funcs.items():
            linker.define(store, module_name, func_name, Func(store, ftype, impl))

    instance = linker.instantiate(store, Module(engine, wasm))
    exports = instance.exports(store)

    if factory_assert_fn:
        factory_assert_fn(exports, store)
    elif assertions:
        assertions(exports, store)
