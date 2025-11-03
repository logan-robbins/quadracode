# Debugging Reference

- Collect log excerpts from `docker compose logs orchestrator-runtime`.
- Capture Redis stream snapshots with `redis-cli XRANGE qc:context:metrics - +`.
- Record all remediation steps in the context governor summary.
