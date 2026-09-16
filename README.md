# AURORA Evidence (Development 2)

Modul 2 AURORA: aplikasi lokal untuk **retrieval & reranking bukti multisumber** dan
**AI detector gambar/teks**. Menerima klaim/caption (dan opsional gambar + atom dari
Development 1), mencari bukti relevan dari corpus lokal (dan sumber eksternal opsional),
melakukan deduplikasi, penilaian temporal, dan pemeringkatan, lalu menghasilkan bagian
`retrieval` (`evidence_list` + `forensic_signals`) sesuai kontrak `AuroraBundle v1.0.0`
yang siap dipakai Development 3.

> **Batas kontribusi.** Modul ini **tidak** menetapkan `final_verdict`, tidak menjalankan
> fusi, dan tidak mengkalibrasi keputusan faktual. Skor AI detector adalah **indikasi asal
> konten**, bukan label hoaks dan bukan stance faktual. Stance faktual diberikan Development 3.

Endpoint utama: `POST /api/v1/retrieve`. Port default: **API 8102**, **frontend 5172**.

---

## Status singkat

| Lapisan | Status | Catatan |
| --- | --- | --- |
| Kontrak v1.0.0 (JCS, validator 16 invarian, ID) | ✅ `demo_verified` | Pure stdlib; teruji tanpa instalasi |
| Retrieval lokal (query planner, BM25, dedup, temporal, ranking) | ✅ `demo_verified` | Jalur lokal nyata |
| Forensics / AI detector (interface, normalisasi, registry, fixture, adapter deklaratif) | ✅ `demo_verified` | Adapter HTTP belum dipanggil ke layanan nyata |
| **API HTTP + Worker + Persistence** | 🟡 `implemented` | Ditulis pada Tahap 1; **belum** dijalankan end-to-end di lingkungan pembuatan (verifikasi lokal, lihat di bawah) |
| Frontend React/Vite | ⛔ belum ada | Tahap berikutnya |
| Fetch web + SSRF + adapter multisumber (Tavily/Serper/MAFINDO/reverse image) | ⛔ belum ada | Interface siap; adapter konkret belum |
| Tests pytest, docs/, Docker, evaluasi IR | ⛔ belum ada | Tahap berikutnya |

Rincian lengkap + bukti perintah ada di **[IMPLEMENTATION_STATUS.md](IMPLEMENTATION_STATUS.md)**.
Kontrak bersama: **[prompt-aurora/KONTRAK_BERSAMA.md](prompt-aurora/KONTRAK_BERSAMA.md)**.
Instruksi implementasi: **[prompt-aurora/02_PROMPT_DEVELOPMENT_RETRIEVAL_AI_DETECTOR.md](prompt-aurora/02_PROMPT_DEVELOPMENT_RETRIEVAL_AI_DETECTOR.md)**.

## Prasyarat

- Python 3.11 atau 3.12, [uv](https://docs.astral.sh/uv/)
- Node.js 22+ dan npm (hanya untuk frontend, yang belum ada — API berjalan tanpa ini)
- (Opsional) Docker + Docker Compose (belum disediakan)

## Cara cepat: verifikasi inti tanpa instalasi apa pun

Bagian inti (kontrak, retrieval lokal, forensics) ditulis dengan **Python standard
library saja**, sehingga bisa langsung diuji melalui CLI tanpa `uv sync`:

```bash
cd backend
PYTHONPATH=. python3.11 -m aurora_evidence.cli selftest
PYTHONPATH=. python3.11 -m aurora_evidence.cli retrieve ../prompt-aurora/contoh/01_INPUT_RETRIEVAL.json
PYTHONPATH=. python3.11 -m aurora_evidence.cli search "banjir Monas Jakarta"
PYTHONPATH=. python3.11 -m aurora_evidence.cli capabilities --mode live --profile ../configs/providers.live.toml
```

`selftest` menjalankan bundle demo lewat seluruh pipeline dan menegaskan invarian
kontrak (mencetak angka nyata, bukan sekadar "ok").

## Menjalankan aplikasi penuh (API + worker)

Windows PowerShell — proyek ini tidak memakai `make`, jadi jalankan perintah langsung:

```powershell
# 1) Salin konfigurasi (default = mode demo, tanpa secret)
copy .env.example .env

# 2) Install dependensi backend
uv sync --extra dev

# 3) Inisialisasi database (SQLite lokal) + jalankan API (+ worker in-process)
uv run python scripts/dev.py
```

`scripts/dev.py` menjalankan `alembic upgrade head` lalu `uvicorn` di
`127.0.0.1:8102`. Frontend dijalankan otomatis **jika** folder `frontend/` sudah ada;
selama belum ada, API tetap berjalan sebagai layanan mandiri.

Buka **http://127.0.0.1:8102/docs** untuk OpenAPI. Uji cepat alur demo:

```bash
curl http://127.0.0.1:8102/health
curl http://127.0.0.1:8102/ready
curl -X POST http://127.0.0.1:8102/api/v1/retrieve \
  -H "Content-Type: application/json" -H "Idempotency-Key: demo-001" \
  --data-binary @prompt-aurora/contoh/01_INPUT_RETRIEVAL.json
# ambil job_id dari respons 202, lalu:
curl http://127.0.0.1:8102/api/v1/jobs/<job_id>
curl http://127.0.0.1:8102/api/v1/runs
```

Yang diharapkan: `/ready` → **200 `degraded`** (provider eksternal belum dikonfigurasi,
BM25 lokal `ok`); `POST /api/v1/retrieve` → **202** lalu job `succeeded`; mengirim ulang
`Idempotency-Key` yang sama dengan payload sama → **job_id yang sama**, payload berbeda → **409**.

> Instalasi dependensi pertama memerlukan internet. Mode **demo** berjalan tanpa API key
> dan tanpa jaringan eksternal (fixture berlabel + corpus lokal BM25).

## API (kontrak v1.0.0)

| Method & path | Fungsi |
| --- | --- |
| `GET /health` | Liveness: `{status, service, schema_version}`. |
| `GET /ready` | Kesiapan DB + worker + corpus lokal, serta status tiap capability provider. 503 `not_ready` bila dependensi lokal gagal; 200 `degraded` bila provider opsional `unconfigured`. |
| `POST /api/v1/media` | Unggah gambar (multipart). `asset_id`/`sha256` dari **byte asli**; decode & validasi MIME nyata. |
| `GET /api/v1/media/{asset_id}` | Resolve asset yang tersimpan. |
| `POST /api/v1/retrieve` | Terima `AuroraBundle`, kembalikan **202** `{job_id, case_id, claim_revision, status}`. Header `Idempotency-Key` **wajib**. |
| `GET /api/v1/jobs/{job_id}` | Poll job: `{job_id, status, result, error}`. `result` = bundle atau `null`; kegagalan → `result: null` + `error`. |
| `GET /api/v1/runs` | Riwayat run persisten (tahan restart). |
| `POST /api/v1/validate-bundle` | Validasi bundle terhadap kontrak tanpa menjalankan pekerjaan (untuk pertukaran dengan Dev 1/Dev 3). |

**Idempotency.** Hash payload memakai JCS (RFC 8785) sebelum modifikasi server: key sama +
payload sama → job sama; key sama + payload beda → **409**. Panjang key dibatasi 200 karakter.

**Worker.** Job diproses asinkron oleh worker persisten (lease/timeout/recovery, retry
maks 3). Bisa jalan sebagai thread dalam proses API (default) atau proses terpisah:
`python -m app.workers.worker`. State job tersimpan di DB, jadi keduanya setara.

## Mode demo vs live

- **demo** — deterministik, fixture berlabel jelas + corpus lokal. Tidak mengklaim hasil nyata.
  Fixture detector **hanya** boleh dipakai pada mode demo.
- **live** — menjalankan adapter/model nyata (termasuk komputasi lokal seperti BM25 corpus).
  Provider tanpa kredensial tetap terlihat `unconfigured`/`unsupported`, **bukan** fallback senyap.
  Fixture detector **ditolak** pada mode live.

## Konfigurasi

Salin `.env.example` menjadi `.env`. Semua secret disimpan **server-side**; jangan pernah
menaruhnya di `VITE_*`/frontend/bundle. Variabel penting:

- `AURORA_PORT` (8102), `AURORA_FRONTEND_PORT` (5172), `AURORA_DATA_DIR` (`./var`)
- `AURORA_CORPUS_PATH` — corpus BM25 lokal (default `fixtures/corpus/demo_corpus.jsonl`)
- `AURORA_PROVIDER_PROFILE` — profil provider TOML (default `configs/providers.demo.toml`)
- `AURORA_MODE_DEFAULT` (`demo`), `AURORA_JOB_TIMEOUT`, `AURORA_MAX_PENDING`, `AURORA_RETRIEVAL_BUDGET_SECONDS`
- Public mode: `AURORA_PUBLIC=true` mewajibkan `AURORA_API_TOKEN` (≥32 char), CORS origin eksplisit,
  dan `AURORA_ALLOWED_HOSTS` — divalidasi saat startup.

Pemilihan provider (retrieval & detector) diatur lewat profil di `configs/*.toml`
sehingga penggantian provider **tidak** mengubah business logic.

## Struktur (aktual)

```
backend/
  aurora_evidence/            # inti — Python standard library saja (bisa diuji tanpa uv sync)
    contract/                 # canonical (JCS/RFC 8785), ids, labels, models, validate (16 invarian)
    retrieval/                # queries, dedup, provenance, ranking, textutil, pipeline
      providers/              # base + local_corpus (BM25 Okapi)
    forensics/                # interfaces, normalization, registry
      adapters/               # fixture, httpclient, declarative (adapter HTTP berbasis konfigurasi)
    cli.py                    # selftest / retrieve / search / capabilities / detect-text / ...
  app/                        # lapisan aplikasi (butuh dependensi via uv sync)
    api/                      #   main + routers: health, media, jobs, retrieve
    services/                 #   media, capabilities, retrieval_service (jembatan + persistence)
    workers/                  #   worker job persisten (lease/recovery)
    models/                   #   ORM: Case, Revision, MediaAsset, RetrievalRun, Job
  migrations/                 # Alembic (0001_initial)
configs/                      # providers.demo.toml, providers.live.toml (profil provider)
fixtures/corpus/              # demo_corpus.jsonl (9 dok sintetis berlabel)
prompt-aurora/                # prompt Dev 2 + kontrak bersama + contoh input
scripts/dev.py                # migrasi + API (+ frontend bila ada)
```

Direktori yang **belum** ada dan direncanakan: `frontend/`, `tests/`,
`backend/aurora_evidence/evaluation/`, `docs/`, serta `Dockerfile`/`docker-compose.yml`.

## Keamanan

Backend memegang semua API key; jangan pernah menaruh secret di `VITE_*`/frontend/bundle.
Konten web/OCR diperlakukan sebagai data tak tepercaya. Rencana fetch URL akan memakai
proteksi SSRF sebelum ada sumber web mana pun (Tahap berikutnya).
