from pathlib import Path
from unittest.mock import Mock

from main import ProcessingResult, process_passport_photo


MRZ_TEXT = (
    "noise\n"
    "P<UTOERIKSSON<<ANNA<MARIA<<<<<<<<<<<<<<<<<<<\n"
    "L898902C36UTO7408122F1204159ZE184226B<<<<<10\n"
)


def test_process_passport_photo_calls_integrations(tmp_path: Path) -> None:
    s3_client = Mock()
    bitrix_client = Mock()
    s3_client.upload_file.return_value = "https://s3.example/presigned"
    bitrix_client.create_lead.return_value = {"result": 123}

    def fake_ocr(_: bytes) -> str:
        return MRZ_TEXT

    photo_bytes = b"fake-image-bytes"
    filename = str(tmp_path / "passport.jpg")

    result = process_passport_photo(
        photo_bytes=photo_bytes,
        filename=filename,
        s3_client=s3_client,
        bitrix_client=bitrix_client,
        ocr_func=fake_ocr,
    )

    assert isinstance(result, ProcessingResult)
    assert result.file_url == "https://s3.example/presigned"
    assert result.mrz_data is not None
    assert result.mrz_data["passport_number"] == "L898902C3"

    s3_client.upload_file.assert_called_once()
    bitrix_client.create_lead.assert_called_once()

    sent_data = bitrix_client.create_lead.call_args.args[0]
    assert sent_data["UF_CRM_FILE_URL"] == "https://s3.example/presigned"
    assert "UF_CRM_MRZ_JSON" in sent_data


def test_process_passport_photo_without_mrz(tmp_path: Path) -> None:
    s3_client = Mock()
    bitrix_client = Mock()
    s3_client.upload_file.return_value = "https://s3.example/presigned"
    bitrix_client.create_lead.return_value = {"result": 1}

    result = process_passport_photo(
        photo_bytes=b"bytes",
        filename=str(tmp_path / "passport2.jpg"),
        s3_client=s3_client,
        bitrix_client=bitrix_client,
        ocr_func=lambda _: "no mrz here",
    )

    assert result.mrz_data is None
    sent_data = bitrix_client.create_lead.call_args.args[0]
    assert "UF_CRM_MRZ_JSON" not in sent_data
