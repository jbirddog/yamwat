#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.11"
# dependencies = [
#   "wasmtime",
# ]
# ///
# run.py — access_check_with_struct example runner
# usage: uv run run.py [--impl=access_check_with_struct] --user_id=1
#
# User struct layout written into wasm linear memory by get_user:
#   offset 0: age              (i32)
#   offset 4: residence        (i32)  1=CA, 2=NV, 3=other
#   offset 8: membership_tier  (i32)  0=none, 1=basic, 2=premium

import argparse
import os
import struct
from wasmtime import Engine, Store, Linker, Module, FuncType, ValType, Func, Memory

USERS = {
    1: {"name": "Alice",   "age": 25, "residence": 2, "membership_tier": 2},  # passes all checks
    2: {"name": "Bob",     "age": 19, "residence": 2, "membership_tier": 1},  # fails age
    3: {"name": "Carol",   "age": 30, "residence": 1, "membership_tier": 2},  # fails residence (CA)
    4: {"name": "Dave",    "age": 22, "residence": 3, "membership_tier": 0},  # fails membership
}

RESIDENCE_NAMES = {1: "CA", 2: "NV", 3: "other"}
TIER_NAMES      = {0: "none", 1: "basic", 2: "premium"}

wasm_memory = None  # set after instantiation

def get_user(user_id, ptr):
    user = USERS.get(user_id)
    if user is None:
        return
    packed = struct.pack("<iii", user["age"], user["residence"], user["membership_tier"])
    data = wasm_memory.data_ptr(store)
    for i, b in enumerate(packed):
        data[ptr + i] = b
    print(
        f"  get_user({user_id}, ptr={ptr}) -> "
        f"age={user['age']}, residence={RESIDENCE_NAMES[user['residence']]}, "
        f"tier={TIER_NAMES[user['membership_tier']]}"
    )

def grant(user_id):
    name = USERS[user_id]["name"] if user_id in USERS else f"user {user_id}"
    print(f"ACCESS GRANTED for {name}")

def deny(user_id):
    name = USERS[user_id]["name"] if user_id in USERS else f"user {user_id}"
    print(f"ACCESS DENIED for {name}")

def main():
    global wasm_memory, store

    parser = argparse.ArgumentParser()
    parser.add_argument("--impl", default="access_check_with_struct")
    parser.add_argument("--user_id", type=int, required=True)
    args = parser.parse_args()

    wasm_path = os.path.join(os.path.dirname(__file__), f"{args.impl}.wasm")
    wasm = open(wasm_path, "rb").read()

    engine = Engine()
    store = Store(engine)
    linker = Linker(engine)

    i32 = ValType.i32()
    linker.define(store, "env", "get_user", Func(store, FuncType([i32, i32], []), get_user))
    linker.define(store, "env", "grant",    Func(store, FuncType([i32],      []), grant))
    linker.define(store, "env", "deny",     Func(store, FuncType([i32],      []), deny))

    instance = linker.instantiate(store, Module(engine, wasm))
    exports = instance.exports(store)

    wasm_memory = exports["mem"]
    exports["check_access"](store, args.user_id)

if __name__ == "__main__":
    main()
