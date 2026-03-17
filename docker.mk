ME ?= $(shell id -u):$(shell id -g)

IMG := yamwat
RUN_ARGS := --rm -u $(ME) -v .:/src $(IMG)
RUN := docker run $(RUN_ARGS)
RUN_IT := docker run -it $(RUN_ARGS)

.PHONY: img run sh

img:
	docker build -t $(IMG) .

run:
	$(RUN)

sh:
	$(RUN_IT) /bin/bash
