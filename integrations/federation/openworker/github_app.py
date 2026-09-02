# OpenWorker GitHub App Broker (Not Implemented)

from integrations.managed.github_app import GitHubAppBroker


class OpenWorkerGitHubAppBroker(GitHubAppBroker):
    """OpenWorker Federation GitHub App Broker.

    NOT IMPLEMENTED. This is a placeholder for future OpenWorker Federation Adapter.
    Delete `integrations/federation/openworker/` to completely remove OpenWorker support.
    """

    def get_installation_token(
        self, installation_id: str, *, force: bool = False
    ) -> str:
        raise NotImplementedError("OpenWorker Federation Adapter not implemented")

    def clear(self, installation_id: str) -> None:
        raise NotImplementedError("OpenWorker Federation Adapter not implemented")