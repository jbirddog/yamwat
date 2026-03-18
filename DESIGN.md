# yamwat

`yamwat` is a simple transpiler from yaml to wat, the webassembly text format. From there a tool like `wat2wasm`
can be used to create webassembly binaries. A main design goal of the project is to keep things straightforward
and try to maintain the beauty of the wat format itself - as much as that can be done in yaml.

The structure of the yaml file tries to be as 1:1 with wat as possible. Some additions are the transpiler,
`yamwat.py`, has added support for two custom yaml tags. `!include` allows including another file and `!raw`
allows embedded raw wat strings. The `func` declarations also support `export: True` which will infer the name
of the export from the func_id.

A simple example of the yaml can be found in `simple.yaml`. A more complicated example is `math.yaml` which
includes an example of the `!include tag`. The files `env.logging.yaml` and `env.signatures.yaml` are examples
of files that declares reusuable definitions. The transpiler also uses the `!include` tags to generate `.d`
files for dependency tracking.

These definitions do not have to be included, but can be added directly as the first document in a yaml file.
`snippet_test.yaml` has an example of this. In this case the definition declares code snippets that can be inlined
into func bodies.

Currently the project is tested by running `test_yamwat.py` in a docker container. It has CI set up that runs the
same Dockerfile as is used in development.

The project lives here - https://github.com/jbirddog/yamwat
