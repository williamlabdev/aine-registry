#!/bin/sh
set -eu

base_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)

for project in \
  "$base_dir/core/checkout-service" \
  "$base_dir/core/web-app" \
  "$base_dir/side-projects/content-tool"
do
  if [ ! -d "$project/.git" ]; then
    git -C "$project" init -q
    git -C "$project" add .
    git -C "$project" -c user.name="AINE Example" -c user.email="example@aine.invalid" commit -qm "initial example project"
  fi
done

echo "Initialized three independent example Git projects."
