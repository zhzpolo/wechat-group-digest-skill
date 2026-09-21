# Research and provenance

Pinned sources were inspected before implementation:

| Project or specification | Pinned source | License / use |
|---|---|---|
| wx-cli-again | `jackwener/wx-cli-again@077a54cbfe679bda963cd038d8440422907fc797` | Apache-2.0. The Windows read strategy and page crypto informed an independent Python implementation. |
| ChatTrace | `qiaodogbear/ChatTrace@ea7c3986b4fc4fd8c467c5be829a4c954a6fe070` | MIT. Informed the 4.1.11+ startup-capture and MMV1-anchor approach; version offsets were not copied. |
| SQLCipher | v4.6.1 `src/sqlcipher.c` | Used to verify page-1 handling, AES-CBC layout, and HMAC derivation. |
| SQLite | WAL file format and wal-index documentation | Used to implement checksum, salt, frame, commit, and mxFrame validation. |

Full upstream license texts are in `third_party/`; attribution is in `NOTICE`. No WeChat executable, DLL, client source, user database, key, memory dump, or real chat export is redistributed.

Rejected shortcuts:

- The current `LC044/WeChatMsg` repository was documentation-only at inspection time, so it was not treated as a working reader.
- `ylytdeng/wechat-decrypt` returned an unavailable/DMCA response and could not be audited.
- A toolkit without a clear root license was studied but no code was copied or binary executed.
- Older WAL examples that skipped checksums/commits or handled page 1 incorrectly were not reused.

Primary documentation:

- https://www.zetetic.net/sqlcipher/design/
- https://github.com/sqlcipher/sqlcipher/blob/v4.6.1/src/sqlcipher.c
- https://www.sqlite.org/fileformat2.html#wal_file_format
- https://www.sqlite.org/walformat.html
