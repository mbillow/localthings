def test_dishwasher_resources_carry_rt(dishwasher_resources):
    assert dishwasher_resources, "no resources loaded"
    # Every rep must expose its resource type so discovery can key on it.
    missing = [h for h, rep in dishwasher_resources.items()
               if not rep.get('rt')]
    assert not missing, f"resources without rt: {missing}"


def test_fridge_has_autofill(fridge_resources):
    assert '/autofill/vs/0' in fridge_resources
    assert 'x.com.samsung.da.autofill' in fridge_resources['/autofill/vs/0']['rt']
