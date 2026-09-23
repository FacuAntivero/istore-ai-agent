"""
Tests de las tools de la vertical "consultorio" (turnos_consultorio):
agendar_turno_consultorio (nuevo, reprogramar, cupo lleno) y
cancelar_turno_consultorio (éxito, sin turno pendiente).

Mismo estilo que test_stock.py: FakeSupabase con una cola de resultados en
el orden exacto en que el código real llama a .table(...). El envío a
QStash (_programar_upstash_desde_tools) se mockea a un no-op porque hace
una llamada de red real y no es lo que este test está verificando.
"""
import tools
from fake_supabase import FakeSupabase

CONFIG_DEFAULT = {"horas_anticipacion_recordatorio": 24, "max_turnos_por_horario": 1}


def test_agendar_turno_nuevo_exito(monkeypatch):
    monkeypatch.setattr(tools, "_programar_upstash_desde_tools", lambda *a, **k: None)
    tools.supabase = FakeSupabase(results=[
        [CONFIG_DEFAULT],       # obtener_configuracion_consultorio
        [],                     # obtener_profesionales_consultorio: sin profesionales (agenda única)
        [],                     # turno_existente: no tiene uno previo
        [],                     # query_cupos: el horario está libre
        [{"id": 501}],          # insert
    ])

    resultado = tools.agendar_turno_consultorio(
        "María Pérez", "5492494000000", "Odontología general", "2026-10-01 10:00:00", comercio_id=1
    )

    assert "agendado" in resultado


def test_agendar_turno_reprograma_existente(monkeypatch):
    monkeypatch.setattr(tools, "_programar_upstash_desde_tools", lambda *a, **k: None)
    tools.supabase = FakeSupabase(results=[
        [CONFIG_DEFAULT],       # obtener_configuracion_consultorio
        [],                     # obtener_profesionales_consultorio: sin profesionales (agenda única)
        [{"id": 77}],           # turno_existente: ya tenía uno pendiente
        [],                     # query_cupos: libre (excluyendo el propio)
        [{"id": 77}],           # update
    ])

    resultado = tools.agendar_turno_consultorio(
        "María Pérez", "5492494000000", "Odontología general", "2026-10-02 11:00:00", comercio_id=1
    )

    assert "reprogramado" in resultado


def test_agendar_turno_cupo_lleno(monkeypatch):
    monkeypatch.setattr(tools, "_programar_upstash_desde_tools", lambda *a, **k: None)
    tools.supabase = FakeSupabase(results=[
        [CONFIG_DEFAULT],       # obtener_configuracion_consultorio (max_turnos_por_horario=1)
        [],                     # obtener_profesionales_consultorio: sin profesionales (agenda única)
        [],                     # turno_existente: no tiene uno previo
        [{"id": 999}],          # query_cupos: ya hay 1 turno en ese horario -> lleno
    ])

    resultado = tools.agendar_turno_consultorio(
        "Juan Gómez", "5492494000001", "Odontología general", "2026-10-01 10:00:00", comercio_id=1
    )

    assert "lleno" in resultado


def test_cancelar_turno_exito():
    tools.supabase = FakeSupabase(results=[
        [{"id": 88}],  # turno_existente: tiene uno pendiente
        [{"id": 88}],  # update a cancelado
    ])

    resultado = tools.cancelar_turno_consultorio("5492494000000", comercio_id=1)

    assert "cancelado" in resultado


def test_cancelar_turno_sin_turno_pendiente():
    tools.supabase = FakeSupabase(results=[
        [],  # turno_existente: no tiene ninguno
    ])

    resultado = tools.cancelar_turno_consultorio("5492494000000", comercio_id=1)

    assert "no tiene ningún turno pendiente" in resultado
