#!/bin/sh
set -eu

if ! nginx_test_output=$(/usr/sbin/nginx -t 2>&1); then
    printf '%s\n' "$nginx_test_output" >&2
    exit 1
fi
/bin/systemctl reload nginx
