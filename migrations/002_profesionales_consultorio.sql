-- Migración: profesionales por consultorio (agendas separadas).
-- Correr esto en Supabase → SQL Editor. Es aditiva: un consultorio sin filas
-- en profesionales_consultorio sigue comportándose exactamente igual que
-- hoy (agenda única compartida) — profesional_id nullable en las tablas
-- existentes significa "agenda general".

CREATE TABLE IF NOT EXISTS profesionales_consultorio (
  id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  comercio_id bigint NOT NULL REFERENCES comercios (id) ON DELETE CASCADE,
  nombre text NOT NULL,
  especialidad text,
  activo boolean NOT NULL DEFAULT true,
  created_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS profesionales_consultorio_comercio_id_idx
  ON profesionales_consultorio (comercio_id);

-- horarios_atencion no está versionada en este repo (se creó a mano en
-- Supabase) — agregar una columna nullable es igual un cambio de metadata
-- seguro, sin reescritura de tabla ni impacto en las filas de celulares.
ALTER TABLE horarios_atencion
  ADD COLUMN IF NOT EXISTS profesional_id bigint REFERENCES profesionales_consultorio (id);

ALTER TABLE turnos_consultorio
  ADD COLUMN IF NOT EXISTS profesional_id bigint REFERENCES profesionales_consultorio (id);

CREATE INDEX IF NOT EXISTS horarios_atencion_profesional_id_idx
  ON horarios_atencion (profesional_id);
CREATE INDEX IF NOT EXISTS turnos_consultorio_profesional_id_idx
  ON turnos_consultorio (profesional_id);
