from main import mrz_checksum, parse_mrz, validate_mrz_checksum


VALID_MRZ = (
    "P<UTOERIKSSON<<ANNA<MARIA<<<<<<<<<<<<<<<<<<<\n"
    "L898902C36UTO7408122F1204159ZE184226B<<<<<10"
)


INVALID_MRZ = (
    "P<UTOERIKSSON<<ANNA<MARIA<<<<<<<<<<<<<<<<<<<\n"
    "L898902C37UTO7408122F1204159ZE184226B<<<<<10"
)


def test_mrz_checksum_core() -> None:
    assert mrz_checksum("L898902C3") == 6
    assert validate_mrz_checksum("740812", "2") is True


def test_parse_valid_td3_mrz() -> None:
    parsed = parse_mrz(VALID_MRZ)
    assert parsed is not None
    assert parsed["passport_number"] == "L898902C3"
    assert parsed["surname"] == "ERIKSSON"
    assert parsed["given_names"] == "ANNA MARIA"
    assert parsed["passport_number_checksum_ok"] == "True"
    assert parsed["birth_date_checksum_ok"] == "True"
    assert parsed["expiry_date_checksum_ok"] == "True"
    assert parsed["final_checksum_ok"] == "True"


def test_parse_invalid_checksum_td3_mrz() -> None:
    parsed = parse_mrz(INVALID_MRZ)
    assert parsed is not None
    assert parsed["passport_number"] == "L898902C3"
    assert parsed["passport_number_checksum_ok"] == "False"


def test_parse_returns_none_when_no_mrz() -> None:
    assert parse_mrz("random OCR content") is None
