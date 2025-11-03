---
name: Debugging Playbook
description: Structured workflow for triaging recurring runtime errors.
tags:
  - debugging
  - errors
links:
  - reference.md
---

## Core Workflow

1. Capture the failing command and full stack trace.
2. Correlate the failure with recent deployments or configuration changes.
3. Inspect Redis stream traffic for anomalous payloads.
4. Externalize bulky traces after recording the persistent location.
5. Summarize remediation steps back into the context log.
