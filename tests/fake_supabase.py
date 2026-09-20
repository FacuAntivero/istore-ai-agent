"""
Doble de prueba mínimo para el cliente de Supabase.

Los módulos reales (tools.py, auth.py) hacen cadenas del estilo
supabase.table("x").select(...).eq(...).execute(), y esperan un objeto con
.data en el resultado. FakeSupabase deja cargar una cola de resultados que
se van devolviendo en el mismo orden en que el código real llama a
.table(...) — sin pegarle a una base real.
"""


class FakeResult:
    def __init__(self, data):
        self.data = data


class FakeQuery:
    def __init__(self, result):
        self._result = result

    # Todos estos métodos solo necesitan devolver self para poder encadenar
    # .select().eq().eq() como hace el código real.
    def select(self, *a, **k):
        return self

    def eq(self, *a, **k):
        return self

    def neq(self, *a, **k):
        return self

    def update(self, *a, **k):
        return self

    def insert(self, *a, **k):
        return self

    def delete(self, *a, **k):
        return self

    def ilike(self, *a, **k):
        return self

    def in_(self, *a, **k):
        return self

    def order(self, *a, **k):
        return self

    def limit(self, *a, **k):
        return self

    def gt(self, *a, **k):
        return self

    def execute(self):
        return self._result


class FakeAuth:
    """Simula supabase.auth.get_user(token)."""

    def __init__(self, user_id=None, exc=None):
        self._user_id = user_id
        self._exc = exc

    def get_user(self, token):
        if self._exc:
            raise self._exc
        user = type("U", (), {"id": self._user_id})() if self._user_id else None
        return type("R", (), {"user": user})()


class FakeSupabase:
    """
    results: lista de FakeResult (o listas de dicts, se envuelven solas),
    consumida en el mismo orden en que el código llama a .table(...).
    """

    def __init__(self, results=None, auth=None):
        self._results = [r if isinstance(r, FakeResult) else FakeResult(r) for r in (results or [])]
        self.auth = auth or FakeAuth()

    def table(self, name):
        if not self._results:
            raise AssertionError(
                f"FakeSupabase: se llamó a table('{name}') pero no quedan resultados cargados en la cola"
            )
        return FakeQuery(self._results.pop(0))
