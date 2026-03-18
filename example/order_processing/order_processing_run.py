#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.11"
# dependencies = [
#   "wasmtime",
# ]
# ///
# run.py — order_processing example runner
# usage: uv run run.py --order_id=1

import argparse
import os
from wasmtime import Engine, Store, Linker, Module, FuncType, ValType, Func

ORDERS = {
    1: {"valid": True,  "in_stock": True,  "payment": True},   # happy path
    2: {"valid": False, "in_stock": True,  "payment": True},   # fails validation
    3: {"valid": True,  "in_stock": False, "payment": True},   # fails inventory
    4: {"valid": True,  "in_stock": True,  "payment": False},  # fails payment
}

def validate_order(order_id):
    result = ORDERS.get(order_id, {}).get("valid", False)
    print(f"  validate_order({order_id}) -> {int(result)}")
    return int(result)

def check_inventory(order_id):
    result = ORDERS.get(order_id, {}).get("in_stock", False)
    print(f"  check_inventory({order_id}) -> {int(result)}")
    return int(result)

def charge_payment(order_id):
    result = ORDERS.get(order_id, {}).get("payment", False)
    print(f"  charge_payment({order_id}) -> {int(result)}")
    return int(result)

def confirm_order(order_id):
    print(f"ORDER CONFIRMED: order {order_id}")

def reject_order(order_id):
    print(f"ORDER REJECTED: order {order_id}")

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--impl", default="order_processing")
    parser.add_argument("--order_id", type=int, required=True)
    args = parser.parse_args()

    wasm_path = os.path.join(os.path.dirname(__file__), f"{args.impl}.wasm")
    wasm = open(wasm_path, "rb").read()

    engine = Engine()
    store = Store(engine)
    linker = Linker(engine)

    i32 = ValType.i32()
    linker.define(store, "env", "validate_order",  Func(store, FuncType([i32], [i32]), validate_order))
    linker.define(store, "env", "check_inventory", Func(store, FuncType([i32], [i32]), check_inventory))
    linker.define(store, "env", "charge_payment",  Func(store, FuncType([i32], [i32]), charge_payment))
    linker.define(store, "env", "confirm_order",   Func(store, FuncType([i32], []),    confirm_order))
    linker.define(store, "env", "reject_order",    Func(store, FuncType([i32], []),    reject_order))

    instance = linker.instantiate(store, Module(engine, wasm))
    instance.exports(store)["process_order"](store, args.order_id)

if __name__ == "__main__":
    main()
