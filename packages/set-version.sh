#!/usr/bin/env bash
# Stamps a release's version (2.4.0) into the SDKs and the widget before they are published, so each package's
# version matches the app release it was generated from. Run by .github/workflows/release.yml.
#
#   packages/set-version.sh 2.4.0
set -euo pipefail
version="${1:?usage: set-version.sh X.Y.Z}"
[[ "$version" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] || { echo "not a version: $version" >&2; exit 2; }
cd "$(dirname "$0")"

for package in sdk-ts widget; do
  (cd "$package" && npm version "$version" --no-git-tag-version --allow-same-version >/dev/null)
done
sed -i -E "s/^version = \"[^\"]+\"/version = \"$version\"/" sdk-py/pyproject.toml
sed -i -E "s|nl-analytics-sdk-py/[0-9.]+\"|nl-analytics-sdk-py/$version\"|" sdk-py/src/nl_analytics/client.py
sed -i -E "s|nl-analytics-sdk-ts/[0-9.]+\"|nl-analytics-sdk-ts/$version\"|" sdk-ts/src/index.ts

grep -q "^version = \"$version\"" sdk-py/pyproject.toml
grep -q "nl-analytics-sdk-py/$version\"" sdk-py/src/nl_analytics/client.py
grep -q "nl-analytics-sdk-ts/$version\"" sdk-ts/src/index.ts
echo "packages set to $version"
