# gclient2nix

Generate Nix expressions for projects based on the Google build tools `gclient` and `gn` from the
[chromium depot_tools](https://chromium.googlesource.com/chromium/tools/depot_tools)

## see also

- https://github.com/NixOS/nixpkgs/issues?q=gclient
   - https://github.com/NixOS/nixpkgs/pull/207766 - Build Electron from source
      - [update.py](https://github.com/NixOS/nixpkgs/blob/c899642750859c6878c3dcb22a3bb7bf2cb31ad1/pkgs/development/tools/electron/update.py) to generate `info.json` from `DEPS` file
- https://github.com/input-output-hk/gclient2nix - written in Python, Haskell
- https://github.com/vroad/gclient2nix - written in Bash
- https://nixos.wiki/wiki/Gn
