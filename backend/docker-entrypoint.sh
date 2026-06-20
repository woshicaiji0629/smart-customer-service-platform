#!/bin/sh

set -eu

if [ -n "${DATABASE_URL:-}" ]; then
    DATABASE_URL=$(
        printf '%s' "$DATABASE_URL" | sed \
            -e 's#//localhost:#//host.docker.internal:#' \
            -e 's#//127\.0\.0\.1:#//host.docker.internal:#' \
            -e 's#@localhost:#@host.docker.internal:#' \
            -e 's#@127\.0\.0\.1:#@host.docker.internal:#'
    )
    export DATABASE_URL
fi

exec "$@"
