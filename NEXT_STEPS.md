# Langkah lanjutan — melanjutkan Development 2 di OpenCode (lokal)

Repo ini sudah berisi **fondasi terverifikasi** (kontrak v1.0.0, backend inti, dan jalur
retrieval BM25 lokal nyata). Bagian pipeline atas, AI detector, frontend, tests, dan docs
**belum selesai** dan dimaksudkan untuk Anda lanjutkan di OpenCode + VSCode.

## Cara melanjutkan

1. Clone repo dan buka foldernya di VSCode:
   ```bash
   git clone https://github.com/NandaGarin/aurora-evidence.git
   cd aurora-evidence
   ```
2. Siapkan lingkungan (butuh internet untuk unduhan pertama):
   ```bash
   copy .env.example .env        # Windows (gunakan cp di macOS/Linux)
   uv sync --extra dev
   uv run alembic upgrade head
   uv run pytest -q              # jalankan setelah tests ditambahkan
   ```
3. Jalankan OpenCode di dalam folder ini, lalu tempel **prompt lanjutan** di bawah.

## Prompt lanjutan untuk OpenCode (tempel apa adanya)

```text
Lanjutkan implementasi AURORA Development 2 (aurora-evidence) sesuai
prompt-aurora/02_PROMPT_DEVELOPMENT_RETRIEVAL_AI_DETECTOR.md dan
prompt-aurora/KONTRAK_BERSAMA.md. Pertahankan seluruh kode yang sudah ada dan
JANGAN mengubah kontrak v1.0.0 di backend/aurora_evidence/contract/.

Kondisi saat ini (sudah ada & terverifikasi standalone, jangan ditulis ulang):
- backend/aurora_evidence/contract/{canonical,ids,models}.py — kontrak + JCS + hashing + ID.
- backend/app/{config,db,errors}.py, backend/app/models/*, migrations/0001_initial.py — DB SQLite + Alembic.
- backend/app/services/media.py — upload/resolve media (asset_id content-addressed).
- backend/aurora_evidence/retrieval/queries.py — query planner deterministik.
- backend/aurora_evidence/retrieval/providers/{base,local_corpus}.py — interface + BM25 lokal nyata.
- fixtures/corpus/demo_corpus.jsonl — corpus demo sintetis.

Selesaikan yang BELUM ada, ikuti struktur di README:
1. backend/app/api/main.py + routers: GET /health, GET /ready (DB+worker+capabilities),
   POST /api/v1/media, GET /api/v1/media/{asset_id}, GET /api/v1/jobs/{job_id},
   POST /api/v1/retrieve (HTTP 202 + Idempotency-Key wajib, hash payload pakai JCS).
2. backend/app/workers/ — worker job persisten (lease/timeout/recovery, restart-safe).
3. backend/app/services/retrieval_service.py — orkestrasi: plan queries -> jalankan provider
   paralel (budget/concurrency/timeout) -> ekstraksi (trafilatura) -> dedup + independence_group_id
   -> ranking RRF + reranker feature-based (CPU) -> susun Retrieval (evidence_list) sesuai kontrak.
4. backend/aurora_evidence/retrieval/{fetching,indexing,dedup,ranking,provenance}.py.
5. backend/aurora_evidence/forensics/ — interface ImageAIDetector/TextAIDetector + registry +
   provider fixture/demo + minimal SATU adapter HTTP konkret gambar & satu teks (normalisasi
   raw_score/raw_scale, error handling 401/403/429/5xx/timeout, forensic_signals sesuai kontrak).
   Skor detector TIDAK menjadi stance/verdict; hanya forensic_signals.
6. configs/ — dua profil provider YAML (buktikan penggantian provider tanpa ubah business logic).
7. frontend/ — React+TS+Vite, UI Indonesia, tab Bukti/Asal Gambar/Deteksi AI/Jejak Pencarian,
   riwayat kasus, ekspor/impor JSON/CSV/ZIP. VITE_API_URL -> http://127.0.0.1:8102.
8. scripts/dev.py (jalankan alembic + uvicorn app.api.main:app + vite), scripts/*.py untuk
   ingest/build-index/retrieve/evaluate/benchmark-providers/export-results.
9. tests/{unit,contract,integration} + fixtures ("AI-generated tapi klaim benar",
   "kamera/manusia tapi caption salah"), docs/{architecture,api,data,evaluation,limitations,handoff,providers}.md,
   Dockerfile + docker-compose.yml.

Aturan kontrak yang wajib dijaga: retrieval dengan analysis=null -> atom_set_id=null & atom_ids=[];
evidence_list hanya bukti nyata (AI detector TERPISAH di forensic_signals); excerpt substring dari
content.text; content.sha256 = sha256 UTF-8 content.text; provider tanpa kredensial = unconfigured
(bukan fallback fixture senyap); semua run.mode = bundle.mode; SSRF guard pada fetch URL.

Mulai dari alur DEMO end-to-end dengan prompt-aurora/contoh/01_INPUT_RETRIEVAL.json + corpus lokal,
jalankan pytest & browser smoke, catat hasil AKTUAL di IMPLEMENTATION_STATUS.md. Jangan berhenti pada rencana.
```

## Verifikasi cepat yang bisa Anda jalankan sekarang (tanpa menunggu sisa fitur)

Logika inti sudah bisa dites tanpa server:

```bash
uv run python - <<'PY'
from aurora_evidence.retrieval.providers.local_corpus import LocalCorpusProvider
p = LocalCorpusProvider("fixtures/corpus/demo_corpus.jsonl"); p.load()
print(p.capability().status, p.capability().message)
for h in p.search("banjir Jakarta Monas", limit=3):
    print(round(h.provider_score,3), h.raw["corpus_id"], h.title)
PY
```

Harus menampilkan corpus `ok` dan me-ranking dokumen banjir/Monas di atas.
