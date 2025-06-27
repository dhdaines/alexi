#!/bin/bash

set -e

PDF=${1:?Usage: $0 PDF}
EXPORT=${PDF/%.pdf/.export}

alexi extract -o "$EXPORT" "$PDF"
open "$EXPORT/index.html"
