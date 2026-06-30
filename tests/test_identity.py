import cbor2

from custom_components.localthings.ocf.registry.identity import DeviceIdentity, read_identity


class FakeSession:
    def __init__(self, table):
        self.table = table   # tuple(path) -> rep dict

    def get(self, path, timeout=10.0):
        rep = self.table.get(tuple(path))
        if rep is None:
            return 0x84, b''   # 4.04 not found
        return 0x45, cbor2.dumps(rep)


def test_read_identity_from_oic_p_and_d():
    sess = FakeSession({
        ('oic', 'p'): {'mnmn': 'Samsung Electronics', 'mnmo': 'RF9000B'},
        ('oic', 'd'): {'n': 'Family Hub'},
    })
    ident = read_identity(sess, serial='ABC123')
    assert ident.manufacturer == 'Samsung Electronics'
    assert ident.model == 'RF9000B'
    assert ident.name == 'Family Hub'
    assert ident.serial == 'ABC123'


def test_read_identity_tolerates_missing_resources():
    ident = read_identity(FakeSession({}), serial=None)
    assert ident.manufacturer == 'Samsung'
    assert ident.model == ''
    assert ident.serial is None
