YAMWAT := ../../yamwat.py
WAT2WASM := wat2wasm

SRCS    := $(wildcard *.yaml)
WATS    := $(SRCS:.yaml=.wat)
WASMS   := $(SRCS:.yaml=.wasm)

# default target — build all wasm binaries
.PHONY: all clean wat

all: $(WASMS)

wat: $(WATS)

# compile yaml → wat (yamwat also writes the .d depfile)
%.wat: %.yaml
	python $(YAMWAT) $<

# assemble wat → wasm
%.wasm: %.wat
	$(WAT2WASM) $< -o $@

# pull in generated dependency files so make knows about !include relationships
-include $(wildcard *.d)

clean:
	rm -f $(WATS) $(WASMS) $(wildcard *.d)
