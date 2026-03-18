#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.11"
# dependencies = [
#   "wasmtime",
# ]
# ///
# run.py — access_check example runner
# usage: uv run run.py [--impl=access_check] --user_id=1

import argparse
import os
from wasmtime import Engine, Store, Linker, Module, FuncType, ValType, Func

USERS = {
    1: {"name": "Alice", "age": 22},
    2: {"name": "Bob",   "age": 16},
}

def get_user_age(user_id):
    return USERS[user_id]["age"] if user_id in USERS else 0

def grant(user_id):
    print(f"ACCESS GRANTED for {USERS[user_id]['name']}")

def deny(user_id):
    print(f"ACCESS DENIED for {USERS[user_id]['name']}")

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--impl", default="access_check")
    parser.add_argument("--user_id", type=int, required=True)
    args = parser.parse_args()

    wasm_path = os.path.join(os.path.dirname(__file__), f"{args.impl}.wasm")
    wasm = open(wasm_path, "rb").read()

    engine = Engine()
    store = Store(engine)
    linker = Linker(engine)

    i32 = ValType.i32()
    linker.define(store, "env", "get_user_age", Func(store, FuncType([i32], [i32]), get_user_age))
    linker.define(store, "env", "grant",        Func(store, FuncType([i32], []),    grant))
    linker.define(store, "env", "deny",         Func(store, FuncType([i32], []),    deny))

    instance = linker.instantiate(store, Module(engine, wasm))
    instance.exports(store)["check_access"](store, args.user_id)

if __name__ == "__main__":
    main()
