"""Build only public Flutter sources. Does not access app code or signing keys."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import tarfile
import zipfile

from verify_elf import inspect

ENGINE = "06a2e2a110089dff50fe635cffd2a61e1b24fbcd"
FRAMEWORK = "9584c6713b324636289d067944a46fd6b49df14b"
DART = "1d1a730ef918d602aedafc939a4cf5940e7589ab"
DEPOT = "a03dfbf7f2d1cefa2795d81d59ca4f16c8616a8f"
SOURCE_SHA = "77e0d98966fd0e1311c05c79afa8feb3dcbac3f8a050166d17f6a04b319a5bd9"
HERE = Path(__file__).resolve().parent


def run(*args, cwd=None, env=None):
    print("+", " ".join(map(str, args)), flush=True)
    subprocess.run(list(map(str, args)), cwd=cwd, env=env, check=True)


def clone(url, revision, destination):
    destination.mkdir()
    run("git", "init", destination)
    run("git", "remote", "add", "origin", url, cwd=destination)
    run("git", "fetch", "--depth=1", "origin", revision, cwd=destination)
    run("git", "checkout", "--detach", "FETCH_HEAD", cwd=destination)
    actual = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=destination, text=True).strip()
    if actual != revision:
        raise RuntimeError("Unexpected source revision")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--plan", action="store_true", help="Offline, no mutation or network")
    parser.add_argument("--root", type=Path)
    args = parser.parse_args()
    plan = {"engineRevision": ENGINE, "matchingFrameworkRevision": FRAMEWORK,
            "dartRevision": DART, "depotToolsRevision": DEPOT,
            "compilerSourceSha256LF": SOURCE_SHA,
            "patch": "Add -Wl,-z,common-page-size=16384; retain max-page-size=65536 and RELRO",
            "target": "android_release_arm64", "host": "host_release (Linux)",
            "containsAppSource": False, "containsCredentials": False,
            "requires": "Linux x86_64, build dependencies, >=45 GiB free space"}
    if args.plan:
        print(json.dumps(plan, indent=2))
        return
    if platform.system() != "Linux" or platform.machine() not in ("x86_64", "AMD64"):
        raise RuntimeError("This build requires a Linux x86_64 host")
    if args.root is None:
        raise RuntimeError("Supply a dedicated new --root directory")
    root = args.root.resolve()
    if root.exists():
        raise RuntimeError("Refusing to overwrite an existing build directory")
    if not root.parent.is_dir() or shutil.disk_usage(root.parent).free < 45 * 1024**3:
        raise RuntimeError("At least 45 GiB free space is required before checkout")
    root.mkdir()
    depot, checkout = root / "depot_tools", root / "flutter"
    clone("https://chromium.googlesource.com/chromium/tools/depot_tools.git", DEPOT, depot)
    clone("https://github.com/flutter/flutter.git", ENGINE, checkout)
    deps = (checkout / "DEPS").read_text()
    if f"'dart_revision': '{DART}'" not in deps:
        raise RuntimeError("Dart revision differs from the current SDK")
    compiler = checkout / "engine/src/build/config/compiler/BUILD.gn"
    original = compiler.read_bytes()
    if hashlib.sha256(original).hexdigest() != SOURCE_SHA:
        raise RuntimeError("Compiler configuration source hash differs")
    env = dict(os.environ, PATH=str(depot) + os.pathsep + os.environ["PATH"],
               DEPOT_TOOLS_UPDATE="0", DEPOT_TOOLS_METRICS="0")
    config = (checkout / "engine/scripts/standard.gclient").read_text()
    config = config.replace('"custom_deps": {},', '"custom_deps": {},\n'
                            '    "custom_vars": {"download_fuchsia_deps": False, "use_rbe": False},')
    (checkout / ".gclient").write_text(config)
    run(depot / "gclient", "sync", "--no-history", "-j", "2", cwd=checkout, env=env)
    if subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=checkout, text=True).strip() != ENGINE:
        raise RuntimeError("Dependency sync changed the source revision")
    if compiler.read_bytes() != original:
        raise RuntimeError("Dependency sync changed compiler configuration")
    anchor = b'      "-Wl,-z,max-page-size=65536",\n'
    if original.count(anchor) != 1:
        raise RuntimeError("Expected exactly one Android linker flag anchor")
    patched = original.replace(anchor, anchor + b'      "-Wl,-z,common-page-size=16384",\n')
    compiler.write_bytes(patched)
    src = checkout / "engine/src"
    common = ["--runtime-mode=release", "--no-rbe", "--no-goma",
              "--no-enable-unittests", "--no-build-embedder-examples"]
    run("python3", "flutter/tools/gn", "--android", "--android-cpu=arm64",
        "--target-dir=android_release_arm64", *common, cwd=src, env=env)
    # Use the distribution Ninja installed by the workflow. The depot_tools
    # wrapper requires a separate Python bootstrap even after gclient succeeds.
    ninja = Path("/usr/bin/ninja")
    if not ninja.is_file():
        raise RuntimeError("The workflow must install the distribution Ninja")
    run(ninja, "-C", "out/android_release_arm64", "-j", "2",
        "flutter/shell/platform/android:android_jar", "clang_x64/gen_snapshot",
        "flutter/lib/snapshot:generate_snapshot_bins", cwd=src, env=env)
    target = src / "out/android_release_arm64"
    with zipfile.ZipFile(target / "flutter.jar") as jar:
        engine_bytes = jar.read("lib/arm64-v8a/libflutter.so")
    packaged_engine = root / "libflutter.so"
    packaged_engine.write_bytes(engine_bytes)
    result = inspect(packaged_engine)
    if result["machine"] != 183 or not result["staticChecksPassed"]:
        raise RuntimeError("Rebuilt packaged engine failed strict ARM64 LOAD/RELRO check")
    run("python3", "flutter/tools/gn", "--target-dir=host_release",
        "--prebuilt-dart-sdk", *common, cwd=src, env=env)
    run(ninja, "-C", "out/host_release", "-j", "2",
        "flutter/build/archives:flutter_patched_sdk", "flutter/sky",
        "flutter/tools/font_subset", "flutter/tools/const_finder",
        "flutter/flutter_frontend_server:frontend_server", cwd=src, env=env)
    host = src / "out/host_release"
    prebuilt_dart = src / "flutter/prebuilts/linux-x64/dart-sdk"
    if not (host / "dart-sdk").exists():
        shutil.copytree(prebuilt_dart, host / "dart-sdk")
    shutil.copy2(host / "gen/frontend_server_aot.dart.snapshot",
                 host / "dart-sdk/bin/snapshots/frontend_server_aot.dart.snapshot")
    result.update(plan)
    result["patchedCompilerSha256"] = hashlib.sha256(patched).hexdigest()
    result["flutterJarSha256"] = hashlib.sha256((target / "flutter.jar").read_bytes()).hexdigest()
    result["runtime16KBAccepted"] = False
    output = root / "delivery"
    output.mkdir()
    (output / "provenance.json").write_text(json.dumps(result, indent=2) + "\n")
    # Explicit output whitelist; no source tree, private app, credentials, or signing material.
    selected = [(target / "flutter.jar", "out/android_release_arm64/flutter.jar"),
                (packaged_engine, "out/android_release_arm64/libflutter.so"),
                (target / "clang_x64/gen_snapshot", "out/android_release_arm64/clang_x64/gen_snapshot"),
                (target / "flutter_patched_sdk", "out/android_release_arm64/flutter_patched_sdk"),
                (host / "flutter_patched_sdk", "out/android_release_arm64/flutter_patched_sdk_product"),
                (host / "dart-sdk", "out/host_release/dart-sdk"),
                (host / "gen/dart-pkg/sky_engine", "out/host_release/gen/dart-pkg/sky_engine"),
                (host / "gen/const_finder.dart.snapshot", "out/host_release/gen/const_finder.dart.snapshot"),
                (host / "font-subset", "out/host_release/font-subset"),
                (host / "gen/dart-pkg/sky_engine/LICENSE", "THIRD_PARTY_NOTICES"),
                (checkout / "LICENSE", "LICENSE")]
    # Flutter's supported local-engine integration also needs these Maven files.
    for artifact in ("flutter_embedding_release", "arm64_v8a_release"):
        for suffix in ("jar", "pom", "maven-metadata.xml"):
            name = f"{artifact}.{suffix}"
            selected.append((target / name, f"out/android_release_arm64/{name}"))
    for item, _ in selected:
        if not item.exists():
            raise RuntimeError(f"Required local-engine output missing: {item.name}")
    archive = output / "flutter-06a2e2a-arm64-16kb-linux.tar.gz"
    with tarfile.open(archive, "w:gz") as bundle:
        for item, name in selected:
            bundle.add(item, arcname=name)
        bundle.add(output / "provenance.json", arcname="provenance.json")
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    (output / "SHA256SUMS").write_text(f"{digest}  {archive.name}\n")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
