#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.11"
# dependencies = [
#   "wasmtime",
# ]
# ///
# host.py — content moderation host runner
# loads policies on demand by inspecting wasm imports recursively,
# routes posts to the right policy via community_id
#
# usage:
#   uv run host.py --post_id=1
#   uv run host.py --community_id=2

import argparse
import os
import struct
from wasmtime import Engine, Store, Linker, Module, FuncType, ValType, Func

# ---------------------------------------------------------------------------
# verdicts — mirrors verdicts.yaml
# ---------------------------------------------------------------------------

VERDICTS = {
    0: "APPROVE",
    1: "HOLD",
    2: "ESCALATE",
    3: "REMOVE",
}

# ---------------------------------------------------------------------------
# post database
# ---------------------------------------------------------------------------
# each post covers an interesting branch across both policies
#
#   fields: score, flag_count, word_count, author_tier, is_repost,
#           community_id, author_id
#
# author_tier: 0=new, 1=established, 2=trusted
# community_id: 1=standard, 2=strict

POSTS = {
    #                        score  flags  words  tier  repost  community  author
    1:  {"desc": "clean post from trusted author",
         "data": ( 42,    0,   120,    2,     0,       1,      10)},
    2:  {"desc": "new author, not yet flagged",
         "data": (  5,    0,    80,    0,     0,       1,      11)},
    3:  {"desc": "low score, established author",
         "data": (-15,    1,   200,    1,     0,       1,      12)},
    4:  {"desc": "repost with flags",
         "data": ( 10,    2,    50,    1,     1,       1,      13)},
    5:  {"desc": "heavily flagged, should remove",
         "data": (  8,   12,   300,    2,     0,       1,      14)},
    6:  {"desc": "clean post, strict community, trusted author",
         "data": ( 20,    0,   100,    2,     0,       2,      10)},
    7:  {"desc": "established author, strict community",
         "data": ( 15,    0,    90,    1,     0,       2,      12)},
    8:  {"desc": "repost, strict community — escalate regardless",
         "data": ( 30,    0,   150,    2,     1,       2,      10)},
    9:  {"desc": "3 flags in strict community — remove",
         "data": (  5,    3,    80,    2,     0,       2,      14)},
    10: {"desc": "zero score, strict community",
         "data": (  0,    0,   200,    2,     0,       2,      10)},
}

# ---------------------------------------------------------------------------
# community -> policy mapping
# ---------------------------------------------------------------------------

COMMUNITY_POLICY = {
    1: "standard_policy",
    2: "strict_policy",
}

# ---------------------------------------------------------------------------
# host env functions
# ---------------------------------------------------------------------------

def make_env_functions(store, memory_ref):
    """
    Returns the env capability functions wired to the given store.
    These are the only functions the host provides — outcome decisions
    are now made by the host after moderate() returns a verdict.
    memory_ref is a mutable list so the memory export can be set
    after instantiation and still be visible to the closure.
    """
    i32 = ValType.i32()

    def get_post(post_id, ptr):
        post = POSTS.get(post_id)
        if not post:
            print(f"  [host] get_post({post_id}) — unknown post")
            return
        score, flag_count, word_count, author_tier, is_repost, community_id, author_id = post["data"]
        packed = struct.pack("<iiiiiii",
            score, flag_count, word_count, author_tier,
            is_repost, community_id, author_id)
        memory_ref[0].write(store, packed, ptr)

    return {
        "get_post": (FuncType([i32, i32], []), get_post),
    }

# ---------------------------------------------------------------------------
# policy loader
# ---------------------------------------------------------------------------

def load_policy(name, here, engine, store, linker, loaded):
    """
    Recursively load a policy and all its dependencies.
    Dependencies are discovered by inspecting the wasm import section —
    any import whose module is not 'env' is a policy dependency.
    Results are cached in loaded: name -> exports.
    """
    if name in loaded:
        return loaded[name]

    wasm_path = os.path.join(here, f"{name}.wasm")
    if not os.path.exists(wasm_path):
        raise SystemExit(f"error: {wasm_path} not found — run yamwat + wat2wasm first")

    wasm = open(wasm_path, "rb").read()
    module = Module(engine, wasm)

    # inspect imports — load any policy dependencies first
    for imp in module.imports:
        if imp.module != "env":
            dep_exports = load_policy(imp.module, here, engine, store, linker, loaded)
            # wire the dependency's moderate function into the linker
            # under its module name so this policy can import it
            i32 = ValType.i32()
            linker.define(
                store, imp.module, imp.name,
                Func(store, FuncType([i32], [i32]),
                     lambda post_id, e=dep_exports: e["moderate"](store, post_id))
            )

    # wire env functions for leaf policies
    memory_ref = [None]
    env_funcs = make_env_functions(store, memory_ref)

    for func_name, (ftype, impl) in env_funcs.items():
        # linker.define is idempotent for the same name so env functions
        # defined for a previous policy are safely reused here
        try:
            linker.define(store, "env", func_name, Func(store, ftype, impl))
        except Exception:
            pass  # already defined for this store

    instance = linker.instantiate(store, module)
    exports = instance.exports(store)

    if "mem" in exports:
        memory_ref[0] = exports["mem"]

    loaded[name] = exports
    print(f"loaded {name}.wasm")
    return exports


def load_policies(engine, here):
    """
    Load and instantiate all community policies.
    A single store and linker are shared so wasm instances can
    cross-call each other's exported functions.
    Returns a dict of policy_name -> exports.
    """
    store = Store(engine)
    linker = Linker(engine)
    loaded = {}

    for policy_name in COMMUNITY_POLICY.values():
        load_policy(policy_name, here, engine, store, linker, loaded)

    return store, loaded

# ---------------------------------------------------------------------------
# moderation
# ---------------------------------------------------------------------------

def moderate_post(post_id, store, loaded):
    post = POSTS.get(post_id)
    if not post:
        print(f"unknown post_id {post_id}")
        return

    _, _, _, _, _, community_id, _ = post["data"]
    policy_name = COMMUNITY_POLICY.get(community_id)
    if not policy_name:
        print(f"  no policy for community_id {community_id}")
        return

    exports = loaded[policy_name]

    print(f"post {post_id}: {post['desc']}")
    print(f"  policy: {policy_name}")

    verdict = exports["moderate"](store, post_id)
    print(f"  => {VERDICTS.get(verdict, f'unknown({verdict})')}")


# ---------------------------------------------------------------------------
# entry point
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser()
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--post_id", type=int)
    group.add_argument("--community_id", type=int)
    args = parser.parse_args()

    here = os.path.dirname(os.path.abspath(__file__))
    engine = Engine()

    print("--- loading policies ---")
    store, loaded = load_policies(engine, here)
    print()

    print("--- moderating ---")
    if args.post_id:
        moderate_post(args.post_id, store, loaded)
    elif args.community_id:
        posts = [
            pid for pid, p in POSTS.items()
            if p["data"][5] == args.community_id
        ]
        if not posts:
            print(f"no posts for community_id {args.community_id}")
            return
        for post_id in posts:
            moderate_post(post_id, store, loaded)
            print()


if __name__ == "__main__":
    main()
