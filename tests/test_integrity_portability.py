from tests.support import source_digest


def test_hash_normalizes_checkout_conventions_but_detects_content_changes(tmp_path):
    directory = tmp_path / 'nested'
    directory.mkdir()
    path = directory / 'fixture.py'
    path.write_bytes(b'first\r\nsecond\r\n')
    windows = source_digest(tmp_path, 'nested\\fixture.py')
    path.write_bytes(b'first\nsecond\n')
    assert source_digest(tmp_path, 'nested/fixture.py') == windows
    path.write_bytes(b'first\nchanged\n')
    assert source_digest(tmp_path, 'nested/fixture.py') != windows
