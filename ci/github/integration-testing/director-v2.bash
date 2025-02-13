#!/bin/bash
# http://redsymbol.net/articles/unofficial-bash-strict-mode/
set -o errexit  # abort on nonzero exitstatus
set -o nounset  # abort on unbound variable
set -o pipefail # don't hide errors within pipes
IFS=$'\n\t'

# in case it's a Pull request, the env are never available, default to itisfoundation to get a maybe not too old version for caching
DOCKER_IMAGE_TAG=$(exec ci/helpers/build_docker_image_tag.bash)
export DOCKER_IMAGE_TAG

install() {
  bash ci/helpers/ensure_python_pip.bash
  pushd services/director-v2
  pip3 install -r requirements/ci.txt
  popd
  pip list -v
  make pull-version || (make build tag-version)
  make info-images
}

test() {
  echo "testing in services/director-v2/tests/integration/$1"
  pytest --log-format="%(asctime)s %(levelname)s %(message)s" \
    --log-date-format="%Y-%m-%d %H:%M:%S" \
    --cov=simcore_service_director_v2 --durations=10 --cov-append \
    --color=yes --cov-report=term-missing --cov-report=xml --cov-config=.coveragerc \
    --asyncio-mode=auto \
    -v -m "not travis" "services/director-v2/tests/integration/$1" --log-level=DEBUG
}

clean_up() {
  docker images
  make down
}

# Check if the function exists (bash specific)
if declare -f "$1" >/dev/null; then
  # call arguments verbatim
  "$@"
else
  # Show a helpful error
  echo "'$1' is not a known function name" >&2
  exit 1
fi
