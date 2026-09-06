# Building LOGY's Windows/macOS installers

Real installers are built by `.github/workflows/build-installers.yml` on
GitHub's own `windows-latest` and `macos-latest` runners - PyInstaller has
to run on the same OS it targets, so this repo doesn't (and can't) build
either one locally in the sandbox this codebase was developed in.

## Cutting a release

```bash
git tag v1.0.0
git push origin v1.0.0
```

Pushing a `v*` tag triggers the workflow, which:

1. Builds LOGY with PyInstaller on Windows and macOS, in parallel.
2. Wraps the Windows build in an Inno Setup installer
   (`installer/windows/logy.iss` -> `LOGY-Setup-1.0.0.exe`).
3. Packages the macOS `.app` into a drag-to-Applications `.dmg`
   (`LOGY-1.0.0-macOS.dmg`).
4. Creates a GitHub Release named `v1.0.0` with both files attached.

## Testing the workflow without cutting a release

Use the "Run workflow" button on the Actions tab (or
`gh workflow run build-installers.yml`). This runs the same two builds
and uploads them as downloadable Actions artifacts, but skips the
release step entirely - nothing gets published.

## Known gap: no macOS icon yet

The Windows build already uses `assets/logo.ico` (already in this repo).
There's no `assets/logo.icns` yet, so the macOS build currently ships
with PyInstaller's generic default icon. To fix:

1. Generate a `.icns` from the existing `assets/logo_icon_transparent.png`
   (on a Mac: `mkdir logo.iconset && for size in 16 32 128 256 512; do
   sips -z $size $size assets/logo_icon_transparent.png --out
   logo.iconset/icon_${size}x${size}.png; done && iconutil -c icns
   logo.iconset -o assets/logo.icns`, or any online PNG->ICNS converter).
2. Commit `assets/logo.icns`.
3. Add `--icon assets/logo.icns` to the `pyinstaller` command in the
   `build-macos` job of `.github/workflows/build-installers.yml`.

## Known gap: unsigned binaries

Neither installer is code-signed yet:

- **Windows**: without an Authenticode certificate, SmartScreen will
  warn "Windows protected your PC" on first run. Users can click "More
  info -> Run anyway"; this goes away once the .exe is signed with a
  purchased code-signing certificate (add a `signtool sign` step after
  the Inno Setup build).
- **macOS**: without an Apple Developer ID and notarization, Gatekeeper
  will refuse to open the .app ("LOGY is damaged and can't be opened" or
  similar) until the user right-clicks -> Open once, or the app is
  properly signed + notarized (needs an active $99/yr Apple Developer
  account; happy to wire up `codesign`/`notarytool` steps once you have
  one).

Both are optional for now - the workflow produces working installers
either way - but worth knowing before sending either file to a
non-technical user.
