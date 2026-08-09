# The Monumental Archive

An open, citation-grade catalogue of the monumental art, architecture,
and everyday places of the Soviet century — and the cultural record
around them. What survives, what is at risk, what is lost.

**[monumentalarchive.org](https://monumentalarchive.org/)**

Mosaics, sculptures, reliefs, frescoes, sgraffito, stained glass, and the
structures that host them — surveyed, photographed, and documented with
primary-source citations, across the territories of the Soviet century.

This organisation holds the archive's engineering: the record deserves
infrastructure as durable as the subject, so it is built on open
standards, implemented exactly:

- **[edtf](https://github.com/CarlAllenn/edtf)** — Extended Date/Time
  Format (ISO 8601-2) in Rust, levels 0–2 in full: historical dates are
  approximate, and the archive records that honestly rather than
  rounding it away.
- **[iiif-server](https://github.com/CarlAllenn/iiif-server)** — the IIIF
  Image API, 3.0 and 2.1 with the complete feature table, as one static
  pure-Rust binary serving the photographic record.
- **The archive database** — PostGIS, schema as code, with an anchored
  checkpoint ledger keeping the record of the record tamper-evident.

Everything is built the same way: toolchains pinned and checksummed,
every linter at maximum, releases signed with verifiable provenance, and
one CI gate no branch — and no maintainer — can bypass.
