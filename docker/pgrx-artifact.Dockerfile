# SPDX-FileCopyrightText: 2026 Carl Allen
# SPDX-License-Identifier: MIT
#
# The pgrx-extension artifact image: FROM scratch, the extension files and
# nothing else. No OS packages, no shell, no rebuild cadence to maintain —
# its CVE surface is structurally empty, and it cannot be used as a base
# by mistake.
#
# Ships BOTH layouts from one image, deliberately:
#   /usr/…               the Debian tree, for `COPY --from` consumers
#   /lib, /share/…       the CloudNativePG extension-ImageVolume layout
# Choosing one would break the other; the duplicate is ~2 MB.
#
# FROM scratch means no RUN steps and therefore no execution: both
# architectures assemble on one runner with --platform and no emulation is
# involved — nothing runs, so there is nothing to emulate. The per-arch
# trees are staged by the workflow from the same verified tarballs whose
# digests the release attests.
FROM scratch

ARG TARGETARCH

COPY staged/${TARGETARCH}/pkgroot/ /
COPY staged/${TARGETARCH}/cnpgroot/ /

# Data-only: there is no process to run and no shell to run it with. USER
# is set anyway so that if someone does `docker run` this by mistake, the
# refusal happens as nobody rather than root. (HEALTHCHECK, by contrast,
# cannot exist without an executable — see .trivyignore.)
USER 65534:65534

# Deliberately no LABEL: metadata comes as --label/--annotation from the
# release's one resolved facts map (docs/release.md, "Image metadata: one
# map"). A LABEL here would be a second mechanism for the same facts —
# and whether a --label overrides a same-key Dockerfile LABEL is not
# specified behaviour to rely on.
