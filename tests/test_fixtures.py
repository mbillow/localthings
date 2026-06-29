def test_dishwasher_resources_loaded(dishwasher_resources):
    assert dishwasher_resources, "no resources loaded"
    # Resources are sourced from the /device/0 batch; rt is not present in
    # batch responses (OCF carries rt at the collection-entry level only).
    # Verify the fixture contains dict values (including empty reps which are
    # valid batch entries for resources that temporarily have no state).
    non_dicts = [h for h, rep in dishwasher_resources.items()
                 if not isinstance(rep, dict)]
    assert not non_dicts, f"resources with non-dict rep: {non_dicts}"


def test_fridge_has_autofill(fridge_resources):
    assert '/autofill/vs/0' in fridge_resources
    # Batch reps do not carry 'rt'; check for the actual autofill field.
    assert 'x.com.samsung.da.autofill' in fridge_resources['/autofill/vs/0']
