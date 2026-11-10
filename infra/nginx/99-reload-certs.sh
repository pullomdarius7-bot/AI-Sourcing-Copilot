#!/bin/sh
# Run by the nginx image's entrypoint. nginx reads certificates only at start and reload, so reload twice a day
# to pick up certificates renewed by the certbot container.
(
  while :; do
    sleep 12h
    nginx -s reload 2>/dev/null || true
  done
) &
