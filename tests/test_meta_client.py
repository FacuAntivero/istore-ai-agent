"""
Tests de meta_client.verificar_firma_meta — la validación de que un webhook
entrante de verdad venga de Meta (HMAC-SHA256 con el App Secret), y no de
alguien que descubrió la URL y manda mensajes falsos.
"""
import hashlib
import hmac

from meta_client import verificar_firma_meta

APP_SECRET = "un-secreto-de-prueba"


def _firmar(payload_bytes: bytes, secret: str = APP_SECRET) -> str:
    return "sha256=" + hmac.new(secret.encode("utf-8"), payload_bytes, hashlib.sha256).hexdigest()


def test_firma_valida():
    payload = b'{"entry": []}'
    firma = _firmar(payload)

    assert verificar_firma_meta(payload, firma, APP_SECRET) is True


def test_firma_invalida_secreto_distinto():
    payload = b'{"entry": []}'
    firma = _firmar(payload, secret="otro-secreto")

    assert verificar_firma_meta(payload, firma, APP_SECRET) is False


def test_firma_invalida_payload_alterado():
    payload_original = b'{"entry": []}'
    firma = _firmar(payload_original)
    payload_alterado = b'{"entry": ["algo distinto"]}'

    assert verificar_firma_meta(payload_alterado, firma, APP_SECRET) is False


def test_firma_sin_header():
    assert verificar_firma_meta(b'{}', None, APP_SECRET) is False


def test_firma_con_formato_incorrecto():
    assert verificar_firma_meta(b'{}', "no-empieza-con-sha256=", APP_SECRET) is False
