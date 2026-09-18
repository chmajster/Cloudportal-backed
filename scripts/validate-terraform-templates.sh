#!/usr/bin/env bash
set -euo pipefail

found=0
for template in terraform/templates/*; do
  [[ -d "$template" && -f "$template/template.json" ]] || continue
  found=1
  terraform -chdir="$template" init -backend=false -input=false
  terraform -chdir="$template" validate
done

[[ "$found" == 1 ]]
