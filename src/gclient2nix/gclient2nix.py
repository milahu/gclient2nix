#! /usr/bin/env nix-shell
#! nix-shell -i python -p python3 nix nix-prefetch-git nix-universal-prefetch prefetch-yarn-deps prefetch-npm-deps

# https://github.com/NixOS/nixpkgs/pull/207766 # Build Electron from source
# https://github.com/NixOS/nixpkgs/tree/c899642750859c6878c3dcb22a3bb7bf2cb31ad1/pkgs/development/tools/electron
# update.py

# TODO what is "default = vars"?

# TODO make this a generic python module, so we can generate arbitrary info.json files

import traceback
import csv
import base64
import os
import re
import tempfile
import subprocess
import json
import sys
import argparse
import hashlib
from codecs import iterdecode
from datetime import datetime
from urllib.request import urlopen

from .depot_tools import gclient_eval
from .depot_tools import gclient_utils

#nix_universal_prefetch_bin = "nix-universal-prefetch"
nix_universal_prefetch_bin = "/nix/store/a0d263m3n24zhdnsmcvhblmsfkdicwlr-nix-universal-prefetch-0.4.0/bin/nix-universal-prefetch"

nix_build_bin = "nix-build"

# TODO merge
cache = {}
cache_extra_data = {}

def remove_hashes(dep):
    return { attr: dep[attr] for attr in dep if attr != "hash" and attr != "sha256" }

def cache_key(dep):
    # TODO sort keys
    return json.dumps(remove_hashes(dep))

class Repo:
    def __init__(self):
        self.deps = {}
        self.hash = "sha256-AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="
        #self.fetcher = "fetchgit" # no, that is GitRepo
        # TODO handle recurse
        self.recurse = False
        self.args = {}

    def get_file(self, filepath):
        key = cache_key(self.flatten_repr())
        if not "store_path" in cache_extra_data[key]:
            print("Repo.get_file: calling Repo.prefetch to set store_path")
            self.prefetch()
        if not "store_path" in cache_extra_data[key]:
            raise Exception("Repo.prefetch failed to set store_path")
        store_path = cache_extra_data[key]["store_path"]
        with open(store_path + "/" + filepath) as f:
            return f.read()

    def get_deps(self, repo_vars, path):
        print("evaluating " + json.dumps(self, default = vars), file=sys.stderr)

        deps_file = self.get_file("DEPS")
        evaluated = gclient_eval.Parse(deps_file, filename='DEPS')

        repo_vars = dict(evaluated["vars"]) | repo_vars

        prefix = f"{path}/" if (evaluated.get("use_relative_paths", False) and path != "") else ""

        self.deps = {
            prefix + dep_name: repo_from_dep(dep)
            for dep_name, dep in evaluated["deps"].items()
            if (gclient_eval.EvaluateCondition(dep["condition"], repo_vars) if "condition" in dep else True) and repo_from_dep(dep) != None
        }

        for key in evaluated.get("recursedeps", []):
            dep_path = prefix + key
            if dep_path in self.deps and dep_path != "src/third_party/squirrel.mac":
                self.deps[dep_path].get_deps(repo_vars, dep_path)

# whats the difference between nix-universal-prefetch and nix-build?
# nix-universal-prefetch returns only the hash
# nix-build returns only the store path (on success)
#
# $ nix-build -E 'with import <nixpkgs> {}; fetchFromGitiles {
#     url = "https://chromium.googlesource.com/chromium/deps/icu";
#     rev = "de4ce0071eb47ed54cbda54869001210cf3a8ae5";
#     sha256 = "";
#   }'
# error: hash mismatch in fixed-output derivation '/nix/store/v0ib3xnbbcx9mgy5vfszkifiy06alk40-source.drv':
#          specified: sha256-AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=
#             got:    sha256-5M7acPuJMkoNR+GNN2psMbrgx20c8fiIl3GXa7kP54Q=
#
# $ nix-build -E 'with import <nixpkgs> {}; fetchFromGitiles { url = "https://chromium.googlesource.com/chromium/deps/icu"; rev = "de4ce0071eb47ed54cbda54869001210cf3a8ae5"; sha256 = "sha256-5M7acPuJMkoNR+GNN2psMbrgx20c8fiIl3GXa7kP54Q="; }'
# /nix/store/ik93j8szrilbv2mzy0agz5z6wphbss59-source
#
# $ nix-universal-prefetch fetchFromGitiles \
#     --url https://chromium.googlesource.com/chromium/deps/icu \
#     --rev de4ce0071eb47ed54cbda54869001210cf3a8ae5
# sha256-5M7acPuJMkoNR+GNN2psMbrgx20c8fiIl3GXa7kP54Q=

    def prefetch(self):

        # TODO remove "hash" and "sha256" values from the cache key
        key = cache_key(self.flatten_repr())

        # TODO use only "rev" as cache key (if rev is a git commit hash)
        # TODO lookup by revision. this is risky because sha1 hashes can collide (rarely)

        # allow passing a known hash to avoid refetching
        if "hash" in self.args:
            cache[key] = self.args["hash"]
            print(f"using hash from args: {cache[key]}")
        elif "sha256" in self.args:
            cache[key] = self.args["sha256"]
            print(f"using hash from args: {cache[key]}")

        #raise Exception(f"prefetch: key = {key}")

        if not key in cache:
            cmd = [nix_universal_prefetch_bin, self.fetcher]
            for arg_name, arg in self.args.items():
                cmd.append(f'--{arg_name}')
                cmd.append(arg)

            print(" ".join(cmd), file=sys.stderr)
            out = subprocess.check_output(cmd)
            cache[key] = out.decode('utf-8').strip()

            # save this value to the temporary cache
            key_hash = hashlib.sha256(key.encode("utf8")).hexdigest()
            cache_file = temporary_cache_dir + "/" + key_hash
            print(f"writing temporary cache file {cache_file}")
            with open(cache_file, "w") as f:
                # no, this is ugly, key is a json string
                #json.dump({ "key": key, "value": cache[key] }, f)
                f.write(key + "\n" + cache[key] + "\n")

            if key not in cache_extra_data:
                cache_extra_data[key] = {}

        if not key in cache_extra_data:
            cache_extra_data[key] = {}

        if not "store_path" in cache_extra_data[key]:
            print("getting store path")
            nix_expr = f"with import <nixpkgs> {{}}; {self.fetcher} {{\n"
            def str_nix_value(value):
                # TODO detect boolean/integer/float/null/path values
                return '"' + value.replace('"', '\\"') + '"'
            for arg_name, arg in remove_hashes(self.args).items():
                nix_expr += f"  {arg_name} = {str_nix_value(arg)};\n"
            fetcher_hash_key = "hash" # some fetchers may require "sha256"
            nix_expr += f"  {fetcher_hash_key} = {str_nix_value(cache[key])};\n"
            nix_expr += "}"
            cmd = [nix_build_bin, "-E", nix_expr]
            print(" ".join(cmd), file=sys.stderr)
            out = subprocess.check_output(cmd)
            store_path = out.decode('utf-8').strip()
            print("store path:", store_path)
            cache_extra_data[key]["store_path"] = store_path

            print("getting store path size")
            cmd = ["du", "-sb", store_path]
            out = subprocess.check_output(cmd)
            store_path_size = int(out.decode('utf-8').strip().split("\t")[0])
            print("store path size:", store_path_size)
            cache_extra_data[key]["store_path_size"] = store_path_size

        self.hash = cache[key]

    def prefetch_all(self):
        self.prefetch()
        for [_, dep] in self.deps.items():
            dep.prefetch_all()

    def flatten_repr(self):
        return {
            "fetcher": self.fetcher,
            "hash": self.hash,
            **self.args
        }

    def flatten(self, path):
        out = {
            path: self.flatten_repr()
        }
        for dep_path, dep in self.deps.items():
            out |= dep.flatten(dep_path)
        return out

class GitRepo(Repo):
    def __init__(self, url, rev):
        super().__init__()
        self.fetcher = 'fetchgit'
        self.args = {
            "url": url,
            "rev": rev,
        }

    #def get_file(self, filepath):
    #    TODO try sparse checkout

class GitHubRepo(Repo):
    def __init__(self, owner, repo, rev):
        super().__init__()
        self.fetcher = 'fetchFromGitHub'
        self.args = {
            "owner": owner,
            "repo": repo,
            "rev": rev,
        }

    def get_file(self, filepath):
        return urlopen(f"https://raw.githubusercontent.com/{self.args['owner']}/{self.args['repo']}/{self.args['rev']}/{filepath}").read().decode('utf-8')

class GitilesRepo(Repo):
    def __init__(self, url, rev):
        super().__init__()
        self.fetcher = 'fetchFromGitiles'
        self.args = {
            "url": url,
            "rev": rev,
        }

        # TODO sparse checkout, partial clone
        #
        # $ du -sh .
        # 5.9G    .
        #
        # $ du -sh .git
        # 1.3G    .git
        #
        # $ expr 59 - 13
        # 46
        #
        # $ expr 46 - 14
        # 32
        #
        # $ du -sh third_party/blink/web_tests third_party/hunspell/tests content/test/data courgette/testdata extensions/test/data media/test/data | sort -h -r
        # 1.4G    third_party/blink/web_tests
        # 67M     media/test/data
        # 63M     content/test/data
        # 4.2M    courgette/testdata
        # 2.8M    extensions/test/data
        # 2.1M    third_party/hunspell/tests

        if url == "https://chromium.googlesource.com/chromium/src.git":
            self.args['postFetch'] = "rm -r $out/third_party/blink/web_tests; "
            self.args['postFetch'] += "rm -r $out/third_party/hunspell/tests; "
            self.args['postFetch'] += "rm -r $out/content/test/data; "
            self.args['postFetch'] += "rm -r $out/courgette/testdata; "
            self.args['postFetch'] += "rm -r $out/extensions/test/data; "
            self.args['postFetch'] += "rm -r $out/media/test/data; "

    def get_file(self, filepath):
        return base64.b64decode(urlopen(f"{self.args['url']}/+/{self.args['rev']}/{filepath}?format=TEXT").read()).decode('utf-8')

def get_yarn_hash(repo, yarn_lock_path = 'yarn.lock'):
    # TODO use yarn2nix for granular caching
    key = "yarn-"+cache_key(repo.flatten_repr())
    if not key in cache:
        print(f'prefetch-yarn-deps', file=sys.stderr)
        with tempfile.TemporaryDirectory() as tmp_dir:
            with open(tmp_dir + '/yarn.lock', 'w') as f:
                f.write(repo.get_file(yarn_lock_path))
            cache[key] = subprocess.check_output(['prefetch-yarn-deps', tmp_dir + '/yarn.lock']).decode('utf-8').strip()
    return cache[key]

def get_npm_hash(repo, package_lock_path = 'package-lock.json'):
    # TODO use npmlock2nix for granular caching
    key = "npm-"+cache_key(repo.flatten_repr())
    if not key in cache:
        print(f'prefetch-npm-deps', file=sys.stderr)
        with tempfile.TemporaryDirectory() as tmp_dir:
            with open(tmp_dir + '/package-lock.json', 'w') as f:
                f.write(repo.get_file(package_lock_path))
            cache[key] = subprocess.check_output(['prefetch-npm-deps', tmp_dir + '/package-lock.json']).decode('utf-8').strip()
    return cache[key]

def repo_from_dep(dep):
    if "url" in dep:
        url, rev = gclient_utils.SplitUrlRevision(dep["url"])

        search_object = re.search(r'https://github.com/(.+)/(.+?)(\.git)?$', url)
        if search_object:
            return GitHubRepo(search_object.group(1), search_object.group(2), rev)

        if re.match(r'https://.+.googlesource.com', url):
            return GitilesRepo(url, rev)

        return GitRepo(url, rev)
    else:
        # Not a git dependency; skip
        return None

def get_gn_source(repo):
    gn_pattern = r"'gn_version': 'git_revision:([0-9a-f]{40})'"
    gn_commit = re.search(gn_pattern, repo.get_file("DEPS")).group(1)
    gn = subprocess.check_output([
        "nix-prefetch-git",
        "--quiet",
        "https://gn.googlesource.com/gn",
        "--rev", gn_commit
        ])
    gn = json.loads(gn)
    return {
        "gn": {
            "version": datetime.fromisoformat(gn["date"]).date().isoformat(),
            "url": gn["url"],
            "rev": gn["rev"],
            "sha256": gn["sha256"]
        }
    }



# main

def parse_args():
    parser = argparse.ArgumentParser(
        prog='gclient2nix',
        #description='What the program does',
        #epilog='Text at the bottom of help',
    )

    # args.main_source_args
    parser.add_argument('--main-source-args', required=True, action="extend", nargs="+", type=str, help='arguments for the main source. example: fetcher=fetchFromGitiles url=https://chromium.googlesource.com/chromium/src rev=147f65333c38ddd1ebf554e89965c243c8ce50b3')

    # args.main_source_path
    parser.add_argument('--main-source-path', default="", help='example: "src/chromium", default: empty string')

    # args.output_file
    parser.add_argument('--output-file', required=True, help='example: "sources.json"')

    # args.use_relative_paths
    parser.add_argument('--use-relative-paths', help='example: "true", default: use value of "use_relative_paths" from DEPS file')

    # args.cache_dir
    parser.add_argument('--cache-dir')

    args = parser.parse_args()

    return args

# persistent cache. ideally use a separate database process to avoid data loss, and to avoid too many disk writes
# compromise solution: use a fixed tempdir path in tmpfs (/run/user/1000/gclient2nix-temp-cache) as a temporary cache
# 1000 == os.getuid()
# when the program is finished, add the temporary cache to a persistent cache on disk
# when the program crashes, on the next run, restore the temporary cache
# the temporary cache allows multiple writers and readers
# by storing each entry in a separate file
# tmpfs: see also: https://pypi.org/project/memory-tempfile/

persistent_cache_dir = os.environ["HOME"] + "/.cache/gclient2nix"

temporary_cache_dir = f"/run/user/{os.getuid()}/gclient2nix-temp-cache"

def main():
    global cache

    args = parse_args()

    # def get_deps(self, repo_vars, path):
    #electron_repo.get_deps({
    #    f"checkout_{platform}": platform == "linux"
    #    for platform in ["ios", "chromeos", "android", "mac", "win", "linux"]
    #}, "src/electron")

    os.makedirs(persistent_cache_dir, exist_ok=True)
    os.makedirs(temporary_cache_dir, exist_ok=True)

    # load the persistent cache
    persistent_cache_file = persistent_cache_dir + "/cache.txt"
    if os.path.exists(persistent_cache_file):
        print(f"loading persistent cache from {persistent_cache_file}")
        with open(persistent_cache_file) as f:
            #cache = json.load(f)
            text = f.read()
            for block in text.split("\n\n\n\n"):
                #print("block", repr(block))
                block_parts = block.split("\n")
                key = block_parts[0]
                value = json.loads(block_parts[1])
                # block can have 3 parts because of "\n" at end of file
                cache[key] = value["hash"]
                cache_extra_data[key] = value
                del cache_extra_data[key]["hash"]

    # load the temporary cache
    # when a previous run did not save the persistent cache
    # then there are values in the temporary cache
    for filename in os.listdir(temporary_cache_dir):
        # sha256 hexdigest has 64 chars
        if len(filename) != 64:
            continue
        cache_file_path = temporary_cache_dir + "/" + filename
        print(f"loading temporary cache file {cache_file_path}")
        with open(cache_file_path) as f:
            text = f.read()
            parts = text.split("\n")
            key = parts[0].strip()
            value = parts[1].strip()
            cache[key] = value
            print(f"loaded cache key: {repr(key)}")
            print(f"loaded cache value: {repr(value)}")

    repo_vars = {
        f"checkout_{platform}": platform == "linux"
        for platform in ["ios", "chromeos", "android", "mac", "win", "linux"]
    }





    # parse args.main_source_args
    main_source_args = {}
    for key_val in args.main_source_args:
        idx = key_val.find("=")
        key = key_val[:idx]
        val = key_val[(idx + 1):]
        # TODO parse non-string nix values: bool, int, float, null, path, attrset, list
        # currently, all values are strings
        main_source_args[key] = val

    print("main_source_args", main_source_args)

    if not "fetcher" in main_source_args:
        # TODO better error type. OptionError? ArgumentError?
        raise ValueError("a value is required for the 'fetcher' main-source-arg. example: --main-source-args fetcher=fetchFromGitiles")

    main_source_fetcher = main_source_args["fetcher"]
    del main_source_args["fetcher"]

    main_repo = Repo()
    main_repo.fetcher = main_source_fetcher
    main_repo.args = main_source_args

    # FIXME repo.recurse is not handled
    #main_repo.recurse = True

    # fetch the main repo, so we have the DEPS file
    print("fetching the main source")
    main_repo.prefetch()

    print("parsing sources of dependencies")
    print("args.main_source_path:", repr(args.main_source_path))
    repo_vars = {
      f"checkout_{platform}": platform == "linux"
      for platform in ["ios", "chromeos", "android", "mac", "win", "linux"]
    }
    main_repo.get_deps(repo_vars, args.main_source_path)

    print("fetching sources of dependencies")
    main_repo.prefetch_all()

    tree = main_repo.flatten(args.main_source_path)

    print(f"writing output file: {args.output_file}")
    with open(args.output_file, "w") as f:
        f.write(json.dumps(tree, indent=2, default = vars) + "\n")

    # TODO load the persistent cache again
    # to add cached values from other gclient2nix processes

    # TODO load the temporary cache again
    # to add cached values from other gclient2nix processes

    # save the persistent cache
    print(f"writing persistent cache to {persistent_cache_file}")
    with open(persistent_cache_file, "w") as f:
        def get_cache_entry_str(key):
            cache_value = {
                "hash": cache[key],
                **cache_extra_data.get(key, {}),
            }
            return key + "\n" + json.dumps(cache_value)
        f.write("\n\n\n\n".join(map(get_cache_entry_str, cache.keys())) + "\n")

    # delete known values from the temporary cache
    # which have been saved in the persistent cache
    for key in cache:
        key_hash = hashlib.sha256(key.encode("utf8")).hexdigest()
        cache_file = temporary_cache_dir + "/" + key_hash
        if os.path.exists(cache_file):
            os.unlink(cache_file)

# __name__ src.gclient2nix.gclient2nix
#print("__name__", __name__)

if __name__ == '__main__':
    main()

# let me call: python -m src.gclient2nix
if __name__ == 'src.gclient2nix.gclient2nix':
    main()

# let me call: python -m gclient2nix
if __name__ == 'gclient2nix.gclient2nix':
    main()

# TODO nix build retry loop?
#   nix-shell . -A pdfium
#   mkdir pdfium-build-shell
#   cd pdfium-build-shell
#   eval unpackPhase
#   cd $sourceRoot
#   eval patchPhase
# -> fetch only needed files, simlar to gitvfs

# TODO keep a persistent list of "large subtrees"
# so we can optimize "git clone" operations

sys.exit()



# orignal main code block for electron

try:
    with open('info.json', 'r') as f:
        old_info = json.loads(f.read())
        for [_, version] in old_info.items():
            for [dep_path, dep] in version["deps"].items():
                cache[cache_key(dep)] = dep["hash"]
            cache["npm-"+cache_key(version["deps"]["src"])] = version["chromium_npm_hash"]
            cache["yarn-"+cache_key(version["deps"]["src/electron"])] = version["electron_yarn_hash"]
except:
    print("not using cache: ", file=sys.stderr)
    traceback.print_exc()

out = {}

electron_releases = json.loads(urlopen("https://releases.electronjs.org/releases.json").read())

for major_version in range(26, 21, -1):
    major_version_releases = filter(lambda item: item["version"].startswith(f"{major_version}."), electron_releases)
    m = max(major_version_releases, key=lambda item: item["date"])

    rev=f"v{m['version']}"

    electron_repo = GitHubRepo("electron", "electron", rev)
    # FIXME repo.recurse is not handled?
    electron_repo.recurse = True

    electron_repo.get_deps({
        f"checkout_{platform}": platform == "linux"
        for platform in ["ios", "chromeos", "android", "mac", "win", "linux"]
    }, "src/electron")

    electron_repo.prefetch_all()

    tree = electron_repo.flatten("src/electron")

    out[f"{major_version}"] = {
      "electron_yarn_hash": get_yarn_hash(electron_repo),
      "chromium_npm_hash": get_npm_hash(electron_repo.deps["src"], "third_party/node/package-lock.json"),
      "deps": tree,
      **{key: m[key] for key in ["version", "modules", "chrome"]},
      "chromium": {
          "version": m['chrome'],
          "deps": get_gn_source(electron_repo.deps["src"])
      }
    }

with open('info.json', 'w') as f:
    f.write(json.dumps(out, indent=4, default = vars))
    f.write('\n')
