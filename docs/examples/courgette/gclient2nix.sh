#!/bin/sh

args=(
  ./bin/gclient2nix
  --output-file docs/examples/courgette/sources.json
  --main-source-path src/courgette
  --main-source-args
    nixpkgs_attr=chromium.browser.src
)

exec "${args[@]}"

###

cat >/dev/null <<'EOF'

  NOTE this is a stupid example, only for demo

  probably the nixpkgs_attr argument makes no sense...

  this takes 50 minutes (50 minutes!!) to fetch all sources
  but this is stupid, because courgette is part of chromium

  so its much faster to use

    chromium.browser.overrideAttrs (oldAttrs: {
      pname = "courgette";
      inherit (chromium.browser) version;
      buildPhase = ''
        runHook preBuild
        TERM=dumb ninja -C "out/Release" -j$NIX_BUILD_CORES "courgette"
        (
          source chrome/installer/linux/common/installer.include
          PACKAGE=$packageName
          MENUNAME="Chromium"
          process_template chrome/app/resources/manpage.1.in "out/Release/chrome.1"
        )
        runHook postBuild
      '';
      # TODO fix fixupPhase
    })

  or

    chromium.browser.mkDerivation rec {
      name = "courgette";
      packageName = name;
      buildTargets = [ "courgette" ];
      installPhase = ''
        mkdir -p $out/bin
        cp $buildPath/courgette $out/bin
      '';
      # TODO fix fixupPhase
    }

EOF
