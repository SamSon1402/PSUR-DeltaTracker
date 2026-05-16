"""In-memory store. Production: Postgres + a job runner for signal detection."""
from collections import defaultdict
from uuid import UUID

from app.schemas import ICSR, Product, PSUR, PSURDelta, Signal


class InMemoryStore:
    def __init__(self):
        self.products: dict[UUID, Product] = {}
        self.icsrs: dict[UUID, ICSR] = {}                          # icsr_id → ICSR
        self.icsrs_by_product: dict[UUID, list[UUID]] = defaultdict(list)
        self.psurs: dict[UUID, PSUR] = {}
        self.psurs_by_product: dict[UUID, list[UUID]] = defaultdict(list)
        self.deltas: dict[UUID, PSURDelta] = {}
        self.deltas_by_product: dict[UUID, list[UUID]] = defaultdict(list)
        self.signals: dict[UUID, Signal] = {}
        self.signals_by_product: dict[UUID, list[UUID]] = defaultdict(list)

    async def connect(self): pass
    async def disconnect(self): pass

    # ---- Products ----
    def put_product(self, p: Product) -> Product:
        self.products[p.id] = p
        return p

    def get_product(self, product_id: UUID) -> Product | None:
        return self.products.get(product_id)

    def list_products(self) -> list[Product]:
        return list(self.products.values())

    # ---- ICSRs ----
    def put_icsr(self, c: ICSR) -> ICSR:
        self.icsrs[c.id] = c
        self.icsrs_by_product[c.product_id].append(c.id)
        return c

    def get_icsr(self, icsr_id: UUID) -> ICSR | None:
        return self.icsrs.get(icsr_id)

    def list_icsrs(self, product_id: UUID) -> list[ICSR]:
        return [self.icsrs[i] for i in self.icsrs_by_product[product_id]]

    # ---- PSURs ----
    def put_psur(self, p: PSUR) -> PSUR:
        self.psurs[p.id] = p
        self.psurs_by_product[p.product_id].append(p.id)
        return p

    def get_psur(self, psur_id: UUID) -> PSUR | None:
        return self.psurs.get(psur_id)

    def list_psurs(self, product_id: UUID) -> list[PSUR]:
        return [self.psurs[i] for i in self.psurs_by_product[product_id]]

    # ---- Deltas ----
    def put_delta(self, d: PSURDelta) -> PSURDelta:
        self.deltas[d.id] = d
        self.deltas_by_product[d.product_id].append(d.id)
        return d

    def get_delta(self, delta_id: UUID) -> PSURDelta | None:
        return self.deltas.get(delta_id)

    def list_deltas(self, product_id: UUID) -> list[PSURDelta]:
        return [self.deltas[i] for i in self.deltas_by_product[product_id]]

    # ---- Signals ----
    def put_signal(self, s: Signal) -> Signal:
        self.signals[s.id] = s
        if s.id not in self.signals_by_product[s.product_id]:
            self.signals_by_product[s.product_id].append(s.id)
        return s

    def get_signal(self, signal_id: UUID) -> Signal | None:
        return self.signals.get(signal_id)

    def list_signals(self, product_id: UUID, status: str | None = None) -> list[Signal]:
        sigs = [self.signals[i] for i in self.signals_by_product[product_id]]
        if status:
            sigs = [s for s in sigs if s.status.value == status]
        return sigs


store = InMemoryStore()
