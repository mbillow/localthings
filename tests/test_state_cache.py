from samsung_appliance.state_cache import StateCache


def test_state_cache_constructs_without_descriptor():
    c = StateCache()
    assert c.links == {}
    assert c.descriptor_state == {}


def test_on_observation_hook_fires():
    c = StateCache()
    seen = []
    c.set_on_observation(lambda st, href, rep: seen.append((href, rep)))
    c.apply_rep('/x/vs/0', {'a': 1}, source='seed')
    assert seen == [('/x/vs/0', {'a': 1})]
