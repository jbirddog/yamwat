#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.11"
# dependencies = [
#   "wasmtime",
# ]
# ///
# run.py — async_approval example runner
# usage: uv run run.py --report_id=1
#
# The host simulates async by calling back into the wasm instance immediately.
# In a real system, request_manager_approval and request_finance_review would
# schedule work and return — the callback would fire later. Here they fire
# inline, but the wasm instance is re-entered the same way either way.

import argparse
import os
from wasmtime import Engine, Store, Linker, Module, FuncType, ValType, Func

REPORTS = {
    1: {"submitter": "Alice", "manager_approves": True,  "finance_approves": True},   # approved
    2: {"submitter": "Bob",   "manager_approves": False, "finance_approves": True},   # rejected by manager
    3: {"submitter": "Carol", "manager_approves": True,  "finance_approves": False},  # rejected by finance
}

table = None   # set after instantiation
store = None   # set after instantiation

def validate_report(report_id):
    result = report_id in REPORTS
    print(f"  validate_report({report_id}) -> {int(result)}")
    return int(result)

def request_manager_approval(report_id, callback_index):
    report = REPORTS.get(report_id, {})
    approved = int(report.get("manager_approves", False))
    print(f"  request_manager_approval({report_id}) -> approved={approved}  [calling back at index {callback_index}]")
    # re-enter the wasm instance at the callback index
    table.get(store, callback_index)(store, report_id, approved)

def request_finance_review(report_id, callback_index):
    report = REPORTS.get(report_id, {})
    approved = int(report.get("finance_approves", False))
    print(f"  request_finance_review({report_id}) -> approved={approved}  [calling back at index {callback_index}]")
    # re-enter the wasm instance at the callback index
    table.get(store, callback_index)(store, report_id, approved)

def confirm_report(report_id):
    name = REPORTS[report_id]["submitter"] if report_id in REPORTS else f"report {report_id}"
    print(f"REPORT APPROVED: {name}'s expense report confirmed")

def decline_report(report_id):
    name = REPORTS[report_id]["submitter"] if report_id in REPORTS else f"report {report_id}"
    print(f"REPORT DECLINED: {name}'s expense report rejected")

def main():
    global table, store

    parser = argparse.ArgumentParser()
    parser.add_argument("--impl", default="async_approval")
    parser.add_argument("--report_id", type=int, required=True)
    args = parser.parse_args()

    wasm_path = os.path.join(os.path.dirname(__file__), f"{args.impl}.wasm")
    wasm = open(wasm_path, "rb").read()

    engine = Engine()
    store = Store(engine)
    linker = Linker(engine)

    i32 = ValType.i32()
    linker.define(store, "env", "validate_report",
        Func(store, FuncType([i32], [i32]), validate_report))
    linker.define(store, "env", "request_manager_approval",
        Func(store, FuncType([i32, i32], []), request_manager_approval))
    linker.define(store, "env", "request_finance_review",
        Func(store, FuncType([i32, i32], []), request_finance_review))
    linker.define(store, "env", "confirm_report",
        Func(store, FuncType([i32], []), confirm_report))
    linker.define(store, "env", "decline_report",
        Func(store, FuncType([i32], []), decline_report))

    instance = linker.instantiate(store, Module(engine, wasm))
    exports = instance.exports(store)

    table = exports["callbacks"]
    exports["process_report"](store, args.report_id)

if __name__ == "__main__":
    main()
