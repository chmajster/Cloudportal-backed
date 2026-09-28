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


    def get_placement_targets(self) -> list[dict]:
        """Return provider-normalized placement targets.

        Generic adapters may expose target discovery, but capacity-based placement
        remains unavailable until get_capacity returns real metrics.
        """
        result = []
        for row in self.discover('nodes') or []:
            node = row.get('node') or row.get('id')
            status = str(row.get('status') or '').lower()
            result.append({
                **dict(row),
                'node': str(node or ''),
                'online': status in {'online', 'available', 'connected', 'up', 'ready'},
            })
        return result

    def get_capacity(self, target: dict) -> dict:
        """Return real normalized capacity metrics for a target.

        The zero-valued default intentionally makes the candidate unusable rather
        than pretending that an unsupported provider has capacity.
        """
        return {
            'cpu_total': 0, 'cpu_used': 0,
            'memory_mb_total': 0, 'memory_mb_used': 0, 'memory_mb_free': 0,
            'storage_gb_total': 0, 'storage_gb_used': 0, 'storage_gb_free': 0,
            'vm_count': 0,
        }

    def get_networks(self, target: dict | None = None) -> list[dict]:
        return list(self.discover('networks', (target or {}).get('node')) or [])

    def get_storages(self, target: dict | None = None) -> list[dict]:
        return list(self.discover('storages', (target or {}).get('node')) or [])

    def validate_placement(self, target: dict, requirements: dict) -> dict:
        node = str(target.get('node') or '')
        candidates = self.get_placement_targets()
        live = next((row for row in candidates if str(row.get('node') or '') == node), None)
        return {
            'valid': bool(live and live.get('online')),
            'reason': None if live and live.get('online') else 'placement target unavailable',
        }

    def reserve_resources(self, target: dict, requirements: dict):
        raise NotImplementedError('Provider-native reservations are not supported; CloudPortal uses durable DB reservations')

    def release_reservation(self, reservation):
        raise NotImplementedError('Provider-native reservations are not supported; CloudPortal uses durable DB reservations')

    def create_vm(self, target: dict, spec: dict):
        raise NotImplementedError('Provider does not implement direct VM creation')
