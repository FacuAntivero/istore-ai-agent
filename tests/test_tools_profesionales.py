"""
Tests de agendas por profesional en la vertical "consultorio":
consultar_horarios_consultorio y agendar_turno_consultorio cuando el
comercio tiene varios profesionales configurados (profesionales_consultorio).

Mismo estilo que test_tools_consultorio.py: FakeSupabase con una cola de
resultados en el orden exacto en que el código llama a .table(...).
"""
import tools
from fake_supabase import FakeSupabase

CONFIG_DEFAULT = {"horas_anticipacion_recordatorio": 24, "max_turnos_por_horario": 1}

PROFESIONALES_DOS = [
    {"id": 10, "nombre": "Dra. Bianchi", "especialidad": "Odontología general"},
    {"id": 20, "nombre": "Dr. Pérez", "especialidad": "Ortodoncia"},
]

PROFESIONALES_MISMA_ESPECIALIDAD = [
    {"id": 10, "nombre": "Dra. Bianchi", "especialidad": "Odontología general"},
    {"id": 30, "nombre": "Dr. Gómez", "especialidad": "Odontología general"},
]


def test_agendar_con_profesional_explicito(monkeypatch):
    monkeypatch.setattr(tools, "_programar_upstash_desde_tools", lambda *a, **k: None)
    tools.supabase = FakeSupabase(results=[
        [CONFIG_DEFAULT],       # obtener_configuracion_consultorio
        PROFESIONALES_DOS,      # obtener_profesionales_consultorio
        [],                     # turno_existente: no tiene uno previo
        [],                     # query_cupos (filtrado por profesional): libre
        [{"id": 1}],            # insert
    ])

    resultado = tools.agendar_turno_consultorio(
        "María Pérez", "5492494000000", "Odontología general", "2026-10-01 10:00:00",
        profesional="Dra. Bianchi", comercio_id=1
    )

    assert "agendado" in resultado


def test_agendar_auto_resuelve_por_especialidad(monkeypatch):
    monkeypatch.setattr(tools, "_programar_upstash_desde_tools", lambda *a, **k: None)
    tools.supabase = FakeSupabase(results=[
        [CONFIG_DEFAULT],
        PROFESIONALES_DOS,
        [],
        [],
        [{"id": 2}],
    ])

    # No se pasa "profesional" — hay un único profesional para "Ortodoncia",
    # así que se resuelve solo.
    resultado = tools.agendar_turno_consultorio(
        "Juan Gómez", "5492494000001", "Ortodoncia", "2026-10-01 11:00:00", comercio_id=1
    )

    assert "agendado" in resultado


def test_agendar_ambiguo_por_especialidad_compartida(monkeypatch):
    tools.supabase = FakeSupabase(results=[
        [CONFIG_DEFAULT],
        PROFESIONALES_MISMA_ESPECIALIDAD,
    ])

    resultado = tools.agendar_turno_consultorio(
        "Ana López", "5492494000002", "Odontología general", "2026-10-01 10:00:00", comercio_id=1
    )

    assert "Dra. Bianchi" in resultado and "Dr. Gómez" in resultado


def test_agendar_profesional_no_encontrado(monkeypatch):
    tools.supabase = FakeSupabase(results=[
        [CONFIG_DEFAULT],
        PROFESIONALES_DOS,
    ])

    resultado = tools.agendar_turno_consultorio(
        "Ana López", "5492494000002", "Odontología general", "2026-10-01 10:00:00",
        profesional="Dr. Inexistente", comercio_id=1
    )

    assert "Dra. Bianchi" in resultado and "Dr. Pérez" in resultado


def test_agendar_cupos_independientes_por_profesional(monkeypatch):
    monkeypatch.setattr(tools, "_programar_upstash_desde_tools", lambda *a, **k: None)
    tools.supabase = FakeSupabase(results=[
        [CONFIG_DEFAULT],
        PROFESIONALES_DOS,
        [],   # turno_existente
        [],   # query_cupos: vacío porque el filtro es por profesional_id — el otro
              # profesional puede tener un turno a la misma hora sin que cuente acá
        [{"id": 3}],
    ])

    resultado = tools.agendar_turno_consultorio(
        "Carla Ruiz", "5492494000003", "Ortodoncia", "2026-10-01 10:00:00",
        profesional="Dr. Pérez", comercio_id=1
    )

    assert "agendado" in resultado


def test_agendar_reprograma_cambia_profesional(monkeypatch):
    monkeypatch.setattr(tools, "_programar_upstash_desde_tools", lambda *a, **k: None)
    tools.supabase = FakeSupabase(results=[
        [CONFIG_DEFAULT],
        PROFESIONALES_DOS,
        [{"id": 55, "profesional_id": 10}],  # turno_existente: tenía turno con Dra. Bianchi
        [],                                   # query_cupos para el nuevo profesional: libre
        [{"id": 55}],                         # update
    ])

    resultado = tools.agendar_turno_consultorio(
        "María Pérez", "5492494000000", "Ortodoncia", "2026-10-03 15:00:00",
        profesional="Dr. Pérez", comercio_id=1
    )

    assert "reprogramado" in resultado


def test_consultar_horarios_desglosa_por_profesional():
    tools.supabase = FakeSupabase(results=[
        PROFESIONALES_DOS,      # obtener_profesionales_consultorio
        [
            {"id": 1, "dia_semana": "Lunes", "hora_apertura": "09:00:00", "hora_cierre": "13:00:00", "profesional_id": 10},
            {"id": 2, "dia_semana": "Martes", "hora_apertura": "14:00:00", "hora_cierre": "20:00:00", "profesional_id": 20},
        ],
    ])

    resultado = tools.consultar_horarios_consultorio(comercio_id=1)

    assert "Dra. Bianchi" in resultado and "Dr. Pérez" in resultado


def test_consultar_horarios_sin_profesionales_formato_simple():
    tools.supabase = FakeSupabase(results=[
        [],                      # obtener_profesionales_consultorio: sin profesionales
        [
            {"id": 1, "dia_semana": "Lunes", "hora_apertura": "09:00:00", "hora_cierre": "18:00:00", "profesional_id": None},
        ],
    ])

    resultado = tools.consultar_horarios_consultorio(comercio_id=1)

    assert "Lunes" in resultado and "None" not in resultado
