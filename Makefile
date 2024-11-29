.PHONY: clean clean-all unittests docker-% %-in-docker

IN_DOCKER_TARGETS := unittests clean clean-all

# Use docker buildkit to build images
# https://docs.docker.com/build/buildkit/
export DOCKER_BUILDKIT := 1

DOCKER_BIN=/usr/bin/docker
GIT_BRANCH=$(shell git branch --show-current)
BASE_NAME=$(shell basename $(shell pwd))

BUILD_IMG_NAME=slippy-tile-proxy-buildimage-$(GIT_BRANCH)-$(USER):latest

run:
	@/bin/bash build-and-run-in-docker

unittests:
	@# Search for python files that end in *_test.py and run the unit test.
	@# Only works if the directory contains the __init__.py file
	@python3 -m unittest discover --pattern *_test.py --verbose


# Run docker-ent-env to enter in the build image and debug
# You must first run the docker-build-image target below
docker-enter-env:
	$(DOCKER_BIN) run --rm \
		--interactive --tty \
		--user $(shell id -u):$(shell id -g) \
		--name $(BASE_NAME)-$(GIT_BRANCH)-$(USER) \
		--mount type=bind,source="$(shell pwd)",target=/$(BASE_NAME) \
		--workdir /$(BASE_NAME) \
		--env USER=$${USER} \
		$(BUILD_IMG_NAME) /bin/bash

docker-build-image:
	$(DOCKER_BIN) build --target build_env \
		--build-arg user=$(shell id -un) \
		--build-arg user_id=$(shell id -u) \
		--build-arg group=$(shell id -gn) \
		--build-arg group_id=$(shell id -g) \
		-t $(BUILD_IMG_NAME) \
		-f Dockerfile .

auto_generated_targets = $(addsuffix -in-docker, $(IN_DOCKER_TARGETS))
$(auto_generated_targets): %-in-docker
%-in-docker:
	$(DOCKER_BIN) run --rm \
		--interactive --tty \
		--user $(shell id -u):$(shell id -g) \
		--name $(BASE_NAME)-$(GIT_BRANCH)-$(USER) \
		--mount type=bind,source="$(shell pwd)",target=/$(BASE_NAME) \
		--workdir /$(BASE_NAME) \
		--env USER=$${USER} \
		$(BUILD_IMG_NAME) make $*

clean:
	git clean -fd

clean-all:
	git clean -ffdx
