#!/bin/bash

set -e

PDF=${1:?Usage: $0 PDF}
CSV=${PDF/%.pdf/.csv}

alexi convert "$PDF" | \
    alexi segment - | \
    alexi label - > "$CSV"
open "$CSV"
