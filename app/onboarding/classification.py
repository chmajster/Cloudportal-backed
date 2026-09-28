import ipaddress
import re

from sqlalchemy import select

from app.onboarding.models import OnboardingRule


SUPPORTED_CONDITIONS = {
    'provider_id', 'provider_type', 'cluster', 'node', 'resource_pool',
    'vm_name_regex', 'hostname_regex', 'tag', 'ip_subnet', 'vlan', 'bridge',
    'storage', 'os', 'resource_type',
}
SUPPORTED_EFFECTS = {
    'apmid', 'environment', 'owner_user_id', 'group', 'cost_center',
    'business_service', 'application', 'support_group', 'guest_credential_id',
    'awx_credential_id', 'integrations',
}


def validate_rule_document(conditions, effects):
    unknown_conditions = sorted(set(conditions or {}) - SUPPORTED_CONDITIONS)
    unknown_effects = sorted(set(effects or {}) - SUPPORTED_EFFECTS)
    if unknown_conditions or unknown_effects:
        errors = []
        if unknown_conditions:
            errors.append('unsupported conditions: ' + ', '.join(unknown_conditions))
        if unknown_effects:
            errors.append('unsupported effects: ' + ', '.join(unknown_effects))
        raise ValueError('; '.join(errors))
    for key in ('vm_name_regex', 'hostname_regex'):
        if (conditions or {}).get(key):
            re.compile(str(conditions[key]))
    if (conditions or {}).get('ip_subnet'):
        ipaddress.ip_network(str(conditions['ip_subnet']), strict=False)


def _equals(actual, expected):
    if isinstance(expected, list):
        return any(_equals(actual, item) for item in expected)
    return str(actual or '').casefold() == str(expected or '').casefold()


def _matches(resource, conditions, provider_type=None):
    location = resource.get('location') or {}
    guest = resource.get('guest') or {}
    networks = resource.get('networks') or []
    disks = resource.get('disks') or []
    conditions = conditions or {}
    for key, expected in conditions.items():
        if key == 'provider_id' and not _equals(resource.get('provider_id'), expected):
            return False
        if key == 'provider_type' and not _equals(provider_type, expected):
            return False
        if key == 'cluster' and not _equals(location.get('cluster'), expected):
            return False
        if key == 'node' and not _equals(location.get('node'), expected):
            return False
        if key == 'resource_pool' and not _equals(location.get('pool'), expected):
            return False
        if key == 'resource_type' and not _equals(resource.get('resource_type'), expected):
            return False
        if key == 'vm_name_regex' and not re.search(str(expected), str(resource.get('name') or ''), re.I):
            return False
        if key == 'hostname_regex' and not re.search(str(expected), str(resource.get('hostname') or ''), re.I):
            return False
        if key == 'tag':
            values = {str(value).casefold() for value in resource.get('tags') or []}
            requested = expected if isinstance(expected, list) else [expected]
            if not all(str(value).casefold() in values for value in requested):
                return False
        if key == 'ip_subnet':
            network = ipaddress.ip_network(str(expected), strict=False)
            addresses = []
            for value in resource.get('addresses') or []:
                try:
                    addresses.append(ipaddress.ip_address(str(value)))
                except ValueError:
                    continue
            if not any(address in network for address in addresses):
                return False
        if key == 'vlan':
            requested = {int(value) for value in (expected if isinstance(expected, list) else [expected])}
            if not any(item.get('vlan') in requested for item in networks):
                return False
        if key == 'bridge':
            requested = expected if isinstance(expected, list) else [expected]
            if not any(any(_equals(item.get('bridge'), value) for value in requested) for item in networks):
                return False
        if key == 'storage':
            requested = expected if isinstance(expected, list) else [expected]
            if not any(any(_equals(item.get('storage'), value) for value in requested) for item in disks):
                return False
        if key == 'os':
            os_values = [guest.get('os'), guest.get('name'), guest.get('pretty_name')]
            requested = expected if isinstance(expected, list) else [expected]
            if not any(
                str(value or '').casefold() in str(actual or '').casefold()
                for value in requested for actual in os_values
            ):
                return False
    return True


def classification_preview(db, resource, *, provider_type=None, manual=None):
    proposed = {}
    sources = {}
    trace = []
    rules = db.scalars(select(OnboardingRule).where(
        OnboardingRule.enabled.is_(True)
    ).order_by(OnboardingRule.priority.asc(), OnboardingRule.name.asc())).all()
    for rule in rules:
        try:
            matches = _matches(resource, rule.conditions_json or {}, provider_type)
        except (ValueError, re.error):
            matches = False
        trace.append({'rule_id': rule.id, 'rule': rule.name, 'matched': matches})
        if not matches:
            continue
        for key, value in (rule.effects_json or {}).items():
            if key in SUPPORTED_EFFECTS and value is not None:
                proposed[key] = value
                sources[key] = 'Rule: ' + rule.name
        if rule.stop_processing:
            break

    for key, value in (manual or {}).items():
        if value not in (None, '', [], {}):
            proposed[key] = value
            sources[key] = 'Manual'
    return {'mapping': proposed, 'sources': sources, 'trace': trace}
