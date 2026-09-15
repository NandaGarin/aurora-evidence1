# AURORA Evidence (Development 2)

Modul 2 AURORA: aplikasi lokal untuk **retrieval & reranking bukti multisumber** dan
**AI detector gambar/teks**. Menerima klaim/caption (dan opsional gambar + atom dari
Development 1), mencari bukti relevan dari corpus lokal/web/fact-check/provenance gambar,
melakukan deduplikasi & pemeringkatan, lalu menghasilkan `retrieval` (evidence_list +
forensic_signals) sesuai kontrak `AuroraBundle v1.0.0` yang siap dipakai Development 3.

> **Batas kontribusi.** Modul ini **tidak** menetapkan `final_verdict`, tidak menjalankan
> fusi, dan tidak mengkalibrasi keputusan faktual. Skor AI detector adalah **indikasi asal
> konten**, bukan label hoaks dan bukan stance faktual. Stance faktual diberikan Development 3.

Endpoint utama: `POST /api/v1/retrieve`. Port default: **API 8102**, **frontend 5172**.

---

## Status singkat

Repo ini sedang dibangun bertahap. Lihat **[IMPLEMENTATION_STATUS.md](IMPLEMENTATION_STATUS.md)**
untuk rincian bagian yang `implemented` / `demo_verified` / belum. Kontrak bersama ada di
**[prompt-aurora/KONTRAK_BERSAMA.md](prompt-aurora/KONTRAK_BERSAMA.md)**; instruksi implementasi
lengkap di **[prompt-aurora/02_PROMPT_DEVELOPMENT_RETRIEVAL_AI_DETECTOR.md](prompt-aurora/02_PROMPT_DEVELOPMENT_RETRIEVAL_AI_DETECTOR.md)**.

## Prasyarat

- Python 3.11 atau 3.12, [uv](https://docs.astral.sh/uv/)
- Node.js 22+ dan npm (untuk frontend)
- (Opsional) Docker + Docker Compose

## Menjalankan (tanpa Docker)

Windows PowerShell — proyek ini tidak memakai `make`, jadi jalankan perintah langsung:

```powershell
# 1) Salin konfigurasi (default = mode demo, tanpa secret)
copy .env.example .env

# 2) Install dependensi backend + frontend
uv sync --extra dev
npm ci --prefix frontend

# 3) Inisialisasi database (SQLite lokal)
uv run alembic upgrade head

# 4) Jalankan backend + frontend
uv run python scripts/dev.py
```

Lalu buka **http://127.0.0.1:5172** (UI) dan **http://127.0.0.1:8102/docs** (OpenAPI).

> Instalasi dependensi pertama memerlukan internet. Mode **demo** berjalan tanpa API key
> dan tanpa jaringan eksternal (memakai fixture berlabel + corpus lokal BM25). Provider
> web/fact-check/reverse-image dan AI detector bersifat **opt-in** dan tampil `unconfigured`
> hingga kredensialnya diisi di `.env`.

## Mode demo vs live

- **demo** — deterministik, fixture berlabel jelas + corpus lokal. Tidak mengklaim hasil nyata.
- **live** — menjalankan adapter/model nyata (termasuk komputasi lokal seperti BM25 corpus).
  Provider tanpa kredensial tetap terlihat `unconfigured`/`unavailable`, bukan fallback senyap.

## Struktur

```
backend/
  aurora_evidence/
    contract/        # model Pydantic + JCS + hashing + ID (kontrak v1.0.0)
    retrieval/       # queries, providers, fetching, indexing, dedup, ranking, provenance
    forensics/       # interfaces, registry, adapters, normalization (AI detector)
    evaluation/      # metrik retrieval & detector
  app/               # api, services, models (DB), workers
frontend/            # React + TypeScript + Vite (UI Indonesia)
configs/             # profil provider (YAML) — penggantian provider tanpa ubah kode
prompt-aurora/       # prompt + kontrak bersama + contoh input
tests/               # unit / contract / integration
docs/                # arsitektur, api, data, evaluasi, batasan, handoff, providers
```

## Keamanan

Backend memegang semua API key; jangan pernah menaruh secret di `VITE_*`/frontend/bundle.
Fetch URL memakai proteksi SSRF; konten web/OCR diperlakukan sebagai data tak tepercaya.
