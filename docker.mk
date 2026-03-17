.PHONY: build run

build:
	docker build -t yamwat .

run:
	docker run --rm -v .:/src yamwat
