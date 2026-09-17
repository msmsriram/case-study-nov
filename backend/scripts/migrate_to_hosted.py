"""One-off migration: local SQLite + embedded Qdrant  ->  hosted Postgres (DATABASE_URL) + Qdrant Cloud (QDRANT_URL).

    python scripts/migrate_to_hosted.py

Copies rows table by table in foreign-key order and copies vector points with their vectors
(no re-embedding). The knowledge graph already lives in Neo4j Aura; the claim -> episode mapping
travels with the claims table, so graph provenance stays intact. Safe to re-run (upserts).
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from qdrant_client import QdrantClient  # noqa: E402
from qdrant_client import models as qm  # noqa: E402
from sqlalchemy import create_engine, func, select  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

from app.config import settings  # noqa: E402
from app.stores import relational as rel  # noqa: E402
from app.stores import vector  # noqa: E402

if rel.backend_name() != "postgresql" or not settings.qdrant_url:
    sys.exit("Set DATABASE_URL (Postgres) and QDRANT_URL in backend/.env first.")

# ------------------------------------------------------------------ relational
src_engine = create_engine(f"sqlite:///{(settings.data_dir / 'city_intel.db').as_posix()}")
Src = sessionmaker(src_engine, expire_on_commit=False)
print(f"relational: sqlite -> {rel.engine.url.host}")
for model in (rel.City, rel.Run, rel.Source, rel.ClaimRow, rel.ConflictRow, rel.GapRow):
    with Src() as s, rel.SessionLocal() as d:
        rows = s.execute(select(model)).scalars().all()
        for r in rows:
            s.expunge(r)
            d.merge(r)
        d.commit()
        n = d.execute(select(func.count()).select_from(model)).scalar()
    print(f"  {model.__tablename__:<10} copied {len(rows):>4} -> hosted now has {n}")

# autoincrement tables: move the Postgres sequences past the copied ids
with rel.engine.begin() as c:
    for t in ("conflicts", "gaps"):
        c.exec_driver_sql(f"SELECT setval(pg_get_serial_sequence('{t}', 'id'), COALESCE((SELECT MAX(id) FROM {t}), 1))")

# ------------------------------------------------------------------ vector
local = QdrantClient(path=str(settings.data_dir / "qdrant"))
cloud = vector.client()            # creates the collection + payload indexes on the cluster
name = settings.qdrant_collection
offset, total = None, 0
while True:
    points, offset = local.scroll(name, limit=128, offset=offset, with_payload=True, with_vectors=True)
    if points:
        cloud.upsert(name, points=[qm.PointStruct(id=p.id, vector=p.vector, payload=p.payload) for p in points])
        total += len(points)
    if offset is None:
        break
local.close()
print(f"vector: copied {total} points -> cloud collection now has {cloud.count(name).count}")
