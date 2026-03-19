#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.11"
# dependencies = [
#   "wasmtime",
# ]
# ///
# host.py — content moderation host runner
# loads all known policies at startup, routes posts to the right policy
# via community_id, calls moderate(post_id) on the appropriate instance
#
# usage:
#   uv run host.py --post_id=1
#   uv run host.py --community_id=2

import argparse
import os
import struct
from wasmtime import Engine, Store, Linker, Module, FuncType, ValType, Func

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
# host functions
# ---------------------------------------------------------------------------

def make_host_functions(store, memory_ref):
    """
    Returns a dict of host functions wired to the given store.
    memory_ref is a mutable list so the memory export can be set
    after instantiation and still be visible to the closures.
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

    def approve(post_id):
        print(f"  => APPROVE")

    def hold(post_id):
        print(f"  => HOLD")

    def remove(post_id):
        print(f"  => REMOVE")

    def escalate(post_id):
        print(f"  => ESCALATE")

    return {
        "env": {
            "get_post": (FuncType([i32, i32], []),  get_post),
            "approve":  (FuncType([i32],      []),  approve),
            "hold":     (FuncType([i32],      []),  hold),
            "remove":   (FuncType([i32],      []),  remove),
            "escalate": (FuncType([i32],      []),  escalate),
        }
    }

# ---------------------------------------------------------------------------
# policy cache
# ---------------------------------------------------------------------------

def load_policies(engine, here):
    """
    Load and instantiate one wasm instance per policy at startup.
    Returns a dict of policy_name -> (instance, store).
    Each instance gets its own store and memory_ref.
    """
    policies = {}

    for community_id, policy_name in COMMUNITY_POLICY.items():
        if policy_name in policies:
            continue

        wasm_path = os.path.join(here, f"{policy_name}.wasm")
        if not os.path.exists(wasm_path):
            raise SystemExit(f"error: {wasm_path} not found — run yamwat + wat2wasm first")

        wasm = open(wasm_path, "rb").read()
        store = Store(engine)
        linker = Linker(engine)
        memory_ref = [None]

        host_functions = make_host_functions(store, memory_ref)
        for module_name, funcs in host_functions.items():
            for func_name, (ftype, impl) in funcs.items():
                linker.define(store, module_name, func_name, Func(store, ftype, impl))

        instance = linker.instantiate(store, Module(engine, wasm))
        exports = instance.exports(store)
        memory_ref[0] = exports["mem"]

        policies[policy_name] = (exports, store)
        print(f"loaded {policy_name}.wasm")

    return policies

# ---------------------------------------------------------------------------
# moderation
# ---------------------------------------------------------------------------

def moderate_post(post_id, policies):
    post = POSTS.get(post_id)
    if not post:
        print(f"unknown post_id {post_id}")
        return

    _, _, _, _, _, community_id, _ = post["data"]
    policy_name = COMMUNITY_POLICY.get(community_id)
    if not policy_name:
        print(f"  no policy for community_id {community_id}")
        return

    exports, store = policies[policy_name]

    print(f"post {post_id}: {post['desc']}")
    print(f"  policy: {policy_name}")
    exports["moderate"](store, post_id)


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
    policies = load_policies(engine, here)
    print()

    print("--- moderating ---")
    if args.post_id:
        moderate_post(args.post_id, policies)
    elif args.community_id:
        posts = [
            pid for pid, p in POSTS.items()
            if p["data"][5] == args.community_id
        ]
        if not posts:
            print(f"no posts for community_id {args.community_id}")
            return
        for post_id in posts:
            moderate_post(post_id, policies)
            print()


if __name__ == "__main__":
    main()
