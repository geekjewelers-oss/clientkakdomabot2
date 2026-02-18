from ocr_service.mrz_parser import MRZParser


def test_td3_parse_checksum_and_hash():
    parser = MRZParser()
    l1 = "P<UTOERIKSSON<<ANNA<MARIA<<<<<<<<<<<<<<<<<<<"
    l2 = "L898902C36UTO7408122F1204159ZE184226B<<<<<10"

    result = parser.parse([l1, l2])

    assert result.format == "TD3"
    assert result.surname == "ERIKSSON"
    assert result.checksum_ok is True
    assert result.passport_hash is not None
    assert len(result.passport_hash) == 64


def test_td1_parse_supported():
    parser = MRZParser()
    lines = [
        "I<UTOD231458907<<<<<<<<<<<<<<<",
        "7408122F1204159UTO<<<<<<<<<<<6",
        "ERIKSSON<<ANNA<MARIA<<<<<<<<<<",
    ]

    result = parser.parse(lines)

    assert result.format == "TD1"
    assert result.given_names.startswith("ANNA")
    assert result.confidence >= 0
