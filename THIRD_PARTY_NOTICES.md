# Third-Party Notices

This project depends on external GNU gettext command-line tools at runtime:

- `msgmerge`
- `msgfmt`

## GNU gettext

- Project: [GNU gettext](https://www.gnu.org/software/gettext/)
- Upstream source: [https://ftp.gnu.org/gnu/gettext/](https://ftp.gnu.org/gnu/gettext/)
- Typical package names:
  - Linux: `gettext`
  - macOS (Homebrew): `gettext`
  - Windows: via MSYS2/WSL/chocolatey/scoop or equivalent packaging

This repository does not vendor gettext source code or binaries by default.
Users are expected to install gettext separately on their system and make it
available on `PATH`.

If you redistribute builds that bundle gettext binaries, you are responsible
for meeting all applicable third-party license obligations for those binaries
(including notices and source-availability requirements where required).

