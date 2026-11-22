# Changelog

All notable changes to this project are recorded here.

<!-- rumdl-disable MD013 -->
<!-- entries are commit subjects, verbatim: a recorded subject's
length is a fact about history, not prose to reflow -->

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and versions follow [Semantic Versioning](https://semver.org/spec/v2.0.0.html)
over the surface named in [MAINTENANCE.md](MAINTENANCE.md).

## [1.76.0](https://github.com/monumental-archive/.github/compare/v1.75.0...v1.76.0) - 2026-11-22

### Added

- record audit:links' broken links from lychee (#1955) ([#1969](https://github.com/monumental-archive/.github/pull/1969))
- keep one red-check issue per red job (#1966) ([#1974](https://github.com/monumental-archive/.github/pull/1974))
- record audit:action-pins' findings from pinact (#1955) ([#1971](https://github.com/monumental-archive/.github/pull/1971))
- record audit:actions' findings from zizmor (#1955) ([#1978](https://github.com/monumental-archive/.github/pull/1978))
- record a rust equivalent mutant at its site (#1987) ([#1992](https://github.com/monumental-archive/.github/pull/1992))
- serve postgres 19 from the pgrx base-image mapping ([#1999](https://github.com/monumental-archive/.github/pull/1999))

### Changed

- write the audit tasks' findings through org_finding ([#1956](https://github.com/monumental-archive/.github/pull/1956))
- write the first lint tasks' findings through org_finding ([#1960](https://github.com/monumental-archive/.github/pull/1960))
- write the later lint tasks' findings through org_finding ([#1961](https://github.com/monumental-archive/.github/pull/1961))
- write the other tasks' findings through org_finding ([#1963](https://github.com/monumental-archive/.github/pull/1963))
- write the belt scripts' findings through org_finding ([#1965](https://github.com/monumental-archive/.github/pull/1965))
- route rekor and docker findings through writers (#1934) ([#1972](https://github.com/monumental-archive/.github/pull/1972))
- write a-l helpers' findings through the writer (#1934) ([#1975](https://github.com/monumental-archive/.github/pull/1975))
- write workflow findings through the writer (#1934) ([#1973](https://github.com/monumental-archive/.github/pull/1973))
- write m-z helpers' findings through the writer (#1934) ([#1982](https://github.com/monumental-archive/.github/pull/1982))
- write release/ and settings/ findings by writer (#1934) ([#1980](https://github.com/monumental-archive/.github/pull/1980))

### Fixed

- update monumental-archive/signer to 8b1eea0 ([#1962](https://github.com/monumental-archive/.github/pull/1962))
- print the codecov-token remedy as block yaml (#1923) ([#1968](https://github.com/monumental-archive/.github/pull/1968))
- update jdx/mise-action to v5.2.9 ([#1970](https://github.com/monumental-archive/.github/pull/1970))
- report a stalled signer-identity watch once (#1967) ([#1983](https://github.com/monumental-archive/.github/pull/1983))
- update github:bnjbvr/cargo-machete to v0.10.1 ([#1914](https://github.com/monumental-archive/.github/pull/1914))
- export a script's task name to its children (#1979) ([#1989](https://github.com/monumental-archive/.github/pull/1989))
- record a character utf-8 cannot carry as its escape (#1984) ([#1990](https://github.com/monumental-archive/.github/pull/1990))
- link the belt at every hook, refuse another pin's belt ([#1993](https://github.com/monumental-archive/.github/pull/1993))
- give each pgrx build its own postgres, never run pgrx init ([#1996](https://github.com/monumental-archive/.github/pull/1996))
- have the writer name its findings file, every job upload it ([#1998](https://github.com/monumental-archive/.github/pull/1998))
- capture each producer before its loop reads it (#2001) ([#2002](https://github.com/monumental-archive/.github/pull/2002))
- hold sc2310 off in the shellcheck level, check two callees ([#2005](https://github.com/monumental-archive/.github/pull/2005))
- exclude single_call_fn and tests_outside_test_module ([#2003](https://github.com/monumental-archive/.github/pull/2003))
- list every linter's files from one tracked-file producer ([#2006](https://github.com/monumental-archive/.github/pull/2006))

### Documentation

- give a mutation survivor a third outcome, delete it ([#1985](https://github.com/monumental-archive/.github/pull/1985))

### Testing

- decide three belt rows without a clock ([#1991](https://github.com/monumental-archive/.github/pull/1991))

## [1.75.0](https://github.com/monumental-archive/.github/compare/v1.74.0...v1.75.0) - 2026-11-21

### Added

- record stele's findings through one adapter ([#1949](https://github.com/monumental-archive/.github/pull/1949))
- hand every job's findings to the report job ([#1950](https://github.com/monumental-archive/.github/pull/1950))

### Fixed

- update monumental-archive/stele to v0.28.1 ([#1954](https://github.com/monumental-archive/.github/pull/1954))
- judge the rust lock through stele, refuse advisory ignores ([#1957](https://github.com/monumental-archive/.github/pull/1957))

## [1.74.0](https://github.com/monumental-archive/.github/compare/v1.73.0...v1.74.0) - 2026-11-21

### Added

- hold rust property tests to the belt's seed ([#1943](https://github.com/monumental-archive/.github/pull/1943))
- hold a pull request's title to what it breaks (#1906) ([#1947](https://github.com/monumental-archive/.github/pull/1947))
- report every finding through one writer, as a record ([#1946](https://github.com/monumental-archive/.github/pull/1946))

### Fixed

- hand the scheduled test runs the gate's atlas-token ([#1937](https://github.com/monumental-archive/.github/pull/1937))
- update monumental-archive/signer to cd4cfbf ([#1938](https://github.com/monumental-archive/.github/pull/1938))
- refuse text after a breaking-change footer (#1924) ([#1941](https://github.com/monumental-archive/.github/pull/1941))
- show the failing step's output in red-check issues ([#1942](https://github.com/monumental-archive/.github/pull/1942))
- keep the canon link out of the caller's git tree (#1944) ([#1945](https://github.com/monumental-archive/.github/pull/1945))

## [1.73.0](https://github.com/monumental-archive/.github/compare/v1.72.0...v1.73.0) - 2026-11-21

### Added

- require mutation and coverage adoption files in the gate ([#1921](https://github.com/monumental-archive/.github/pull/1921))
- hold every fuzz target's corpus depth in the coverage floor ([#1922](https://github.com/monumental-archive/.github/pull/1922))
- propose what the scheduled fuzz search finds ([#1928](https://github.com/monumental-archive/.github/pull/1928))
- run the deep tests nightly, one runner per fuzz target ([#1929](https://github.com/monumental-archive/.github/pull/1929))

### Fixed

- hold every member to the derived rust-version ([#1917](https://github.com/monumental-archive/.github/pull/1917))
- update first-party canon ([#1918](https://github.com/monumental-archive/.github/pull/1918))
- read a python helper through its syntax tree alone ([#1919](https://github.com/monumental-archive/.github/pull/1919))

## [1.72.0](https://github.com/monumental-archive/.github/compare/v1.71.1...v1.72.0) - 2026-11-21

### Added

- derive each crate's rust-version from debian and its deps ([#1908](https://github.com/monumental-archive/.github/pull/1908))
- report each scheduled run in one red-check issue ([#1912](https://github.com/monumental-archive/.github/pull/1912))

### Fixed

- update monumental-archive/signer to 8e4d3b0 ([#1881](https://github.com/monumental-archive/.github/pull/1881))
- group pgrx's packages by their source url ([#1892](https://github.com/monumental-archive/.github/pull/1892))
- decode the plan's majors as json in both consumers ([#1893](https://github.com/monumental-archive/.github/pull/1893))
- refuse suppressions clippy cannot see in lint:rust ([#1894](https://github.com/monumental-archive/.github/pull/1894))
- split the weekly audit into one workflow per subject ([#1900](https://github.com/monumental-archive/.github/pull/1900))
- name every coverage directory for its compiler ([#1895](https://github.com/monumental-archive/.github/pull/1895))
- require and replay a committed fuzz corpus ([#1904](https://github.com/monumental-archive/.github/pull/1904))
- split the canon's scheduled legs into subject workflows ([#1907](https://github.com/monumental-archive/.github/pull/1907))
- run the slsa walks once, not in two groups ([#1913](https://github.com/monumental-archive/.github/pull/1913))
- decide each scheduled job's applicability before its runner ([#1915](https://github.com/monumental-archive/.github/pull/1915))

### Documentation

- point the lab's major range at the mapping (#1902) ([#1905](https://github.com/monumental-archive/.github/pull/1905))

## [1.71.1](https://github.com/monumental-archive/.github/compare/v1.71.0...v1.71.1) - 2026-11-21

### Fixed

- unlink every planted stub before writing it (#1869) ([#1871](https://github.com/monumental-archive/.github/pull/1871))
- update monumental-archive/signer to e0e7b86 ([#1873](https://github.com/monumental-archive/.github/pull/1873))
- judge each coverage leg in the one job that owns it ([#1876](https://github.com/monumental-archive/.github/pull/1876))

### Testing

- run python mutants in 48 shards of 180 min (#1530) ([#1870](https://github.com/monumental-archive/.github/pull/1870))
- cut rust mutant shards round-robin, 60 min timeout ([#1878](https://github.com/monumental-archive/.github/pull/1878))

## [1.71.0](https://github.com/monumental-archive/.github/compare/v1.70.7...v1.71.0) - 2026-11-20

### Added

- read workflow yaml through one module on a pinned yq ([#1855](https://github.com/monumental-archive/.github/pull/1855))

### Changed

- remove the two helpers only tests call ([#1852](https://github.com/monumental-archive/.github/pull/1852))

### Fixed

- install the clang driver and llvm in pgrx builds ([#1856](https://github.com/monumental-archive/.github/pull/1856))
- turn off ruff's two audit-prompt rules (#1838) ([#1859](https://github.com/monumental-archive/.github/pull/1859))
- retire pg14 and hold pg-majors to the canon's mapping ([#1862](https://github.com/monumental-archive/.github/pull/1862))
- read every run: value through the parser ([#1860](https://github.com/monumental-archive/.github/pull/1860))
- read only steps' run values, every scalar as its text ([#1863](https://github.com/monumental-archive/.github/pull/1863))
- key the cargo caches on the compiler that fills them ([#1866](https://github.com/monumental-archive/.github/pull/1866))

### Documentation

- describe the removed biome wrapper, not its name ([#1854](https://github.com/monumental-archive/.github/pull/1854))

### Testing

- delete the skipped server oracle for the upgrade guard ([#1851](https://github.com/monumental-archive/.github/pull/1851))

## [1.70.7](https://github.com/monumental-archive/.github/compare/v1.70.6...v1.70.7) - 2026-11-20

### Fixed

- serve ICU headers per Postgres major on a Mac ([#1794](https://github.com/monumental-archive/.github/pull/1794))
- hold MOVING's stale entries until #1604 lands (#1604) ([#1816](https://github.com/monumental-archive/.github/pull/1816))
- red every type-ignore comment, coded ones too (#1604) ([#1826](https://github.com/monumental-archive/.github/pull/1826))
- re-export pinned_names from gate_tools (#1604) ([#1828](https://github.com/monumental-archive/.github/pull/1828))
- gate mypy as lint:python-types (#1604) ([#1830](https://github.com/monumental-archive/.github/pull/1830))
- remove lint:in-process, a law with no reason left (#1790) ([#1834](https://github.com/monumental-archive/.github/pull/1834))
- cap each mutant test file's processes ([#1836](https://github.com/monumental-archive/.github/pull/1836))
- read every block header in steps() (#1837) ([#1839](https://github.com/monumental-archive/.github/pull/1839))
- read prose from the shell parser in job-installs (#1785) ([#1841](https://github.com/monumental-archive/.github/pull/1841))
- derive each pgrx extension's major once (#1795) ([#1842](https://github.com/monumental-archive/.github/pull/1842))
- restore each file a python mutant writes (#1832) ([#1835](https://github.com/monumental-archive/.github/pull/1835))
- ask the tool-venv interpreter check in one helper (#1788) ([#1844](https://github.com/monumental-archive/.github/pull/1844))

### Documentation

- record the gate's lazy-tools design (#1785) ([#1825](https://github.com/monumental-archive/.github/pull/1825))

### Testing

- type slice 1's property files to zero (#1604) ([#1799](https://github.com/monumental-archive/.github/pull/1799))
- type slice 5's test files under strict mypy (#1604) ([#1798](https://github.com/monumental-archive/.github/pull/1798))
- type slice 17's property files to zero (#1604) ([#1803](https://github.com/monumental-archive/.github/pull/1803))
- type slice 21's test files at mypy's strictest (#1604) ([#1801](https://github.com/monumental-archive/.github/pull/1801))
- bring slice 9's tests to zero mypy findings (#1604) ([#1796](https://github.com/monumental-archive/.github/pull/1796))
- type slice 14 test files to zero (#1604) ([#1804](https://github.com/monumental-archive/.github/pull/1804))
- type slice 2's property files to zero (#1604) ([#1805](https://github.com/monumental-archive/.github/pull/1805))
- type slice 23's property files at mypy's strictest (#1604) ([#1813](https://github.com/monumental-archive/.github/pull/1813))
- type slice 7's test files under strict mypy (#1604) ([#1815](https://github.com/monumental-archive/.github/pull/1815))
- type slice 13, test_subject_budget, to zero (#1604) ([#1797](https://github.com/monumental-archive/.github/pull/1797))
- type slice 6's test files under strict mypy (#1604) ([#1824](https://github.com/monumental-archive/.github/pull/1824))
- type slice 24's test files at mypy's strictest (#1604) ([#1818](https://github.com/monumental-archive/.github/pull/1818))
- bring slice 10's tests to zero mypy findings (#1604) ([#1811](https://github.com/monumental-archive/.github/pull/1811))
- bring slice 12's tests to zero mypy findings (#1604) ([#1823](https://github.com/monumental-archive/.github/pull/1823))
- bring slice 11's tests to zero mypy findings (#1604) ([#1821](https://github.com/monumental-archive/.github/pull/1821))
- type slice 19's files to zero (#1604) ([#1817](https://github.com/monumental-archive/.github/pull/1817))
- type slice 15 test files to zero (#1604) ([#1808](https://github.com/monumental-archive/.github/pull/1808))
- type slice 18's files to zero (#1604) ([#1814](https://github.com/monumental-archive/.github/pull/1814))
- type slice 3's test files to zero (#1604) ([#1809](https://github.com/monumental-archive/.github/pull/1809))
- type slice 20's files to zero (#1604) ([#1822](https://github.com/monumental-archive/.github/pull/1822))
- type slice 22's test files at mypy's strictest (#1604) ([#1806](https://github.com/monumental-archive/.github/pull/1806))
- type slice 4's test files to zero (#1604) ([#1819](https://github.com/monumental-archive/.github/pull/1819))
- type slice 8's test files under strict mypy (#1604) ([#1820](https://github.com/monumental-archive/.github/pull/1820))
- type slice 16, test_zizmor_signer, to zero (#1604) ([#1812](https://github.com/monumental-archive/.github/pull/1812))
- type the coverage rows' planted keywords (#1604) ([#1827](https://github.com/monumental-archive/.github/pull/1827))
- type slice 25's test files under strict mypy (#1604) ([#1829](https://github.com/monumental-archive/.github/pull/1829))
- let every python shard finish at 360 minutes (#1530) ([#1843](https://github.com/monumental-archive/.github/pull/1843))

## [1.70.6](https://github.com/monumental-archive/.github/compare/v1.70.5...v1.70.6) - 2026-11-20

### Changed

- remove what only mutmut's copy needed (#1772) ([#1784](https://github.com/monumental-archive/.github/pull/1784))

### Fixed

- mutate the python leg in place with cosmic-ray (#1771) ([#1775](https://github.com/monumental-archive/.github/pull/1775))
- make the debt directory before generating it (#1746) ([#1776](https://github.com/monumental-archive/.github/pull/1776))
- delete the default task shell setting (#1745) ([#1779](https://github.com/monumental-archive/.github/pull/1779))
- take the held-out list out of org_pgrx_provision ([#1781](https://github.com/monumental-archive/.github/pull/1781))
- move every entry of an array mise pin (#1773) ([#1778](https://github.com/monumental-archive/.github/pull/1778))
- read a :? expansion's message as prose (#1782) ([#1783](https://github.com/monumental-archive/.github/pull/1783))
- update monumental-archive/signer to f8157c8 ([#1789](https://github.com/monumental-archive/.github/pull/1789))
- drop the machine-belt source and its note (#1739) ([#1780](https://github.com/monumental-archive/.github/pull/1780))
- cap the floor at 90 and release on the gate's line ([#1791](https://github.com/monumental-archive/.github/pull/1791))

### Testing

- reach each refusal of the python mutation leg (#1771) ([#1787](https://github.com/monumental-archive/.github/pull/1787))

## [1.70.5](https://github.com/monumental-archive/.github/compare/v1.70.4...v1.70.5) - 2026-11-20

### Fixed

- look up mise conda tools on conda-forge (#1743) ([#1748](https://github.com/monumental-archive/.github/pull/1748))
- update jdx/mise to 2026.11.8 ([#1751](https://github.com/monumental-archive/.github/pull/1751))
- update monumental-archive/signer to 8e69dcf ([#1750](https://github.com/monumental-archive/.github/pull/1750))
- run zizmor at the auditor persona in the gate (#1719) ([#1752](https://github.com/monumental-archive/.github/pull/1752))
- refuse two files of one module name at the gate (#1737) ([#1749](https://github.com/monumental-archive/.github/pull/1749))
- find the clone's belt link under a git hook (#1744) ([#1753](https://github.com/monumental-archive/.github/pull/1753))
- read the repository's source in every law (#1726) ([#1755](https://github.com/monumental-archive/.github/pull/1755))
- replace each per-clone link by rename (#1731) ([#1756](https://github.com/monumental-archive/.github/pull/1756))
- take the go toolchain out of the canon (#1733) ([#1759](https://github.com/monumental-archive/.github/pull/1759))
- run the gate as three legs side by side (#1740) ([#1762](https://github.com/monumental-archive/.github/pull/1762))
- print each root's slowest test files (#1732) ([#1763](https://github.com/monumental-archive/.github/pull/1763))
- measure rust coverage on the test task's own run (#1653) ([#1761](https://github.com/monumental-archive/.github/pull/1761))
- fan the property runner out over its own shards (#1741) ([#1764](https://github.com/monumental-archive/.github/pull/1764))

### Testing

- call the release helpers in-process (#1698) ([#1765](https://github.com/monumental-archive/.github/pull/1765))

## [1.70.4](https://github.com/monumental-archive/.github/compare/v1.70.3...v1.70.4) - 2026-11-20

### Changed

- move the badge walk into a delivered script (#1633) ([#1639](https://github.com/monumental-archive/.github/pull/1639))

### Fixed

- update ruff to 0.17.5 ([#1620](https://github.com/monumental-archive/.github/pull/1620))
- update jdx/mise-action to v5.2.8 ([#1619](https://github.com/monumental-archive/.github/pull/1619))
- stage mypy as audit:python-types (#1604) ([#1625](https://github.com/monumental-archive/.github/pull/1625))
- measure coverage once, in the gate ([#1566](https://github.com/monumental-archive/.github/pull/1566))
- ask scorecard through the audit's one asker (#1621) ([#1630](https://github.com/monumental-archive/.github/pull/1630))
- group a push gate by its commit (#1617) ([#1626](https://github.com/monumental-archive/.github/pull/1626))
- refuse two vex decisions on the pull request (#1603) ([#1611](https://github.com/monumental-archive/.github/pull/1611))
- deliver a typed given for property files (#1604) ([#1642](https://github.com/monumental-archive/.github/pull/1642))
- list a tree's tracked files from a copy of it ([#1632](https://github.com/monumental-archive/.github/pull/1632))
- hold a go condition floor with gobco (#1546) ([#1597](https://github.com/monumental-archive/.github/pull/1597))
- sign a decision a merge renamed (#1615) ([#1644](https://github.com/monumental-archive/.github/pull/1644))
- ask every badge issuer through one asker (#1633) ([#1647](https://github.com/monumental-archive/.github/pull/1647))
- read first-match probes to the end (#1637) ([#1648](https://github.com/monumental-archive/.github/pull/1648))
- run the suite's test files in parallel (#1623) ([#1635](https://github.com/monumental-archive/.github/pull/1635))
- read a stale cached answer as unanswered (#1649) ([#1650](https://github.com/monumental-archive/.github/pull/1650))
- measure PL/pgSQL coverage with plpgsql_check (#1547) ([#1607](https://github.com/monumental-archive/.github/pull/1607))
- test captured listings, not grep -q on a pipe (#1637) ([#1654](https://github.com/monumental-archive/.github/pull/1654))
- escape pack-wasm's refusal as one workflow command ([#1658](https://github.com/monumental-archive/.github/pull/1658))
- read the environment law from the syntax tree ([#1657](https://github.com/monumental-archive/.github/pull/1657))
- read the test task through one walk in every leg (#1608) ([#1645](https://github.com/monumental-archive/.github/pull/1645))
- keep an x_ method name in mutants-python (#1555) ([#1664](https://github.com/monumental-archive/.github/pull/1664))
- run test once where the measurement already runs it (#1623) ([#1663](https://github.com/monumental-archive/.github/pull/1663))
- write coverage reports outside the working tree (#1628) ([#1646](https://github.com/monumental-archive/.github/pull/1646))
- update rumdl to 0.3.4 ([#1672](https://github.com/monumental-archive/.github/pull/1672))
- import every helper as a module (#1634) ([#1643](https://github.com/monumental-archive/.github/pull/1643))
- judge a stale presence, bound answers by the week (#1649) ([#1669](https://github.com/monumental-archive/.github/pull/1669))
- measure a pgrx extension through test:pgrx (#1656) ([#1665](https://github.com/monumental-archive/.github/pull/1665))
- ask codecov one question, its branch route (#1668) ([#1671](https://github.com/monumental-archive/.github/pull/1671))
- hand a child the mutation leg's environment ([#1674](https://github.com/monumental-archive/.github/pull/1674))
- record the legs a coverage floor holds (#1666) ([#1675](https://github.com/monumental-archive/.github/pull/1675))
- write workflow commands through one escaping module ([#1685](https://github.com/monumental-archive/.github/pull/1685))
- parse every document through one strict typed loader (#1676) ([#1687](https://github.com/monumental-archive/.github/pull/1687))
- test the default branch by its name in the gate (#1693) ([#1700](https://github.com/monumental-archive/.github/pull/1700))
- scan load_json's depth in one linear pass (#1676) ([#1706](https://github.com/monumental-archive/.github/pull/1706))
- look test tools up by name, type results (#1684) ([#1690](https://github.com/monumental-archive/.github/pull/1690))
- decode subject_budget's documents once, typed (#1677) ([#1691](https://github.com/monumental-archive/.github/pull/1691))
- decode the shell and badge-audit helpers, typed (#1681) ([#1704](https://github.com/monumental-archive/.github/pull/1704))
- chain every start-up hook to the next on the path ([#1689](https://github.com/monumental-archive/.github/pull/1689))
- check PL/pgSQL bodies with plpgsql_check (#1605) ([#1694](https://github.com/monumental-archive/.github/pull/1694))
- decode gate and security-config documents (#1678) ([#1695](https://github.com/monumental-archive/.github/pull/1695))
- type the mutation, coverage and runner helpers (#1679) ([#1707](https://github.com/monumental-archive/.github/pull/1707))
- type the rekor-monitor helpers' documents (#1683) ([#1702](https://github.com/monumental-archive/.github/pull/1702))
- load each clone's belt from its own pin (#1686) ([#1703](https://github.com/monumental-archive/.github/pull/1703))
- read the gate's coverage figures (#1622) ([#1688](https://github.com/monumental-archive/.github/pull/1688))
- decode the cargo, lockfile and tool-config helpers (#1680) ([#1701](https://github.com/monumental-archive/.github/pull/1701))
- test helper logic in-process, per function (#1673) ([#1705](https://github.com/monumental-archive/.github/pull/1705))
- refuse every plain ls-files, whatever the root ([#1716](https://github.com/monumental-archive/.github/pull/1716))
- type the release/ helpers clean under mypy (#1682) ([#1709](https://github.com/monumental-archive/.github/pull/1709))
- read each pgrx job's coverage figure (#1692) ([#1708](https://github.com/monumental-archive/.github/pull/1708))
- declare the audit environment for vex-attest's plan (#1602) ([#1721](https://github.com/monumental-archive/.github/pull/1721))
- measure coverage in the one test run (#1667) ([#1712](https://github.com/monumental-archive/.github/pull/1712))
- update monumental-archive/signer to 5edff31 ([#1723](https://github.com/monumental-archive/.github/pull/1723))
- write the shell's workflow commands through one writer ([#1710](https://github.com/monumental-archive/.github/pull/1710))
- run the weekly properties in eight timed shards (#1631) ([#1724](https://github.com/monumental-archive/.github/pull/1724))
- measure go coverage in the test task's run (#1651) ([#1729](https://github.com/monumental-archive/.github/pull/1729))
- tell shellcheck where .org-canon is, on every machine ([#1720](https://github.com/monumental-archive/.github/pull/1720))

### Documentation

- record the exhaustruct_v5 measurement (stele#317) ([#1627](https://github.com/monumental-archive/.github/pull/1627))

### Testing

- vary the inputs of mutants-python (#1555) ([#1613](https://github.com/monumental-archive/.github/pull/1613))
- hold the debt round trip to the umbrella rule (#1555) ([#1624](https://github.com/monumental-archive/.github/pull/1624))
- state the hookless child's PYTHONPATH ([#1655](https://github.com/monumental-archive/.github/pull/1655))
- compare, not subtract, in the quantise property (#1555) ([#1662](https://github.com/monumental-archive/.github/pull/1662))
- take the untested helper branches (#1659) ([#1661](https://github.com/monumental-archive/.github/pull/1661))
- take the coverage helpers' untaken branches (#1659) ([#1670](https://github.com/monumental-archive/.github/pull/1670))
- call plpgsql_coverage in-process (#1711) ([#1713](https://github.com/monumental-archive/.github/pull/1713))
- call the remaining mise helpers in-process (#1697) ([#1722](https://github.com/monumental-archive/.github/pull/1722))
- plant whole belts and canons through hermetic (#1714) ([#1718](https://github.com/monumental-archive/.github/pull/1718))
- test the mutation and runner helpers in-process (#1696) ([#1725](https://github.com/monumental-archive/.github/pull/1725))
- test the rekor-monitor helpers in-process (#1699) ([#1717](https://github.com/monumental-archive/.github/pull/1717))
- hold the squash properties to the committer guard (#1727) ([#1730](https://github.com/monumental-archive/.github/pull/1730))

## [1.70.3](https://github.com/monumental-archive/.github/compare/v1.70.2...v1.70.3) - 2026-11-19

### Fixed

- update first-party canon ([#1539](https://github.com/monumental-archive/.github/pull/1539))
- update jdx/mise to 2026.11.7 ([#1541](https://github.com/monumental-archive/.github/pull/1541))
- validate pgrx majors in one place ([#1553](https://github.com/monumental-archive/.github/pull/1553))
- update aqua:astral-sh/uv to 0.13.8 ([#1561](https://github.com/monumental-archive/.github/pull/1561))
- update jdx/mise-action to v5.2.8 ([#1563](https://github.com/monumental-archive/.github/pull/1563))
- decide the humantime and chrono unmaintained notices ([#1551](https://github.com/monumental-archive/.github/pull/1551))
- hold task bodies to the 15-line shell ceiling ([#1560](https://github.com/monumental-archive/.github/pull/1560))
- red any tracked codecov config by name ([#1552](https://github.com/monumental-archive/.github/pull/1552))
- compare the bestpractices.dev entry with its file ([#1559](https://github.com/monumental-archive/.github/pull/1559))
- load a child's config where the run does ([#1554](https://github.com/monumental-archive/.github/pull/1554))
- refuse a body line starting with committer (#1529) ([#1571](https://github.com/monumental-archive/.github/pull/1571))
- refuse a spelled go stamp and non-table pins (#1556) ([#1572](https://github.com/monumental-archive/.github/pull/1572))
- refuse malformed manifests by name (#1556) ([#1577](https://github.com/monumental-archive/.github/pull/1577))
- update pipx:coverage to 7.17.3 ([#1581](https://github.com/monumental-archive/.github/pull/1581))
- refuse a queue group built from an old head (#1422) ([#1558](https://github.com/monumental-archive/.github/pull/1558))
- name an undecodable file in mutmut-pragmas (#1555) ([#1579](https://github.com/monumental-archive/.github/pull/1579))
- update jdx/mise-action to v5.2.8 ([#1580](https://github.com/monumental-archive/.github/pull/1580))
- drop only the one skip in each deny variant (#1555) ([#1583](https://github.com/monumental-archive/.github/pull/1583))
- refuse an unread index and malformed base rows (#1556) ([#1585](https://github.com/monumental-archive/.github/pull/1585))
- run fast-check properties at the belt's seed (#1549) ([#1569](https://github.com/monumental-archive/.github/pull/1569))
- add a typescript mutation leg (#1358) ([#1575](https://github.com/monumental-archive/.github/pull/1575))
- refuse a pgrx package that is not a table (#1556) ([#1587](https://github.com/monumental-archive/.github/pull/1587))
- record a wrong-shaped manifest in subject-budget (#1555) ([#1588](https://github.com/monumental-archive/.github/pull/1588))
- run every helper under the belt's python ([#1565](https://github.com/monumental-archive/.github/pull/1565))
- read an [[env]] array in belt-shell (#1555) ([#1593](https://github.com/monumental-archive/.github/pull/1593))
- read past a task reference in coverage-js-runner (#1555) ([#1595](https://github.com/monumental-archive/.github/pull/1595))
- read only ASCII digits as a version in msrv-plan (#1555) ([#1596](https://github.com/monumental-archive/.github/pull/1596))
- read only ASCII digits as a major in pgrx-postgres (#1555) ([#1598](https://github.com/monumental-archive/.github/pull/1598))
- refuse a non-object config in tsconfig-dials (#1555) ([#1599](https://github.com/monumental-archive/.github/pull/1599))
- keep a wrong-shaped finding in zizmor-signer (#1555) ([#1601](https://github.com/monumental-archive/.github/pull/1601))
- refuse a non-object cargo metadata in gate-surface (#1555) ([#1606](https://github.com/monumental-archive/.github/pull/1606))
- read a step shell key wherever it sits (#1555) ([#1612](https://github.com/monumental-archive/.github/pull/1612))
- read the population with the audit app token (#1602) ([#1609](https://github.com/monumental-archive/.github/pull/1609))
- label copied fixtures with their source's licence (#1589) ([#1594](https://github.com/monumental-archive/.github/pull/1594))
- start task helpers under the belt's python (#1582) ([#1614](https://github.com/monumental-archive/.github/pull/1614))
- name the pgrx predecessor by one function (#1592) ([#1610](https://github.com/monumental-archive/.github/pull/1610))

### Documentation

- re-answer regression_tests_added50 from the backfill (#1545) ([#1590](https://github.com/monumental-archive/.github/pull/1590))

### Testing

- vary three small readers (#1555) ([#1562](https://github.com/monumental-archive/.github/pull/1562))
- vary five file judges (#1555) ([#1567](https://github.com/monumental-archive/.github/pull/1567))
- vary five environment readers (#1555) ([#1568](https://github.com/monumental-archive/.github/pull/1568))
- vary coverage and gate readers (#1555) ([#1574](https://github.com/monumental-archive/.github/pull/1574))
- backfill regression rows, second part (#1545) ([#1570](https://github.com/monumental-archive/.github/pull/1570))
- property tests for the three helpers (#1557) ([#1573](https://github.com/monumental-archive/.github/pull/1573))
- backfill regression rows, first part (#1545) ([#1564](https://github.com/monumental-archive/.github/pull/1564))
- vary cargo, lock and plan readers (#1555) ([#1576](https://github.com/monumental-archive/.github/pull/1576))
- backfill regression rows, third part (#1545) ([#1578](https://github.com/monumental-archive/.github/pull/1578))
- vary four large judges (#1555) ([#1584](https://github.com/monumental-archive/.github/pull/1584))
- vary the inputs of mutants-rust (#1555) ([#1586](https://github.com/monumental-archive/.github/pull/1586))
- refuse a nested mise in the real lint:badges (#1545) ([#1600](https://github.com/monumental-archive/.github/pull/1600))

## [1.70.2](https://github.com/monumental-archive/.github/compare/v1.70.1...v1.70.2) - 2026-11-18

### Fixed

- update first-party canon ([#1534](https://github.com/monumental-archive/.github/pull/1534))
- sign a tag's source verdict before attach (#1350) ([#1535](https://github.com/monumental-archive/.github/pull/1535))
- owe the source verdict from v1.70.2 ([#1538](https://github.com/monumental-archive/.github/pull/1538))

## [1.70.1](https://github.com/monumental-archive/.github/compare/v1.70.0...v1.70.1) - 2026-11-18

### Changed

- compare gomu's build go in python ([#1525](https://github.com/monumental-archive/.github/pull/1525))

### Fixed

- update monumental-archive/signer to f1bb0d6 ([#1518](https://github.com/monumental-archive/.github/pull/1518))
- let mutmut's child processes import mutmut ([#1503](https://github.com/monumental-archive/.github/pull/1503))
- build every fixture environment through one allow-list ([#1521](https://github.com/monumental-archive/.github/pull/1521))
- build gomu with the repository's own go ([#1524](https://github.com/monumental-archive/.github/pull/1524))
- enforce the no-coverage-exclusions rule ([#1523](https://github.com/monumental-archive/.github/pull/1523))
- update monumental-archive/signer to 54e77ff ([#1527](https://github.com/monumental-archive/.github/pull/1527))

## [1.70.0](https://github.com/monumental-archive/.github/compare/v1.69.3...v1.70.0) - 2026-11-18

### Added

- adopt the canon's own python coverage floor ([#1492](https://github.com/monumental-archive/.github/pull/1492))

### Fixed

- run the canon's suite with git aimed at a decoy ([#1470](https://github.com/monumental-archive/.github/pull/1470))
- time out the rust mutation shards at 25 minutes ([#1474](https://github.com/monumental-archive/.github/pull/1474))
- vary python inputs with a hypothesis property leg ([#1475](https://github.com/monumental-archive/.github/pull/1475))
- land a release only where it was planned ([#1477](https://github.com/monumental-archive/.github/pull/1477))
- read mutmut pragmas from comments, not strings ([#1478](https://github.com/monumental-archive/.github/pull/1478))
- read the plan's majors as the strings it prints ([#1480](https://github.com/monumental-archive/.github/pull/1480))
- move registry operations into a tested helper (#1382) ([#1464](https://github.com/monumental-archive/.github/pull/1464))
- show the output of a gomu run that fails ([#1484](https://github.com/monumental-archive/.github/pull/1484))
- read the actions allowlist from one list ([#1485](https://github.com/monumental-archive/.github/pull/1485))
- run gomu's binary, never mise's shim ([#1487](https://github.com/monumental-archive/.github/pull/1487))
- copy the canon's whole tree into mutmut's mutants/ ([#1489](https://github.com/monumental-archive/.github/pull/1489))
- move the rust and python mutation legs' logic to python ([#1493](https://github.com/monumental-archive/.github/pull/1493))
- measure the test task's own vitest run ([#1494](https://github.com/monumental-archive/.github/pull/1494))
- name a test that leaves the process elsewhere ([#1495](https://github.com/monumental-archive/.github/pull/1495))
- take the pgrx init lock from the kernel ([#1490](https://github.com/monumental-archive/.github/pull/1490))
- move the canon to stele v0.25.0 at schema 9 ([#1497](https://github.com/monumental-archive/.github/pull/1497))
- drop python comments by token in test_job_installs ([#1498](https://github.com/monumental-archive/.github/pull/1498))
- read the command a python helper runs as a call ([#1501](https://github.com/monumental-archive/.github/pull/1501))
- derive the declared classes with stele (#1378) ([#1505](https://github.com/monumental-archive/.github/pull/1505))
- open the commit point with stele (#1374) ([#1506](https://github.com/monumental-archive/.github/pull/1506))
- derive build subjects with one stele call (#1364) ([#1507](https://github.com/monumental-archive/.github/pull/1507))
- take the population from stele (#1379) ([#1509](https://github.com/monumental-archive/.github/pull/1509))
- update typos to 1.52.2 ([#1510](https://github.com/monumental-archive/.github/pull/1510))
- verify releases through stele calls (#1363) ([#1513](https://github.com/monumental-archive/.github/pull/1513))
- verify base images with stele (#1377) ([#1515](https://github.com/monumental-archive/.github/pull/1515))
- compare builds with stele verify repro (#1376) ([#1511](https://github.com/monumental-archive/.github/pull/1511))
- give a fixture only the environment its row names ([#1512](https://github.com/monumental-archive/.github/pull/1512))
- derive inventories through stele calls (#1371) ([#1516](https://github.com/monumental-archive/.github/pull/1516))
- update monumental-archive/stele to v0.26.0 ([#1500](https://github.com/monumental-archive/.github/pull/1500))
- dequeue and regenerate a queued release pr ([#1517](https://github.com/monumental-archive/.github/pull/1517))

### Documentation

- record the pgrx test class cargo-mutants mutates ([#1479](https://github.com/monumental-archive/.github/pull/1479))
- re-answer regression tests from the checklist ([#1482](https://github.com/monumental-archive/.github/pull/1482))
- name both belt test legs in CLAUDE.md ([#1488](https://github.com/monumental-archive/.github/pull/1488))
- count the pgrx init lock row in the answer ([#1502](https://github.com/monumental-archive/.github/pull/1502))

### Testing

- hold the baseline check and the pgrx init to their bugs ([#1454](https://github.com/monumental-archive/.github/pull/1454))

## [1.69.3](https://github.com/monumental-archive/.github/compare/v1.69.2...v1.69.3) - 2026-11-18

### Fixed

- stop counting a bare fixtures directory as a test ([#1456](https://github.com/monumental-archive/.github/pull/1456))
- publish crates and npm via tested helpers (#1383) ([#1457](https://github.com/monumental-archive/.github/pull/1457))
- package and build go through tested helpers (#1384) ([#1459](https://github.com/monumental-archive/.github/pull/1459))
- read every action file in the action lints (#1448) ([#1461](https://github.com/monumental-archive/.github/pull/1461))
- update first-party canon ([#1460](https://github.com/monumental-archive/.github/pull/1460))
- stand up the python leg of mutation testing ([#1450](https://github.com/monumental-archive/.github/pull/1450))
- hold a rust branch floor on the monday audit ([#1455](https://github.com/monumental-archive/.github/pull/1455))
- run the pre-push gate inline, never from the cache ([#1465](https://github.com/monumental-archive/.github/pull/1465))
- prove the extension the pgrx image ships (#1439) ([#1467](https://github.com/monumental-archive/.github/pull/1467))

### Testing

- hold the badge derivation's image and licence to their bugs ([#1466](https://github.com/monumental-archive/.github/pull/1466))

## [1.69.2](https://github.com/monumental-archive/.github/compare/v1.69.1...v1.69.2) - 2026-11-17

### Fixed

- pgrx images staging and proof as helpers (#1381) ([#1449](https://github.com/monumental-archive/.github/pull/1449))
- measure typescript with vitest's own provider ([#1451](https://github.com/monumental-archive/.github/pull/1451))

## [1.69.1](https://github.com/monumental-archive/.github/compare/v1.69.0...v1.69.1) - 2026-11-17

### Fixed

- update rumdl to 0.3.3 ([#1442](https://github.com/monumental-archive/.github/pull/1442))
- keep blank lines after a run: block out of shfmt ([#1443](https://github.com/monumental-archive/.github/pull/1443))
- derive the build matrices in one tested helper (#1385) ([#1445](https://github.com/monumental-archive/.github/pull/1445))
- stand up the go leg of mutation testing ([#1446](https://github.com/monumental-archive/.github/pull/1446))

### Testing

- hold the oldest canon fixes to their reverted bugs ([#1440](https://github.com/monumental-archive/.github/pull/1440))

## [1.69.0](https://github.com/monumental-archive/.github/compare/v1.68.12...v1.69.0) - 2026-11-17

### Added

- measure python with a statement and branch leg ([#1434](https://github.com/monumental-archive/.github/pull/1434))

### Fixed

- count the fixes that arrived with a regression test ([#1437](https://github.com/monumental-archive/.github/pull/1437))
- let the rust selector measure the rust leg ([#1438](https://github.com/monumental-archive/.github/pull/1438))
- stand up the rust leg of mutation testing ([#1429](https://github.com/monumental-archive/.github/pull/1429))

## [1.68.12](https://github.com/monumental-archive/.github/compare/v1.68.11...v1.68.12) - 2026-11-17

### Fixed

- install biome wherever lint:biome runs (#1410) ([#1428](https://github.com/monumental-archive/.github/pull/1428))

## [1.68.11](https://github.com/monumental-archive/.github/compare/v1.68.10...v1.68.11) - 2026-11-17

### Fixed

- run go fuzz seeds only where a target is declared ([#1423](https://github.com/monumental-archive/.github/pull/1423))
- install only the belt tools a tree's gate tasks call ([#1424](https://github.com/monumental-archive/.github/pull/1424))

## [1.68.10](https://github.com/monumental-archive/.github/compare/v1.68.9...v1.68.10) - 2026-11-17

### Fixed

- update first-party canon ([#1420](https://github.com/monumental-archive/.github/pull/1420))
- give the pgrx legs and coverage upload a named install list ([#1415](https://github.com/monumental-archive/.github/pull/1415))

### Documentation

- write the org testing standard ([#1419](https://github.com/monumental-archive/.github/pull/1419))

## [1.68.9](https://github.com/monumental-archive/.github/compare/v1.68.8...v1.68.9) - 2026-11-17

### Fixed

- save gate caches from the default branch only ([#1411](https://github.com/monumental-archive/.github/pull/1411))
- cut the signer watch over to stele's reader ([#1407](https://github.com/monumental-archive/.github/pull/1407))
- key the gate's tool cache on the belt's lockfile ([#1418](https://github.com/monumental-archive/.github/pull/1418))
- keep mise run from installing past the named list ([#1416](https://github.com/monumental-archive/.github/pull/1416))

## [1.68.8](https://github.com/monumental-archive/.github/compare/v1.68.7...v1.68.8) - 2026-11-17

### Fixed

- update monumental-archive/stele to v0.23.0 ([#1402](https://github.com/monumental-archive/.github/pull/1402))
- update first-party canon ([#1404](https://github.com/monumental-archive/.github/pull/1404))
- lint folded run: blocks as the text bash receives ([#1405](https://github.com/monumental-archive/.github/pull/1405))

## [1.68.7](https://github.com/monumental-archive/.github/compare/v1.68.6...v1.68.7) - 2026-11-17

### Fixed

- hold workflow run: blocks to a 15-line ceiling ([#1399](https://github.com/monumental-archive/.github/pull/1399))

## [1.68.6](https://github.com/monumental-archive/.github/compare/v1.68.5...v1.68.6) - 2026-11-17

### Fixed

- keep one standing issue while the watch is behind ([#1392](https://github.com/monumental-archive/.github/pull/1392))
- ask the first-mint title guard the full title ([#1397](https://github.com/monumental-archive/.github/pull/1397))
- give the prebuilt cargo-auditable key a short topic ([#1398](https://github.com/monumental-archive/.github/pull/1398))

## [1.68.5](https://github.com/monumental-archive/.github/compare/v1.68.4...v1.68.5) - 2026-11-17

### Fixed

- update aqua:google/osv-scanner to 2.7.1 ([#1337](https://github.com/monumental-archive/.github/pull/1337))
- update monumental-archive/signer to b746956 ([#1345](https://github.com/monumental-archive/.github/pull/1345))
- update typos to 1.52.1 ([#1347](https://github.com/monumental-archive/.github/pull/1347))
- pass the job token to the links check ([#1366](https://github.com/monumental-archive/.github/pull/1366))
- update jdx/mise to 2026.11.6 ([#1367](https://github.com/monumental-archive/.github/pull/1367))
- declare release-lab's continuous stream on the roster ([#1368](https://github.com/monumental-archive/.github/pull/1368))
- drop ref-version-mismatch on the signer's pins ([#1369](https://github.com/monumental-archive/.github/pull/1369))
- install a named tool list in every release job ([#1370](https://github.com/monumental-archive/.github/pull/1370))
- check npm package links against the registry ([#1390](https://github.com/monumental-archive/.github/pull/1390))
- pin cargo-auditable in its prebuilt github: form ([#1387](https://github.com/monumental-archive/.github/pull/1387))
- move the zenodo client into a tested mint-doi helper ([#1388](https://github.com/monumental-archive/.github/pull/1388))
- judge each crate on its own in audit:semver ([#1391](https://github.com/monumental-archive/.github/pull/1391))

## [1.68.4](https://github.com/monumental-archive/.github/compare/v1.68.3...v1.68.4) - 2026-11-16

### Fixed

- make the pgrx upgrade proof read the catalog ([#1326](https://github.com/monumental-archive/.github/pull/1326))

## [1.68.3](https://github.com/monumental-archive/.github/compare/v1.68.2...v1.68.3) - 2026-11-16

### Fixed

- update first-party canon ([#1320](https://github.com/monumental-archive/.github/pull/1320))
- mint the tag first and name a workflows refusal ([#1323](https://github.com/monumental-archive/.github/pull/1323))
- update jdx/mise to 2026.11.5 ([#1325](https://github.com/monumental-archive/.github/pull/1325))

### Documentation

- say both archetypes bear build and dependency evidence ([#1319](https://github.com/monumental-archive/.github/pull/1319))

## [1.68.2](https://github.com/monumental-archive/.github/compare/v1.68.1...v1.68.2) - 2026-11-16

### Fixed

- update first-party canon ([#1315](https://github.com/monumental-archive/.github/pull/1315))
- declare the dependency track on the db ([#1317](https://github.com/monumental-archive/.github/pull/1317))
- update monumental-archive/stele to v0.22.3 ([#1318](https://github.com/monumental-archive/.github/pull/1318))

## [1.68.1](https://github.com/monumental-archive/.github/compare/v1.68.0...v1.68.1) - 2026-11-15

### Fixed

- update monumental-archive/stele to v0.22.2 ([#1313](https://github.com/monumental-archive/.github/pull/1313))

## [1.68.0](https://github.com/monumental-archive/.github/compare/v1.67.4...v1.68.0) - 2026-11-15

### Added

- ship one image layout and prove both consumers ([#1305](https://github.com/monumental-archive/.github/pull/1305))
- strip with a debug sibling, pack once, state the floor ([#1312](https://github.com/monumental-archive/.github/pull/1312))

### Fixed

- rule the struct-field pattern pair for the new lint ([#1300](https://github.com/monumental-archive/.github/pull/1300))
- render the ghcr shield of a continuous publisher ([#1306](https://github.com/monumental-archive/.github/pull/1306))
- publish per cargo's key, exclude only compiles ([#1307](https://github.com/monumental-archive/.github/pull/1307))
- publish the decisions where the level judge reads ([#1309](https://github.com/monumental-archive/.github/pull/1309))
- apply registry tags last, after attach ([#1308](https://github.com/monumental-archive/.github/pull/1308))
- ship only the crates crates.io will receive ([#1311](https://github.com/monumental-archive/.github/pull/1311))

### Documentation

- record the audit token's issues read grant ([#1304](https://github.com/monumental-archive/.github/pull/1304))

## [1.67.4](https://github.com/monumental-archive/.github/compare/v1.67.3...v1.67.4) - 2026-11-15

### Fixed

- update monumental-archive/signer to dd3d082 ([#1295](https://github.com/monumental-archive/.github/pull/1295))

## [1.67.3](https://github.com/monumental-archive/.github/compare/v1.67.2...v1.67.3) - 2026-11-15

### Fixed

- correct the db repo's floor and roster reason ([#1275](https://github.com/monumental-archive/.github/pull/1275))
- name the call a refused release-pr step died on ([#1270](https://github.com/monumental-archive/.github/pull/1270))
- reconcile each signer certificate with a signing ([#1271](https://github.com/monumental-archive/.github/pull/1271))
- name the signer by the canon's owner ([#1284](https://github.com/monumental-archive/.github/pull/1284))
- restore the gate's go cache only on an exact key ([#1287](https://github.com/monumental-archive/.github/pull/1287))
- run the pre-push gate as a script so it is never skipped ([#1282](https://github.com/monumental-archive/.github/pull/1282))
- pre-set only org-level answers in the bestpractices stub ([#1289](https://github.com/monumental-archive/.github/pull/1289))
- update monumental-archive/signer to 3dc4846 ([#1292](https://github.com/monumental-archive/.github/pull/1292))
- call a fuzz probe failure stale only when it is ([#1294](https://github.com/monumental-archive/.github/pull/1294))

### Documentation

- state stub permission grants in the caller's terms ([#1286](https://github.com/monumental-archive/.github/pull/1286))
- commit to crediting vulnerability reporters ([#1293](https://github.com/monumental-archive/.github/pull/1293))

## [1.67.2](https://github.com/monumental-archive/.github/compare/v1.67.1...v1.67.2) - 2026-11-15

### Fixed

- update first-party canon ([#1259](https://github.com/monumental-archive/.github/pull/1259))
- update monumental-archive/signer to 1b66989 ([#1266](https://github.com/monumental-archive/.github/pull/1266))
- mint a version doi for every licence the tree states ([#1255](https://github.com/monumental-archive/.github/pull/1255))
- watch fulcio's certificate log, not rekor ([#1254](https://github.com/monumental-archive/.github/pull/1254))
- say no verdict when the compare never ran ([#1265](https://github.com/monumental-archive/.github/pull/1265))
- never open or keep a Release PR from a stale plan ([#1268](https://github.com/monumental-archive/.github/pull/1268))
- roster the ledger on the source track (#1264) ([#1269](https://github.com/monumental-archive/.github/pull/1269))

### Documentation

- point the project links at the org ([#1267](https://github.com/monumental-archive/.github/pull/1267))

## [1.67.1](https://github.com/monumental-archive/.github/compare/v1.67.0...v1.67.1) - 2026-11-15

### Fixed

- update monumental-archive/signer to 3ede177 ([#1244](https://github.com/monumental-archive/.github/pull/1244))
- skip short commit shas in the delivered typos config ([#1252](https://github.com/monumental-archive/.github/pull/1252))
- leave pg-schema snapshots out of lint:sql and fix:sql ([#1253](https://github.com/monumental-archive/.github/pull/1253))
- accept a posix exit-trap guard with no waiver ([#1256](https://github.com/monumental-archive/.github/pull/1256))

## [1.67.0](https://github.com/monumental-archive/.github/compare/v1.66.0...v1.67.0) - 2026-11-15

### Added

- hold the security records to the live org ([#1239](https://github.com/monumental-archive/.github/pull/1239))

### Fixed

- stop the local gate at the tree it runs in ([#1234](https://github.com/monumental-archive/.github/pull/1234))
- fail an EXIT-trapped script that never reached its end ([#1238](https://github.com/monumental-archive/.github/pull/1238))
- print where a hung monitor is stuck ([#1211](https://github.com/monumental-archive/.github/pull/1211))
- refuse go-binary beside a plan-emitting class ([#1242](https://github.com/monumental-archive/.github/pull/1242))
- drop stale-action-refs findings on the signer in the audit ([#1243](https://github.com/monumental-archive/.github/pull/1243))

## [1.66.0](https://github.com/monumental-archive/.github/compare/v1.65.0...v1.66.0) - 2026-11-14

### Added

- pin every tracked file under vendor/ with lint:vendored ([#1221](https://github.com/monumental-archive/.github/pull/1221))
- lint the lock against the declared release platforms ([#1227](https://github.com/monumental-archive/.github/pull/1227))
- ship the security-release stub and the hold label ([#1226](https://github.com/monumental-archive/.github/pull/1226))
- audit each mise.lock against a fresh mise lock ([#1231](https://github.com/monumental-archive/.github/pull/1231))
- check pgrx sql surfaces against their snapshot ([#1223](https://github.com/monumental-archive/.github/pull/1223))

### Fixed

- update first-party canon ([#1192](https://github.com/monumental-archive/.github/pull/1192))
- update jdx/mise to 2026.11.4 ([#1193](https://github.com/monumental-archive/.github/pull/1193))
- update monumental-archive/stele to v0.22.0 ([#1206](https://github.com/monumental-archive/.github/pull/1206))
- pin pgrx headers to the image's server version ([#1207](https://github.com/monumental-archive/.github/pull/1207))
- write checksums.txt outside the directory it hashes ([#1208](https://github.com/monumental-archive/.github/pull/1208))
- skip the release age for go patch updates ([#1209](https://github.com/monumental-archive/.github/pull/1209))
- group pull request runs by number, never cancel main ([#1210](https://github.com/monumental-archive/.github/pull/1210))
- turn off dependabot security updates, keep alerts ([#1212](https://github.com/monumental-archive/.github/pull/1212))
- name each lint's fixer and document hand-made bumps ([#1214](https://github.com/monumental-archive/.github/pull/1214))
- drop the wasm-pack registry correction ([#1217](https://github.com/monumental-archive/.github/pull/1217))
- red a Best Practices entry bound to another repository ([#1219](https://github.com/monumental-archive/.github/pull/1219))
- wait out a registry 404, fail fast on anything else ([#1220](https://github.com/monumental-archive/.github/pull/1220))
- reconcile the org listing against the roster by name ([#1222](https://github.com/monumental-archive/.github/pull/1222))
- drop the three exclusions that exclude nothing ([#1216](https://github.com/monumental-archive/.github/pull/1216))
- pin the aqua registry to the corrected file ([#1229](https://github.com/monumental-archive/.github/pull/1229))
- skip crates already on the index when publishing ([#1230](https://github.com/monumental-archive/.github/pull/1230))

### Documentation

- stop naming a lab release as a gate ([#1200](https://github.com/monumental-archive/.github/pull/1200))
- state grants in stubs, never the callee's internals ([#1215](https://github.com/monumental-archive/.github/pull/1215))
- stop naming the dropped exclusions in audit:links ([#1228](https://github.com/monumental-archive/.github/pull/1228))

### Testing

- thread mise's data directory through the pgrx fixture ([#1205](https://github.com/monumental-archive/.github/pull/1205))

## [1.65.0](https://github.com/monumental-archive/.github/compare/v1.64.4...v1.65.0) - 2026-11-14

### Added

- pin stele v0.21.0 and name what a registry holds ([#1187](https://github.com/monumental-archive/.github/pull/1187))
- let a fixed main release itself ([#1184](https://github.com/monumental-archive/.github/pull/1184))

### Fixed

- rename the tool cache so it is saved with the toolchains ([#1182](https://github.com/monumental-archive/.github/pull/1182))
- update first-party canon ([#1183](https://github.com/monumental-archive/.github/pull/1183))

## [1.64.4](https://github.com/monumental-archive/.github/compare/v1.64.3...v1.64.4) - 2026-11-14

### Fixed

- run the coverage upload where no pgrx leg runs ([#1176](https://github.com/monumental-archive/.github/pull/1176))

## [1.64.3](https://github.com/monumental-archive/.github/compare/v1.64.2...v1.64.3) - 2026-11-14

### Fixed

- render every shield and ask each issuer one way ([#1174](https://github.com/monumental-archive/.github/pull/1174))

## [1.64.2](https://github.com/monumental-archive/.github/compare/v1.64.1...v1.64.2) - 2026-11-14

### Fixed

- take the org level walk back off a personal token ([#1162](https://github.com/monumental-archive/.github/pull/1162))
- update monumental-archive/signer to 50c2de6 ([#1170](https://github.com/monumental-archive/.github/pull/1170))
- give the org walks a budget of their own ([#1171](https://github.com/monumental-archive/.github/pull/1171))
- ask crates.io by name and derive the pgrx image ([#1172](https://github.com/monumental-archive/.github/pull/1172))
- stop rebuilding pgrx and rust toolchains every job ([#1173](https://github.com/monumental-archive/.github/pull/1173))

### Documentation

- record that an expect depends on the clippy config ([#1159](https://github.com/monumental-archive/.github/pull/1159))

## [1.64.1](https://github.com/monumental-archive/.github/compare/v1.64.0...v1.64.1) - 2026-11-14

### Fixed

- let the population walks see the private member ([#1154](https://github.com/monumental-archive/.github/pull/1154))
- update the postgres base images to the november minors ([#1156](https://github.com/monumental-archive/.github/pull/1156))

### Documentation

- record the actions binding on the required check ([#1158](https://github.com/monumental-archive/.github/pull/1158))

## [1.64.0](https://github.com/monumental-archive/.github/compare/v1.63.2...v1.64.0) - 2026-11-14

### Added

- run a pgrx extension one major per leg ([#1153](https://github.com/monumental-archive/.github/pull/1153))

### Fixed

- make a dead rekor monitor say so ([#1149](https://github.com/monumental-archive/.github/pull/1149))
- ask whether a base digest resolves by digest alone ([#1151](https://github.com/monumental-archive/.github/pull/1151))

## [1.63.2](https://github.com/monumental-archive/.github/compare/v1.63.1...v1.63.2) - 2026-11-13

### Fixed

- supply ICU's headers to a pgrx build on macOS ([#1137](https://github.com/monumental-archive/.github/pull/1137))
- initialise a pgrx Postgres one task at a time ([#1140](https://github.com/monumental-archive/.github/pull/1140))

## [1.63.1](https://github.com/monumental-archive/.github/compare/v1.63.0...v1.63.1) - 2026-11-13

### Fixed

- disable exhaustruct's successor in the Go stub ([#1133](https://github.com/monumental-archive/.github/pull/1133))
- update monumental-archive/signer to 0ea8f64 ([#1135](https://github.com/monumental-archive/.github/pull/1135))
- give coverage a Postgres and the pin a short title ([#1136](https://github.com/monumental-archive/.github/pull/1136))

## [1.63.0](https://github.com/monumental-archive/.github/compare/v1.62.0...v1.63.0) - 2026-11-13

### Added

- catch a machine running behind the toolchain CI uses ([#1129](https://github.com/monumental-archive/.github/pull/1129))

### Fixed

- update first-party canon ([#1130](https://github.com/monumental-archive/.github/pull/1130))

## [1.62.0](https://github.com/monumental-archive/.github/compare/v1.61.1...v1.62.0) - 2026-11-13

### Added

- run the gate for the merge queue ([#1117](https://github.com/monumental-archive/.github/pull/1117))
- make the merge queue the org's baseline ([#1127](https://github.com/monumental-archive/.github/pull/1127))

### Fixed

- update monumental-archive/signer to acd15d0 ([#920](https://github.com/monumental-archive/.github/pull/920))
- update aqua:google/osv-scanner to 2.5.1 ([#919](https://github.com/monumental-archive/.github/pull/919))
- update rumdl to 0.2.56 ([#921](https://github.com/monumental-archive/.github/pull/921))
- update rumdl to 0.2.58 ([#923](https://github.com/monumental-archive/.github/pull/923))
- update aqua:golangci/golangci-lint to 2.13.0 ([#924](https://github.com/monumental-archive/.github/pull/924))
- update aqua:golangci/golangci-lint to 2.13.1 ([#925](https://github.com/monumental-archive/.github/pull/925))
- update ruff to 0.16.4 ([#926](https://github.com/monumental-archive/.github/pull/926))
- update lefthook to 2.1.11 ([#927](https://github.com/monumental-archive/.github/pull/927))
- update github/codeql-action to v4.37.8 ([#928](https://github.com/monumental-archive/.github/pull/928))
- update monumental-archive/signer to 9c52c01 ([#929](https://github.com/monumental-archive/.github/pull/929))
- update rumdl to 0.2.60 ([#930](https://github.com/monumental-archive/.github/pull/930))
- update jdx/mise to 2026.8.11 ([#931](https://github.com/monumental-archive/.github/pull/931))
- lock file maintenance ([#932](https://github.com/monumental-archive/.github/pull/932))
- update jdx/mise to 2026.8.12 ([#933](https://github.com/monumental-archive/.github/pull/933))
- update postgres to 5d1d70e ([#935](https://github.com/monumental-archive/.github/pull/935))
- update postgres to 1c59e2c ([#934](https://github.com/monumental-archive/.github/pull/934))
- update postgres to 185c7c7 ([#937](https://github.com/monumental-archive/.github/pull/937))
- update postgres to 051f7b7 ([#936](https://github.com/monumental-archive/.github/pull/936))
- update postgres to bb3e1a5 ([#938](https://github.com/monumental-archive/.github/pull/938))
- update aqua:astral-sh/uv to 0.12.6 ([#939](https://github.com/monumental-archive/.github/pull/939))
- update postgres to 67f4172 ([#944](https://github.com/monumental-archive/.github/pull/944))
- update monumental-archive/signer to 8018458 ([#945](https://github.com/monumental-archive/.github/pull/945))
- update postgres to 4ef4dbc ([#943](https://github.com/monumental-archive/.github/pull/943))
- update postgres to 156f0b2 ([#946](https://github.com/monumental-archive/.github/pull/946))
- update postgres to f1c3376 ([#947](https://github.com/monumental-archive/.github/pull/947))
- update aqua:astral-sh/uv to 0.12.7 ([#951](https://github.com/monumental-archive/.github/pull/951))
- update postgres to 9b1d34a ([#954](https://github.com/monumental-archive/.github/pull/954))
- update aqua:golangci/golangci-lint to 2.13.2 ([#952](https://github.com/monumental-archive/.github/pull/952))
- update github/codeql-action to v4.37.9 ([#948](https://github.com/monumental-archive/.github/pull/948))
- update editorconfig-checker to 3.11.2 ([#940](https://github.com/monumental-archive/.github/pull/940))
- update jdx/mise to 2026.8.14 ([#941](https://github.com/monumental-archive/.github/pull/941))
- update lefthook to 2.1.12 ([#955](https://github.com/monumental-archive/.github/pull/955))
- update ruff to 0.16.5 ([#949](https://github.com/monumental-archive/.github/pull/949))
- update rumdl to 0.2.62 ([#950](https://github.com/monumental-archive/.github/pull/950))
- update jdx/mise-action to v4.3.0 ([#942](https://github.com/monumental-archive/.github/pull/942))
- update shfmt to 3.14.0 ([#956](https://github.com/monumental-archive/.github/pull/956))
- update typos to 1.49.1 ([#953](https://github.com/monumental-archive/.github/pull/953))
- update jdx/mise to 2026.8.15 ([#957](https://github.com/monumental-archive/.github/pull/957))
- update aqua:astral-sh/uv to 0.12.8 ([#959](https://github.com/monumental-archive/.github/pull/959))
- update jdx/mise to 2026.8.16 ([#960](https://github.com/monumental-archive/.github/pull/960))
- update jdx/mise to 2026.9.0 ([#961](https://github.com/monumental-archive/.github/pull/961))
- update typos to 1.50.1 ([#962](https://github.com/monumental-archive/.github/pull/962))
- update aqua:astral-sh/uv to 0.12.9 ([#963](https://github.com/monumental-archive/.github/pull/963))
- update jdx/mise to 2026.9.1 ([#964](https://github.com/monumental-archive/.github/pull/964))
- update rumdl to 0.2.63 ([#965](https://github.com/monumental-archive/.github/pull/965))
- update rumdl to 0.2.64 ([#966](https://github.com/monumental-archive/.github/pull/966))
- update editorconfig-checker to 3.11.3 ([#967](https://github.com/monumental-archive/.github/pull/967))
- update ruff to 0.16.6 ([#968](https://github.com/monumental-archive/.github/pull/968))
- update aqua:astral-sh/uv to 0.12.10 ([#970](https://github.com/monumental-archive/.github/pull/970))
- update rumdl to 0.2.65 ([#971](https://github.com/monumental-archive/.github/pull/971))
- update rumdl to 0.2.66 ([#972](https://github.com/monumental-archive/.github/pull/972))
- update aqua:taiki-e/cargo-llvm-cov to 0.9.1 ([#973](https://github.com/monumental-archive/.github/pull/973))
- update rumdl to 0.2.67 ([#975](https://github.com/monumental-archive/.github/pull/975))
- update shfmt to 3.14.1 ([#974](https://github.com/monumental-archive/.github/pull/974))
- lock file maintenance ([#976](https://github.com/monumental-archive/.github/pull/976))
- update rumdl to 0.2.68 ([#977](https://github.com/monumental-archive/.github/pull/977))
- update jdx/mise to 2026.9.2 ([#978](https://github.com/monumental-archive/.github/pull/978))
- update jdx/mise to 2026.9.3 ([#979](https://github.com/monumental-archive/.github/pull/979))
- update aqua:astral-sh/uv to 0.12.11 ([#980](https://github.com/monumental-archive/.github/pull/980))
- update rumdl to 0.2.69 ([#981](https://github.com/monumental-archive/.github/pull/981))
- update github/codeql-action to v4.38.0 ([#982](https://github.com/monumental-archive/.github/pull/982))
- update monumental-archive/signer to 324ef8c ([#983](https://github.com/monumental-archive/.github/pull/983))
- update aqua:astral-sh/uv to 0.12.12 ([#984](https://github.com/monumental-archive/.github/pull/984))
- update jdx/mise to 2026.9.4 ([#985](https://github.com/monumental-archive/.github/pull/985))
- update aqua:astral-sh/uv to 0.12.13 ([#986](https://github.com/monumental-archive/.github/pull/986))
- update jdx/mise to 2026.9.5 ([#988](https://github.com/monumental-archive/.github/pull/988))
- update ruff to 0.16.7 ([#987](https://github.com/monumental-archive/.github/pull/987))
- update rumdl to 0.2.71 ([#989](https://github.com/monumental-archive/.github/pull/989))
- update rumdl to 0.2.73 ([#990](https://github.com/monumental-archive/.github/pull/990))
- update jdx/mise to 2026.9.6 ([#992](https://github.com/monumental-archive/.github/pull/992))
- update jdx/mise to 2026.9.7 ([#993](https://github.com/monumental-archive/.github/pull/993))
- update aqua:google/osv-scanner to 2.6.0 ([#994](https://github.com/monumental-archive/.github/pull/994))
- update lefthook to 2.1.14 ([#995](https://github.com/monumental-archive/.github/pull/995))
- update jdx/mise to 2026.9.8 ([#996](https://github.com/monumental-archive/.github/pull/996))
- update codecov/codecov-action to v7.1.0 ([#998](https://github.com/monumental-archive/.github/pull/998))
- update aqua:astral-sh/uv to 0.12.14 ([#997](https://github.com/monumental-archive/.github/pull/997))
- update aqua:astral-sh/uv to 0.12.15 ([#1000](https://github.com/monumental-archive/.github/pull/1000))
- update jdx/mise to 2026.9.9 ([#999](https://github.com/monumental-archive/.github/pull/999))
- update jdx/mise to 2026.9.10 ([#1002](https://github.com/monumental-archive/.github/pull/1002))
- update codecov/codecov-action to v7.1.1 ([#1004](https://github.com/monumental-archive/.github/pull/1004))
- update aqua:astral-sh/uv to 0.12.16 ([#1005](https://github.com/monumental-archive/.github/pull/1005))
- update monumental-archive/signer to ac555a3 ([#1007](https://github.com/monumental-archive/.github/pull/1007))
- update github/codeql-action to v4.38.1 ([#1008](https://github.com/monumental-archive/.github/pull/1008))
- update aqua:astral-sh/uv to 0.12.17 ([#1009](https://github.com/monumental-archive/.github/pull/1009))
- update jdx/mise to 2026.9.11 ([#1006](https://github.com/monumental-archive/.github/pull/1006))
- update postgres to 539ceaa ([#1011](https://github.com/monumental-archive/.github/pull/1011))
- update postgres to 3725f4e ([#1013](https://github.com/monumental-archive/.github/pull/1013))
- update postgres to dcc2ca9 ([#1014](https://github.com/monumental-archive/.github/pull/1014))
- update postgres to 639ab7c ([#1012](https://github.com/monumental-archive/.github/pull/1012))
- update postgres to efedf35 ([#1015](https://github.com/monumental-archive/.github/pull/1015))
- update rumdl to 0.2.74 ([#1010](https://github.com/monumental-archive/.github/pull/1010))
- update ruff to 0.16.8 ([#1003](https://github.com/monumental-archive/.github/pull/1003))
- update jdx/mise to 2026.9.12 ([#1016](https://github.com/monumental-archive/.github/pull/1016))
- update typos to 1.50.2 ([#1001](https://github.com/monumental-archive/.github/pull/1001))
- update rumdl to 0.2.75 ([#1017](https://github.com/monumental-archive/.github/pull/1017))
- lock file maintenance ([#1018](https://github.com/monumental-archive/.github/pull/1018))
- update aqua:astral-sh/uv to 0.12.18 ([#1019](https://github.com/monumental-archive/.github/pull/1019))
- update rumdl to 0.2.76 ([#1020](https://github.com/monumental-archive/.github/pull/1020))
- update rumdl to 0.2.77 ([#1021](https://github.com/monumental-archive/.github/pull/1021))
- update monumental-archive/signer to b09e968 ([#1022](https://github.com/monumental-archive/.github/pull/1022))
- update postgres to 1a6ab3f ([#1024](https://github.com/monumental-archive/.github/pull/1024))
- update postgres to 5a5a84b ([#1023](https://github.com/monumental-archive/.github/pull/1023))
- update postgres to 724292d ([#1025](https://github.com/monumental-archive/.github/pull/1025))
- update postgres to d74eeac ([#1027](https://github.com/monumental-archive/.github/pull/1027))
- update postgres to c2427de ([#1026](https://github.com/monumental-archive/.github/pull/1026))
- update aqua:astral-sh/uv to 0.12.19 ([#1028](https://github.com/monumental-archive/.github/pull/1028))
- update github/codeql-action to v4.38.2 ([#1029](https://github.com/monumental-archive/.github/pull/1029))
- update jdx/mise to 2026.9.13 ([#1030](https://github.com/monumental-archive/.github/pull/1030))
- update ruff to 0.16.9 ([#1031](https://github.com/monumental-archive/.github/pull/1031))
- update typos to 1.50.3 ([#1033](https://github.com/monumental-archive/.github/pull/1033))
- update aqua:golangci/golangci-lint to 2.14.0 ([#1032](https://github.com/monumental-archive/.github/pull/1032))
- update jdx/mise to 2026.9.15 ([#1034](https://github.com/monumental-archive/.github/pull/1034))
- lock file maintenance ([#1035](https://github.com/monumental-archive/.github/pull/1035))
- update aqua:astral-sh/uv to 0.12.20 ([#1038](https://github.com/monumental-archive/.github/pull/1038))
- update aqua:astral-sh/uv to 0.12.21 ([#1039](https://github.com/monumental-archive/.github/pull/1039))
- update lefthook to 2.1.15 ([#1040](https://github.com/monumental-archive/.github/pull/1040))
- update rumdl to 0.2.78 ([#1041](https://github.com/monumental-archive/.github/pull/1041))
- update python to 3.14.8 ([#1042](https://github.com/monumental-archive/.github/pull/1042))
- update trivy to 0.75.0 ([#1043](https://github.com/monumental-archive/.github/pull/1043))
- update ruff to 0.16.10 ([#1045](https://github.com/monumental-archive/.github/pull/1045))
- update lefthook to 2.1.16 ([#1044](https://github.com/monumental-archive/.github/pull/1044))
- update aqua:astral-sh/uv to 0.12.22 ([#1046](https://github.com/monumental-archive/.github/pull/1046))
- update pipx:sqlfluff to 4.4.0 ([#1047](https://github.com/monumental-archive/.github/pull/1047))
- update aqua:astral-sh/uv to 0.12.23 ([#1048](https://github.com/monumental-archive/.github/pull/1048))
- update cargo-semver-checks to v0.51.0 ([#1049](https://github.com/monumental-archive/.github/pull/1049))
- lock file maintenance ([#1050](https://github.com/monumental-archive/.github/pull/1050))
- update lefthook to 2.1.17 ([#1051](https://github.com/monumental-archive/.github/pull/1051))
- update postgres to 5a05f61 ([#1053](https://github.com/monumental-archive/.github/pull/1053))
- update postgres to 0ea6700 ([#1052](https://github.com/monumental-archive/.github/pull/1052))
- update postgres to 3645570 ([#1054](https://github.com/monumental-archive/.github/pull/1054))
- update postgres to afc7e2d ([#1055](https://github.com/monumental-archive/.github/pull/1055))
- update postgres to d4a8e1f ([#1056](https://github.com/monumental-archive/.github/pull/1056))
- update typos to 1.51.0 ([#1057](https://github.com/monumental-archive/.github/pull/1057))
- update monumental-archive/signer to 62c42e8 ([#1058](https://github.com/monumental-archive/.github/pull/1058))
- update actions/download-artifact to v8.0.2 ([#1059](https://github.com/monumental-archive/.github/pull/1059))
- update postgres to 2d2b899 ([#1060](https://github.com/monumental-archive/.github/pull/1060))
- update postgres to 74935e7 ([#1062](https://github.com/monumental-archive/.github/pull/1062))
- update postgres to 14bfab5 ([#1061](https://github.com/monumental-archive/.github/pull/1061))
- update postgres to c961aa2 ([#1063](https://github.com/monumental-archive/.github/pull/1063))
- update postgres to ca0bd48 ([#1064](https://github.com/monumental-archive/.github/pull/1064))
- update github/codeql-action to v4.38.3 ([#1066](https://github.com/monumental-archive/.github/pull/1066))
- update actions/upload-artifact to v7.0.2 ([#1065](https://github.com/monumental-archive/.github/pull/1065))
- update rumdl to 0.2.79 ([#1067](https://github.com/monumental-archive/.github/pull/1067))
- update aqua:astral-sh/uv to 0.13.0 ([#1068](https://github.com/monumental-archive/.github/pull/1068))
- update lefthook to 2.2.1 ([#1069](https://github.com/monumental-archive/.github/pull/1069))
- update ruff to 0.17.0 ([#1071](https://github.com/monumental-archive/.github/pull/1071))
- update python to 3.15.0 ([#1070](https://github.com/monumental-archive/.github/pull/1070))
- update aqua:google/osv-scanner to 2.7.0 ([#1073](https://github.com/monumental-archive/.github/pull/1073))
- update monumental-archive/signer to 639cefb ([#1075](https://github.com/monumental-archive/.github/pull/1075))
- update create-github-app-token to v3.3.0 ([#1077](https://github.com/monumental-archive/.github/pull/1077))
- update lefthook to 2.2.2 ([#1076](https://github.com/monumental-archive/.github/pull/1076))
- update ossf/scorecard-action to v2.5.0 ([#1074](https://github.com/monumental-archive/.github/pull/1074))
- update jdx/mise to 2026.9.16 ([#1036](https://github.com/monumental-archive/.github/pull/1036))
- lock file maintenance ([#1072](https://github.com/monumental-archive/.github/pull/1072))
- update aqua:astral-sh/uv to 0.13.1 ([#1079](https://github.com/monumental-archive/.github/pull/1079))
- update trivy to 0.76.0 ([#1081](https://github.com/monumental-archive/.github/pull/1081))
- update jdx/mise to 2026.10.10 ([#1080](https://github.com/monumental-archive/.github/pull/1080))
- update monumental-archive/signer to aa070b2 ([#1082](https://github.com/monumental-archive/.github/pull/1082))
- update jdx/mise to 2026.10.11 ([#1083](https://github.com/monumental-archive/.github/pull/1083))
- update ruff to 0.17.1 ([#1084](https://github.com/monumental-archive/.github/pull/1084))
- update github/codeql-action to v4.39.0 ([#1085](https://github.com/monumental-archive/.github/pull/1085))
- update aqua:golangci/golangci-lint to 2.15.0 ([#1086](https://github.com/monumental-archive/.github/pull/1086))
- update jdx/mise to 2026.10.12 ([#1087](https://github.com/monumental-archive/.github/pull/1087))
- update rumdl to 0.3.0 ([#1088](https://github.com/monumental-archive/.github/pull/1088))
- update jdx/mise to 2026.10.13 ([#1089](https://github.com/monumental-archive/.github/pull/1089))
- update jdx/mise to 2026.10.14 ([#1090](https://github.com/monumental-archive/.github/pull/1090))
- lock file maintenance ([#1091](https://github.com/monumental-archive/.github/pull/1091))
- update jdx/mise to 2026.10.15 ([#1092](https://github.com/monumental-archive/.github/pull/1092))
- update aqua:astral-sh/uv to 0.13.2 ([#1093](https://github.com/monumental-archive/.github/pull/1093))
- update aqua:astral-sh/uv to 0.13.3 ([#1094](https://github.com/monumental-archive/.github/pull/1094))
- update aqua:astral-sh/uv to 0.13.4 ([#1095](https://github.com/monumental-archive/.github/pull/1095))
- update aqua:sigstore/cosign to 3.1.4 ([#1096](https://github.com/monumental-archive/.github/pull/1096))
- update aqua:sigstore/gitsign to 0.18.0 ([#1097](https://github.com/monumental-archive/.github/pull/1097))
- update jdx/mise to 2026.10.17 ([#1098](https://github.com/monumental-archive/.github/pull/1098))
- update python to 3.15.1 ([#1099](https://github.com/monumental-archive/.github/pull/1099))
- update github/codeql-action to v4.39.1 ([#1101](https://github.com/monumental-archive/.github/pull/1101))
- update monumental-archive/signer to deecf02 ([#1102](https://github.com/monumental-archive/.github/pull/1102))
- update trivy to 0.76.1 ([#1100](https://github.com/monumental-archive/.github/pull/1100))
- update aqua:astral-sh/uv to 0.13.5 ([#1103](https://github.com/monumental-archive/.github/pull/1103))
- update ruff to 0.17.2 ([#1104](https://github.com/monumental-archive/.github/pull/1104))
- update jdx/mise to 2026.10.18 ([#1105](https://github.com/monumental-archive/.github/pull/1105))
- update jdx/mise to 2026.10.19 ([#1106](https://github.com/monumental-archive/.github/pull/1106))
- update monumental-archive/signer to da6af99 ([#1108](https://github.com/monumental-archive/.github/pull/1108))
- let actionlint pass the ubuntu-26.04 runner labels ([#1111](https://github.com/monumental-archive/.github/pull/1111))
- update aqua:astral-sh/uv to 0.13.7 ([#1109](https://github.com/monumental-archive/.github/pull/1109))
- move to editorconfig-checker 4 and its new name ([#1110](https://github.com/monumental-archive/.github/pull/1110))
- update github/codeql-action to v4.39.3 ([#1114](https://github.com/monumental-archive/.github/pull/1114))
- update committed to 1.1.12 ([#1113](https://github.com/monumental-archive/.github/pull/1113))
- spell pinact's long flags with two dashes ([#1107](https://github.com/monumental-archive/.github/pull/1107))
- give the gate job a thirty-minute timeout ([#1112](https://github.com/monumental-archive/.github/pull/1112))
- update ubuntu to 26.04 ([#1078](https://github.com/monumental-archive/.github/pull/1078))
- update jdx/mise-action to v5.2.6 ([#1037](https://github.com/monumental-archive/.github/pull/1037))
- move to zizmor 1.32 and keep local workflow calls ([#1115](https://github.com/monumental-archive/.github/pull/1115))
- update python to 3.15.2 ([#1118](https://github.com/monumental-archive/.github/pull/1118))
- update ruff to 0.17.4 ([#1119](https://github.com/monumental-archive/.github/pull/1119))
- update rumdl to 0.3.2 ([#1121](https://github.com/monumental-archive/.github/pull/1121))
- update trivy to 0.76.2 ([#1122](https://github.com/monumental-archive/.github/pull/1122))
- update jdx/mise to 2026.11.3 ([#1123](https://github.com/monumental-archive/.github/pull/1123))
- update pipx:reuse to 6.3.0 ([#1124](https://github.com/monumental-archive/.github/pull/1124))
- update pipx:sqlfluff to 4.5.0 ([#1125](https://github.com/monumental-archive/.github/pull/1125))
- update typos to 1.52.0 ([#1126](https://github.com/monumental-archive/.github/pull/1126))
- update jdx/mise-action to v5.2.7 ([#1120](https://github.com/monumental-archive/.github/pull/1120))
- judge only the subject of a queued squash commit ([#1128](https://github.com/monumental-archive/.github/pull/1128))
- update aqua:suzuki-shunsuke/pinact to 5.0.2 ([#991](https://github.com/monumental-archive/.github/pull/991))

## [1.61.1](https://github.com/monumental-archive/.github/compare/v1.61.0...v1.61.1) - 2026-08-24

### Fixed

- let fix:rust write the tree it formatted (#892) ([#906](https://github.com/monumental-archive/.github/pull/906))
- lock file maintenance ([#909](https://github.com/monumental-archive/.github/pull/909))
- name the org in every registry curl (#907) ([#910](https://github.com/monumental-archive/.github/pull/910))
- let derive-badges say what it found (#905) ([#913](https://github.com/monumental-archive/.github/pull/913))
- let citation.sh say which field is missing (#911) ([#915](https://github.com/monumental-archive/.github/pull/915))
- say why npm could not use trusted publishing (#907) ([#914](https://github.com/monumental-archive/.github/pull/914))

## [1.61.0](https://github.com/monumental-archive/.github/compare/v1.60.0...v1.61.0) - 2026-08-24

### Added

- add the wasm-pack arch correction and audit (#811) ([#870](https://github.com/monumental-archive/.github/pull/870))
- record and guard the org-wide grant model (#751) ([#853](https://github.com/monumental-archive/.github/pull/853))
- run the scaffold as a repository in the gate (#777) ([#877](https://github.com/monumental-archive/.github/pull/877))
- report classic branch protection as drift (#761) ([#874](https://github.com/monumental-archive/.github/pull/874))
- select the delivered aqua registry (#811) ([#882](https://github.com/monumental-archive/.github/pull/882))
- stage the committed arch correction (#854) ([#885](https://github.com/monumental-archive/.github/pull/885))
- correct the committed architecture (#854) ([#898](https://github.com/monumental-archive/.github/pull/898))
- refuse a [workspace.package] key nobody inherits (#863) ([#890](https://github.com/monumental-archive/.github/pull/890))
- report a repo's own rulesets as drift (#876) ([#897](https://github.com/monumental-archive/.github/pull/897))
- move to stele v0.20.0 and jsonx epoch 7 ([#901](https://github.com/monumental-archive/.github/pull/901))
- compile the declared MSRV at its pinned rustc (#820) ([#865](https://github.com/monumental-archive/.github/pull/865))
- compile the pgrx crate the gate never saw (#813) ([#900](https://github.com/monumental-archive/.github/pull/900))

### Fixed

- guard canon-only legs on identity, red on inputs (#738) ([#855](https://github.com/monumental-archive/.github/pull/855))
- scrub git's environment from the pgrx harness (#857) ([#869](https://github.com/monumental-archive/.github/pull/869))
- read installability from the release path's record ([#871](https://github.com/monumental-archive/.github/pull/871))
- let the canon track its own delivered rust configs ([#856](https://github.com/monumental-archive/.github/pull/856))
- guard canon-only lints on identity and file shape (#858) ([#879](https://github.com/monumental-archive/.github/pull/879))
- resolve a repo's own top-level message fields (#725) ([#873](https://github.com/monumental-archive/.github/pull/873))
- catch a gate measuring another belt (#867) ([#878](https://github.com/monumental-archive/.github/pull/878))
- declare the contents grant the tag job spends ([#883](https://github.com/monumental-archive/.github/pull/883))
- update monumental-archive/signer to 32fa844 ([#888](https://github.com/monumental-archive/.github/pull/888))
- refuse a nested mise run inside a task body (#764) ([#866](https://github.com/monumental-archive/.github/pull/866))
- resume from HEAD's tag, not from the plan's (#864) ([#889](https://github.com/monumental-archive/.github/pull/889))
- key the evidence by the digests consumers pull ([#887](https://github.com/monumental-archive/.github/pull/887))
- name the endpoint and grant on a failed read (#862) ([#895](https://github.com/monumental-archive/.github/pull/895))
- guard every fixer on what it rewrites (#860) ([#893](https://github.com/monumental-archive/.github/pull/893))
- hold the squash body to the commit canon (#880) ([#886](https://github.com/monumental-archive/.github/pull/886))
- map each ts extension to the js one tsc resolves (#868) ([#894](https://github.com/monumental-archive/.github/pull/894))
- lock file maintenance ([#902](https://github.com/monumental-archive/.github/pull/902))
- relock five tools onto their musl assets (#899) ([#903](https://github.com/monumental-archive/.github/pull/903))

### Testing

- table-test the release scripts' decisions (#772) ([#881](https://github.com/monumental-archive/.github/pull/881))

## [1.60.0](https://github.com/monumental-archive/.github/compare/v1.59.0...v1.60.0) - 2026-08-24

### Added

- split the crate-built image into its own evidence class ([#844](https://github.com/monumental-archive/.github/pull/844))
- require a root changelog in versioned repos (#742) ([#847](https://github.com/monumental-archive/.github/pull/847))
- emit the rust-crate plan and lint the join (#833) ([#861](https://github.com/monumental-archive/.github/pull/861))

### Fixed

- update first-party canon ([#835](https://github.com/monumental-archive/.github/pull/835))
- update postgres to 06cad38 ([#837](https://github.com/monumental-archive/.github/pull/837))
- update postgres to e384114 ([#838](https://github.com/monumental-archive/.github/pull/838))
- update postgres to 5f72c7b ([#839](https://github.com/monumental-archive/.github/pull/839))
- update postgres to e17e860 ([#840](https://github.com/monumental-archive/.github/pull/840))
- update postgres to 2fdfb9b ([#841](https://github.com/monumental-archive/.github/pull/841))
- update aqua:taiki-e/cargo-llvm-cov to 0.9.0 ([#842](https://github.com/monumental-archive/.github/pull/842))
- draw the org-wide walks from AUDIT_TOKEN (#749) ([#845](https://github.com/monumental-archive/.github/pull/845))
- count the clippy exclusions the run applied ([#846](https://github.com/monumental-archive/.github/pull/846))
- refuse a PR title ending in its own reference (#800) ([#848](https://github.com/monumental-archive/.github/pull/848))
- drop the GITHUB_TOKEN no release step reads (#741) ([#849](https://github.com/monumental-archive/.github/pull/849))
- catch the setup-* family's silent cache (#732) ([#851](https://github.com/monumental-archive/.github/pull/851))
- read every matcher a packageRule carries (#724) ([#852](https://github.com/monumental-archive/.github/pull/852))

## [1.59.0](https://github.com/monumental-archive/.github/compare/v1.58.11...v1.59.0) - 2026-08-21

### Added

- accept a discretionary atlas-token secret ([#831](https://github.com/monumental-archive/.github/pull/831))

## [1.58.11](https://github.com/monumental-archive/.github/compare/v1.58.10...v1.58.11) - 2026-08-21

### Fixed

- mint canon bumps under the topic canon ([#827](https://github.com/monumental-archive/.github/pull/827))

## [1.58.10](https://github.com/monumental-archive/.github/compare/v1.58.9...v1.58.10) - 2026-08-21

### Fixed

- judge pg-upgrade-path from installable predecessors ([#822](https://github.com/monumental-archive/.github/pull/822))

## [1.58.9](https://github.com/monumental-archive/.github/compare/v1.58.8...v1.58.9) - 2026-08-21

### Fixed

- derive the predecessor from what is installable ([#818](https://github.com/monumental-archive/.github/pull/818))

### Documentation

- name the image subject in the stranger recipe ([#817](https://github.com/monumental-archive/.github/pull/817))

## [1.58.8](https://github.com/monumental-archive/.github/compare/v1.58.7...v1.58.8) - 2026-08-21

### Fixed

- stop judging describe callbacks by their case count ([#814](https://github.com/monumental-archive/.github/pull/814))

## [1.58.7](https://github.com/monumental-archive/.github/compare/v1.58.6...v1.58.7) - 2026-08-21

### Fixed

- update aqua:astral-sh/uv to 0.12.5 ([#807](https://github.com/monumental-archive/.github/pull/807))

## [1.58.6](https://github.com/monumental-archive/.github/compare/v1.58.5...v1.58.6) - 2026-08-21

### Fixed

- update first-party canon ([#806](https://github.com/monumental-archive/.github/pull/806))

## [1.58.5](https://github.com/monumental-archive/.github/compare/v1.58.4...v1.58.5) - 2026-08-21

### Fixed

- shorten the psql guard, and keep it on one line ([#801](https://github.com/monumental-archive/.github/pull/801))

## [1.58.4](https://github.com/monumental-archive/.github/compare/v1.58.3...v1.58.4) - 2026-08-21

### Fixed

- turn noConditionalExpect off, all 8 were false (#788) (#797) ([#797](https://github.com/monumental-archive/.github/pull/797))

## [1.58.3](https://github.com/monumental-archive/.github/compare/v1.58.2...v1.58.3) - 2026-08-21

### Fixed

- exempt test files from two literal rules (#783) (#793) ([#793](https://github.com/monumental-archive/.github/pull/793))

## [1.58.2](https://github.com/monumental-archive/.github/compare/v1.58.1...v1.58.2) - 2026-08-21

### Fixed

- keep the generated psql guard inside 80 columns ([#794](https://github.com/monumental-archive/.github/pull/794))

## [1.58.1](https://github.com/monumental-archive/.github/compare/v1.58.0...v1.58.1) - 2026-08-21

### Fixed

- pin the LTO mode, the fourth determinism input (#778) ([#789](https://github.com/monumental-archive/.github/pull/789))

## [1.58.0](https://github.com/monumental-archive/.github/compare/v1.57.3...v1.58.0) - 2026-08-21

### Added

- declare the oci-image class's binary (#775) ([#785](https://github.com/monumental-archive/.github/pull/785))

## [1.57.3](https://github.com/monumental-archive/.github/compare/v1.57.2...v1.57.3) - 2026-08-21

### Fixed

- derive where the previous package tree is, once ([#781](https://github.com/monumental-archive/.github/pull/781))

## [1.57.2](https://github.com/monumental-archive/.github/compare/v1.57.1...v1.57.2) - 2026-08-21

### Fixed

- rule the two rules a database boundary breaks (#767) ([#774](https://github.com/monumental-archive/.github/pull/774))

## [1.57.1](https://github.com/monumental-archive/.github/compare/v1.57.0...v1.57.1) - 2026-08-21

### Fixed

- stop two biome safe fixes from breaking code (#758) ([#770](https://github.com/monumental-archive/.github/pull/770))

## [1.57.0](https://github.com/monumental-archive/.github/compare/v1.56.0...v1.57.0) - 2026-08-21

### Added

- give continuous a dependency commit point (#717) ([#765](https://github.com/monumental-archive/.github/pull/765))

### Fixed

- derive the pgrx upgrade baseline from the extension ([#766](https://github.com/monumental-archive/.github/pull/766))
- turn useLiteralKeys off, it contradicts a tsc dial (#759) ([#763](https://github.com/monumental-archive/.github/pull/763))

## [1.56.0](https://github.com/monumental-archive/.github/compare/v1.55.1...v1.56.0) - 2026-08-21

### Added

- verify a caller's first-party FROM before the build (#715) ([#755](https://github.com/monumental-archive/.github/pull/755))

### Fixed

- deliver the org shellcheck config (#696) ([#733](https://github.com/monumental-archive/.github/pull/733))

## [1.55.1](https://github.com/monumental-archive/.github/compare/v1.55.0...v1.55.1) - 2026-08-21

### Fixed

- apply a repo's domains to biome's nursery rules (#720) ([#744](https://github.com/monumental-archive/.github/pull/744))
- lint the whole workspace, crate by crate (#726) ([#750](https://github.com/monumental-archive/.github/pull/750))
- feed the badge the profile the gate measures ([#753](https://github.com/monumental-archive/.github/pull/753))

## [1.55.0](https://github.com/monumental-archive/.github/compare/v1.54.0...v1.55.0) - 2026-08-21

### Added

- collect a repo's own audit:* tasks (#704) ([#736](https://github.com/monumental-archive/.github/pull/736))
- declare edtf in the population, epoch and floors ([#739](https://github.com/monumental-archive/.github/pull/739))

### Fixed

- model the advisory subject in the subject budget ([#735](https://github.com/monumental-archive/.github/pull/735))
- authenticate the org walks and name the unreadable (#692) ([#730](https://github.com/monumental-archive/.github/pull/730))
- anchor the unattested-path marker at its cache step ([#729](https://github.com/monumental-archive/.github/pull/729))
- deliver the org TOML format (#691) ([#745](https://github.com/monumental-archive/.github/pull/745))
- retire the head -1 idiom in the thirteen sites ([#747](https://github.com/monumental-archive/.github/pull/747))
- derive adoption from the roster, not the listing (#693) ([#748](https://github.com/monumental-archive/.github/pull/748))

### Documentation

- name the delivered configs, not stubs that never existed (#689) ([#737](https://github.com/monumental-archive/.github/pull/737))
- record sqlfluff's two escapes and the noqa trap (#708) ([#740](https://github.com/monumental-archive/.github/pull/740))

## [1.54.0](https://github.com/monumental-archive/.github/compare/v1.53.1...v1.54.0) - 2026-08-21

### Added

- let a repo declare its biome domains (#695) ([#719](https://github.com/monumental-archive/.github/pull/719))
- check links and workflows in every repo (#681) ([#727](https://github.com/monumental-archive/.github/pull/727))

### Fixed

- lint:yaml is for YAML a human wrote (#718) ([#721](https://github.com/monumental-archive/.github/pull/721))
- fold repo packageRules into the subject budget ([#722](https://github.com/monumental-archive/.github/pull/722))
- require the org's dials in a repo's tsconfig (#699) ([#731](https://github.com/monumental-archive/.github/pull/731))

## [1.53.1](https://github.com/monumental-archive/.github/compare/v1.53.0...v1.53.1) - 2026-08-21

### Fixed

- reach repo tasks with the strict task shell ([#710](https://github.com/monumental-archive/.github/pull/710))

## [1.53.0](https://github.com/monumental-archive/.github/compare/v1.52.0...v1.53.0) - 2026-08-21

### Added

- derive the coverage floor and only ever raise it ([#706](https://github.com/monumental-archive/.github/pull/706))

### Fixed

- declare iiif-server, raise population guards to nine ([#705](https://github.com/monumental-archive/.github/pull/705))

## [1.52.0](https://github.com/monumental-archive/.github/compare/v1.51.1...v1.52.0) - 2026-08-21

### Added

- exclude the tenth restriction contradiction ([#701](https://github.com/monumental-archive/.github/pull/701))

### Fixed

- read first lines with sed -n 1p, not head -1 ([#679](https://github.com/monumental-archive/.github/pull/679))
- declare the shipped set's security cadence ([#685](https://github.com/monumental-archive/.github/pull/685))

## [1.51.1](https://github.com/monumental-archive/.github/compare/v1.51.0...v1.51.1) - 2026-08-21

### Fixed

- mint fix for the canon's shipped managers ([#675](https://github.com/monumental-archive/.github/pull/675))

### CI

- warm the unsigned gate's toolchain cache ([#674](https://github.com/monumental-archive/.github/pull/674))

### Dependencies

- update jdx/mise to 2026.8.10 ([#663](https://github.com/monumental-archive/.github/pull/663))
- update dependency ruff to v0.16.3 ([#603](https://github.com/monumental-archive/.github/pull/603))
- update trivy to 0.74.0 ([#664](https://github.com/monumental-archive/.github/pull/664))
- lock file maintenance ([#665](https://github.com/monumental-archive/.github/pull/665))

## [1.51.0](https://github.com/monumental-archive/.github/compare/v1.50.0...v1.51.0) - 2026-08-20

### Added

- audit:go-vulns joins through stele ([#647](https://github.com/monumental-archive/.github/pull/647))

### Fixed

- judge the targets a rebuild declared, not the class ([#653](https://github.com/monumental-archive/.github/pull/653))

## [1.50.0](https://github.com/monumental-archive/.github/compare/v1.49.0...v1.50.0) - 2026-08-20

### Added

- lint the shell inside mise task bodies ([#646](https://github.com/monumental-archive/.github/pull/646))

### Fixed

- refuse to rewrite a body a list `run` shares ([#651](https://github.com/monumental-archive/.github/pull/651))

## [1.49.0](https://github.com/monumental-archive/.github/compare/v1.48.0...v1.49.0) - 2026-08-20

### Added

- load every committed policy against the pinned engine ([#643](https://github.com/monumental-archive/.github/pull/643))

### Fixed

- judge the class under rebuild, not the whole release ([#638](https://github.com/monumental-archive/.github/pull/638))

### Documentation

- describe slsa shields as measured levels ([#634](https://github.com/monumental-archive/.github/pull/634))
- make a delivered config's deletion ride the pin bump ([#636](https://github.com/monumental-archive/.github/pull/636))

### Dependencies

- update aqua:astral-sh/uv to 0.12.4 ([#642](https://github.com/monumental-archive/.github/pull/642))

## [1.48.0](https://github.com/monumental-archive/.github/compare/v1.47.0...v1.48.0) - 2026-08-20

### Added

- point every badge at the branch its own repo publishes ([#629](https://github.com/monumental-archive/.github/pull/629))

## [1.47.0](https://github.com/monumental-archive/.github/compare/v1.46.0...v1.47.0) - 2026-08-20

### Added

- make a leftover draft the record of its own burn ([#600](https://github.com/monumental-archive/.github/pull/600))
- deliver the commit canon and budget what it mints ([#608](https://github.com/monumental-archive/.github/pull/608))
- move org identity out of the belt's text into data ([#614](https://github.com/monumental-archive/.github/pull/614))
- give the go advisory leg a written-decision exit ([#623](https://github.com/monumental-archive/.github/pull/623))
- cross to stele schema 6 with the manifest class split ([#626](https://github.com/monumental-archive/.github/pull/626))
- let each repository publish its own measured levels ([#625](https://github.com/monumental-archive/.github/pull/625))

### Fixed

- recover every decision the store does not hold ([#607](https://github.com/monumental-archive/.github/pull/607))
- prove draft visibility by making a draft ([#609](https://github.com/monumental-archive/.github/pull/609))
- match the citation form, not every semver in docs ([#612](https://github.com/monumental-archive/.github/pull/612))
- alias stele's nine over-budget go modules ([#616](https://github.com/monumental-archive/.github/pull/616))
- give golangci-lint a per-checkout cache ([#621](https://github.com/monumental-archive/.github/pull/621))
- mint fix(deps) for cargo and npm bumps ([#622](https://github.com/monumental-archive/.github/pull/622))

### Documentation

- give the runbook the vex consumer recipe ([#613](https://github.com/monumental-archive/.github/pull/613))

## [1.46.0](https://github.com/monumental-archive/.github/compare/v1.45.2...v1.46.0) - 2026-08-20

### Added

- mint tags that carry their own Rekor receipt ([#593](https://github.com/monumental-archive/.github/pull/593))

## [1.45.2](https://github.com/monumental-archive/.github/compare/v1.45.1...v1.45.2) - 2026-08-20

### Fixed

- check out before the artifacts land, not after ([#590](https://github.com/monumental-archive/.github/pull/590))

## [1.45.1](https://github.com/monumental-archive/.github/compare/v1.45.0...v1.45.1) - 2026-08-20

### Fixed

- write the manifest where every asset exists ([#588](https://github.com/monumental-archive/.github/pull/588))

## [1.45.0](https://github.com/monumental-archive/.github/compare/v1.44.3...v1.45.0) - 2026-08-20

### Added

- declare the population, the floor and the join ([#577](https://github.com/monumental-archive/.github/pull/577))
- move the permissions join into the engine ([#580](https://github.com/monumental-archive/.github/pull/580))
- derive the affected releases in the engine ([#582](https://github.com/monumental-archive/.github/pull/582))
- publish the board from the engine ([#584](https://github.com/monumental-archive/.github/pull/584))
- bear the release decision per planned inventory ([#585](https://github.com/monumental-archive/.github/pull/585))
- execute the plan the engine emits ([#587](https://github.com/monumental-archive/.github/pull/587))

### Fixed

- let the belt-dir template resolve empty instead of crashing ([#570](https://github.com/monumental-archive/.github/pull/570))
- pass the released manifest whole ([#581](https://github.com/monumental-archive/.github/pull/581))
- stamp the manifest's entries and iterate the judged plans ([#583](https://github.com/monumental-archive/.github/pull/583))
- converge a resume on the build subjects ([#586](https://github.com/monumental-archive/.github/pull/586))

### Documentation

- add the mechanism-change issue template ([#563](https://github.com/monumental-archive/.github/pull/563))
- ask for dependencies and mechanics in the mechanism template ([#566](https://github.com/monumental-archive/.github/pull/566))
- name the mechanism template in the repo instructions ([#573](https://github.com/monumental-archive/.github/pull/573))

### Dependencies

- update dependency rumdl to v0.2.55 ([#567](https://github.com/monumental-archive/.github/pull/567))
- update jdx/mise-action action to v4.2.5 ([#575](https://github.com/monumental-archive/.github/pull/575))
- update github/codeql-action action to v4.37.7 ([#578](https://github.com/monumental-archive/.github/pull/578))

## [1.44.3](https://github.com/monumental-archive/.github/compare/v1.44.2...v1.44.3) - 2026-08-19

### Fixed

- install the belt where the image facts are asserted ([#561](https://github.com/monumental-archive/.github/pull/561))

## [1.44.2](https://github.com/monumental-archive/.github/compare/v1.44.1...v1.44.2) - 2026-08-19

### Fixed

- treat a track a repo does not do as absent, not broken ([#555](https://github.com/monumental-archive/.github/pull/555))
- compare only the artifacts a rebuild can produce ([#559](https://github.com/monumental-archive/.github/pull/559))

## [1.44.1](https://github.com/monumental-archive/.github/compare/v1.44.0...v1.44.1) - 2026-08-19

### Fixed

- grant the verdict job the read it now needs ([#551](https://github.com/monumental-archive/.github/pull/551))

## [1.44.0](https://github.com/monumental-archive/.github/compare/v1.43.0...v1.44.0) - 2026-08-19

### Added

- hand the last audits and guards to stele ([#547](https://github.com/monumental-archive/.github/pull/547))

## [1.43.0](https://github.com/monumental-archive/.github/compare/v1.42.0...v1.43.0) - 2026-08-19

### Added

- build legs emit the inventory plans their artifacts owe ([#541](https://github.com/monumental-archive/.github/pull/541))

### Dependencies

- update dependency jdx/mise to v2026.8.5 ([#538](https://github.com/monumental-archive/.github/pull/538))

## [1.42.0](https://github.com/monumental-archive/.github/compare/v1.41.0...v1.42.0) - 2026-08-19

### Added

- adopt stele 0.14.0, wire pgrx and own the obligations ([#535](https://github.com/monumental-archive/.github/pull/535))

## [1.41.0](https://github.com/monumental-archive/.github/compare/v1.40.0...v1.41.0) - 2026-08-19

### Added

- cut derive over to stele and adopt the declared contracts ([#529](https://github.com/monumental-archive/.github/pull/529))

## [1.40.0](https://github.com/monumental-archive/.github/compare/v1.39.0...v1.40.0) - 2026-08-18

### Added

- warm go and cargo caches on the unsigned gate ([#525](https://github.com/monumental-archive/.github/pull/525))

### Fixed

- exempt the merged release commit from the mirror gate ([#523](https://github.com/monumental-archive/.github/pull/523))

## [1.39.0](https://github.com/monumental-archive/.github/compare/v1.38.0...v1.39.0) - 2026-08-18

### Added

- version-mirror drift gate via stele derive bump --check ([#521](https://github.com/monumental-archive/.github/pull/521))

## [1.38.0](https://github.com/monumental-archive/.github/compare/v1.37.0...v1.38.0) - 2026-08-18

### Added

- version mirrors move by stele derive bump, not seds ([#520](https://github.com/monumental-archive/.github/pull/520))

### Fixed

- prove the versioned archetype by tags, not by cliff.toml ([#510](https://github.com/monumental-archive/.github/pull/510))
- policy schema 2 and the /v3 source-provenance URI ([#518](https://github.com/monumental-archive/.github/pull/518))
- pin stele 0.11.0 and move both policies to schema 3 ([#519](https://github.com/monumental-archive/.github/pull/519))

### Documentation

- name stele derive where git-cliff was still claimed ([#513](https://github.com/monumental-archive/.github/pull/513))
- hand the chain format spec to stele and correct the recipes ([#515](https://github.com/monumental-archive/.github/pull/515))

## [1.37.0](https://github.com/monumental-archive/.github/compare/v1.36.0...v1.37.0) - 2026-08-18

### Added

- adopt stele 0.8.0 — neutral policy names and the tag audit ([#504](https://github.com/monumental-archive/.github/pull/504))
- derive versions and notes with stele, retire git-cliff ([#507](https://github.com/monumental-archive/.github/pull/507))

### Fixed

- drop the identity export the guard removal orphaned ([#502](https://github.com/monumental-archive/.github/pull/502))

### Dependencies

- update dependency rumdl to v0.2.54 ([#498](https://github.com/monumental-archive/.github/pull/498))

## [1.36.0](https://github.com/monumental-archive/.github/compare/v1.35.1...v1.36.0) - 2026-08-18

### Added

- cut the evidence audits over to stele assert ([#495](https://github.com/monumental-archive/.github/pull/495))
- retire preflight.sh and the yaml identity guard ([#499](https://github.com/monumental-archive/.github/pull/499))

### Dependencies

- update dependency jdx/mise to v2026.8.4 ([#497](https://github.com/monumental-archive/.github/pull/497))

### Fixed

- converge on the published record, never on curl's exit code ([#501](https://github.com/monumental-archive/.github/pull/501))

## [1.35.1](https://github.com/monumental-archive/.github/compare/v1.35.0...v1.35.1) - 2026-08-17

### Fixed

- scan the tree unless the binaries are the release ([#489](https://github.com/monumental-archive/.github/pull/489))

## [1.35.0](https://github.com/monumental-archive/.github/compare/v1.34.1...v1.35.0) - 2026-08-17

### Added

- derive the go-binary sbom from the shipped binaries ([#486](https://github.com/monumental-archive/.github/pull/486))

## [1.34.1](https://github.com/monumental-archive/.github/compare/v1.34.0...v1.34.1) - 2026-08-17

### Fixed

- let the verdict step resolve the stele shim ([#482](https://github.com/monumental-archive/.github/pull/482))

## [1.34.0](https://github.com/monumental-archive/.github/compare/v1.33.2...v1.34.0) - 2026-08-17

### Added

- pin stele as a belt tool, retiring the go run bootstrap ([#478](https://github.com/monumental-archive/.github/pull/478))

### Fixed

- install stele where the callers actually need it ([#481](https://github.com/monumental-archive/.github/pull/481))

## [1.33.2](https://github.com/monumental-archive/.github/compare/v1.33.1...v1.33.2) - 2026-08-17

### Fixed

- stamp the Go root module's version into the SBOM ([#476](https://github.com/monumental-archive/.github/pull/476))

## [1.33.1](https://github.com/monumental-archive/.github/compare/v1.33.0...v1.33.1) - 2026-08-17

### Fixed

- coverage measures every tracked language, not the first one ([#471](https://github.com/monumental-archive/.github/pull/471))

## [1.33.0](https://github.com/monumental-archive/.github/compare/v1.32.1...v1.33.0) - 2026-08-17

### Added

- adopt tsc as the type checking layer (#445) ([#465](https://github.com/monumental-archive/.github/pull/465))
- wire go mod tidy and go fuzzing into the belt (#445) ([#467](https://github.com/monumental-archive/.github/pull/467))
- stand up the go-binary class, repro-gated (stele#7) ([#469](https://github.com/monumental-archive/.github/pull/469))

### Dependencies

- update dependency rumdl to v0.2.53 ([#470](https://github.com/monumental-archive/.github/pull/470))

### Documentation

- stop sending readers to scaffold files that moved ([#460](https://github.com/monumental-archive/.github/pull/460))
- tell a Go repo what it gets and what it must write (#445) ([#468](https://github.com/monumental-archive/.github/pull/468))

### Fixed

- run every biome rule, fail on any severity (#445) ([#463](https://github.com/monumental-archive/.github/pull/463))

## [1.32.1](https://github.com/monumental-archive/.github/compare/v1.32.0...v1.32.1) - 2026-08-16

### Fixed

- read a rust pin that is a list of toolchains ([#457](https://github.com/monumental-archive/.github/pull/457))

## [1.32.0](https://github.com/monumental-archive/.github/compare/v1.31.0...v1.32.0) - 2026-08-16

### Added

- deliver the language tool configs too, minus two ([#455](https://github.com/monumental-archive/.github/pull/455))

### Documentation

- record what the belt now delivers, and that skips expire ([#453](https://github.com/monumental-archive/.github/pull/453))

## [1.31.0](https://github.com/monumental-archive/.github/compare/v1.30.0...v1.31.0) - 2026-08-16

### Added

- deliver tool configs from the canon, not copies ([#451](https://github.com/monumental-archive/.github/pull/451))

## [1.30.0](https://github.com/monumental-archive/.github/compare/v1.29.1...v1.30.0) - 2026-08-16

### Added

- stand up clippy at every group ([#446](https://github.com/monumental-archive/.github/pull/446))
- stand up rustfmt at its stable maximum ([#447](https://github.com/monumental-archive/.github/pull/447))
- fuzz under AddressSanitizer, stop the lint writing ([#448](https://github.com/monumental-archive/.github/pull/448))

### Documentation

- record the emit cutover — #434 closed, the chains green ([#443](https://github.com/monumental-archive/.github/pull/443))

## [1.29.1](https://github.com/monumental-archive/.github/compare/v1.29.0...v1.29.1) - 2026-08-15

### Fixed

- read the trusted root without a pipeline the pipefail kills ([#438](https://github.com/monumental-archive/.github/pull/438))

## [1.29.0](https://github.com/monumental-archive/.github/compare/v1.28.1...v1.29.0) - 2026-08-15

### Added

- lint gate stubs for trigger closure over gate inputs ([#432](https://github.com/monumental-archive/.github/pull/432))
- audit:source-vsa verifies through stele, the verify cutover ([#436](https://github.com/monumental-archive/.github/pull/436))
- cut the emitter and verdict assembly over to stele emit ([#437](https://github.com/monumental-archive/.github/pull/437))

### Documentation

- document the ledger-fork heal path ([#435](https://github.com/monumental-archive/.github/pull/435))

### Fixed

- trigger on edited so title and base changes re-run the gate ([#430](https://github.com/monumental-archive/.github/pull/430))

## [1.28.1](https://github.com/monumental-archive/.github/compare/v1.28.0...v1.28.1) - 2026-08-15

### Fixed

- exclude vendor trees from every enumerating ls-files walk ([#429](https://github.com/monumental-archive/.github/pull/429))

### Miscellaneous

- untrack the compiled embedded-shell bytecode ([#426](https://github.com/monumental-archive/.github/pull/426))

## [1.28.0](https://github.com/monumental-archive/.github/compare/v1.27.0...v1.28.0) - 2026-08-15

### Added

- cap standalone shell at 130 columns (#403) ([#423](https://github.com/monumental-archive/.github/pull/423))

## [1.27.0](https://github.com/monumental-archive/.github/compare/v1.26.2...v1.27.0) - 2026-08-15

### Added

- stand up yamllint at full inventory (#403) ([#413](https://github.com/monumental-archive/.github/pull/413))
- adopt editorconfig-checker (#403) ([#416](https://github.com/monumental-archive/.github/pull/416))
- adopt gitleaks at commit depth (#403) ([#417](https://github.com/monumental-archive/.github/pull/417))
- adopt hadolint at style threshold (#403) ([#418](https://github.com/monumental-archive/.github/pull/418))
- adopt cargo-machete, static, github backend (#403) ([#419](https://github.com/monumental-archive/.github/pull/419))
- adopt cargo-semver-checks as a repo audit (#403) ([#420](https://github.com/monumental-archive/.github/pull/420))
- adopt sqlfluff, third pipx exception (#403) ([#422](https://github.com/monumental-archive/.github/pull/422))

### Fixed

- refresh stale postgres digest pins ([#415](https://github.com/monumental-archive/.github/pull/415))
- unset ec indent_style for Go — raw literals red (#403) ([#421](https://github.com/monumental-archive/.github/pull/421))

## [1.26.2](https://github.com/monumental-archive/.github/compare/v1.26.1...v1.26.2) - 2026-08-15

### Fixed

- gate the squash subject like a commit ([#411](https://github.com/monumental-archive/.github/pull/411))

### Miscellaneous

- record stele's scorecard floor ([#407](https://github.com/monumental-archive/.github/pull/407))
- stele's source chain is founded — retire the optout row ([#409](https://github.com/monumental-archive/.github/pull/409))

## [1.26.1](https://github.com/monumental-archive/.github/compare/v1.26.0...v1.26.1) - 2026-08-15

### Fixed

- carry the stele proof run back into the scaffold ([#405](https://github.com/monumental-archive/.github/pull/405))

## [1.26.0](https://github.com/monumental-archive/.github/compare/v1.25.0...v1.26.0) - 2026-08-15

### Added

- adopt biome at preset all as the js/ts/json layer ([#385](https://github.com/monumental-archive/.github/pull/385))
- adopt ruff at select ALL and clear the python it found ([#387](https://github.com/monumental-archive/.github/pull/387))
- adopt pinact and point zizmor at the composite actions ([#391](https://github.com/monumental-archive/.github/pull/391))
- stand up the go layer — lint:go, coverage leg, govulncheck (#392) ([#404](https://github.com/monumental-archive/.github/pull/404))

### Dependencies

- update dependency python to v3.14.7 ([#389](https://github.com/monumental-archive/.github/pull/389))
- update dependency aqua:google/osv-scanner to v2.5.0 ([#395](https://github.com/monumental-archive/.github/pull/395))
- update dependency ruff to v0.16.2 ([#396](https://github.com/monumental-archive/.github/pull/396))

### Documentation

- seed .bestpractices.json and correct the crib's stale claims ([#375](https://github.com/monumental-archive/.github/pull/375))
- correct the badge catalogue's stale coverage entry ([#382](https://github.com/monumental-archive/.github/pull/382))
- revoke the bash test framework and record the belt-shell verdict ([#399](https://github.com/monumental-archive/.github/pull/399))
- correct the set -e verdict, which now contradicts the belt ([#402](https://github.com/monumental-archive/.github/pull/402))

### Fixed

- rename the baseline keys the JSON pre-fill silently dropped ([#379](https://github.com/monumental-archive/.github/pull/379))
- report a burned release instead of reddening the audit forever ([#380](https://github.com/monumental-archive/.github/pull/380))
- stop citing an upstream revision as if it were an org tag ([#381](https://github.com/monumental-archive/.github/pull/381))
- state BuildEnv L0 as attribution rather than a chosen ceiling ([#383](https://github.com/monumental-archive/.github/pull/383))
- make the spec-drift audit check coverage, not just movement ([#384](https://github.com/monumental-archive/.github/pull/384))
- carry the release-age floor into python, and adjudicate uv ([#388](https://github.com/monumental-archive/.github/pull/388))
- pin jq, a gate dependency that never was ([#390](https://github.com/monumental-archive/.github/pull/390))
- lint and format every line of shell, wherever it lives ([#393](https://github.com/monumental-archive/.github/pull/393))
- stop lint:badges nesting mise, which made the gate unrunnable ([#394](https://github.com/monumental-archive/.github/pull/394))
- verify link fragments, and say why trivy skips licences ([#400](https://github.com/monumental-archive/.github/pull/400))
- make every task fail on error, by default ([#401](https://github.com/monumental-archive/.github/pull/401))

## [1.25.0](https://github.com/monumental-archive/.github/compare/v1.24.1...v1.25.0) - 2026-08-13

### Added

- check a consumer's caller grants in its own gate ([#372](https://github.com/monumental-archive/.github/pull/372))

## [1.24.1](https://github.com/monumental-archive/.github/compare/v1.24.0...v1.24.1) - 2026-08-13

### Documentation

- canon releases first and cheaply; the lab is the full-width proof ([#369](https://github.com/monumental-archive/.github/pull/369))

### Fixed

- drop the decision subject check that refused a correct verdict ([#367](https://github.com/monumental-archive/.github/pull/367))

## [1.24.0](https://github.com/monumental-archive/.github/compare/v1.23.2...v1.24.0) - 2026-08-13

### Added

- guard the seam class so a release cannot be its first test ([#365](https://github.com/monumental-archive/.github/pull/365))

### Fixed

- derive the coverage shield, and say blocked where we said permanent ([#362](https://github.com/monumental-archive/.github/pull/362))

## [1.23.2](https://github.com/monumental-archive/.github/compare/v1.23.1...v1.23.2) - 2026-08-13

### Fixed

- schedule the two unrun audit legs, and ask the shield's question ([#356](https://github.com/monumental-archive/.github/pull/356))
- move the lab's tag-signing epoch to its first signed tag ([#359](https://github.com/monumental-archive/.github/pull/359))
- find the SBOM the decision covers, not the first one alphabetically ([#361](https://github.com/monumental-archive/.github/pull/361))

## [1.23.1](https://github.com/monumental-archive/.github/compare/v1.23.0...v1.23.1) - 2026-08-13

### Documentation

- record how the badge registrations actually work ([#351](https://github.com/monumental-archive/.github/pull/351))

### Fixed

- floor the signer at its lower scorecard reading ([#348](https://github.com/monumental-archive/.github/pull/348))
- close #349 and the #347 doc surface — barrier, blind-audit repairs, full S-series ([#353](https://github.com/monumental-archive/.github/pull/353))

## [1.23.0](https://github.com/monumental-archive/.github/compare/v1.22.2...v1.23.0) - 2026-08-13

### Added

- wear the REUSE shield the tree already earned ([#343](https://github.com/monumental-archive/.github/pull/343))
- wear the best-practices and OSPS baseline shields ([#345](https://github.com/monumental-archive/.github/pull/345))

### Fixed

- strip inline comments so the badge walk reads shields ([#341](https://github.com/monumental-archive/.github/pull/341))
- report a failed codecov upload instead of passing ([#346](https://github.com/monumental-archive/.github/pull/346))

## [1.22.2](https://github.com/monumental-archive/.github/compare/v1.22.1...v1.22.2) - 2026-08-13

### Fixed

- share one exclusion set between the ratchet and the badge feed ([#339](https://github.com/monumental-archive/.github/pull/339))

## [1.22.1](https://github.com/monumental-archive/.github/compare/v1.22.0...v1.22.1) - 2026-08-13

### Fixed

- follow the concept-record redirect on resolve ([#337](https://github.com/monumental-archive/.github/pull/337))

## [1.22.0](https://github.com/monumental-archive/.github/compare/v1.21.0...v1.22.0) - 2026-08-13

### Added

- machine-check REUSE compliance before registration ([#336](https://github.com/monumental-archive/.github/pull/336))

### Documentation

- record the codecov registration model and two scorecard caps ([#334](https://github.com/monumental-archive/.github/pull/334))

### Fixed

- serialise release runs on the workflow, not the commit ([#335](https://github.com/monumental-archive/.github/pull/335))

### Miscellaneous

- record the concept DOI from the first mint ([#331](https://github.com/monumental-archive/.github/pull/331))

## [1.21.0](https://github.com/monumental-archive/.github/compare/v1.20.0...v1.21.0) - 2026-08-13

### Added

- run audit:fuzz from the shared repo audit ([#328](https://github.com/monumental-archive/.github/pull/328))

### Fixed

- converge attach on an immutable release so the mint can resume ([#330](https://github.com/monumental-archive/.github/pull/330))

## [1.20.0](https://github.com/monumental-archive/.github/compare/v1.19.0...v1.20.0) - 2026-08-13

### Added

- stand up coverage and fuzz as belt machinery ([#323](https://github.com/monumental-archive/.github/pull/323))
- mint the canon's own DOI on self-publish ([#326](https://github.com/monumental-archive/.github/pull/326))

### Fixed

- render date-released unquoted, matching the release stamper ([#327](https://github.com/monumental-archive/.github/pull/327))

## [1.19.0](https://github.com/monumental-archive/.github/compare/v1.18.0...v1.19.0) - 2026-08-13

### Added

- derive CITATION.cff from REUSE.toml and mint the DOI from it ([#320](https://github.com/monumental-archive/.github/pull/320))
- delete the zenodo sandbox lever — every mint is real ([#321](https://github.com/monumental-archive/.github/pull/321))
- derive the badge block — the canon consumes its own machinery ([#322](https://github.com/monumental-archive/.github/pull/322))

### Fixed

- single-source the signer identity and unstick the gate ([#317](https://github.com/monumental-archive/.github/pull/317))
- claim only the base images a class actually instantiated ([#319](https://github.com/monumental-archive/.github/pull/319))

## [1.18.0](https://github.com/monumental-archive/.github/compare/v1.17.0...v1.18.0) - 2026-08-13

### Added

- derive --signer-digest from the tree's own sign.yml pin ([#315](https://github.com/monumental-archive/.github/pull/315))

### Fixed

- correct the four findings of the #310 audit re-run ([#311](https://github.com/monumental-archive/.github/pull/311))

## [1.17.0](https://github.com/monumental-archive/.github/compare/v1.16.1...v1.17.0) - 2026-08-13

### Added

- declare each input's forwarding class where it is born ([#306](https://github.com/monumental-archive/.github/pull/306))

### Fixed

- give the prepare compile SOURCE_DATE_EPOCH, the real #295 cause ([#308](https://github.com/monumental-archive/.github/pull/308))

## [1.16.1](https://github.com/monumental-archive/.github/compare/v1.16.0...v1.16.1) - 2026-08-13

### Fixed

- thread prepare through the continuous archetype too ([#302](https://github.com/monumental-archive/.github/pull/302))

## [1.16.0](https://github.com/monumental-archive/.github/compare/v1.15.1...v1.16.0) - 2026-08-13

### Added

- move the oci-image compile out of the dockerfile (#295) ([#299](https://github.com/monumental-archive/.github/pull/299))

### Fixed

- correct the seven findings of the #290 audit pass ([#296](https://github.com/monumental-archive/.github/pull/296))
- repair the signer-pin bumper, which ate its own marker ([#301](https://github.com/monumental-archive/.github/pull/301))

## [1.15.1](https://github.com/monumental-archive/.github/compare/v1.15.0...v1.15.1) - 2026-08-12

### Fixed

- make the image builds deterministic, as the gate demanded ([#291](https://github.com/monumental-archive/.github/pull/291))

## [1.15.0](https://github.com/monumental-archive/.github/compare/v1.14.1...v1.15.0) - 2026-08-12

### Added

- stand up the canon's badge surface ([#284](https://github.com/monumental-archive/.github/pull/284))
- re-check every shield against its source of truth ([#287](https://github.com/monumental-archive/.github/pull/287))
- verify the upstream base provenance and sign the result ([#288](https://github.com/monumental-archive/.github/pull/288))
- block every irreversible step on a bit-for-bit rebuild ([#289](https://github.com/monumental-archive/.github/pull/289))

### Documentation

- retire the claims the final pass made stale ([#283](https://github.com/monumental-archive/.github/pull/283))

### Miscellaneous

- bump the emitter pin to v1.14.1 ([#281](https://github.com/monumental-archive/.github/pull/281))

## [1.14.1](https://github.com/monumental-archive/.github/compare/v1.14.0...v1.14.1) - 2026-08-12

### Fixed

- hand the genesis flag to the emit step too ([#278](https://github.com/monumental-archive/.github/pull/278))

### Miscellaneous

- bump the emitter pin to v1.14.0 ([#276](https://github.com/monumental-archive/.github/pull/276))

## [1.14.0](https://github.com/monumental-archive/.github/compare/v1.13.0...v1.14.0) - 2026-08-12

### Added

- carry the VSA SHOULDs in the source VSA via one shared assembler ([#269](https://github.com/monumental-archive/.github/pull/269))
- heal chain holes on every push, with computed levels ([#270](https://github.com/monumental-archive/.github/pull/270))
- sign the verdict with the verifier's own identity ([#271](https://github.com/monumental-archive/.github/pull/271))
- sign the implicit build facts as a verifier-computed enrichment ([#272](https://github.com/monumental-archive/.github/pull/272))
- make an unactivated repo a finding, not a skip ([#273](https://github.com/monumental-archive/.github/pull/273))
- land the canon's licence and make the licence a step ([#274](https://github.com/monumental-archive/.github/pull/274))

### Documentation

- record the measured release-path triage run ([#261](https://github.com/monumental-archive/.github/pull/261))
- correct the claims the third conformance pass falsified ([#268](https://github.com/monumental-archive/.github/pull/268))
- complete the docs set — build-track, verdict register, provenance format ([#275](https://github.com/monumental-archive/.github/pull/275))

## [1.13.0](https://github.com/monumental-archive/.github/compare/v1.12.1...v1.13.0) - 2026-08-12

### Added

- verify buildType and externalParameters in the verdict ([#256](https://github.com/monumental-archive/.github/pull/256))
- triage the tagged lock on the release path ([#260](https://github.com/monumental-archive/.github/pull/260))
- render a verdict for every class after publish ([#259](https://github.com/monumental-archive/.github/pull/259))

## [1.12.1](https://github.com/monumental-archive/.github/compare/v1.12.0...v1.12.1) - 2026-08-12

### Fixed

- skip the signer-pin lint where no pin is declared ([#254](https://github.com/monumental-archive/.github/pull/254))

## [1.12.0](https://github.com/monumental-archive/.github/compare/v1.11.1...v1.12.0) - 2026-08-12

### Added

- activate the canon's own source-track emitter ([#248](https://github.com/monumental-archive/.github/pull/248))
- record the source track at Level 3, emitted and verifiable ([#249](https://github.com/monumental-archive/.github/pull/249))
- verify the evidence the vsa summarises ([#253](https://github.com/monumental-archive/.github/pull/253))

### Documentation

- record the measured token grant for the claims job ([#246](https://github.com/monumental-archive/.github/pull/246))
- retire the pre-activation language across the doc set ([#250](https://github.com/monumental-archive/.github/pull/250))
- correct the source-attest template metadata ([#251](https://github.com/monumental-archive/.github/pull/251))

### Fixed

- pair the two-stage template with the release that understands it ([#244](https://github.com/monumental-archive/.github/pull/244))

## [1.11.1](https://github.com/monumental-archive/.github/compare/v1.11.0...v1.11.1) - 2026-08-12

### Fixed

- make an unreadable tag-ruleset read loud, split the emitter's capabilities ([#241](https://github.com/monumental-archive/.github/pull/241))

## [1.11.0](https://github.com/monumental-archive/.github/compare/v1.10.1...v1.11.0) - 2026-08-12

### Added

- require template pins to name a released tag ([#235](https://github.com/monumental-archive/.github/pull/235))
- give the source emitter an environment contract ([#237](https://github.com/monumental-archive/.github/pull/237))

### Documentation

- name v1.10.0 as the activation minimum, not v1.9.0 ([#225](https://github.com/monumental-archive/.github/pull/225))

### Fixed

- stop renovate majoring the pgrx base-image matrix flat ([#238](https://github.com/monumental-archive/.github/pull/238))

## [1.10.1](https://github.com/monumental-archive/.github/compare/v1.10.0...v1.10.1) - 2026-08-12

### Fixed

- stop the self-pin release loop with a chore(canon) scope ([#231](https://github.com/monumental-archive/.github/pull/231))

## [1.10.0](https://github.com/monumental-archive/.github/compare/v1.9.0...v1.10.0) - 2026-08-12

### Added

- lint every uses: against the org actions allowlist ([#222](https://github.com/monumental-archive/.github/pull/222))

### Documentation

- name the expected red run in the activation checklist ([#219](https://github.com/monumental-archive/.github/pull/219))

### Fixed

- take cosign from the belt, not sigstore's installer ([#221](https://github.com/monumental-archive/.github/pull/221))

## [1.9.0](https://github.com/monumental-archive/.github/compare/v1.8.0...v1.9.0) - 2026-08-12

### Added

- key vex decisions by dependency, derive coverage everywhere ([#216](https://github.com/monumental-archive/.github/pull/216))
- point blast-radius at the remediation and the inert decisions ([#217](https://github.com/monumental-archive/.github/pull/217))
- emit source provenance and VSAs in-org ([#218](https://github.com/monumental-archive/.github/pull/218))

### Dependencies

- update monumental-archive/signer digest to b05fe88 ([#204](https://github.com/monumental-archive/.github/pull/204))

### Documentation

- correct the buildenv claim and the base-image measurement ([#203](https://github.com/monumental-archive/.github/pull/203))

## [1.8.0](https://github.com/monumental-archive/.github/compare/v1.7.1...v1.8.0) - 2026-08-12

### Added

- freeze the source-track claims contract, reserve the identity ([#201](https://github.com/monumental-archive/.github/pull/201))

### Documentation

- fill signer's boundary-b start revision in the ledger ([#202](https://github.com/monumental-archive/.github/pull/202))

### Fixed

- scope the audit token to a dedicated environment ([#194](https://github.com/monumental-archive/.github/pull/194))
- teach audit:links the scaffold's placeholder tokens ([#197](https://github.com/monumental-archive/.github/pull/197))
- render the slsa badge from shields, not slsa.dev ([#198](https://github.com/monumental-archive/.github/pull/198))

## [1.7.1](https://github.com/monumental-archive/.github/compare/v1.7.0...v1.7.1) - 2026-08-11

### Fixed

- pin mise itself on the build legs — the installer is a build input ([#191](https://github.com/monumental-archive/.github/pull/191))

## [1.7.0](https://github.com/monumental-archive/.github/compare/v1.6.0...v1.7.0) - 2026-08-11

### Added

- close the build track's unattested-input holes (#117) ([#188](https://github.com/monumental-archive/.github/pull/188))

## [1.6.0](https://github.com/monumental-archive/.github/compare/v1.5.3...v1.6.0) - 2026-08-11

### Added

- triage-class the blast radius and decide the base-layer set ([#186](https://github.com/monumental-archive/.github/pull/186))

### Fixed

- extend the serde_cbor decision to v0.18.1 ([#185](https://github.com/monumental-archive/.github/pull/185))

## [1.5.3](https://github.com/monumental-archive/.github/compare/v1.5.2...v1.5.3) - 2026-08-11

### Fixed

- reach assert-image-facts through the canon tree in the pgrx image cells ([#180](https://github.com/monumental-archive/.github/pull/180))

## [1.5.2](https://github.com/monumental-archive/.github/compare/v1.5.1...v1.5.2) - 2026-08-11

### Fixed

- serialize lint:deny behind the rust-driving tasks ([#177](https://github.com/monumental-archive/.github/pull/177))
- complete the rust task serialization chain ([#179](https://github.com/monumental-archive/.github/pull/179))

## [1.5.1](https://github.com/monumental-archive/.github/compare/v1.5.0...v1.5.1) - 2026-08-11

### Fixed

- restore the executable bit on collect-vex.sh, and gate script modes ([#174](https://github.com/monumental-archive/.github/pull/174))

## [1.5.0](https://github.com/monumental-archive/.github/compare/v1.4.1...v1.5.0) - 2026-08-11

### Added

- the dependency track, complete — deny, blast-radius, class-shaped sboms, auditable binaries, vex live ([#172](https://github.com/monumental-archive/.github/pull/172))

### Documentation

- stand up the dependency track at its chosen ceiling ([#169](https://github.com/monumental-archive/.github/pull/169))

### Fixed

- generate the changelog md013 waiver from the cliff header ([#173](https://github.com/monumental-archive/.github/pull/173))

## [1.4.1](https://github.com/monumental-archive/.github/compare/v1.4.0...v1.4.1) - 2026-08-11

### Documentation

- correct two image-metadata claims the lab disproved ([#159](https://github.com/monumental-archive/.github/pull/159))
- state the direction and the slsa ceiling ([#162](https://github.com/monumental-archive/.github/pull/162))
- correct the vsa stranger one-liner to the bundle form ([#163](https://github.com/monumental-archive/.github/pull/163))

### Fixed

- stamp the canon pin as a literal the release commit writes ([#164](https://github.com/monumental-archive/.github/pull/164))
- consume the canon through $/ self-repository resolution ([#167](https://github.com/monumental-archive/.github/pull/167))

## [1.4.0](https://github.com/monumental-archive/.github/compare/v1.3.2...v1.4.0) - 2026-08-11

### Added

- emit oci image metadata from one resolved facts map ([#155](https://github.com/monumental-archive/.github/pull/155))

## [1.3.2](https://github.com/monumental-archive/.github/compare/v1.3.1...v1.3.2) - 2026-08-11

### Fixed

- hand work-dir ownership back before cleaning up ([#152](https://github.com/monumental-archive/.github/pull/152))

## [1.3.1](https://github.com/monumental-archive/.github/compare/v1.3.0...v1.3.1) - 2026-08-11

### Fixed

- let postgres traverse the derive work directory ([#149](https://github.com/monumental-archive/.github/pull/149))

## [1.3.0](https://github.com/monumental-archive/.github/compare/v1.2.2...v1.3.0) - 2026-08-11

### Added

- derive and prove pgrx upgrade scripts on the release pr ([#147](https://github.com/monumental-archive/.github/pull/147))

### Dependencies

- update monumental-archive/signer digest to e50552b ([#148](https://github.com/monumental-archive/.github/pull/148))

## [1.2.2](https://github.com/monumental-archive/.github/compare/v1.2.1...v1.2.2) - 2026-08-10

### Fixed

- widen the zero-cooldown rule to all first-party packages ([#142](https://github.com/monumental-archive/.github/pull/142))

## [1.2.1](https://github.com/monumental-archive/.github/compare/v1.2.0...v1.2.1) - 2026-08-10

### Fixed

- grant self-publish the orchestrator's full permission union ([#140](https://github.com/monumental-archive/.github/pull/140))

## [1.2.0](https://github.com/monumental-archive/.github/compare/v1.1.0...v1.2.0) - 2026-08-10

### Added

- publish the canon through the pipeline — the source-archive class ([#138](https://github.com/monumental-archive/.github/pull/138))

## [1.1.0](https://github.com/monumental-archive/.github/compare/v1.0.0...v1.1.0) - 2026-08-10

### Added

- pin every canon reference to v1.0.0 and alarm the bump loop ([#136](https://github.com/monumental-archive/.github/pull/136))

## [1.0.0](https://github.com/monumental-archive/.github/releases/tag/v1.0.0) - 2026-08-10

### Added

- the org rulesets, as reviewable JSON
- mise toolbelt skeleton with actionlint + shellcheck ([#5](https://github.com/monumental-archive/.github/pull/5))
- shellcheck at max — every optional check, no rc escape hatch ([#6](https://github.com/monumental-archive/.github/pull/6))
- the security configuration, as reviewable JSON ([#7](https://github.com/monumental-archive/.github/pull/7))
- the repo baseline — settings GitHub gives no org lever for ([#10](https://github.com/monumental-archive/.github/pull/10))
- org Renovate preset (default.json) + toolbelt rename ([#11](https://github.com/monumental-archive/.github/pull/11))
- the canonical task contract ([#12](https://github.com/monumental-archive/.github/pull/12))
- typos standup — spell-check in the belt ([#14](https://github.com/monumental-archive/.github/pull/14))
- lefthook standup — shared git hooks via remotes ([#15](https://github.com/monumental-archive/.github/pull/15))
- trivy standup — deterministic scanners in the gate ([#16](https://github.com/monumental-archive/.github/pull/16))
- zizmor standup — Actions security audit in the gate ([#17](https://github.com/monumental-archive/.github/pull/17))
- committed standup — conventional commits enforced ([#18](https://github.com/monumental-archive/.github/pull/18))
- taplo standup — TOML canon in the gate ([#19](https://github.com/monumental-archive/.github/pull/19))
- shfmt standup — shell formatting in the gate ([#20](https://github.com/monumental-archive/.github/pull/20))
- rumdl standup — markdown linting in the gate ([#21](https://github.com/monumental-archive/.github/pull/21))
- lychee standup — link auditing, deliberately outside the gate ([#22](https://github.com/monumental-archive/.github/pull/22))
- shared CI workflow — enforcement goes remote ([#23](https://github.com/monumental-archive/.github/pull/23))
- workflow template + new-repo scaffold ([#25](https://github.com/monumental-archive/.github/pull/25))
- org-default community health files ([#26](https://github.com/monumental-archive/.github/pull/26))
- shared release phase-1 workflow and canonical scripts ([#34](https://github.com/monumental-archive/.github/pull/34))
- wire the tag-mint app into the release-tag ruleset ([#35](https://github.com/monumental-archive/.github/pull/35))
- git-cliff standup — the phase-1 version decision joins the belt ([#37](https://github.com/monumental-archive/.github/pull/37))
- flip the v* creation lock to active ([#39](https://github.com/monumental-archive/.github/pull/39))
- narrow the id-token rule and enforce it; close three belt gaps ([#41](https://github.com/monumental-archive/.github/pull/41))
- require a DCO sign-off on every commit ([#42](https://github.com/monumental-archive/.github/pull/42))
- make immutable OIDC subject claims part of the repo baseline ([#44](https://github.com/monumental-archive/.github/pull/44))
- harden the toolbelt settings to the standard two repos already use ([#46](https://github.com/monumental-archive/.github/pull/46))
- exempt code blocks from the markdown line-length rule ([#48](https://github.com/monumental-archive/.github/pull/48))
- add the phase-2 publish orchestrator and the rust-crate class ([#57](https://github.com/monumental-archive/.github/pull/57))
- lint nested permissions, and add the phase-2 caller stub ([#59](https://github.com/monumental-archive/.github/pull/59))
- forbid bash 4+ syntax in belt tasks and release scripts ([#64](https://github.com/monumental-archive/.github/pull/64))
- detect packages left private under a public repository ([#70](https://github.com/monumental-archive/.github/pull/70))
- add the oci-image artifact class ([#65](https://github.com/monumental-archive/.github/pull/65))
- add the rust-binary and wasm-npm artifact classes ([#71](https://github.com/monumental-archive/.github/pull/71))
- add the badge layer — scorecard, dependency review, insights, continuity ([#75](https://github.com/monumental-archive/.github/pull/75))
- add the continuous archetype workflow ([#76](https://github.com/monumental-archive/.github/pull/76))
- watch the transparency log for the signer's identity ([#78](https://github.com/monumental-archive/.github/pull/78))
- prove releases rebuild to the published bytes ([#81](https://github.com/monumental-archive/.github/pull/81))
- add the pgrx-extension artifact class ([#84](https://github.com/monumental-archive/.github/pull/84))
- let the crate and binary classes exclude container-built members ([#85](https://github.com/monumental-archive/.github/pull/85))
- let the wasm-npm class publish under an npm scope ([#90](https://github.com/monumental-archive/.github/pull/90))
- add the runbook, the doi job, and the container-owned cnpg mirror ([#91](https://github.com/monumental-archive/.github/pull/91))
- make canon score-ready — coverage ratchet, badge feed, scaffolds ([#93](https://github.com/monumental-archive/.github/pull/93))
- let the bump rename pending upgrade scripts ([#99](https://github.com/monumental-archive/.github/pull/99))
- add lint:template-pins to the belt ([#113](https://github.com/monumental-archive/.github/pull/113))
- sign the artifact VSA — the verdict beside the evidence ([#115](https://github.com/monumental-archive/.github/pull/115))
- add source-track policies, org-owned ([#128](https://github.com/monumental-archive/.github/pull/128))
- share one source policy across the org ([#129](https://github.com/monumental-archive/.github/pull/129))
- move the policy clock past genesis ([#130](https://github.com/monumental-archive/.github/pull/130))
- version the canon and generalise the phase-1 version source ([#134](https://github.com/monumental-archive/.github/pull/134))

### Documentation

- what this repo is, and the rule that keeps L3 intact
- anything shared lives here, renovate-config included
- the UI applies what the API refuses — record the applied config ([#9](https://github.com/monumental-archive/.github/pull/9))
- rewrite CLAUDE.md, README, org profile; fix drift-audit token ([#29](https://github.com/monumental-archive/.github/pull/29))
- record the real AUDIT_TOKEN permission requirement ([#31](https://github.com/monumental-archive/.github/pull/31))
- repo migration playbook ([#32](https://github.com/monumental-archive/.github/pull/32))
- org release canon; stage v* tag-creation ruleset ([#33](https://github.com/monumental-archive/.github/pull/33))
- record the tag lock as proven, and why evaluate mode proves nothing ([#38](https://github.com/monumental-archive/.github/pull/38))
- SLSA and attestation reference from primary sources ([#40](https://github.com/monumental-archive/.github/pull/40))
- rename the lab to release-lab; record the OIDC claim measurement ([#43](https://github.com/monumental-archive/.github/pull/43))
- point the canon at signer, not trusted-builder ([#49](https://github.com/monumental-archive/.github/pull/49))
- correct the phase-2 canon on where publish steps may live ([#51](https://github.com/monumental-archive/.github/pull/51))
- settle experiment 1 and the subject-claim reading ([#52](https://github.com/monumental-archive/.github/pull/52))
- record that environment secrets do not reach a reusable ([#53](https://github.com/monumental-archive/.github/pull/53))
- retract the runner-hardening agent from the canon ([#54](https://github.com/monumental-archive/.github/pull/54))
- correct the fuzzing score route and the ruleset blocker ([#56](https://github.com/monumental-archive/.github/pull/56))
- canonise the image build rules and the scanning obligation ([#60](https://github.com/monumental-archive/.github/pull/60))
- settle the multi-arch attestation subject ([#63](https://github.com/monumental-archive/.github/pull/63))
- treat shared-workflow permissions as a public contract ([#68](https://github.com/monumental-archive/.github/pull/68))
- record the release pass as complete ([#109](https://github.com/monumental-archive/.github/pull/109))
- bring every doc in line with what is now true ([#112](https://github.com/monumental-archive/.github/pull/112))

### Fixed

- restore imperative commit subjects ([#24](https://github.com/monumental-archive/.github/pull/24))
- REST-only repo listing in the drift check ([#30](https://github.com/monumental-archive/.github/pull/30))
- canon checkouts must ride job_workflow_sha, not workflow_sha ([#36](https://github.com/monumental-archive/.github/pull/36))
- capability-boundary must not match id-token in comments ([#45](https://github.com/monumental-archive/.github/pull/45))
- stop narrowing lockfile_platforms; move locked back to CI ([#47](https://github.com/monumental-archive/.github/pull/47))
- quote the DCO hook message so the shared config parses ([#50](https://github.com/monumental-archive/.github/pull/50))
- carry the full commit-type list into the scaffold ([#55](https://github.com/monumental-archive/.github/pull/55))
- derive the release sign-off without the user endpoint ([#58](https://github.com/monumental-archive/.github/pull/58))
- correct the branch-protection scoring and record org application ([#61](https://github.com/monumental-archive/.github/pull/61))
- keep exactly one release branch and one release PR ([#62](https://github.com/monumental-archive/.github/pull/62))
- authenticate git-cliff so releases stop failing on rate limits ([#66](https://github.com/monumental-archive/.github/pull/66))
- make every caller state its grant, and lint for it ([#67](https://github.com/monumental-archive/.github/pull/67))
- identify the app by client id, and stop callers carrying it ([#69](https://github.com/monumental-archive/.github/pull/69))
- build legs install the caller's toolchain, never the belt ([#73](https://github.com/monumental-archive/.github/pull/73))
- survive download-artifact's single-bundle flattening ([#74](https://github.com/monumental-archive/.github/pull/74))
- run the rekor monitor ourselves, github-owned actions only ([#79](https://github.com/monumental-archive/.github/pull/79))
- keep trivy out of the toolbelt checkout ([#86](https://github.com/monumental-archive/.github/pull/86))
- require an upgrade path only from a release that shipped the extension ([#87](https://github.com/monumental-archive/.github/pull/87))
- run the pgrx build and tests as the unprivileged postgres user ([#89](https://github.com/monumental-archive/.github/pull/89))
- publish the tarball as a path, execute upgrades, teach repro-check classes ([#95](https://github.com/monumental-archive/.github/pull/95))
- refuse a wasm package whose repository does not name the caller ([#96](https://github.com/monumental-archive/.github/pull/96))
- gate the doi on attach explicitly and ship the upgrade scripts ([#97](https://github.com/monumental-archive/.github/pull/97))
- authenticate git-cliff in the tag job ([#100](https://github.com/monumental-archive/.github/pull/100))
- commit the renamed upgrade script's old path as a deletion ([#101](https://github.com/monumental-archive/.github/pull/101))
- probe for CITATION.cff by gh's exit status ([#103](https://github.com/monumental-archive/.github/pull/103))
- adapt the baseline to org-enforced signoff and entry-only environments ([#104](https://github.com/monumental-archive/.github/pull/104))
- let repro-check exclude workspace members like the publish does ([#105](https://github.com/monumental-archive/.github/pull/105))

### Miscellaneous

- move the stub pins to the four-class orchestrator ([#72](https://github.com/monumental-archive/.github/pull/72))
- move the stub pins to the continuous-archetype orchestrator ([#77](https://github.com/monumental-archive/.github/pull/77))
- remove the deprecated app-id input ([#80](https://github.com/monumental-archive/.github/pull/80))
- complete the badge block — registry shields and passive observers ([#94](https://github.com/monumental-archive/.github/pull/94))
- add the codeowners scaffold ([#98](https://github.com/monumental-archive/.github/pull/98))
- chore/publish env baseline ([#102](https://github.com/monumental-archive/.github/pull/102))
- move the workflow-template pins to today's canon ([#110](https://github.com/monumental-archive/.github/pull/110))
- bump the workflow-template pins to current canon ([#114](https://github.com/monumental-archive/.github/pull/114))
- move the workflow-template pins to today's canon ([#116](https://github.com/monumental-archive/.github/pull/116))
- replace the ruleset JSON mirror with docs/rulesets.md ([#127](https://github.com/monumental-archive/.github/pull/127))
- park the source track until upstream is org-ready ([#131](https://github.com/monumental-archive/.github/pull/131))
