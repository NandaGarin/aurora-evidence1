# IMPLEMENTATION_STATUS — AURORA Evidence (Development 2)

Kontrak: **v1.0.0**. Status memakai taksonomi: `implemented`, `demo_verified`,
`live_verified`, `research_evaluated`. Satu status tidak membuktikan yang lain.

> **Catatan lingkungan pembuatan.** Fondasi awal repo ini dibuat di sandbox tanpa akses
> PyPI/npm, sehingga **belum ada `uv sync` / `npm install` / pytest yang dijalankan** di
> sana. Verifikasi `demo_verified` harus dijalankan di mesin lokal (OpenCode) setelah
> `uv sync`. Bagian yang sudah diuji unit murni Python dicatat eksplisit di bawah.

## Ringkasan per komponen

| Komponen | Status | Bukti / catatan |
| --- | --- | --- |
| Kontrak v1.0.0 — canonical JCS, hashing, ID | implemented | Logika JCS/hash/ID diuji standalone di Python 3.12 (key ordering, UTF-8, reject NaN, 64-hex, format `ev_`/`fs_`, `atom_set_id` deterministik). Lihat "Pemeriksaan yang sudah dijalankan". |
| Kontrak v1.0.0 — model Pydantic + validator invarian | implemented | Sintaks tervalidasi (`py_compile`). Validasi runtime (probabilities sum, excerpt-substring, forensic status↔assessment, bbox, mode-consistency) memerlukan `uv sync` untuk pytest. |
| Backend: config, DB (SQLAlchemy+Alembic), models, migrasi | implemented | `app/config.py`, `app/db.py`, `app/models/*`, `migrations/0001_initial.py`. Syntax-checked; migrasi belum dijalankan (butuh `uv sync`). |
| Backend: media service (upload/resolve, asset_id) | implemented | `app/services/media.py`. Butuh Pillow (via `uv sync`) untuk dijalankan. |
| Backend: error envelope kontrak | implemented | `app/errors.py` (422/409/404/401). |
| Query planner deterministik | implemented + verified | `retrieval/queries.py`. Diuji standalone py3.12. |
| Provider interfaces + Local corpus BM25 (jalur lokal nyata) | implemented + verified | `retrieval/providers/{base,local_corpus}.py` + `fixtures/corpus/demo_corpus.jsonl`. BM25 diuji standalone (rank_bm25 + fallback pure-python), me-ranking dok relevan di atas. |
| Backend: FastAPI app + routers (health/ready, media, jobs, /retrieve) | todo | Lanjut di OpenCode — lihat NEXT_STEPS.md |
| Worker job persisten (lease/timeout/recovery) | todo | Lanjut di OpenCode |
| Retrieval service (orkestrasi: fetch/dedup/ranking) | todo | Lanjut di OpenCode |
| Dedup / independence groups | todo | Lanjut di OpenCode |
| Ranking RRF + reranker feature-based | todo | Lanjut di OpenCode |
| AI detector (image+text): fixture + adapter HTTP | todo | Lanjut di OpenCode |
| Frontend React/Vite (4 tab + riwayat + ekspor) | todo | Lanjut di OpenCode |
| Tests (unit/contract/integration) + fixtures | todo | Lanjut di OpenCode |
| Docs (docs/*.md, providers.md) | todo | Lanjut di OpenCode |
| Dockerfile + docker-compose | todo | Lanjut di OpenCode |

## Pemeriksaan yang sudah dijalankan (aktual)

```
$ python3.12 -m py_compile backend/aurora_evidence/contract/*.py   # SYNTAX OK
$ python3.12 <standalone test canonical+ids>
  JCS: {"a":"héllo","b":1,"nested":{"y":[1,2,3],"z":true}}
  OK nan rejected
  ev: ev_11111111111141118111111111111111_000001
  fs: fs_11111111111141118111111111111111_000042
  atom_set_id: aset_66de5de2...09f9d1
  ALL CONTRACT LOGIC OK
```

## Yang masih memerlukan kredensial / data (belum live_verified)

- Web search (Tavily/Serper), fact-check/MAFINDO, reverse image search: butuh API key + verifikasi
  endpoint dari dokumentasi resmi saat implementasi. Tanpa kunci → `unconfigured`.
- AI detector gambar (Sightengine/Hive) & teks (GPTZero/Copyleaks): butuh API key; dukungan
  bahasa Indonesia harus diverifikasi. `live_verified` hanya bila request nyata berhasil.
- Dense retrieval: butuh unduhan model multilingual (opsional; BM25-only tetap berfungsi).

## Langkah berikutnya (untuk dilanjutkan di OpenCode lokal)

1. `uv sync --extra dev && npm ci --prefix frontend` lalu jalankan `pytest` untuk memverifikasi model.
2. Selesaikan backend API + worker + migrasi DB, jalankan `scripts/dev.py`.
3. Verifikasi alur demo dengan `prompt-aurora/contoh/01_INPUT_RETRIEVAL.json`.
4. Isi kredensial provider di `.env` untuk menaikkan status ke `live_verified` per capability.
