# OpenWorker Relay Transport (Not Implemented)

from integrations.managed.relay import RelayTransport


class OpenWorkerRelayTransport(RelayTransport):
    """OpenWorker Federation Relay Transport.

    NOT IMPLEMENTED. This is a placeholder for future OpenWorker Federation Adapter.
    Delete `integrations/federation/openworker/` to completely remove OpenWorker support.
    """

    async def open(self) -> None:
        raise NotImplementedError("OpenWorker Federation Adapter not implemented")

    async def recv(self) -> dict | None:
        raise NotImplementedError("OpenWorker Federation Adapter not implemented")

    async def close(self) -> None:
        raise NotImplementedError("OpenWorker Federation Adapter not implemented")