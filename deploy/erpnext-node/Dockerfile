# Runtime-only security patch. Keep ERPNext/Frappe and the existing NVM path.
FROM node:24.22.1-bookworm-slim@sha256:55fb5f5a527ea2c5c4a95f963e48ef90aa38c7b346054fdc8ee9957a2b55d205 AS node
FROM frappe/erpnext@sha256:e1479ded7bfd78cd76015e62da703311a50c16ecca0ef381e851cfa52f0aab16
COPY --from=node --chown=frappe:frappe /usr/local/bin/node /home/frappe/.nvm/versions/node/v24.12.0/bin/node
RUN test "$(node --version)" = "v24.22.1" \
    && node -e 'const z = require("node:zlib"); const b = z.brotliCompressSync(Buffer.from("runtime-smoke")); if (z.brotliDecompressSync(b).toString() !== "runtime-smoke") process.exit(1)'
LABEL org.hyperailab.node-security-release="2026-10-21" \
      org.hyperailab.node-version="24.22.1" \
      org.hyperailab.erpnext-base-version="16.21.1"
