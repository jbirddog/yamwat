1. Diagram from yaml(s)
1. host/moderator/util.yaml
1. Text level `::` handling
1. Split some yaml parts out into own lib

## wasm as config

```
Ha, that's a great property. The entire moderation policy for a community — all the logic, all the thresholds — fits in something smaller than a typical JSON API response. Loading and caching a dozen of those at startup costs essentially nothing, and shipping a new policy is just swapping a 200-byte blob.
It also makes the "wasm as config" framing feel very concrete. Most systems would represent policy as a JSON/YAML config file interpreted at runtime — but that means building and maintaining an interpreter, handling malformed configs gracefully, and the policy logic is only as expressive as your config schema allows. Here the policy is the executable, it's already validated by wat2wasm, and it's still tiny. You get expressiveness for free without paying for it in size.
That 200 bytes will grow as policies get more complex, but probably not by much — even a fairly elaborate policy with a dozen checks and a struct with 20 fields is still going to be kilobytes at most. The wasm binary format is remarkably compact for what it represents.
```

## rules engine

```
yamwat future exploration: game rules engine
yamwat is a YAML→WAT transpiler whose north star is a hosting provider that exposes host functions as capabilities. We've built a working content moderation prototype (verdict model, policy composition, recursive wasm import resolution).
Proposed next host application: a game rules engine / turn validator. The host maintains game state in memory. A wasm blob encodes rules for a specific game (move legality, win conditions, action validity). Host calls validate_move(game_id, player_id, move_encoded) -> i32 then apply_move if valid.
What to stress-test:

Richer input encoding (a move is more than one i32)
Boundary between host-owned state and wasm-owned rules
Swapping rule sets mid-game (variant/house rules as a different blob)
```
