# OpenWorker OAuth Broker (Not Implemented)

from integrations.managed.oauth import OAuthBroker


class OpenWorkerOAuthBroker(OAuthBroker):
    """OpenWorker Federation OAuth Broker.

    NOT IMPLEMENTED. This is a placeholder for future OpenWorker Federation Adapter.
    Delete `integrations/federation/openworker/` to completely remove OpenWorker support.
    """

    async def begin(
        self,
        connector: str,
        *,
        access: str = "",
        flow: str = "",
        redirect: str = "",
        app_state: str = "",
    ) -> dict[str, any]:
        raise NotImplementedError("OpenWorker Federation Adapter not implemented")

    async def exchange(self, form: dict[str, str]) -> dict[str, any]:
        raise NotImplementedError("OpenWorker Federation Adapter not implemented")

    async def refresh(
        self, connector: str, *, profile_key: str | None = None
    ) -> dict[str, any] | None:
        raise NotImplementedError("OpenWorker Federation Adapter not implemented")

    async def disconnect(
        self, connector: str, *, profile_key: str | None = None
    ) -> None:
        raise NotImplementedError("OpenWorker Federation Adapter not implemented")