from scripts.verify_multicase_evidence import verify


def test_packaged_multicase_evidence() -> None:
    result = verify()
    assert result["decision"] == "GO"
    assert result["cases"] == 8
    assert result["configurations"] == 36
