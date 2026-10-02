# Security policy

Report vulnerabilities privately through GitHub Security Advisories for
`fileworks/unpacksort`. Do not include personal mailbox content.

Security fixes target the latest release. `unpacksort` bounds extraction and
never follows or recreates links, but parsers run in-process and the tool is not
a malware scanner or hardened sandbox. Process untrusted hostile data inside an
additional operating-system sandbox.
