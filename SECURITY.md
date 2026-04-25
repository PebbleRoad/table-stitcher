# Security policy

## Reporting a vulnerability

Please **do not** file public GitHub issues for security vulnerabilities.

Email security reports to **maish.nichani@gmail.com** with:

- A description of the issue and its impact
- Steps to reproduce (or a minimal proof-of-concept)
- The version of `table-stitcher` affected

We aim to acknowledge reports within 3 business days and provide a fix or
mitigation timeline within 10 business days for confirmed vulnerabilities.

## Scope

`table-stitcher` is a parser-agnostic library that operates on extracted
table metadata. It does not parse PDFs directly, open network connections,
or execute untrusted input. The most plausible vulnerability classes are:

- Issues in dependencies (`pandas`, `docling`) — please report those upstream
- Denial-of-service via crafted `TableMeta` inputs (e.g., adversarial column
  counts or row counts that exhaust memory)
- Logic errors in the merger that could leak data across logically distinct
  tables when fed adversarial fragments

## Supported versions

Only the latest minor release receives security fixes. We recommend pinning
to `>=X.Y` and upgrading promptly when a security release is announced.
