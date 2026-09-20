"""
Tests de _reservar_stock_atomico / _liberar_stock (tools.py).

Cubren específicamente el bug que arreglamos: antes se leía el stock y se
actualizaba en dos pasos separados, sin ninguna garantía de que no cambiara
en el medio — dos clientes escribiendo a la vez podían reservar la misma
última unidad. El fix agrega un "compare-and-swap": el UPDATE solo se aplica
si el stock sigue siendo exactamente el que se leyó.
"""
import tools
from fake_supabase import FakeSupabase


def test_reservar_stock_exito_descuenta_uno():
    tools.supabase = FakeSupabase(results=[
        [{"stock": 3}],           # SELECT: hay 3 unidades
        [{"id": 1, "stock": 2}],  # UPDATE: el compare-and-swap pegó, bajó a 2
    ])
    assert tools._reservar_stock_atomico(1) is True


def test_reservar_stock_sin_stock_disponible():
    tools.supabase = FakeSupabase(results=[
        [{"stock": 0}],  # SELECT: no queda nada — ni siquiera debería intentar el UPDATE
    ])
    assert tools._reservar_stock_atomico(1) is False


def test_reservar_stock_condicion_de_carrera():
    """
    El caso que antes fallaba: leemos stock=1, pero justo antes de nuestro
    UPDATE, otra conversación ya se llevó la última unidad. El UPDATE con el
    guard .eq("stock", 1) no matchea ninguna fila -> data vacío -> debemos
    detectarlo y decir que no hay stock, en vez de pisar la reserva ajena.
    """
    tools.supabase = FakeSupabase(results=[
        [{"stock": 1}],  # SELECT: leemos 1 unidad disponible
        [],               # UPDATE: alguien más ya cambió el stock -> 0 filas afectadas
    ])
    assert tools._reservar_stock_atomico(1) is False


def test_reservar_stock_ultima_unidad_marca_pendiente():
    """Al llegar a 0, además de descontar tiene que marcar estado_venta=pendiente."""
    capturado = {}

    class FakeQueryConCaptura(FakeSupabase):
        pass

    fake = FakeSupabase(results=[[{"stock": 1}], [{"id": 1, "stock": 0}]])
    original_table = fake.table

    def table_con_captura(name):
        query = original_table(name)
        original_update = query.update

        def update_con_captura(payload, *a, **k):
            capturado.setdefault("updates", []).append(payload)
            return original_update(payload, *a, **k)

        query.update = update_con_captura
        return query

    fake.table = table_con_captura
    tools.supabase = fake

    assert tools._reservar_stock_atomico(1) is True
    assert capturado["updates"][0]["estado_venta"] == "pendiente"
    assert capturado["updates"][0]["stock"] == 0


def test_liberar_stock_suma_uno():
    capturado = {}
    fake = FakeSupabase(results=[[{"stock": 2}], [{"stock": 3}]])
    original_table = fake.table

    def table_con_captura(name):
        query = original_table(name)
        original_update = query.update

        def update_con_captura(payload, *a, **k):
            capturado["payload"] = payload
            return original_update(payload, *a, **k)

        query.update = update_con_captura
        return query

    fake.table = table_con_captura
    tools.supabase = fake

    tools._liberar_stock(1)
    assert capturado["payload"]["stock"] == 3
    assert capturado["payload"]["estado_venta"] == "disponible"


def test_liberar_stock_item_inexistente_no_rompe():
    tools.supabase = FakeSupabase(results=[[]])  # SELECT no encuentra el item
    tools._liberar_stock(999)  # no debe lanzar excepción
