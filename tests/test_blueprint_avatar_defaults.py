from app.blueprint_avatars import (
    DEFAULT_BLUEPRINT_AVATARS,
    _seed_default_blueprint_avatars,
    normalize_blueprint_avatar_data_uri,
)


def test_default_linux_blueprint_avatars_are_valid_ico_data_uris():
    avatars = {item['id']: item for item in DEFAULT_BLUEPRINT_AVATARS}

    assert set(avatars) == {'ubuntu', 'sles', 'rhel'}
    assert avatars['ubuntu']['name'] == 'Ubuntu'
    assert avatars['sles']['name'] == 'SUSE Linux Enterprise Server (SLES)'
    assert avatars['rhel']['name'] == 'Red Hat Enterprise Linux (RHEL)'

    for item in avatars.values():
        normalized = normalize_blueprint_avatar_data_uri(item['data_uri'])
        assert normalized == item['data_uri']
        assert normalized.startswith('data:image/x-icon;base64,')


def test_default_linux_blueprint_avatar_seed_is_idempotent_and_preserves_custom_items():
    custom = {
        'id': 'custom-linux',
        'name': 'Custom Linux',
        'data_uri': DEFAULT_BLUEPRINT_AVATARS[0]['data_uri'],
    }

    seeded, changed = _seed_default_blueprint_avatars([custom])

    assert changed is True
    assert [item['id'] for item in seeded] == ['custom-linux', 'ubuntu', 'sles', 'rhel']

    seeded_again, changed_again = _seed_default_blueprint_avatars(seeded)

    assert changed_again is False
    assert [item['id'] for item in seeded_again] == ['custom-linux', 'ubuntu', 'sles', 'rhel']
