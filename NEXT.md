1. In definition yaml files, should the `key: &key` be the default shown or something like `_: &key`, `:: &key`
1. Reference other yamls (subworkflows)
1. Diagram from yaml(s)

## wasm as config

```
Ha, that's a great property. The entire moderation policy for a community — all the logic, all the thresholds — fits in something smaller than a typical JSON API response. Loading and caching a dozen of those at startup costs essentially nothing, and shipping a new policy is just swapping a 200-byte blob.
It also makes the "wasm as config" framing feel very concrete. Most systems would represent policy as a JSON/YAML config file interpreted at runtime — but that means building and maintaining an interpreter, handling malformed configs gracefully, and the policy logic is only as expressive as your config schema allows. Here the policy is the executable, it's already validated by wat2wasm, and it's still tiny. You get expressiveness for free without paying for it in size.
That 200 bytes will grow as policies get more complex, but probably not by much — even a fairly elaborate policy with a dozen checks and a struct with 20 fields is still going to be kilobytes at most. The wasm binary format is remarkably compact for what it represents.
```
