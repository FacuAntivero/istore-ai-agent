"""
Tests de auth.py — la verificación de que un comercio no pueda leer ni tocar
los datos de otro comercio (el hueco de seguridad que arreglamos: antes
ningún endpoint chequeaba esto).
"""
import asyncio

import pytest
from fastapi import HTTPException

import auth
from fake_supabase import FakeAuth, FakeSupabase


def run(coro):
    return asyncio.run(coro)


# ---------- obtener_usuario_autenticado ----------

def test_sin_header_authorization_es_401():
    with pytest.raises(HTTPException) as exc:
        run(auth.obtener_usuario_autenticado(None))
    assert exc.value.status_code == 401


def test_header_sin_bearer_es_401():
    with pytest.raises(HTTPException) as exc:
        run(auth.obtener_usuario_autenticado("Token abc123"))
    assert exc.value.status_code == 401


def test_token_invalido_es_401():
    auth.supabase = FakeSupabase(auth=FakeAuth(exc=Exception("jwt expirado")))
    with pytest.raises(HTTPException) as exc:
        run(auth.obtener_usuario_autenticado("Bearer token-viejo"))
    assert exc.value.status_code == 401


def test_token_valido_devuelve_user_id():
    auth.supabase = FakeSupabase(auth=FakeAuth(user_id="user-123"))
    user_id = run(auth.obtener_usuario_autenticado("Bearer token-bueno"))
    assert user_id == "user-123"


# ---------- verificar_dueno_de_comercio ----------

def test_dueno_del_comercio_no_lanza_error():
    auth.supabase = FakeSupabase(results=[[{"id": 5}]])
    run(auth.verificar_dueno_de_comercio(5, "user-123"))  # no debe lanzar


def test_comercio_ajeno_es_403():
    """El caso central del bug: pedir un comercio_id que no es tuyo."""
    auth.supabase = FakeSupabase(results=[[]])  # no matchea ningún comercio con ese owner_id
    with pytest.raises(HTTPException) as exc:
        run(auth.verificar_dueno_de_comercio(999, "user-123"))
    assert exc.value.status_code == 403


# ---------- verificar_dueno_de_registro ----------

def test_registro_inexistente_es_404():
    auth.supabase = FakeSupabase(results=[[]])
    with pytest.raises(HTTPException) as exc:
        run(auth.verificar_dueno_de_registro("plantillas_postventa", 42, "user-123"))
    assert exc.value.status_code == 404


def test_registro_de_otro_comercio_es_403():
    auth.supabase = FakeSupabase(results=[
        [{"comercio_id": 7}],  # la plantilla 42 pertenece al comercio 7
        [],                     # pero el comercio 7 no le pertenece a user-123
    ])
    with pytest.raises(HTTPException) as exc:
        run(auth.verificar_dueno_de_registro("plantillas_postventa", 42, "user-123"))
    assert exc.value.status_code == 403


def test_registro_propio_devuelve_comercio_id():
    auth.supabase = FakeSupabase(results=[
        [{"comercio_id": 7}],
        [{"id": 7}],
    ])
    comercio_id = run(auth.verificar_dueno_de_registro("plantillas_postventa", 42, "user-123"))
    assert comercio_id == 7
