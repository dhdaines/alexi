#!/bin/sh

set -e

alexi -v extract -O playa -m download/index.json -o export/vdsa download/*.pdf
alexi -v extract -O playa -m download/vsadm/index.json -o export/vsadm download/vsadm/*.pdf
alexi -v extract -O playa -m download/vss/index.json -o export/vss download/vss/*.pdf
alexi -v extract -O playa -m download/prevost/index.json -o export/prevost download/prevost/*.pdf
# alexi -v extract -O playa -m download/laval/index.json -o export/laval download/laval/*.pdf
echo '<meta http-equiv="refresh" content="0; url=vdsa/index.html">' > export/index.html
