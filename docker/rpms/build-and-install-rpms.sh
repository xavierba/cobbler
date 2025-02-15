#!/bin/bash
# Utility script to build RPMs in a Docker container and then install them

set -eo pipefail

RUN_TESTS=false
RUN_SYSTEM_TESTS=false

case ${1} in
    --with-tests)
        RUN_TESTS=true
        shift
        ;;
    --with-system-tests)
        RUN_SYSTEM_TESTS=true
        shift
        ;;
esac

TAG=$1
DOCKERFILE=$2

IMAGE=cobbler:$TAG

# Build container
echo "==> Build container ..."
docker build -t "$IMAGE" -f "$DOCKERFILE" .

# Build RPMs
echo "==> Build RPMs ..."
mkdir -p rpm-build
docker run -t -v "$PWD/rpm-build:/usr/src/cobbler/rpm-build" "$IMAGE"

# Launch container and install cobbler
echo "==> Start container ..."
docker run -t -d --name cobbler \
    -v "$PWD/rpm-build:/usr/src/cobbler/rpm-build" \
    -v "$PWD/system-tests:/usr/src/cobbler/system-tests" \
    "$IMAGE" /bin/bash

echo "==> Install fresh RPMs ..."
docker exec -t cobbler bash -c 'rpm -Uvh rpm-build/cobbler-*.noarch.rpm'

# openSUSE does not have this file so skip it
if test "${TAG#*opensuse}" == "$TAG"
then
    echo "==> Remove httpd SSL default config which is automatically loaded normally"
    docker exec -t cobbler bash -c 'rm /etc/httpd/conf.d/ssl.conf'
fi

echo "==> Start Supervisor ..."
if docker exec -t cobbler bash -c 'test ! -d "/var/log/supervisor"'
then
    echo "==> /var/log/supervisor does not exist, create it"
    docker exec -t cobbler bash -c 'mkdir -p /var/log/supervisor'
fi
docker exec -t cobbler bash -c 'supervisord -c /etc/supervisord.conf'

echo "==> Show Logs ..."
docker exec -t cobbler bash -c 'cat /var/log/supervisor/supervisor.log'

echo "==> Wait 5 sec. and show Cobbler version ..."
docker exec -t cobbler bash -c 'sleep 5 && cobbler version'

if $RUN_TESTS
then
    echo "==> Running tests ..."
    docker exec -t cobbler bash -c 'pip3 install coverage distro future setuptools sphinx mod_wsgi requests future'
    docker exec -t cobbler bash -c 'pip3 install pyyaml netaddr Cheetah3 pymongo distro ldap3 librepo'
    docker exec -t cobbler bash -c 'pip3 install dnspython pyflakes pycodestyle pytest pytest-cov codecov'
    docker exec -t cobbler bash -c 'pytest'
fi

if $RUN_SYSTEM_TESTS
then
    echo "==> Preparing the container for system tests..."
    docker exec --privileged -t cobbler make system-test-env
    echo "==> Running system tests ..."
    docker exec --privileged -t cobbler make system-test
fi

# Clean up
echo "==> Stop Cobbler container ..."
docker stop cobbler
echo "==> Delete Cobbler container ..."
docker rm cobbler
