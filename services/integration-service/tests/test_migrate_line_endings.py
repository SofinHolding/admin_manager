"""Checksum migration không phụ thuộc kiểu xuống dòng (CRLF/LF), nhưng vẫn phát hiện sửa NỘI DUNG."""

from integration_service.migrate import _accepted_checksums, _checksum


def test_line_endings_do_not_change_the_checksum_but_content_does(tmp_path):
    lf, crlf, edited = tmp_path / "a.sql", tmp_path / "b.sql", tmp_path / "c.sql"
    lf.write_bytes(b"CREATE TABLE t (id int);\n-- x\n")
    crlf.write_bytes(b"CREATE TABLE t (id int);\r\n-- x\r\n")
    edited.write_bytes(b"CREATE TABLE t (id bigint);\n-- x\n")
    assert _checksum(lf) == _checksum(crlf) != _checksum(edited)
    # DB cũ có thể giữ băm trên byte thô của bản CRLF: vẫn được chấp nhận cho file LF (và ngược lại)
    import hashlib
    raw_crlf = hashlib.sha256(crlf.read_bytes()).hexdigest()
    assert raw_crlf in _accepted_checksums(lf) and _checksum(lf) in _accepted_checksums(crlf)
    assert raw_crlf not in _accepted_checksums(edited)
