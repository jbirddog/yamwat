.PHONY: img run

img:
	docker build -t yamwat .

run:
	docker run --rm -v .:/src yamwat
