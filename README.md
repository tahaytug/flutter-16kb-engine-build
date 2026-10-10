# Pinned Flutter ARM64 16 KB engine build

This is a build recipe, not a built or accepted engine. No application source,
signing material, personal information, or service credentials are included.

The existing engine revision is `06a2e2a110089dff50fe635cffd2a61e1b24fbcd`, matching
Flutter framework `9584c6713b324636289d067944a46fd6b49df14b` (3.47.4).
The Dart revision is pinned and checked before dependency sync. Only the Android
link flag `-Wl,-z,common-page-size=16384` is added. The existing 64 KB maximum page
size, RELRO protection, and normal release LTO remain enabled.

Linux x86_64 is required, with at least 45 GiB free space. A standard public
GitHub-hosted Ubuntu runner is the proposed build host. The workflow runs only
on manual dispatch and only in a public repository, uses two Ninja jobs, and
has a 330-minute timeout. It does not use paid larger runners, Actions caches,
or Actions artifact storage. Verified outputs are stored as repository release
assets. Account/site policy may still prevent running the workflow.

Before dispatch, review `build_engine.py`, `verify_elf.py`, and the workflow.
The disposable runner cleanup deletes only five named preinstalled tool
directories; it does not run on this Windows computer. An existing build root
is never overwritten.

Offline review: `python build_engine.py --plan`.
Linux build: `python3 -u build_engine.py --root /path/to/new-build-directory`.

The packaged `flutter.jar` engine is inspected, not just a different output
file. The verifier requires ARM64, 16 KB-aligned LOAD segments and an aligned
existing RELRO segment. Failure stops before publishing. Output includes
revision/hash provenance and a checksum file.

The archive contains Linux local-engine host tools. They cannot execute on
Windows. Integration must use the official local-engine interface on Linux,
or separately obtain matching Windows host tools; do not replace the SDK cache
with another revision's engine. The source has not changed Dart or dart:ui,
but matching compiler/runtime provenance must still be checked.

After an actual successful build, validate the application's newly signed AAB,
all native ELF files, bundle/ZIP alignment, licenses and content; then test in
an environment where `adb shell getconf PAGE_SIZE` returns `16384`.
This recipe does not claim runtime acceptance and does not start Google Play
testing, upload the application, or publish Yansıma.

References:
- https://github.com/flutter/flutter/blob/main/docs/engine/contributing/Compiling-the-engine.md
- https://developer.android.com/guide/practices/page-sizes
- https://docs.github.com/en/billing/concepts/product-billing/github-actions
- https://docs.github.com/en/repositories/releasing-projects-on-github/about-releases
