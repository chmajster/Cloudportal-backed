from abc import ABC, abstractmethod


class InfrastructureProvider(ABC):
    """Provider adapters never receive shell commands or caller-selected code paths."""
    @abstractmethod
    def test(self) -> dict:
        raise NotImplementedError

    @abstractmethod
    def discover(self, resource: str, node: str | None = None) -> list:
        raise NotImplementedError

    @abstractmethod
    def guest_addresses(self, node: str, vm_id: int) -> list[str]:
        raise NotImplementedError
