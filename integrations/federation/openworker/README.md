# OpenWorker Federation Adapter (Optional)

This directory is a **placeholder** for a future OpenWorker Federation Adapter.

## Key Constraint

> Deleting this entire directory MUST NOT affect Delta Hub or Delta Desktop native functionality.

This adapter is **optional** and **explicitly disabled by default**. It is only loaded when:
1. The user explicitly enables it in configuration
2. A valid OpenWorker Cloud endpoint is configured

## Current Status

**Not implemented.** All implementations raise `NotImplementedError`.

To implement:
1. `oauth.py` - `OpenWorkerOAuthBroker` implementing `OAuthBroker`
2. `relay.py` - `OpenWorkerRelayTransport` implementing `RelayTransport`
3. `github_app.py` - `OpenWorkerGitHubAppBroker` implementing `GitHubAppBroker`

## Removal

To completely remove OpenWorker support:
```bash
rm -rf integrations/federation/openworker/
```

This has **zero impact** on Delta Desktop or Delta Hub native functionality.