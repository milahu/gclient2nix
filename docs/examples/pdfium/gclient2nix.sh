#!/bin/sh

./bin/gclient2nix --output-file docs/examples/pdfium/sources.json --main-source-path src/pdfium --main-source-args fetcher=fetchFromGitiles url=https://pdfium.googlesource.com/pdfium rev=39292f3808ea2e5e656969e00c3ce01abab2b2f1 hash=sha256-j3G+5T/9SB++53B10i4HJhnPr+0sC20iDwB7N89ivlw=
