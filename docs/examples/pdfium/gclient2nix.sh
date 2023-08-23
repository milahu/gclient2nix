#!/bin/sh

python -m src.gclient2nix --deps-file docs/examples/pdfium/DEPS --output-file docs/examples/pdfium/info.json --main-source-path src/pdfium
