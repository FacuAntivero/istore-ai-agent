-- Migración: soporte para la vertical "consultorio" (Meta WhatsApp Cloud API).
-- Correr esto en Supabase → SQL Editor. Es aditivo: no borra ni modifica
-- ninguna fila existente de "celulares"/Novva (los ALTER TABLE agregan
-- columnas nullable, y las filas actuales quedan con vertical='celulares'
-- por el DEFAULT).

-- 1. Nuevas columnas en comercios, para identificar la vertical y guardar
--    las credenciales de Meta por tenant.
ALTER TABLE comercios
  ADD COLUMN IF NOT EXISTS vertical text NOT NULL DEFAULT 'celulares',
  ADD COLUMN IF NOT EXISTS meta_phone_number_id text,
  ADD COLUMN IF NOT EXISTS meta_access_token text;

CREATE UNIQUE INDEX IF NOT EXISTS comercios_meta_phone_number_id_key
  ON comercios (meta_phone_number_id)
  WHERE meta_phone_number_id IS NOT NULL;

-- 2. Configuración propia de cada consultorio (1:1 con comercio_id).
CREATE TABLE IF NOT EXISTS configuracion_consultorios (
  id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  comercio_id bigint NOT NULL REFERENCES comercios (id) ON DELETE CASCADE,
  especialidades text,
  direccion_fisica text,
  telefono_dueno text,
  faq_texto text,
  horas_anticipacion_recordatorio integer NOT NULL DEFAULT 24,
  max_turnos_por_horario integer NOT NULL DEFAULT 1,
  created_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (comercio_id)
);

-- 3. Preguntas frecuentes por comercio (se cargan todas al armar el prompt).
CREATE TABLE IF NOT EXISTS preguntas_frecuentes (
  id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  comercio_id bigint NOT NULL REFERENCES comercios (id) ON DELETE CASCADE,
  pregunta text NOT NULL,
  respuesta text NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS preguntas_frecuentes_comercio_id_idx
  ON preguntas_frecuentes (comercio_id);

-- 4. Turnos del consultorio (separada de turnos_clientes, que está atada a
--    reserva de stock de celulares y no aplica a este rubro).
CREATE TABLE IF NOT EXISTS turnos_consultorio (
  id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  comercio_id bigint NOT NULL REFERENCES comercios (id) ON DELETE CASCADE,
  paciente_nombre text NOT NULL,
  telefono text NOT NULL,
  especialidad text,
  fecha_turno timestamp NOT NULL,
  estado text NOT NULL DEFAULT 'pendiente' CHECK (estado IN ('pendiente', 'procesando', 'cancelado', 'completado')),
  recordatorio_enviado boolean NOT NULL DEFAULT false,
  created_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS turnos_consultorio_comercio_id_idx ON turnos_consultorio (comercio_id);
CREATE INDEX IF NOT EXISTS turnos_consultorio_telefono_idx ON turnos_consultorio (telefono);

-- Nota: horarios_atencion NO se toca — ya es genérica (comercio_id +
-- dia_semana + hora_apertura/cierre) y se reutiliza tal cual para
-- consultorios.
